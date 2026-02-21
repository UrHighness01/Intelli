from pathlib import Path
import json
import os
import hashlib
import secrets
import time
from typing import Optional, Dict, Set
import traceback

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:
    keyring = None  # type: ignore
    _HAS_KEYRING = False

USERS_PATH = Path(__file__).with_name('users.json')
REVOKED_PATH = Path(__file__).with_name('revoked_tokens.json')
KEYRING_SERVICE = 'intelli-agent-gateway-users'

# in-memory token stores
_TOKENS: Dict[str, Dict] = {}
_REFRESH_TOKENS: Dict[str, Dict] = {}

# persistent revocation set: {sha256_hex: expiry_unix}
_REVOKED: Dict[str, float] = {}

# token lifetimes
ACCESS_EXPIRE = int(os.environ.get('AGENT_GATEWAY_ACCESS_EXPIRE', 3600))
REFRESH_EXPIRE = int(os.environ.get('AGENT_GATEWAY_REFRESH_EXPIRE', 7 * 24 * 3600))


def _token_hash(token: str) -> str:
    """SHA-256 hex digest of a token string (used for revocation storage)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _load_revoked() -> None:
    """Load the on-disk revocation list into `_REVOKED`, pruning expired entries."""
    global _REVOKED
    try:
        if REVOKED_PATH.exists():
            with REVOKED_PATH.open('r', encoding='utf-8') as f:
                raw: Dict[str, float] = json.load(f)
            now = time.time()
            _REVOKED = {h: exp for h, exp in raw.items() if exp > now}
        else:
            _REVOKED = {}
    except Exception:
        _REVOKED = {}


def _save_revoked() -> None:
    """Persist `_REVOKED` to disk, pruning expired entries first."""
    global _REVOKED
    now = time.time()
    _REVOKED = {h: exp for h, exp in _REVOKED.items() if exp > now}
    try:
        with REVOKED_PATH.open('w', encoding='utf-8') as f:
            json.dump(_REVOKED, f, indent=2)
    except Exception:
        pass


def _is_revoked(token: str) -> bool:
    h = _token_hash(token)
    exp = _REVOKED.get(h)
    if exp is None:
        return False
    if time.time() > exp:
        # expired — clean up lazily
        try:
            del _REVOKED[h]
        except Exception:
            pass
        return False
    return True


def _hash_password(password: str, salt: Optional[bytes] = None) -> Dict:
    if salt is None:
        salt = secrets.token_bytes(16)
    pwd = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return {'salt': salt.hex(), 'hash': pwd.hex()}


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    pwd = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100_000)
    return pwd.hex() == hash_hex


def _load_users() -> Dict[str, Dict]:
    try:
        if USERS_PATH.exists():
            with USERS_PATH.open('r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_users(users: Dict[str, Dict]):
    try:
        with USERS_PATH.open('w', encoding='utf-8') as f:
            json.dump(users, f, indent=2)
    except Exception:
        pass


def create_user(username: str, password: str, roles=('admin',)) -> bool:
    users = _load_users()
    if username in users:
        return False
    h = _hash_password(password)
    # attempt to store secret in OS keyring
    if _HAS_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, username, json.dumps({'salt': h['salt'], 'hash': h['hash']}))
            # store roles only in users.json
            users[username] = {'roles': list(roles)}
            _save_users(users)
            return True
        except Exception:
            pass
    if True:  # fallback: store salt/hash in users.json (less secure)
        try:
            users[username] = {'salt': h['salt'], 'hash': h['hash'], 'roles': list(roles)}
            _save_users(users)
            return True
        except Exception:
            return False


def authenticate_user(username: str, password: str) -> Optional[Dict[str, str]]:
    users = _load_users()
    u = users.get(username)
    if not u:
        return None
    # try to fetch secret from keyring
    salt = None
    hashv = None
    if _HAS_KEYRING:
        try:
            sec = keyring.get_password(KEYRING_SERVICE, username)
            if sec:
                try:
                    j = json.loads(sec)
                    salt = j.get('salt')
                    hashv = j.get('hash')
                except Exception:
                    pass
        except Exception:
            pass

    # fallback to users.json stored values
    if not salt or not hashv:
        salt = u.get('salt')
        hashv = u.get('hash')

    if not salt or not hashv:
        return None

    if not _verify_password(password, salt, hashv):
        return None
    # create access + refresh tokens
    at = secrets.token_urlsafe(32)
    rt = secrets.token_urlsafe(48)
    now = int(time.time())
    _TOKENS[at] = {'username': username, 'expires': now + ACCESS_EXPIRE}
    _REFRESH_TOKENS[rt] = {'username': username, 'expires': now + REFRESH_EXPIRE}
    return {'access_token': at, 'refresh_token': rt}


def get_user_for_token(token: str) -> Optional[Dict]:
    # Check persistent revocation list first
    if _is_revoked(token):
        return None
    info = _TOKENS.get(token)
    if not info:
        return None
    if int(time.time()) > info.get('expires', 0):
        # expired
        try:
            del _TOKENS[token]
        except Exception:
            pass
        return None
    users = _load_users()
    u = users.get(info['username'], {})
    return {
        'username': info['username'],
        'roles': u.get('roles', []),
        'allowed_tools': u.get('allowed_tools'),  # None → no restriction
    }


def refresh_access_token(refresh_token: str) -> Optional[str]:
    # Check persistent revocation list before allowing a refresh
    if _is_revoked(refresh_token):
        return None
    info = _REFRESH_TOKENS.get(refresh_token)
    if not info:
        return None
    if int(time.time()) > info.get('expires', 0):
        try:
            del _REFRESH_TOKENS[refresh_token]
        except Exception:
            pass
        return None
    username = info['username']
    at = secrets.token_urlsafe(32)
    _TOKENS[at] = {'username': username, 'expires': int(time.time()) + ACCESS_EXPIRE}
    return at


def revoke_token(token: str) -> bool:
    removed = False
    # Determine expiry of the token so the revocation entry can be pruned later
    expiry = 0.0
    if token in _TOKENS:
        expiry = float(_TOKENS[token].get('expires', 0))
        try:
            del _TOKENS[token]
            removed = True
        except Exception:
            pass
    if token in _REFRESH_TOKENS:
        expiry = max(expiry, float(_REFRESH_TOKENS[token].get('expires', 0)))
        try:
            del _REFRESH_TOKENS[token]
            removed = True
        except Exception:
            pass
    # Always add to the persistent revocation list with an expiry so it can be
    # cleaned up automatically after the token's natural lifetime.
    if expiry == 0.0:
        expiry = time.time() + REFRESH_EXPIRE  # worst-case lifetime
    _REVOKED[_token_hash(token)] = expiry
    _save_revoked()
    return removed


def check_role(token: str, role: str) -> bool:
    info = get_user_for_token(token)
    if not info:
        return False
    return role in info.get('roles', [])


# ---------------------------------------------------------------------------
# Per-user scoped tool permissions
# ---------------------------------------------------------------------------

def get_user_allowed_tools(username: str) -> Optional[list]:
    """Return the ``allowed_tools`` list for *username*, or ``None`` if no
    restriction is set (meaning all tools are permitted)."""
    users = _load_users()
    u = users.get(username)
    if u is None:
        return None
    # ``allowed_tools`` absent or None → no restriction
    return u.get('allowed_tools')


def set_user_allowed_tools(username: str, tools: Optional[list]) -> bool:
    """Persist the ``allowed_tools`` allow-list for *username*.

    Pass ``None`` (or an empty list) to remove any existing restriction.
    Returns ``False`` if the user does not exist.
    """
    users = _load_users()
    if username not in users:
        return False
    if tools is None or tools == []:
        users[username].pop('allowed_tools', None)
    else:
        users[username]['allowed_tools'] = sorted(set(tools))
    _save_users(users)
    return True


# ---------------------------------------------------------------------------
# User lifecycle management
# ---------------------------------------------------------------------------

def list_users():
    """Return a list of user summaries (no secrets).

    Each element is ``{'username': str, 'roles': list, 'has_tool_restrictions': bool}``.
    """
    users = _load_users()
    result = []
    for u, d in users.items():
        result.append({
            'username': u,
            'roles': d.get('roles', []),
            'has_tool_restrictions': bool(d.get('allowed_tools')),
        })
    return result


def delete_user(username: str) -> bool:
    """Remove *username* permanently.

    Returns ``False`` when the user does not exist or is the built-in ``admin``
    (the default admin cannot be deleted via the API).
    """
    if username == 'admin':
        return False
    users = _load_users()
    if username not in users:
        return False
    del users[username]
    _save_users(users)
    # Best-effort removal from keyring
    if _HAS_KEYRING:
        try:
            keyring.delete_password(KEYRING_SERVICE, username)
        except Exception:
            pass
    return True


def change_password(username: str, new_password: str) -> bool:
    """Set a new password for *username*.

    Returns ``False`` when the user does not exist.
    """
    users = _load_users()
    if username not in users:
        return False
    h = _hash_password(new_password)
    if _HAS_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, username,
                                 json.dumps({'salt': h['salt'], 'hash': h['hash']}))
            # Ensure salt/hash are NOT left in the JSON file when keyring is used
            users[username].pop('salt', None)
            users[username].pop('hash', None)
        except Exception:
            users[username].update({'salt': h['salt'], 'hash': h['hash']})
    else:
        users[username].update({'salt': h['salt'], 'hash': h['hash']})
    _save_users(users)
    return True


# Create default admin from env if the admin user does not yet exist.
# Called at import time AND lazily at login time so monkeypatch works in tests.
def _ensure_default_admin():
    users = _load_users()
    if 'admin' in users:
        return
    default_pw = os.environ.get('AGENT_GATEWAY_ADMIN_PASSWORD')
    if default_pw:
        create_user('admin', default_pw, roles=['admin'])


# Load persisted revocation list on module import
_load_revoked()
_ensure_default_admin()

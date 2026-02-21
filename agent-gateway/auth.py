from pathlib import Path
import json
import os
import hashlib
import secrets
import time
from typing import Optional, Dict
import keyring
import traceback

USERS_PATH = Path(__file__).with_name('users.json')
KEYRING_SERVICE = 'intelli-agent-gateway-users'

# in-memory token stores
_TOKENS: Dict[str, Dict] = {}
_REFRESH_TOKENS: Dict[str, Dict] = {}

# token lifetimes
ACCESS_EXPIRE = int(os.environ.get('AGENT_GATEWAY_ACCESS_EXPIRE', 3600))
REFRESH_EXPIRE = int(os.environ.get('AGENT_GATEWAY_REFRESH_EXPIRE', 7 * 24 * 3600))


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
    try:
        keyring.set_password(KEYRING_SERVICE, username, json.dumps({'salt': h['salt'], 'hash': h['hash']}))
        # store roles only in users.json
        users[username] = {'roles': list(roles)}
        _save_users(users)
        return True
    except Exception:
        # fallback: store salt/hash in users.json (less secure)
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
        # keyring unavailable; continue to fallback
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
    return {'username': info['username'], 'roles': users.get(info['username'], {}).get('roles', [])}


def refresh_access_token(refresh_token: str) -> Optional[str]:
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
    if token in _TOKENS:
        try:
            del _TOKENS[token]
            removed = True
        except Exception:
            pass
    if token in _REFRESH_TOKENS:
        try:
            del _REFRESH_TOKENS[token]
            removed = True
        except Exception:
            pass
    return removed


def check_role(token: str, role: str) -> bool:
    info = get_user_for_token(token)
    if not info:
        return False
    return role in info.get('roles', [])


# on import, create default admin from env if present and users empty
def _ensure_default_admin():
    users = _load_users()
    if users:
        return
    default_pw = os.environ.get('AGENT_GATEWAY_ADMIN_PASSWORD')
    if default_pw:
        create_user('admin', default_pw, roles=['admin'])


_ensure_default_admin()

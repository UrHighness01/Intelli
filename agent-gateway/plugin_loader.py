"""Extension / Plugin System for Intelli Gateway.

Plugins are Python packages that live in ``INTELLI_PLUGINS_DIR``
(default: ``~/.intelli/plugins/``).  Each plugin is a directory containing:

  intelli_plugin.json   — machine-readable manifest (required)
  main.py               — Python entry-point (required for tool plugins)
  README.md             — human-readable docs (optional)
  requirements.txt      — extra pip deps (optional; user must install manually)

Manifest schema (intelli_plugin.json)
--------------------------------------
::

    {
        "name":        "intelli-weather",        # unique slug, kebab-case
        "version":     "1.0.0",
        "description": "Fetch current weather via OpenWeatherMap",
        "author":      "yourname",
        "homepage":    "https://github.com/yourname/intelli-weather",
        "tools": [
            {
                "name":        "weather_get",
                "description": "Get current weather for a city",
                "module":      "main",
                "function":    "weather_get",
                "args": {
                    "city": {"type": "string", "required": true,
                             "description": "City name, e.g. London"}
                }
            }
        ],
        "env_vars": ["OPENWEATHER_API_KEY"]
    }

Install sources
---------------
- **Local path**: existing directory copied into plugins dir
- **HTTP/HTTPS .zip**: downloaded and extracted
- **GitHub shorthand** ``owner/repo[@ref]``: resolved to a zip download

Enable / disable
----------------
Enabled state is persisted to ``INTELLI_PLUGINS_STATE``
(default: ``~/.intelli/plugins_state.json``).

Public API
----------
    load_all()                    → None   (call once at startup)
    install(source)               → dict   (manifest of installed plugin)
    uninstall(slug)               → bool
    enable(slug)                  → bool
    disable(slug)                 → bool
    list_plugins()                → list[dict]
    get_plugin(slug)              → Optional[dict]
    reload_plugin(slug)           → dict
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import types
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PLUGINS_DIR = Path(os.environ.get('INTELLI_PLUGINS_DIR', Path.home() / '.intelli' / 'plugins'))
STATE_FILE   = Path(os.environ.get('INTELLI_PLUGINS_STATE', Path.home() / '.intelli' / 'plugins_state.json'))
_MANIFEST    = 'intelli_plugin.json'
_TIMEOUT     = int(os.environ.get('INTELLI_PLUGIN_DOWNLOAD_TIMEOUT', '30'))

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_loaded_modules: Dict[str, types.ModuleType] = {}   # slug → imported module
_registry_snapshot: Dict[str, List[str]] = {}        # slug → list of registered tool names


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_state() -> Dict[str, Dict[str, Any]]:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_state(state: Dict[str, Dict[str, Any]]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _read_manifest(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    mf = plugin_dir / _MANIFEST
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding='utf-8'))
    except Exception:
        return None


def _slug(manifest: Dict[str, Any]) -> str:
    return manifest.get('name', '').strip().lower().replace(' ', '-')


_PLUGIN_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


def _safe_plugin_slug(slug: str) -> str:
    """Validate *slug* and verify it stays inside PLUGINS_DIR.

    Raises ValueError if the slug is syntactically invalid or the resolved path
    would escape the plugins directory (prevents path-traversal attacks).
    """
    clean = slug.strip().lower()
    if not _PLUGIN_SLUG_RE.match(clean):
        raise ValueError(f'Invalid plugin slug: {slug!r}')
    candidate = (PLUGINS_DIR / clean).resolve()
    try:
        candidate.relative_to(PLUGINS_DIR.resolve())
    except ValueError:
        raise ValueError(f'Plugin slug {slug!r} escapes plugins directory')
    return clean


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def _import_module(slug: str, plugin_dir: Path, module_name: str) -> types.ModuleType:
    """Import *module_name* from *plugin_dir* in an isolated namespace."""
    plugin_str = str(plugin_dir)
    if plugin_str not in sys.path:
        sys.path.insert(0, plugin_str)

    spec_name = f'_intelli_plugin_{slug}_{module_name}'
    spec = importlib.util.spec_from_file_location(
        spec_name, plugin_dir / f'{module_name}.py'
    )
    if spec is None or spec.loader is None:
        raise ImportError(f'Cannot find module "{module_name}" in {plugin_dir}')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _register_tools(slug: str, manifest: Dict[str, Any], plugin_dir: Path) -> List[str]:
    """Import plugin module(s) and register tool functions into tool_runner._REGISTRY."""
    from tools.tool_runner import _REGISTRY

    registered: List[str] = []
    tool_specs = manifest.get('tools', [])

    # Group by module so we import each module once
    by_module: Dict[str, List[Dict[str, Any]]] = {}
    for spec in tool_specs:
        mod_name = spec.get('module', 'main')
        by_module.setdefault(mod_name, []).append(spec)

    for mod_name, specs in by_module.items():
        try:
            mod = _import_module(slug, plugin_dir, mod_name)
            _loaded_modules[slug] = mod
        except Exception as exc:
            raise RuntimeError(f'Plugin "{slug}": failed to import module "{mod_name}": {exc}') from exc

        for spec in specs:
            fn_name  = spec.get('function', spec.get('name', ''))
            tool_name = spec.get('name', fn_name)
            fn = getattr(mod, fn_name, None)
            if fn is None:
                raise RuntimeError(f'Plugin "{slug}": function "{fn_name}" not found in "{mod_name}"')
            _REGISTRY[tool_name] = {
                'fn':          fn,
                'description': spec.get('description', ''),
                'args':        spec.get('args', {}),
                '_plugin':     slug,   # tag so we can unregister later
            }
            registered.append(tool_name)

    return registered


def _unregister_tools(slug: str) -> None:
    """Remove all tools registered by *slug* from tool_runner._REGISTRY."""
    from tools.tool_runner import _REGISTRY
    to_remove = [k for k, v in _REGISTRY.items() if v.get('_plugin') == slug]
    for k in to_remove:
        del _REGISTRY[k]
    _loaded_modules.pop(slug, None)


# ---------------------------------------------------------------------------
# Core: load_all
# ---------------------------------------------------------------------------

def load_all() -> None:
    """Scan plugins dir and load every enabled plugin.  Call once at startup."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()

    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        manifest = _read_manifest(entry)
        if manifest is None:
            continue
        slug = _slug(manifest)
        if not slug:
            continue
        enabled = state.get(slug, {}).get('enabled', True)   # default enabled on first discovery
        if not enabled:
            continue
        try:
            with _lock:
                names = _register_tools(slug, manifest, entry)
                _registry_snapshot[slug] = names
            # Persist first-discovery state
            if slug not in state:
                state[slug] = {'enabled': True, 'installed_at': _ts()}
                _save_state(state)
        except Exception as exc:
            _warn(f'Plugin "{slug}" failed to load: {exc}')


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def _resolve_source(source: str) -> Tuple[str, str]:
    """Return (kind, resolved_url_or_path).  kind ∈ {'url', 'local', 'github'}."""
    if source.startswith(('http://', 'https://')):
        return 'url', source
    if '/' in source and not source.startswith('/') and not Path(source).exists():
        # GitHub shorthand: owner/repo or owner/repo@ref
        parts = source.split('@', 1)
        repo  = parts[0]
        ref   = parts[1] if len(parts) > 1 else 'main'
        url   = f'https://github.com/{repo}/archive/refs/heads/{ref}.zip'
        return 'github', url
    return 'local', source


def _download_zip(url: str) -> Path:
    fd, path = tempfile.mkstemp(suffix='.zip', prefix='intelli_plugin_')
    os.close(fd)
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Intelli-Gateway/1.0')]
    with opener.open(url, timeout=_TIMEOUT) as resp, open(path, 'wb') as fh:
        fh.write(resp.read())
    return Path(path)


def _find_manifest_root(extract_dir: Path) -> Optional[Path]:
    """Find the directory inside an extracted zip that contains intelli_plugin.json."""
    # Could be at root or one level deep (GitHub archives add a top-level dir)
    if (extract_dir / _MANIFEST).exists():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / _MANIFEST).exists():
            return child
    return None


def install(source: str) -> Dict[str, Any]:
    """Install a plugin from *source*.

    Parameters
    ----------
    source:
        One of:
        - Local directory path
        - HTTP/HTTPS URL to a ``.zip`` archive
        - GitHub shorthand ``owner/repo`` or ``owner/repo@branch``

    Returns the installed plugin's manifest dict.
    """
    kind, resolved = _resolve_source(source)
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_zip: Optional[Path] = None

    try:
        if kind == 'local':
            src = Path(resolved)
            if not src.is_dir():
                raise ValueError(f'Local path is not a directory: {src}')
            manifest = _read_manifest(src)
            if manifest is None:
                raise ValueError(f'No {_MANIFEST} found in {src}')
            slug = _slug(manifest)
            dest = PLUGINS_DIR / slug
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)

        else:  # url / github
            tmp_zip = _download_zip(resolved)
            with tempfile.TemporaryDirectory(prefix='intelli_plugin_extract_') as tmpdir:
                with zipfile.ZipFile(tmp_zip, 'r') as zf:
                    zf.extractall(tmpdir)
                root = _find_manifest_root(Path(tmpdir))
                if root is None:
                    raise ValueError(f'No {_MANIFEST} found in downloaded archive')
                manifest = _read_manifest(root)
                if manifest is None:
                    raise ValueError(f'Invalid {_MANIFEST} in archive')
                slug = _slug(manifest)
                dest = PLUGINS_DIR / slug
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(str(root), str(dest))

        # Register tools
        with _lock:
            _unregister_tools(slug)
            names = _register_tools(slug, manifest, dest)
            _registry_snapshot[slug] = names

        # Persist state
        state = _load_state()
        state[slug] = {'enabled': True, 'installed_at': _ts(), 'source': source}
        _save_state(state)

        return {**manifest, 'slug': slug, 'path': str(dest), 'tools_registered': names}

    finally:
        if tmp_zip and tmp_zip.exists():
            tmp_zip.unlink()


# ---------------------------------------------------------------------------
# Uninstall / enable / disable / reload
# ---------------------------------------------------------------------------

def uninstall(slug: str) -> bool:
    """Remove a plugin completely.  Returns True if it existed."""
    slug = _safe_plugin_slug(slug)
    dest = PLUGINS_DIR / slug
    if not dest.exists():
        return False
    with _lock:
        _unregister_tools(slug)
        _registry_snapshot.pop(slug, None)
    shutil.rmtree(dest)
    state = _load_state()
    state.pop(slug, None)
    _save_state(state)
    return True


def enable(slug: str) -> bool:
    """Enable a plugin and register its tools.  Returns True on success."""
    slug = _safe_plugin_slug(slug)
    dest = PLUGINS_DIR / slug
    manifest = _read_manifest(dest)
    if manifest is None:
        return False
    with _lock:
        names = _register_tools(slug, manifest, dest)
        _registry_snapshot[slug] = names
    state = _load_state()
    state.setdefault(slug, {})['enabled'] = True
    _save_state(state)
    return True


def disable(slug: str) -> bool:
    """Disable a plugin and unregister its tools.  Returns True if it was enabled."""
    slug = _safe_plugin_slug(slug)
    with _lock:
        _unregister_tools(slug)
        _registry_snapshot.pop(slug, None)
    state = _load_state()
    if slug not in state:
        return False
    state[slug]['enabled'] = False
    _save_state(state)
    return True


def reload_plugin(slug: str) -> Dict[str, Any]:
    """Disable then re-enable a plugin (picks up code changes)."""
    slug = _safe_plugin_slug(slug)
    disable(slug)
    if not enable(slug):
        raise FileNotFoundError(f'Plugin "{slug}" not found in {PLUGINS_DIR}')
    manifest = _read_manifest(PLUGINS_DIR / slug) or {}
    return {**manifest, 'slug': slug, 'tools_registered': _registry_snapshot.get(slug, [])}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_plugins() -> List[Dict[str, Any]]:
    """Return metadata for all installed plugins."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    results: List[Dict[str, Any]] = []
    for entry in sorted(PLUGINS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        manifest = _read_manifest(entry)
        if manifest is None:
            continue
        slug = _slug(manifest)
        st   = state.get(slug, {})
        results.append({
            'slug':             slug,
            'name':             manifest.get('name', slug),
            'version':          manifest.get('version', ''),
            'description':      manifest.get('description', ''),
            'author':           manifest.get('author', ''),
            'homepage':         manifest.get('homepage', ''),
            'enabled':          st.get('enabled', True),
            'installed_at':     st.get('installed_at', ''),
            'tools':            [t.get('name') for t in manifest.get('tools', [])],
            'tools_registered': _registry_snapshot.get(slug, []),
            'env_vars':         manifest.get('env_vars', []),
        })
    return results


def get_plugin(slug: str) -> Optional[Dict[str, Any]]:
    """Return metadata for a single plugin, or None."""
    hits = [p for p in list_plugins() if p['slug'] == slug.lower()]
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _ts() -> str:
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _warn(msg: str) -> None:
    import logging
    logging.getLogger(__name__).warning(msg)

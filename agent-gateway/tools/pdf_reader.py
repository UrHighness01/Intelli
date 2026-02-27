"""PDF reader tool — extracts text from PDF URLs or local paths.

Uses pypdf for extraction, httpx for fetching remote URLs.
Long PDFs are chunked and the first N pages returned with a summary.
"""
from __future__ import annotations

import io
import os
import pathlib
import tempfile
from typing import Union

_MAX_PAGES    = int(os.environ.get('INTELLI_PDF_MAX_PAGES', '20'))
_MAX_CHARS    = int(os.environ.get('INTELLI_PDF_MAX_CHARS', '40000'))
_MAX_FILE_MB  = 20


def _fetch_bytes(url: str, timeout: int = 30) -> bytes:
    """Fetch a URL and return raw bytes."""
    import httpx
    r = httpx.get(
        url,
        follow_redirects=True,
        timeout=timeout,
        headers={'User-Agent': 'IntelliPDFReader/1.0'},
    )
    r.raise_for_status()
    if len(r.content) > _MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f'PDF too large (>{_MAX_FILE_MB} MB).')
    return r.content


def _extract_text_from_bytes(pdf_bytes: bytes, max_pages: int) -> tuple[str, int]:
    """Return (extracted_text, total_page_count)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError('pypdf is required: pip install pypdf')

    reader = PdfReader(io.BytesIO(pdf_bytes))
    total  = len(reader.pages)
    pages  = reader.pages[:max_pages]
    chunks = []
    for i, page in enumerate(pages, 1):
        text = page.extract_text() or ''
        if text.strip():
            chunks.append(f'--- Page {i} ---\n{text.strip()}')
    return '\n\n'.join(chunks), total


def pdf_read(
    url: str = '',
    path: str = '',
    max_pages: int = _MAX_PAGES,
) -> str:
    """Extract text from a PDF given a URL or local file path.

    Args:
        url:       HTTP/HTTPS URL of the PDF (use this OR path).
        path:      Local file path of the PDF (use this OR url).
        max_pages: Maximum number of pages to extract (default 20).

    Returns plain text with page separators, truncated to 40 000 chars.
    Includes a header showing total/extracted page counts.
    """
    max_pages = max(1, min(int(max_pages), 50))

    if not url and not path:
        return '[ERROR] Provide either url= or path= argument.'

    try:
        if url:
            # Validate scheme (SSRF guard: no private IPs, only http/https)
            from urllib.parse import urlparse
            p = urlparse(url)
            if p.scheme not in ('http', 'https'):
                return '[ERROR] Only http/https URLs are supported.'
            pdf_bytes = _fetch_bytes(url)
            source = url
        else:
            fp = pathlib.Path(path).expanduser().resolve()
            # ── Path-injection guard ─────────────────────────────────────
            # Only allow reading PDFs from within the user's home directory.
            # This prevents traversal attacks like path="/etc/passwd" or
            # path="~/../../etc/shadow".
            _safe_root = pathlib.Path.home().resolve()
            try:
                fp.relative_to(_safe_root)
            except ValueError:
                return (
                    '[ERROR] Access denied: PDF path must be inside your home '
                    f'directory ({_safe_root}). Absolute paths outside home are '
                    'not allowed.'
                )
            if not fp.exists():
                return f'[ERROR] File not found: {path}'
            if fp.suffix.lower() not in ('.pdf',):
                return '[ERROR] File does not appear to be a PDF.'
            if fp.stat().st_size > _MAX_FILE_MB * 1024 * 1024:
                return f'[ERROR] File too large (>{_MAX_FILE_MB} MB).'
            pdf_bytes = fp.read_bytes()
            source = str(fp)

        text, total = _extract_text_from_bytes(pdf_bytes, max_pages)

        if not text.strip():
            return (
                f'[INFO] PDF has {total} page(s) but no extractable text was found.\n'
                'The PDF may be image-based (scanned). Try vision analysis instead.'
            )

        header = (
            f'PDF: {source}\n'
            f'Pages shown: {min(max_pages, total)} / {total} total\n'
            + ('(Truncated to first page limit — increase max_pages for more)\n' if total > max_pages else '')
            + '\n'
        )
        body = header + text
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS] + f'\n… (truncated, {len(body)} chars total)'
        return body

    except ImportError as e:
        return f'[ERROR] {e}'
    except Exception as e:
        return f'[ERROR] pdf_read failed: {e}'


# ---------------------------------------------------------------------------
# Tool spec for registration in tool_runner.py
# ---------------------------------------------------------------------------
PDF_TOOLS: dict = {
    'pdf_read': {
        'fn':          pdf_read,
        'description': (
            'Extract text from a PDF file given a URL or local file path. '
            'Returns page-by-page text content. '
            'Best for research papers, reports, documentation. '
            'For scanned/image PDFs use vision tools instead.'
        ),
        'args': {
            'url':       {'type': 'string',  'required': False, 'description': 'HTTP/HTTPS URL of the PDF'},
            'path':      {'type': 'string',  'required': False, 'description': 'Local file path of the PDF'},
            'max_pages': {'type': 'integer', 'required': False, 'description': f'Max pages to extract (default {_MAX_PAGES}, max 50)'},
        },
    },
}

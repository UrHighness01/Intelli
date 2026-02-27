"""
workspace_manager.py — Agent workspace for Intelli.

Inspired by OpenClaw's ~/.openclaw/workspace model, the Intelli workspace gives
the embedded agent a persistent, structured place to store:

  • AGENTS.md    — agent identity, capabilities and instructions (system prompt)
  • SOUL.md      — agent personality and communication style
  • TOOLS.md     — description of available tools the agent can use
  • skills/      — skill definition files (Markdown + optional JS/Python)
  • context/     — arbitrary context files (algorithms, domain knowledge, notes)

Layout
------
  <gateway_dir>/workspace/
    AGENTS.md
    SOUL.md
    TOOLS.md
    skills/
      <skill-slug>/
        SKILL.md
        <optional supporting files>
    context/
      <any files>

All paths are restricted to stay within the workspace root (path-traversal
is blocked by design).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT = Path(__file__).parent / 'workspace'
_lock = threading.Lock()

BUILTIN_FILES = ('AGENTS.md', 'SOUL.md', 'TOOLS.md')


def _ensure_root() -> Path:
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    (_WORKSPACE_ROOT / 'skills').mkdir(exist_ok=True)
    (_WORKSPACE_ROOT / 'context').mkdir(exist_ok=True)
    _seed_defaults()
    return _WORKSPACE_ROOT


def _seed_defaults() -> None:
    """Write default builtin files and starter skills if they don't exist yet."""
    agents_md = _WORKSPACE_ROOT / 'AGENTS.md'
    if not agents_md.exists():
        agents_md.write_text(_DEFAULT_AGENTS_MD, encoding='utf-8')

    soul_md = _WORKSPACE_ROOT / 'SOUL.md'
    if not soul_md.exists():
        soul_md.write_text(_DEFAULT_SOUL_MD, encoding='utf-8')

    tools_md = _WORKSPACE_ROOT / 'TOOLS.md'
    if not tools_md.exists():
        tools_md.write_text(_DEFAULT_TOOLS_MD, encoding='utf-8')

    # Seed starter skills
    _seed_skill('page-summarize', _SKILL_PAGE_SUMMARIZE)
    _seed_skill('web-search', _SKILL_WEB_SEARCH)
    _seed_skill('translate', _SKILL_TRANSLATE)

    # Keep the Skill Creator section in AGENTS.md current (replace if outdated)
    agents_md = _WORKSPACE_ROOT / 'AGENTS.md'
    if agents_md.exists():
        current = agents_md.read_text(encoding='utf-8')
        # Strip any existing Skill Creator section before re-appending the latest
        if '## Skill Creator' in current:
            current = re.sub(r'\n*## Skill Creator\b.*', '', current, flags=re.DOTALL).rstrip()
        agents_md.write_text(current + '\n\n' + _SKILL_CREATOR_SECTION, encoding='utf-8')


def _seed_skill(slug: str, content: str) -> None:
    skill_dir = _WORKSPACE_ROOT / 'skills' / slug
    skill_md  = skill_dir / 'SKILL.md'
    if not skill_md.exists():
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md.write_text(content, encoding='utf-8')


_SKILL_PAGE_SUMMARIZE = """\
---
name: Page Summarize
slug: page-summarize
description: Summarize the current browser tab in a few sentences.
trigger: /summarize
---

# Page Summarize

When the user types `/summarize` or asks to summarize the page:

1. Read the active tab HTML from context (enable Page context in the chat bar).
2. Extract the main content — ignore navbars, ads, footers.
3. Return a structured summary:
   - **Title & URL**
   - **What it is** (1 sentence)
   - **Key points** (3-5 bullets)
   - **Takeaway** (1 sentence)

Be concise.  Do not pad.  If the page is code/docs, summarize the API surface.
"""

_SKILL_WEB_SEARCH = """\
---
name: Web Search
slug: web-search
description: Search the web and return top results with a synthesized answer.
trigger: /search
---

# Web Search

When asked to search for something or when the user types `/search <query>`:

1. Call the `web_search` tool with the user's query.
2. Review the titles and snippets returned.
3. If you need more detail on a result, call `web_fetch` on its URL.
4. Synthesize a clear answer citing the sources (title + URL).

Always cite your sources at the bottom as:
> Source: [Title](URL)
"""

_SKILL_TRANSLATE = """\
---
name: Translate
slug: translate
description: Translate text into any language.
trigger: /translate
---

# Translate

When the user types `/translate <text>` or asks you to translate something:

1. Detect the source language (or use what the user specified).
2. Identify the target language from the request, defaulting to English if unclear.
3. Provide the translation, then optionally add a pronunciation guide for non-Latin scripts.
4. If the text is from the active page, reference the page title.

Keep translations natural, not word-for-word literal.
"""


_SKILL_CREATOR_SECTION = """\
## Skill Creator

When asked to create a skill, **do it in one single `skill_create` call — no
back-and-forth, no reading first**.  Plan the steps in your head, then fire.

| Tool | Purpose |
|---|---|
| `skill_list` | List all installed skills |
| `skill_read(slug)` | Read a skill's full SKILL.md |
| `skill_create(slug, name, description, content)` | Install a new skill (one shot) |
| `skill_update(slug, content)` | Overwrite an existing skill's SKILL.md |
| `skill_delete(slug)` | Permanently remove a skill |

### content format — CRITICAL

`skill_create` auto-generates YAML frontmatter from `slug`, `name`, `description`.
**Never put `---` frontmatter inside `content`.**
`content` = Markdown body only: a `# Heading`, then numbered steps using tool names.
**Use `\\n` for newlines in the JSON string — literal newlines in JSON values are invalid.**

### Available tool names to reference inside skill steps

`web_fetch`  `web_search`  `browser_exec_js`  `browser_snapshot`  `browser_navigate`
`browser_summarize_page`  `browser_click`  `browser_fill`  `file_read`  `file_write`
`shell_exec`  `memory_add`  `memory_search`  `video_describe`  `canvas_render`
`addon_create_and_activate`  `schedule_task`

### Rules

- Write detailed, numbered steps — include exact tool names, example args, fallback steps.
- slug: lowercase + hyphens only (e.g. `ocr-extractor`, `pdf-builder`, `news-digest`).
- Do NOT ask for confirmation — just create it immediately.
- If slug already exists, `skill_create` returns an error → use `skill_update` instead
  and pass the complete SKILL.md text (frontmatter + body) as `content`.
"""


_DEFAULT_AGENTS_MD = """\
# Intelli Agent

You are Intelli, a context-aware AI assistant embedded inside the Intelli
browser — a Chromium-based desktop browser with an integrated AI gateway.

## Browser capabilities you have access to

- **Active tab context**: You receive the HTML source, URL, and title of the
  page the user is currently viewing when they attach it to a chat message.
- **Addon system**: You can write JavaScript snippets (addons) that are
  injected into the active tab at runtime to extend or modify page behaviour.
- **Workspace**: You have a persistent workspace where skills, context files,
  and this configuration live.  The user can add files here to give you
  additional knowledge.

## When page context is attached

Analyse the HTML carefully.  You can:
- Answer questions about the page content
- Generate addons (injected JS) that modify the page behaviour on the fly
- Extract structured data from the page
- Suggest or write custom functionality for the page

## Addon generation rules

When writing an addon (JavaScript to run in the active tab):
1. Wrap your code in a self-executing function: `(function() { ... })();`
2. Never use `alert()` — use `console.log()` or create DOM elements instead
3. Prefer non-destructive augmentation over replacing content
4. Include a comment block at the top with: name, description, safe-to-rerun flag

## Communication style

Be concise, direct, and technically precise.  When writing code, always
include brief inline comments.  Prefer showing over telling.
"""

_DEFAULT_SOUL_MD = """\
# Soul

You are curious, helpful, and technically sharp.  You love exploring page
source code and finding elegant ways to extend browser behaviour.  You treat
every page as a hackable surface waiting to be improved.

When working with code: be precise, show your reasoning, and always test
edge cases in your mind before presenting a solution.
"""

_DEFAULT_TOOLS_MD = """\
# Available Agent Tools

## Web tools (built-in, always available)

These tools are called using the ReAct protocol:

    TOOL_CALL: {"name": "<tool>", "args": {<arg>: <value>}}

### web_search
Search the web using DuckDuckGo.  Returns title, URL and snippet per result.
- `query` (string, required) — search query
- `max_results` (integer, optional, default 5)

Example:
    TOOL_CALL: {"name": "web_search", "args": {"query": "Python asyncio tutorial"}}

### web_fetch
Fetch a URL and return clean, readable plain text.  Best for articles, docs, GitHub.
- `url` (string, required) — full http/https URL
- `max_chars` (integer, optional, default 8000)

Example:
    TOOL_CALL: {"name": "web_fetch", "args": {"url": "https://docs.python.org/3/library/asyncio.html"}}

## Browser / gateway REST endpoints
- `GET /tab/snapshot` — HTML source + URL + title of active tab
- `POST /addons` — Create a JS addon injected into the active tab
- `GET /workspace/files` — List workspace files
- `GET/POST /workspace/file?path=<rel>` — Read/write file
- `GET /workspace/skills` — List skills
"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _safe_path(rel: str) -> Path:
    """Resolve *rel* inside the workspace root, raising ValueError if it escapes."""
    root = _ensure_root()
    resolved = (root / rel).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise ValueError(f'Path {rel!r} escapes workspace root')
    return resolved


# ---------------------------------------------------------------------------
# File CRUD
# ---------------------------------------------------------------------------

def list_files() -> list[dict]:
    """Return metadata for every file in the workspace (recursive)."""
    root = _ensure_root()
    result = []
    for p in sorted(root.rglob('*')):
        if p.is_file():
            rel = str(p.relative_to(root))
            stat = p.stat()
            result.append({
                'path': rel,
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return result


def read_file(rel: str) -> str:
    """Read and return the text content of a workspace file."""
    p = _safe_path(rel)
    if not p.exists():
        raise FileNotFoundError(f'workspace file not found: {rel!r}')
    return p.read_text(encoding='utf-8')


def write_file(rel: str, content: str) -> dict:
    """Write (create or overwrite) a workspace file.  Parent dirs are created."""
    p = _safe_path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')
    return {
        'path': rel,
        'size': len(content.encode()),
        'modified': datetime.now(timezone.utc).isoformat(),
    }


def delete_file(rel: str) -> None:
    """Delete a workspace file."""
    p = _safe_path(rel)
    if not p.exists():
        raise FileNotFoundError(f'workspace file not found: {rel!r}')
    p.unlink()


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


def _parse_skill_frontmatter(text: str) -> dict:
    """Extract YAML-like key:value pairs from the first fenced block or leading lines."""
    meta: dict = {}
    lines = text.splitlines()
    in_fm = False
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith('---'):
            in_fm = not in_fm
            continue
        if in_fm or not in_fm:
            m = re.match(r'^(\w[\w\s]*):\s*(.+)$', line)
            if m:
                meta[m.group(1).strip().lower()] = m.group(2).strip()
            elif in_fm and stripped:
                continue
            elif not in_fm and stripped.startswith('#'):
                meta.setdefault('name', stripped.lstrip('#').strip())
    return meta


def list_skills() -> list[dict]:
    """Return a summary list of all installed skills."""
    root = _ensure_root()
    skills_dir = root / 'skills'
    result = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / 'SKILL.md'
        text = skill_md.read_text(encoding='utf-8') if skill_md.exists() else ''
        meta = _parse_skill_frontmatter(text)
        result.append({
            'slug':        skill_dir.name,
            'name':        meta.get('name', skill_dir.name),
            'description': meta.get('description', ''),
            'version':     meta.get('version', ''),
            'modified':    datetime.fromtimestamp(
                skill_dir.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        })
    return result


def create_skill(slug: str, name: str, description: str, content: str) -> dict:
    """Create a new skill directory with a SKILL.md."""
    if not _SLUG_RE.match(slug):
        raise ValueError(f'Invalid skill slug {slug!r} — use lowercase letters, digits, _ or -')
    root = _ensure_root()
    skill_dir = root / 'skills' / slug
    if skill_dir.exists():
        raise ValueError(f"Skill '{slug}' already exists")
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / 'SKILL.md'
    header = f'---\nname: {name}\ndescription: {description}\ncreated: {datetime.now(timezone.utc).isoformat()}\n---\n\n'
    skill_md.write_text(header + content, encoding='utf-8')
    return {
        'slug': slug,
        'name': name,
        'description': description,
        'path': str(skill_md.relative_to(root)),
    }


def delete_skill(slug: str) -> None:
    """Remove a skill directory entirely."""
    root = _ensure_root()
    skill_dir = root / 'skills' / slug
    if not skill_dir.exists():
        raise FileNotFoundError(f"Skill '{slug}' not found")
    import shutil
    shutil.rmtree(skill_dir)


def get_skill(slug: str) -> dict:
    """Return metadata + full SKILL.md content for a single skill."""
    root = _ensure_root()
    skill_dir = root / 'skills' / slug
    if not skill_dir.exists():
        raise FileNotFoundError(f"Skill '{slug}' not found")
    skill_md = skill_dir / 'SKILL.md'
    content = skill_md.read_text(encoding='utf-8') if skill_md.exists() else ''
    meta = _parse_skill_frontmatter(content)
    return {
        'slug':        slug,
        'name':        meta.get('name', slug),
        'description': meta.get('description', ''),
        'content':     content,
        'path':        str(skill_md.relative_to(root)) if skill_md.exists() else f'skills/{slug}/SKILL.md',
        'modified':    datetime.fromtimestamp(
            skill_dir.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def update_skill(slug: str, content: str) -> dict:
    """Overwrite the SKILL.md of an existing skill."""
    root = _ensure_root()
    skill_dir = root / 'skills' / slug
    if not skill_dir.exists():
        raise FileNotFoundError(f"Skill '{slug}' not found")
    skill_md = skill_dir / 'SKILL.md'
    skill_md.write_text(content, encoding='utf-8')
    meta = _parse_skill_frontmatter(content)
    return {
        'slug':        slug,
        'name':        meta.get('name', slug),
        'description': meta.get('description', ''),
        'path':        str(skill_md.relative_to(root)),
        'modified':    datetime.now(timezone.utc).isoformat(),
    }


_SUPPORTED_ATTRS = frozenset({
    'name', 'description', 'user-invokable', 'compatibility',
    'argument-hint', 'disable-model-invocation', 'license', 'metadata',
})


def validate_skill(content: str) -> dict:
    """Validate SKILL.md content.  Returns {valid, errors, warnings, meta}."""
    errors: list[str]   = []
    warnings: list[str] = []
    meta = _parse_skill_frontmatter(content)

    if not meta.get('name'):
        errors.append('Missing required frontmatter field: name')
    if not meta.get('description'):
        warnings.append('Missing recommended frontmatter field: description')
    for key in meta:
        if key not in _SUPPORTED_ATTRS:
            warnings.append(f'Unsupported frontmatter attribute {key!r} — will be ignored')

    # Check that a body exists beyond frontmatter
    stripped = re.sub(r'^---.*?---', '', content, flags=re.DOTALL).strip()
    if not stripped:
        warnings.append('Skill body is empty — add agent instructions below the frontmatter')

    return {
        'valid':    len(errors) == 0,
        'errors':   errors,
        'warnings': warnings,
        'meta':     meta,
    }


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_system_prompt(include_tools: bool = False) -> str:
    """Assemble the agent system prompt from AGENTS.md + SOUL.md + optionally TOOLS.md."""
    _ensure_root()
    parts: list[str] = []
    for fname in ('AGENTS.md', 'SOUL.md'):
        p = _WORKSPACE_ROOT / fname
        if p.exists():
            parts.append(p.read_text(encoding='utf-8').strip())
    if include_tools:
        p = _WORKSPACE_ROOT / 'TOOLS.md'
        if p.exists():
            parts.append(p.read_text(encoding='utf-8').strip())
    return '\n\n---\n\n'.join(parts)


def build_page_context_block(snapshot: dict, max_html: int = 8000) -> str:
    """Format a tab snapshot dict into a context block for the system prompt."""
    url   = snapshot.get('url', '')
    title = snapshot.get('title', '')
    html  = snapshot.get('html', '')
    if len(html) > max_html:
        html = html[:max_html] + f'\n\n[… HTML truncated at {max_html} chars …]'
    return (
        f'## Active browser tab\n'
        f'**URL**: {url}\n'
        f'**Title**: {title}\n\n'
        f'### Page HTML source\n```html\n{html}\n```'
    )

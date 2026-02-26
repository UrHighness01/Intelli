"""
tool_runner.py — ReAct-style tool execution loop for Intelli agents.

Protocol (provider-agnostic plain-text):
  The LLM may include one or more tool calls anywhere in its response using:

    TOOL_CALL: {"name": "<tool>", "args": {<key>: <value>, ...}}

  After detecting a call the gateway executes the tool, appends the result as
  a new user message, and calls the LLM again (up to MAX_ROUNDS times).

  Final responses that contain no TOOL_CALL are returned as-is.
"""

from __future__ import annotations

import json
import re
import threading
import traceback
from typing import Any

from tools.web_tools import TOOLS as _WEB_TOOLS

# ---------------------------------------------------------------------------
# Per-thread context (session_id + approval event queue)
# ---------------------------------------------------------------------------
_CTX = threading.local()

# Tools that require explicit user approval before execution.
# Checked inside _run_tool; user must confirm via /agent/approvals/{id}/approve.
_APPROVAL_TOOLS: frozenset[str] = frozenset({
    'shell_exec',    # executes arbitrary shell commands
    'file_write',    # creates / overwrites files
    'file_patch',    # modifies files via unified diff
    'file_delete',   # permanently deletes files
    'browser_eval',  # executes arbitrary JS in the active tab
})


def _canvas_render(html: str, title: str = '') -> str:
    """Render HTML into the Intelli Canvas panel."""
    try:
        import canvas_manager as _cm
        _cm.get_canvas().render(html, title)
        return f'Canvas updated: {len(html)} chars rendered. The user can see it in the Canvas panel.'
    except Exception as exc:
        return f'[ERROR] canvas_render failed: {exc}'

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, dict] = {}
_REGISTRY.update(_WEB_TOOLS)
_REGISTRY['canvas_render'] = {
    'fn': _canvas_render,
    'description': (
        'Render HTML into the live Canvas panel next to the browser. '
        'Use this to show formatted results, charts, tables, or any rich content. '
        'The user sees it immediately in a dedicated panel.'
    ),
    'args': {
        'html':  {'type': 'string', 'required': True,  'description': 'Full HTML document or fragment to display'},
        'title': {'type': 'string', 'required': False, 'description': 'Optional title shown in the canvas toolbar'},
    },
}


# ---- Memory tools --------------------------------------------------------

def _memory_search(query: str, n: int = 4) -> str:
    """Search the persistent vector memory store."""
    try:
        import memory_store as _ms
        results = _ms.get_store().search(query, n=n)
        if not results:
            return 'No relevant memories found.'
        lines = []
        for r in results:
            meta = r['metadata']
            src  = meta.get('source', '?')
            url  = meta.get('url', '')
            age  = _ms._fmt_age(meta.get('timestamp_unix', 0))
            snippet = r['text'][:300].replace('\n', ' ')
            lines.append(f'[{src}] {url or meta.get("title","unknown")} ({age}, score={r["score"]})\n  {snippet}')
        return '\n\n'.join(lines)
    except Exception as exc:
        return f'[ERROR] memory_search: {exc}'


def _memory_add(text: str, title: str = '', url: str = '') -> str:
    """Add a fact or note to the persistent memory store."""
    try:
        import memory_store as _ms
        doc_id = _ms.get_store().add(text=text, source='manual', url=url, title=title, pinned=True)
        return f'Memory saved (id={doc_id}).'
    except Exception as exc:
        return f'[ERROR] memory_add: {exc}'


_REGISTRY['memory_search'] = {
    'fn': _memory_search,
    'description': (
        'Search the agent\'s persistent vector memory for relevant past pages, '
        'conversations, and pinned facts. Use this when the user asks about '
        'something you may have seen before or to recall pinned knowledge.'
    ),
    'args': {
        'query': {'type': 'string', 'required': True,  'description': 'Natural-language search query'},
        'n':     {'type': 'integer', 'required': False, 'description': 'Max results to return (default 4)'},
    },
}

_REGISTRY['memory_add'] = {
    'fn': _memory_add,
    'description': (
        'Save a fact, note, or important piece of information to persistent memory '
        'so it can be recalled in future sessions.'
    ),
    'args': {
        'text':  {'type': 'string', 'required': True,  'description': 'The text/fact to remember'},
        'title': {'type': 'string', 'required': False, 'description': 'Optional label for the memory'},
        'url':   {'type': 'string', 'required': False, 'description': 'Optional source URL'},
    },
}

# Coding-agent tools (file I/O + shell execution)
try:
    from tools.coding_tools import CODING_TOOLS as _CODING_TOOLS
    _REGISTRY.update(_CODING_TOOLS)
except Exception as _ce:  # pragma: no cover
    import logging as _logging
    _logging.getLogger(__name__).warning('coding_tools unavailable: %s', _ce)

# Browser automation tools (DOM control via Electron IPC)
try:
    from tools.browser_tools import BROWSER_TOOLS as _BROWSER_TOOLS
    _REGISTRY.update(_BROWSER_TOOLS)
except Exception as _be:  # pragma: no cover
    import logging as _logging
    _logging.getLogger(__name__).warning('browser_tools unavailable: %s', _be)


# ---------------------------------------------------------------------------
# Scheduled-agent tool
# ---------------------------------------------------------------------------
def _schedule_task_fn(
    name: str,
    tool: str,
    args: dict | None = None,
    interval_seconds: int = 3600,
) -> str:
    """Create a recurring scheduled task that runs a named tool every N seconds."""
    try:
        import sys as _sys, os as _os
        _gw = _os.path.dirname(_os.path.dirname(__file__))
        if _gw not in _sys.path:
            _sys.path.insert(0, _gw)
        import scheduler as _sched  # type: ignore
        task = _sched.add_task(
            name=name,
            tool=tool,
            args=args or {},
            interval_seconds=int(interval_seconds),
        )
        tid = task.get('id', '?')
        return (
            f"Scheduled task '{name}' created (id={tid}, "
            f"tool={tool}, interval={interval_seconds}s). "
            "Use the Schedule panel in the admin UI to manage tasks."
        )
    except Exception as _exc:
        return f'[ERROR] schedule_task: {_exc}'


_REGISTRY['schedule_task'] = {
    'fn': _schedule_task_fn,
    'description': (
        'Create a recurring background task that runs a tool on a fixed interval. '
        'Returns confirmation with the new task ID.'
    ),
    'args': {
        'name':             {'type': 'string',  'required': True,  'description': 'Human-readable task name'},
        'tool':             {'type': 'string',  'required': True,  'description': 'Tool name to run (must exist in the tool registry)'},
        'args':             {'type': 'object',  'required': False, 'description': 'Arguments to pass to the tool on each run'},
        'interval_seconds': {'type': 'integer', 'required': False, 'description': 'Run interval in seconds (default 3600 = 1 hour)'},
    },
}


# ---------------------------------------------------------------------------
# Page-diff watcher tool
# ---------------------------------------------------------------------------

def _watch_page_fn(
    url: str,
    interval_minutes: int = 60,
    label: str = '',
    notify_threshold: float = 0.02,
) -> str:
    try:
        import sys as _sys, os as _os
        _gw = _os.path.dirname(_os.path.dirname(__file__))
        if _gw not in _sys.path:
            _sys.path.insert(0, _gw)
        import watcher as _w
        w = _w.add_watcher(
            url=url,
            label=label,
            interval_minutes=interval_minutes,
            notify_threshold=notify_threshold,
        )
        return (
            f"Now watching '{w['label']}' (id={w['id']}, "
            f"every {interval_minutes} min, "
            f"alert threshold {notify_threshold:.1%}). "
            'Changes will appear in the Watchers panel.'
        )
    except Exception as _exc:
        return f'[ERROR] watch_page: {_exc}'


_REGISTRY['watch_page'] = {
    'fn': _watch_page_fn,
    'description': (
        'Start monitoring a URL for content changes. '
        'An alert is triggered whenever the page content changes by more than the threshold. '
        'Use this to track news pages, documentation, pricing pages, etc.'
    ),
    'args': {
        'url':               {'type': 'string',  'required': True,  'description': 'URL to monitor'},
        'interval_minutes':  {'type': 'integer', 'required': False, 'description': 'Poll interval in minutes (default 60)'},
        'label':             {'type': 'string',  'required': False, 'description': 'Human-readable label for this watcher'},
        'notify_threshold':  {'type': 'number',  'required': False, 'description': 'Fraction of content change required to trigger alert (default 0.02 = 2%)'},
    },
}


# ---------------------------------------------------------------------------
# PDF reader tool
# ---------------------------------------------------------------------------

def _pdf_read_fn(url: str = '', path: str = '', max_pages: int = 20) -> str:
    try:
        from tools.pdf_reader import pdf_read as _pdf_read
        return _pdf_read(url=url, path=path, max_pages=max_pages)
    except Exception as _exc:
        return f'[ERROR] pdf_read: {_exc}'


_REGISTRY['pdf_read'] = {
    'fn': _pdf_read_fn,
    'description': (
        'Extract text from a PDF file given a URL or a local file path. '
        'Returns page-by-page text content up to max_pages. '
        'Best for research papers, reports, and documentation. '
        'For scanned / image-only PDFs, use vision tools instead.'
    ),
    'args': {
        'url':       {'type': 'string',  'required': False, 'description': 'HTTP/HTTPS URL of the PDF'},
        'path':      {'type': 'string',  'required': False, 'description': 'Local file path of the PDF'},
        'max_pages': {'type': 'integer', 'required': False, 'description': 'Maximum pages to extract (default 20, max 50)'},
    },
}


# ---------------------------------------------------------------------------
# Sub-agent spawner
# ---------------------------------------------------------------------------

_SUBAGENT_DEPTH = threading.local()  # per-thread recursion depth counter


def _spawn_agent_fn(
    task: str,
    context: str = '',
    provider: str = '',
    model: str = '',
    max_rounds: int = 3,
) -> str:
    depth = getattr(_SUBAGENT_DEPTH, 'depth', 0)
    if depth >= 2:
        return '[ERROR] Maximum sub-agent nesting depth (2) reached. Cannot spawn further sub-agents.'

    _SUBAGENT_DEPTH.depth = depth + 1
    try:
        import sys as _sys, os as _os
        _gw = _os.path.dirname(_os.path.dirname(__file__))
        if _gw not in _sys.path:
            _sys.path.insert(0, _gw)
        from providers.adapters import get_adapter, available_providers

        prov   = provider or (available_providers()[0] if available_providers() else 'openai')
        adpt   = get_adapter(prov)

        # Build isolated message history
        msgs: list[dict] = []
        if context:
            msgs.append({'role': 'user',      'content': f'Context:\n{context}'})
            msgs.append({'role': 'assistant', 'content': 'Understood. Ready to work on your task.'})
        msgs.append({'role': 'user', 'content': task})

        # Build tool system block without spawn_agent to prevent runaway recursion
        _saved = _REGISTRY.pop('spawn_agent', None)
        try:
            sys_block = build_tool_system_block()
        finally:
            if _saved is not None:
                _REGISTRY['spawn_agent'] = _saved

        result = run_tool_loop(
            adpt,
            msgs,
            temperature=0.7,
            max_tokens=2048,
            model=model or '',
            system=sys_block,
            max_rounds=max(1, min(int(max_rounds), 5)),
        )
        content = result.get('content', '').strip() or '(sub-agent returned no content)'
        return f'[Sub-agent result — provider={prov}]\n{content}'
    except Exception as _exc:
        return f'[ERROR] spawn_agent failed: {_exc}'
    finally:
        _SUBAGENT_DEPTH.depth = depth


_REGISTRY['spawn_agent'] = {
    'fn': _spawn_agent_fn,
    'description': (
        'Spawn a sub-agent to handle a complex sub-task autonomously. '
        'The sub-agent has access to all tools (except spawn_agent itself) and '
        'runs its own tool loop, then returns its final answer. '
        'Use this to parallelise or delegate clearly-scoped work.'
    ),
    'args': {
        'task':       {'type': 'string',  'required': True,  'description': 'Full description of the task the sub-agent should complete'},
        'context':    {'type': 'string',  'required': False, 'description': 'Optional background context to give the sub-agent before the task'},
        'provider':   {'type': 'string',  'required': False, 'description': 'LLM provider name (defaults to active provider)'},
        'model':      {'type': 'string',  'required': False, 'description': 'Model name override'},
        'max_rounds': {'type': 'integer', 'required': False, 'description': 'Maximum tool-call rounds for the sub-agent (1-5, default 3)'},
    },
}


def register_tool(name: str, fn, description: str, args: dict) -> None:
    """Dynamically register an additional tool."""
    _REGISTRY[name] = {'fn': fn, 'description': description, 'args': args}


def list_tools() -> list[dict]:
    """Return a list of tool specs for injection into system prompts."""
    out = []
    for name, spec in _REGISTRY.items():
        out.append({'name': name, 'description': spec['description'], 'args': spec.get('args', {})})
    return out


# ---------------------------------------------------------------------------
# System prompt fragment
# ---------------------------------------------------------------------------

def build_tool_system_block() -> str:
    """Return the tool-use instruction block for injection into the system prompt."""
    tool_lines = []
    for name, spec in _REGISTRY.items():
        arg_parts = []
        for arg_name, arg_spec in spec.get('args', {}).items():
            req  = '' if arg_spec.get('required', True) else ' (optional)'
            desc = arg_spec.get('description', '')
            arg_parts.append(f'    {arg_name!r}: {arg_spec["type"]}{req} — {desc}')
        args_str = '\n'.join(arg_parts) if arg_parts else '    (none)'
        tool_lines.append(
            f'• {name}\n  Description: {spec["description"]}\n  Args:\n{args_str}'
        )

    tools_block = '\n\n'.join(tool_lines)
    return f"""\
## Available Tools

You may call tools to look up information, fetch web pages, or perform actions.
To call a tool, output EXACTLY this format (one call per line, valid JSON):

    TOOL_CALL: {{"name": "<tool_name>", "args": {{<arg>: <value>}}}}

The gateway will execute the tool and return the result in a new message prefixed with:

    TOOL_RESULT [<tool_name>]: <result>

Rules:
- Only call tools when you need external information not already in context.
- After receiving TOOL_RESULT, synthesize it into your final answer.
- If a tool call fails, say so and use what you know.
- Do NOT fabricate tool results.

### Tools

{tools_block}
"""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(
    r'TOOL_CALL\s*:\s*(\{.+?\})',
    re.DOTALL | re.IGNORECASE,
)
_MAX_JSON_SEARCH = 2000  # chars to scan for JSON completeness


def _extract_tool_calls(text: str) -> list[dict]:
    """Return all TOOL_CALL JSON objects from an LLM response."""
    calls = []
    for m in _TOOL_CALL_RE.finditer(text):
        raw = m.group(1).strip()
        # Try to fix truncated JSON by counting braces
        depth = 0
        end = 0
        for i, ch in enumerate(raw[:_MAX_JSON_SEARCH]):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            if depth == 0:
                end = i + 1
                break
        fragment = raw[:end] if end else raw
        try:
            obj = json.loads(fragment)
            if isinstance(obj, dict) and 'name' in obj:
                calls.append(obj)
        except json.JSONDecodeError:
            pass
    return calls


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def _run_tool(name: str, args: dict) -> str:
    """Execute a registered tool and return a plain-text result string."""
    spec = _REGISTRY.get(name)
    if not spec:
        return f'[ERROR] Unknown tool: {name!r}. Available: {list(_REGISTRY)}'

    # Basic arg validation
    fn_args: dict[str, Any] = {}
    for arg_name, arg_spec in spec.get('args', {}).items():
        if arg_name in args:
            val = args[arg_name]
            # Coerce integer args
            if arg_spec.get('type') == 'integer' and not isinstance(val, int):
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    pass
            fn_args[arg_name] = val
        elif arg_spec.get('required', True):
            return f'[ERROR] Missing required arg {arg_name!r} for tool {name!r}'

    # ---- Approval gate -------------------------------------------------
    if name in _APPROVAL_TOOLS:
        try:
            import sys as _sys, os as _os
            _gw = _os.path.dirname(_os.path.dirname(__file__))
            if _gw not in _sys.path:
                _sys.path.insert(0, _gw)
            import approval_gate as _ag
        except ImportError:
            _ag = None  # approval_gate not available — proceed without gating
        if _ag is not None:
            _sid = getattr(_CTX, 'session_id', '')
            _q   = getattr(_CTX, 'event_queue', None)
            _aid = _ag.register(tool=name, args=fn_args, session_id=_sid)
            # Push event into SSE queue so the UI can show the approval banner
            if _q is not None:
                _q.put({
                    'type': 'approval_required',
                    'id': _aid,
                    'tool': name,
                    'args': fn_args,
                    'session_id': _sid,
                    'expires_in': _ag.DEFAULT_TIMEOUT,
                })
            _approved = _ag.wait_for_decision(_aid)
            if not _approved:
                return (
                    f'[DENIED] The action "{name}" was not approved by the user '
                    f'(approval id={_aid}). No changes were made.'
                )
    # --------------------------------------------------------------------

    try:
        result = spec['fn'](**fn_args)
    except Exception as exc:
        tb = traceback.format_exc(limit=3)
        return f'[ERROR] Tool {name!r} raised an exception:\n{tb}'

    # Format result as readable text
    if isinstance(result, list):
        if not result:
            return '(no results)'
        parts = []
        for i, item in enumerate(result, 1):
            if isinstance(item, dict):
                if 'error' in item and not item.get('title'):
                    parts.append(f'{i}. ERROR: {item["error"]}')
                else:
                    title   = item.get('title', '')
                    url     = item.get('url', '')
                    snippet = item.get('snippet', '')
                    line = f'{i}. **{title}**'
                    if url:
                        line += f'\n   URL: {url}'
                    if snippet:
                        line += f'\n   {snippet}'
                    parts.append(line)
            else:
                parts.append(f'{i}. {item}')
        return '\n'.join(parts)
    if isinstance(result, dict):
        return json.dumps(result, indent=2, ensure_ascii=False)
    return str(result)


# ---------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------

MAX_ROUNDS = 5  # max tool-call → result cycles per request


def run_tool_loop(
    adapter,
    messages: list[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    model: str = '',
    system: str = '',
    max_rounds: int = 0,      # 0 = use module-level MAX_ROUNDS
    session_id: str = '',     # propagated to approval_gate entries
    approval_queue=None,      # queue.Queue for approval_required events
    on_tool_call=None,        # optional callback(name, args) for streaming UX
    on_tool_result=None,      # optional callback(name, result)
) -> dict:
    """Run the LLM + tool-execution loop.

    Calls the adapter until the response contains no TOOL_CALL or MAX_ROUNDS
    is reached, then returns the final adapter result dict.
    """
    # Propagate approval context into thread-local so _run_tool can read it
    _CTX.session_id  = session_id
    _CTX.event_queue = approval_queue

    kwargs = {}
    if model:
        kwargs['model'] = model
    if system:
        kwargs['system'] = system

    msgs = list(messages)
    rounds = max(1, min(int(max_rounds), 10)) if max_rounds > 0 else MAX_ROUNDS

    for _round in range(rounds):
        result = adapter.chat_complete(
            messages=msgs,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        content: str = result.get('content', '')
        calls = _extract_tool_calls(content)

        if not calls:
            # No tool call — we're done
            return result

        # Remove TOOL_CALL lines from the displayed content for cleanliness
        display_content = _TOOL_CALL_RE.sub('', content).strip()
        result['content'] = display_content

        # Push assistant turn (cleaned) and execute each tool call
        msgs.append({'role': 'assistant', 'content': content})

        tool_results = []
        for call in calls:
            name = call.get('name', '?')
            args = call.get('args', {})
            if on_tool_call:
                on_tool_call(name, args)
            res_text = _run_tool(name, args)
            if on_tool_result:
                on_tool_result(name, res_text)
            tool_results.append(f'TOOL_RESULT [{name}]:\n{res_text}')

        # Inject all results as a single user message
        msgs.append({'role': 'user', 'content': '\n\n'.join(tool_results)})

    # Hit round limit — return whatever we have
    return result

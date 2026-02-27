"""browser_tools.py — Browser automation tools for the Intelli agent.

The agent can control the active Electron BrowserView tab via these tools:
  - browser_click(selector)
  - browser_type(selector, text)
  - browser_scroll(pixels)
  - browser_navigate(url)
  - browser_screenshot()
  - browser_eval(js_code)

Commands are queued in the gateway and polled by the Electron shell via
GET /browser/command-queue. Results are posted back via POST /browser/result.

This enables fully autonomous browser control: form filling, page scraping,
navigation, testing, and any task that requires DOM interaction.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any, Dict, Optional
from collections import deque

# ---------------------------------------------------------------------------
# Command queue (shared module state)
# ---------------------------------------------------------------------------

_command_queue: deque = deque()  # [{id, command, args, timestamp}]
_pending_results: Dict[str, Any] = {}  # {command_id: result}
_result_ready: Dict[str, asyncio.Event] = {}  # {command_id: Event}

COMMAND_TIMEOUT = 30  # seconds


def _enqueue_command(command: str, args: dict) -> str:
    """Add a command to the queue and return its ID."""
    cmd_id = str(uuid.uuid4())
    _command_queue.append({
        'id': cmd_id,
        'command': command,
        'args': args,
        'timestamp': time.time(),
    })
    _result_ready[cmd_id] = asyncio.Event()
    return cmd_id


async def _wait_for_result(cmd_id: str, timeout: float = COMMAND_TIMEOUT) -> Any:
    """Wait for the Electron shell to execute the command and post the result."""
    try:
        await asyncio.wait_for(_result_ready[cmd_id].wait(), timeout=timeout)
        result = _pending_results.pop(cmd_id, None)
        _result_ready.pop(cmd_id, None)
        return result
    except asyncio.TimeoutError:
        _result_ready.pop(cmd_id, None)
        return {'error': f'Command timed out after {timeout}s'}


def pop_command_queue() -> Optional[dict]:
    """Called by GET /browser/command-queue — returns next command or None."""
    if _command_queue:
        return _command_queue.popleft()
    return None


def post_command_result(cmd_id: str, result: Any):
    """Called by POST /browser/result — stores result and signals the waiting tool."""
    _pending_results[cmd_id] = result
    if cmd_id in _result_ready:
        _result_ready[cmd_id].set()


# ---------------------------------------------------------------------------
# Browser automation tools
# ---------------------------------------------------------------------------

async def browser_click(selector: str, button: str = 'left') -> str:
    """Click an element in the active browser tab.

    Args:
        selector: CSS selector for the element to click.
        button: Mouse button ('left', 'right', 'middle').

    Returns a success message or error description.
    """
    cmd_id = _enqueue_command('click', {'selector': selector, 'button': button})
    result = await _wait_for_result(cmd_id)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Click failed: {result['error']}"
    if isinstance(result, dict) and result.get('success'):
        return f"✓ Clicked element: {selector}"
    return f"[ERROR] Unexpected result: {result}"


async def browser_type(selector: str, text: str, clear: bool = True) -> str:
    """Type text into an input field in the active browser tab.

    Args:
        selector: CSS selector for the input element.
        text: Text to type.
        clear: Whether to clear existing content first (default True).

    Returns a success message or error description.
    """
    cmd_id = _enqueue_command('type', {'selector': selector, 'text': text, 'clear': clear})
    result = await _wait_for_result(cmd_id)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Type failed: {result['error']}"
    if isinstance(result, dict) and result.get('success'):
        return f"✓ Typed into {selector}: {text[:50]}{'...' if len(text) > 50 else ''}"
    return f"[ERROR] Unexpected result: {result}"


async def browser_scroll(pixels: int = 0, to_bottom: bool = False) -> str:
    """Scroll the active browser tab.

    Args:
        pixels: Number of pixels to scroll (positive = down, negative = up).
        to_bottom: If True, scroll to the bottom of the page.

    Returns a success message.
    """
    cmd_id = _enqueue_command('scroll', {'pixels': pixels, 'to_bottom': to_bottom})
    result = await _wait_for_result(cmd_id)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Scroll failed: {result['error']}"
    if to_bottom:
        return "✓ Scrolled to bottom of page"
    return f"✓ Scrolled {pixels}px"


async def browser_navigate(url: str) -> str:
    """Navigate the active browser tab to a URL.

    Args:
        url: The URL to navigate to.

    Returns a success message or error description.
    """
    cmd_id = _enqueue_command('navigate', {'url': url})
    result = await _wait_for_result(cmd_id)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Navigation failed: {result['error']}"
    if isinstance(result, dict) and result.get('success'):
        return f"✓ Navigated to: {url}"
    return f"[ERROR] Unexpected result: {result}"


async def browser_screenshot() -> str:
    """Capture a screenshot of the active browser tab.

    Returns a base64-encoded PNG image or error description.
    """
    cmd_id = _enqueue_command('screenshot', {})
    result = await _wait_for_result(cmd_id, timeout=10)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Screenshot failed: {result['error']}"
    if isinstance(result, dict) and result.get('image'):
        # Return as a data URI that can be embedded in chat
        img_b64 = result['image']
        return f"[Screenshot captured, {len(img_b64)} bytes]\ndata:image/png;base64,{img_b64[:100]}..."
    return f"[ERROR] Unexpected result: {result}"


async def browser_eval(js_code: str) -> str:
    """Execute arbitrary JavaScript in the active browser tab.

    Args:
        js_code: JavaScript code to execute.

    Returns the result of the evaluation (converted to string) or error.
    
    WARNING: This is a powerful tool. Use with caution.
    """
    cmd_id = _enqueue_command('eval', {'code': js_code})
    result = await _wait_for_result(cmd_id, timeout=15)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Eval failed: {result['error']}"
    if isinstance(result, dict) and 'result' in result:
        res = result['result']
        if isinstance(res, str) and len(res) > 2000:
            return f"[eval result, {len(res)} chars]\n{res[:2000]}..."
        return f"[eval result]\n{res}"
    return f"[ERROR] Unexpected result: {result}"


async def browser_wait(selector: str, timeout: int = 10) -> str:
    """Wait for an element to appear in the active browser tab.

    Args:
        selector: CSS selector to wait for.
        timeout: Max seconds to wait (default 10).

    Returns a success message or timeout error.
    """
    cmd_id = _enqueue_command('wait', {'selector': selector, 'timeout': timeout})
    result = await _wait_for_result(cmd_id, timeout=timeout + 2)
    
    if isinstance(result, dict) and result.get('error'):
        return f"[ERROR] Wait failed: {result['error']}"
    if isinstance(result, dict) and result.get('success'):
        return f"✓ Element appeared: {selector}"
    return f"[ERROR] Unexpected result: {result}"


# ---------------------------------------------------------------------------
# Addon management tools  (server-side — calls addons.py directly)
# ---------------------------------------------------------------------------

def addon_list() -> str:
    """List all Intelli addons and their status."""
    try:
        import sys, os
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        items = _addons.list_addons()
        if not items:
            return 'No addons installed yet.'
        lines = []
        for a in items:
            status = '✓ active' if a.get('active') else '○ inactive'
            lines.append(f"  {status}  {a['name']} — {a.get('description', '')}")
        return 'Installed addons:\n' + '\n'.join(lines)
    except Exception as e:
        return f'[ERROR] addon_list: {e}'


def addon_create(name: str, description: str, code_js: str) -> str:
    """Create a new Intelli addon.

    Args:
        name: Short slug (no spaces) for the addon.
        description: What the addon does.
        code_js: JavaScript snippet run inside the active browser tab on activation.
    """
    try:
        import sys, os, re
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        slug = re.sub(r'[^a-zA-Z0-9_-]', '-', name.strip())
        addon = _addons.create_addon(slug, description, code_js)
        return f'Addon "{slug}" created successfully. Use addon_activate to inject it into the active tab.'
    except ValueError as e:
        return f'[ERROR] addon_create: {e}'
    except Exception as e:
        return f'[ERROR] addon_create: {e}'


def addon_activate(name: str) -> str:
    """Activate an addon — injects its JavaScript into the currently active browser tab.

    Args:
        name: The addon name/slug to activate.
    """
    try:
        import sys, os
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        _addons.activate_addon(name)
        return (
            f'Addon "{name}" activated and queued for injection. '
            'The browser shell will inject the JS into the active tab within ~2 seconds.'
        )
    except KeyError:
        return f'[ERROR] addon_activate: addon "{name}" not found — create it first with addon_create.'
    except Exception as e:
        return f'[ERROR] addon_activate: {e}'


def addon_create_and_activate(name: str, description: str, code_js: str) -> str:
    """Create an addon and immediately activate it — injects JS into the active tab in one step.

    Args:
        name: Short slug for the addon (no spaces).
        description: What the addon does.
        code_js: JavaScript snippet to inject into the active browser tab.
    """
    import re as _re
    slug = _re.sub(r'[^a-zA-Z0-9_-]', '-', name.strip())

    # If the addon already exists, update its code instead of erroring out.
    # This allows re-running the same request with improved code.
    try:
        import sys, os
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        existing = _addons.get_addon(slug)
        if existing is not None:
            _addons.update_addon(slug, description=description, code_js=code_js)
        else:
            _addons.create_addon(slug, description, code_js)
    except Exception as e:
        return f'[ERROR] addon_create_and_activate (create/update): {e}'

    return addon_activate(slug)


def addon_deactivate(name: str) -> str:
    """Deactivate an addon (marks it inactive; does not undo already-injected JS).

    Args:
        name: The addon name/slug to deactivate.
    """
    try:
        import sys, os
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        _addons.deactivate_addon(name)
        return f'Addon "{name}" deactivated.'
    except KeyError:
        return f'[ERROR] addon_deactivate: addon "{name}" not found.'
    except Exception as e:
        return f'[ERROR] addon_deactivate: {e}'


def addon_delete(name: str) -> str:
    """Permanently delete an addon.

    Args:
        name: The addon name/slug to delete.
    """
    try:
        import sys, os
        _gw = os.path.dirname(os.path.dirname(__file__))
        if _gw not in sys.path:
            sys.path.insert(0, _gw)
        import addons as _addons
        _addons.delete_addon(name)
        return f'Addon "{name}" deleted.'
    except KeyError:
        return f'[ERROR] addon_delete: addon "{name}" not found.'
    except Exception as e:
        return f'[ERROR] addon_delete: {e}'


ADDON_TOOLS: Dict[str, Any] = {
    'addon_list': {
        'fn': addon_list,
        'description': 'List all installed Intelli addons with their name, status (active/inactive), and description.',
        'args': {},
    },
    'addon_create': {
        'fn': addon_create,
        'description': (
            'Save a new Intelli addon WITHOUT activating it — use this ONLY when the user explicitly '
            'says they do not want it to run yet. '
            'In ALL other cases ("make an addon", "create an addon", "change something on the page") '
            'use addon_create_and_activate instead. '
            'Names must be short slugs (no spaces). '
            'IMPORTANT: code_js must be valid JavaScript wrapped in an IIFE: '
            '(function(){...})(); — use style.textContent not innerHTML, '
            'and guard against double-injection with document.getElementById.'
        ),
        'args': {
            'name':        {'type': 'string', 'required': True,  'description': 'Addon slug, e.g. pink-x-logo (no spaces, hyphens ok)'},
            'description': {'type': 'string', 'required': False, 'description': 'Human description of what the addon does'},
            'code_js':     {'type': 'string', 'required': True,  'description': 'Valid JS wrapped in IIFE. Example: (function(){var s=document.createElement(\'style\');s.id=\'my-addon\';s.textContent=\'svg{color:pink}\';\ document.head.appendChild(s);})();'},
        },
    },
    'addon_activate': {
        'fn': addon_activate,
        'description': (
            'Activate an Intelli addon by name — queues its JavaScript for injection into the active browser tab. '
            'The JS runs within ~2 seconds in whatever page the user is currently viewing.'
        ),
        'args': {
            'name': {'type': 'string', 'required': True, 'description': 'Addon slug to activate'},
        },
    },
    'addon_create_and_activate': {
        'fn': addon_create_and_activate,
        'description': (
            'Create an Intelli addon and immediately inject its JavaScript into the active browser tab — one-step shortcut. '
            'Use this for ANY request to create, make, or build an addon, or modify/change something on the current page. '
            'Trigger phrases: "make an addon", "create an addon", "make an intelli addon", '
            '"inject", "add", "change", "modify", "hide", "replace" anything on the page. '
            'If an addon with the same name exists, it will be updated with the new code. '
            'CRITICAL — code_js rules:\n'
            '  1. Must be syntactically valid JavaScript.\n'
            '  2. Always wrap in an IIFE: (function(){ ... })();\n'
            '  3. Inject CSS with style.textContent = "...", NEVER style.innerHTML.\n'
            '  4. Do NOT check window.location.href — the code runs in the user\'s active tab already.\n'
            '  5. Guard double-injection: if (document.getElementById(\'MY-ID\')) return;\n'
            'Example for "make X logo pink":\n'
            '(function(){'
            'var id="intelli-pink-x";'
            'if(document.getElementById(id))return;'
            'var s=document.createElement("style");'
            's.id=id;'
            's.textContent="header svg,a[href=\'/\'] svg,a[href=\'/home\'] svg{color:#ff69b4!important;fill:currentColor!important}";'
            'document.head.appendChild(s);'
            '})();'
        ),
        'args': {
            'name':        {'type': 'string', 'required': True,  'description': 'Addon slug, e.g. pink-x-logo (hyphens ok, no spaces)'},
            'description': {'type': 'string', 'required': False, 'description': 'What the addon does'},
            'code_js':     {'type': 'string', 'required': True,  'description': 'Valid JS IIFE that manipulates the DOM. Do not check location.href. Use style.textContent for CSS injection.'},
        },
    },
    'addon_deactivate': {
        'fn': addon_deactivate,
        'description': 'Deactivate (disable) an Intelli addon by name.',
        'args': {
            'name': {'type': 'string', 'required': True, 'description': 'Addon slug to deactivate'},
        },
    },
    'addon_delete': {
        'fn': addon_delete,
        'description': 'Permanently delete an Intelli addon by name.',
        'args': {
            'name': {'type': 'string', 'required': True, 'description': 'Addon slug to delete'},
        },
    },
}


# ---------------------------------------------------------------------------
# Tool registry for tool_runner.py
# ---------------------------------------------------------------------------

BROWSER_TOOLS: Dict[str, Any] = {
    'browser_click': {
        'fn': browser_click,
        'description': (
            'Click an element in the active browser tab. '
            'Use CSS selectors to target elements (e.g., "#submit-btn", ".login-link").'
        ),
        'args': {
            'selector': {'type': 'string', 'required': True, 'description': 'CSS selector for the element'},
            'button':   {'type': 'string', 'required': False, 'description': 'Mouse button: left, right, or middle (default: left)'},
        },
    },
    'browser_type': {
        'fn': browser_type,
        'description': (
            'Type text into an input field in the active browser tab. '
            'Clears existing content by default. Use for form filling and search boxes.'
        ),
        'args': {
            'selector': {'type': 'string',  'required': True,  'description': 'CSS selector for the input element'},
            'text':     {'type': 'string',  'required': True,  'description': 'Text to type into the field'},
            'clear':    {'type': 'boolean', 'required': False, 'description': 'Clear existing content first (default: true)'},
        },
    },
    'browser_scroll': {
        'fn': browser_scroll,
        'description': (
            'Scroll the active browser tab. '
            'Use positive pixels to scroll down, negative to scroll up, or set to_bottom=true.'
        ),
        'args': {
            'pixels':    {'type': 'integer', 'required': False, 'description': 'Pixels to scroll (positive=down, negative=up)'},
            'to_bottom': {'type': 'boolean', 'required': False, 'description': 'Scroll to bottom of page (default: false)'},
        },
    },
    'browser_navigate': {
        'fn': browser_navigate,
        'description': 'Navigate the active browser tab to a new URL.',
        'args': {
            'url': {'type': 'string', 'required': True, 'description': 'The URL to navigate to'},
        },
    },
    'browser_screenshot': {
        'fn': browser_screenshot,
        'description': (
            'Capture a screenshot of the active browser tab as a base64-encoded PNG. '
            'Useful for visual verification or debugging.'
        ),
        'args': {},
    },
    'browser_eval': {
        'fn': browser_eval,
        'description': (
            'Execute arbitrary JavaScript code in the active browser tab and return the result. '
            'Use for complex DOM queries, data extraction, or custom interactions. '
            'WARNING: Powerful tool — use with caution.'
        ),
        'args': {
            'js_code': {'type': 'string', 'required': True, 'description': 'JavaScript code to execute'},
        },
    },
    'browser_wait': {
        'fn': browser_wait,
        'description': (
            'Wait for an element to appear in the active browser tab. '
            'Useful before clicking or typing to ensure the element is ready.'
        ),
        'args': {
            'selector': {'type': 'string',  'required': True,  'description': 'CSS selector to wait for'},
            'timeout':  {'type': 'integer', 'required': False, 'description': 'Max seconds to wait (default: 10)'},
        },
    },
}

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

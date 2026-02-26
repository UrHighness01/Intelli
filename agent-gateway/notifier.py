"""Outbound notification push for Intelli Gateway.

Supports three channels configured via environment variables:

  Telegram
    INTELLI_TELEGRAM_BOT_TOKEN  – bot token from BotFather
    INTELLI_TELEGRAM_CHAT_ID    – numeric chat / channel id

  Discord
    INTELLI_DISCORD_WEBHOOK_URL – incoming-webhook URL from Discord server settings

  Slack
    INTELLI_SLACK_WEBHOOK_URL   – incoming-webhook URL from Slack app settings

Usage::

    from notifier import send, list_channels
    send('telegram', 'Hello from Intelli!')
    send('discord', 'Task complete', title='Agent Result')
    send('slack', 'Error occurred', title='Alert')
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMEOUT = float(os.environ.get('INTELLI_NOTIFY_TIMEOUT', '10'))


def _post(url: str, json: Dict[str, Any]) -> Dict[str, Any]:
    """POST *json* to *url* and return a normalised result dict."""
    try:
        r = httpx.post(url, json=json, timeout=_TIMEOUT)
        return {'ok': r.is_success, 'status_code': r.status_code, 'body': r.text}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


# ---------------------------------------------------------------------------
# Channel implementations
# ---------------------------------------------------------------------------

def _send_telegram(message: str, title: str = '', **_: Any) -> Dict[str, Any]:
    token = os.environ.get('INTELLI_TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.environ.get('INTELLI_TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat_id:
        return {'ok': False, 'error': 'INTELLI_TELEGRAM_BOT_TOKEN and INTELLI_TELEGRAM_CHAT_ID not configured'}
    text = f'*{title}*\n{message}' if title else message
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    return _post(url, {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'})


def _send_discord(message: str, title: str = '', image_url: str = '', **_: Any) -> Dict[str, Any]:
    webhook_url = os.environ.get('INTELLI_DISCORD_WEBHOOK_URL', '').strip()
    if not webhook_url:
        return {'ok': False, 'error': 'INTELLI_DISCORD_WEBHOOK_URL not configured'}
    embed: Dict[str, Any] = {'description': message}
    if title:
        embed['title'] = title
    if image_url:
        embed['image'] = {'url': image_url}
    return _post(webhook_url, {'embeds': [embed]})


def _send_slack(message: str, title: str = '', **_: Any) -> Dict[str, Any]:
    webhook_url = os.environ.get('INTELLI_SLACK_WEBHOOK_URL', '').strip()
    if not webhook_url:
        return {'ok': False, 'error': 'INTELLI_SLACK_WEBHOOK_URL not configured'}
    text = f'*{title}*\n{message}' if title else message
    return _post(webhook_url, {'text': text})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CHANNELS: Dict[str, Any] = {
    'telegram': _send_telegram,
    'discord':  _send_discord,
    'slack':    _send_slack,
}


def send(channel: str, message: str, title: str = '', image_url: str = '') -> Dict[str, Any]:
    """Send *message* to *channel*.

    Parameters
    ----------
    channel:
        One of ``'telegram'``, ``'discord'``, ``'slack'``.
    message:
        Plain text body.  Markdown formatting accepted by Telegram and Discord.
    title:
        Optional title / embed title.
    image_url:
        Optional image URL (Discord only).

    Returns a dict with at least ``ok: bool`` and optionally ``error``,
    ``status_code``, ``body``.
    """
    channel = channel.lower().strip()
    fn = _CHANNELS.get(channel)
    if fn is None:
        return {'ok': False, 'error': f'Unknown channel "{channel}". Valid: {sorted(_CHANNELS)}'}
    return fn(message, title=title, image_url=image_url)


def list_channels() -> List[Dict[str, Any]]:
    """Return status of all supported channels (configured / unconfigured)."""
    checks = [
        ('telegram', bool(os.environ.get('INTELLI_TELEGRAM_BOT_TOKEN')) and bool(os.environ.get('INTELLI_TELEGRAM_CHAT_ID'))),
        ('discord',  bool(os.environ.get('INTELLI_DISCORD_WEBHOOK_URL'))),
        ('slack',    bool(os.environ.get('INTELLI_SLACK_WEBHOOK_URL'))),
    ]
    return [{'channel': name, 'configured': cfg} for name, cfg in checks]

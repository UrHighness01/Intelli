"""
compaction.py — Session compaction for Intelli chat.

When a conversation approaches the model's context limit, older messages are
summarized by the LLM into a compact block so the session can continue without
losing continuity.

Token estimation is intentionally rough (4 chars ≈ 1 token) — accurate enough
to trigger compaction at the right time without requiring tiktoken.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from providers.adapters import BaseAdapter

# ---------------------------------------------------------------------------
# Model context limits (tokens)
# ---------------------------------------------------------------------------
# Listed conservatively — actual limits are higher but we need headroom
# for the system prompt + assistant reply.

_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI
    'gpt-3.5-turbo':       16_385,
    'gpt-4':               8_192,
    'gpt-4-turbo':         128_000,
    'gpt-4o':              128_000,
    'gpt-4o-mini':         128_000,
    'gpt-4.1':             128_000,
    'gpt-4.1-mini':        128_000,
    'o1':                  200_000,
    'o1-mini':             128_000,
    'o3-mini':             200_000,
    # Anthropic
    'claude-3-haiku-20240307':  200_000,
    'claude-3-sonnet-20240229': 200_000,
    'claude-3-opus-20240229':   200_000,
    'claude-sonnet-4.5':        200_000,
    'claude-sonnet-4.6':        200_000,
    # Google / OpenRouter / misc
    'gemini-pro':           32_000,
    'gemini-1.5-pro':       1_000_000,
    'mistral-7b-instruct':  32_000,
    'llama3':               8_192,
    'llama3:8b':            8_192,
    'llama3:70b':           8_192,
    'mistral':              32_000,
    # GitHub Copilot
    'copilot':              128_000,
}

_DEFAULT_LIMIT = 32_000   # conservative fallback for unknown models
_COMPACT_THRESHOLD = 0.78  # trigger when using ≥78% of context


def estimate_tokens(text: str) -> int:
    """Rough token count: 4 chars ≈ 1 token (OpenAI rule of thumb)."""
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of chat messages."""
    total = 0
    for m in messages:
        # ~4 per-message overhead (role + formatting)
        total += 4 + estimate_tokens(m.get('content', '') or '')
    return total


def context_limit_for(model: str) -> int:
    """Return the context window size for a model name (or conservative default)."""
    if not model:
        return _DEFAULT_LIMIT
    model_lower = model.lower().strip()
    # Exact match first
    if model_lower in _CONTEXT_LIMITS:
        return _CONTEXT_LIMITS[model_lower]
    # Prefix match (e.g. "gpt-4o-mini-2024-07-18" → "gpt-4o-mini")
    for key, limit in _CONTEXT_LIMITS.items():
        if model_lower.startswith(key) or key in model_lower:
            return limit
    return _DEFAULT_LIMIT


def usage_fraction(messages: list[dict], model: str) -> float:
    """Return the fraction (0–1) of the context window currently used."""
    used  = estimate_messages_tokens(messages)
    limit = context_limit_for(model)
    return used / limit


def needs_compaction(messages: list[dict], model: str) -> bool:
    """Return True when the conversation should be compacted."""
    return usage_fraction(messages, model) >= _COMPACT_THRESHOLD


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------

_COMPACT_SYSTEM = """\
You are a conversation compactor.
Summarize the following chat history into a concise block that preserves:
- All concrete facts, decisions, and outcomes
- Any code snippets or technical details that were produced
- The user's goals and the assistant's conclusions

Output ONLY the summary — no preamble, no "Here is a summary:" prefix.
Be thorough but terse. Bullet points are fine.
"""

_KEEP_LAST_N = 4  # always keep the most recent N messages uncompacted


def compact_messages(
    messages: list[dict],
    adapter,
    model: str = '',
    temperature: float = 0.3,
) -> tuple[list[dict], str, int]:
    """Summarize old messages and return compacted message list.

    Returns:
        (compacted_messages, summary_text, tokens_saved)
    """
    if len(messages) <= _KEEP_LAST_N + 1:
        # Nothing to compact
        return messages, '', 0

    to_compact = messages[:-_KEEP_LAST_N]
    to_keep    = messages[-_KEEP_LAST_N:]

    # Build the history text for summarization
    history_text = '\n'.join(
        f'{m["role"].upper()}: {m.get("content", "")}'
        for m in to_compact
        if m.get('content', '').strip()
    )

    kwargs: dict = {}
    if model:
        kwargs['model'] = model

    result = adapter.chat_complete(
        messages=[{'role': 'user', 'content': history_text}],
        temperature=temperature,
        max_tokens=1024,
        system=_COMPACT_SYSTEM,
        **kwargs,
    )
    summary = result.get('content', '').strip()

    tokens_before = estimate_messages_tokens(messages)
    summary_msg   = {
        'role': 'system',
        'content': f'[CONVERSATION SUMMARY — earlier messages compacted]\n\n{summary}',
    }
    compacted = [summary_msg] + list(to_keep)
    tokens_after  = estimate_messages_tokens(compacted)
    tokens_saved  = max(0, tokens_before - tokens_after)

    return compacted, summary, tokens_saved

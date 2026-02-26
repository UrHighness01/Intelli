"""voice.py — Whisper STT + edge-tts TTS for the Intelli gateway.

Speech-to-text   (STT):
  transcribe(audio_bytes, filename, provider)
  → calls OpenAI Whisper API (requires OPENAI_API_KEY)
  → falls back to local ``whisper`` package if installed
  → falls back to echoing silence with a clear error message

Text-to-speech   (TTS):
  speak_bytes(text, voice, rate, pitch)
  → edge-tts (Microsoft neural voices, no API key needed, free)
  → yields MP3 bytes chunks suitable for streaming via FastAPI StreamingResponse

Voice listing:
  list_voices(locale_prefix)
  → returns filtered list from edge-tts voice catalogue
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from typing import AsyncIterator, List, Dict, Any, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_VOICE = os.environ.get('INTELLI_TTS_VOICE', 'en-US-JennyNeural')
DEFAULT_RATE  = os.environ.get('INTELLI_TTS_RATE',  '+0%')
DEFAULT_PITCH = os.environ.get('INTELLI_TTS_PITCH', '+0Hz')

# Max text length for a single TTS call (large texts should be chunked by caller)
_MAX_TTS_CHARS = 5000
# Max audio bytes accepted for transcription (25 MB — matches OpenAI limit)
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# STT — Whisper
# ---------------------------------------------------------------------------

def transcribe(
    audio_bytes: bytes,
    filename: str = 'audio.webm',
    provider_key: Optional[str] = None,
) -> str:
    """Transcribe audio bytes to text.

    Args:
        audio_bytes: Raw audio file contents (WebM, WAV, MP4, MP3 …).
        filename:    Original filename (extension used as format hint).
        provider_key: OpenAI API key override (falls back to OPENAI_API_KEY env).

    Returns:
        Transcribed text string, or error message prefixed with ``[ERROR]``.
    """
    if not audio_bytes:
        return '[ERROR] No audio received'
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        return f'[ERROR] Audio too large ({len(audio_bytes)//1024//1024} MB, max 25 MB)'

    api_key = provider_key or os.environ.get('OPENAI_API_KEY', '')

    # ── Strategy 1: OpenAI Whisper API ──────────────────────────────────────
    if api_key:
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            with io.BytesIO(audio_bytes) as buf:
                buf.name = filename  # openai SDK uses .name for format detection
                transcript = client.audio.transcriptions.create(
                    model='whisper-1',
                    file=buf,
                    response_format='text',
                )
            return str(transcript).strip()
        except Exception as exc:
            log.warning('OpenAI Whisper failed: %s — trying local fallback', exc)

    # ── Strategy 2: local whisper package ───────────────────────────────────
    try:
        import whisper as _whisper  # type: ignore
        with tempfile.NamedTemporaryFile(suffix=_ext(filename), delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            model = _whisper.load_model('tiny')
            result = model.transcribe(tmp_path)
            return result.get('text', '').strip()
        finally:
            os.unlink(tmp_path)
    except ImportError:
        pass  # local whisper not installed — that's fine
    except Exception as exc:
        log.warning('Local Whisper failed: %s', exc)

    # ── Strategy 3: graceful error ───────────────────────────────────────────
    if not api_key:
        return (
            '[ERROR] No OPENAI_API_KEY set and local whisper package not installed. '
            'Add your OpenAI API key in Providers settings to enable voice transcription.'
        )
    return '[ERROR] Transcription failed. Check gateway logs for details.'


def _ext(filename: str) -> str:
    """Return the file extension including the dot, e.g. '.webm'."""
    _, ext = os.path.splitext(filename)
    return ext or '.webm'


# ---------------------------------------------------------------------------
# TTS — edge-tts
# ---------------------------------------------------------------------------

async def speak_bytes(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> bytes:
    """Convert text to speech using edge-tts and return MP3 bytes.

    Args:
        text:   Text to speak (max ``_MAX_TTS_CHARS`` characters).
        voice:  edge-tts voice short name, e.g. ``en-US-JennyNeural``.
        rate:   Speaking rate delta, e.g. ``+0%``, ``+20%``, ``-10%``.
        pitch:  Pitch delta in Hz, e.g. ``+0Hz``, ``+5Hz``.

    Returns:
        MP3 audio bytes.
    """
    import edge_tts  # type: ignore

    text = text[:_MAX_TTS_CHARS].strip()
    if not text:
        return b''

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk['type'] == 'audio':
            buf.write(chunk['data'])
    return buf.getvalue()


async def speak_stream(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
) -> AsyncIterator[bytes]:
    """Stream MP3 audio chunks for a text string — more responsive for long text."""
    import edge_tts  # type: ignore

    text = text[:_MAX_TTS_CHARS].strip()
    if not text:
        return

    comm = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    async for chunk in comm.stream():
        if chunk['type'] == 'audio':
            yield chunk['data']


# ---------------------------------------------------------------------------
# Voice listing
# ---------------------------------------------------------------------------

_voice_cache: Optional[List[Dict[str, Any]]] = None


async def list_voices(locale_prefix: str = '') -> List[Dict[str, Any]]:
    """Return available edge-tts voices, optionally filtered by locale prefix.

    Results are cached after the first call.

    Args:
        locale_prefix: Filter by locale, e.g. ``'en-'`` or ``'en-US'``.
                       Empty string returns all voices.
    """
    global _voice_cache
    import edge_tts  # type: ignore

    if _voice_cache is None:
        raw = await edge_tts.list_voices()
        _voice_cache = [
            {
                'name':   v['ShortName'],
                'locale': v['Locale'],
                'gender': v['Gender'],
                'label':  v.get('FriendlyName', v['ShortName']),
            }
            for v in raw
        ]

    if locale_prefix:
        return [v for v in _voice_cache if v['locale'].startswith(locale_prefix)]
    return _voice_cache


# ---------------------------------------------------------------------------
# Utility — split long text into sentences for chunked TTS
# ---------------------------------------------------------------------------

def split_sentences(text: str, max_chars: int = 1000) -> List[str]:
    """Split text into sentence-sized chunks for streaming TTS."""
    import re
    # Split on sentence boundaries
    parts = re.split(r'(?<=[.!?])\s+', text)
    chunks: List[str] = []
    current = ''
    for part in parts:
        if len(current) + len(part) + 1 > max_chars:
            if current:
                chunks.append(current.strip())
            current = part
        else:
            current = f'{current} {part}' if current else part
    if current.strip():
        chunks.append(current.strip())
    return chunks

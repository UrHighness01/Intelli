"""Video frame extraction for Intelli Gateway.

Extracts evenly-spaced frames from a video (URL or local path) using
``ffmpeg`` and returns them as base-64-encoded JPEG strings for downstream
vision-model analysis.

Requirements
------------
``ffmpeg`` must be present on ``$PATH``.  The module works without it at
import time — errors surface only when :func:`extract_frames` is called.

Public API
----------
    extract_frames(source, n_frames, quality) -> list[FrameResult]
    describe_video(source, n_frames, provider, model) -> str
    ffmpeg_available() -> bool
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class FrameResult(TypedDict):
    frame: int              # 1-based frame index
    timestamp_s: float      # approximate position in seconds
    b64: str                # base-64 JPEG


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def ffmpeg_available() -> bool:
    """Return True if ffmpeg is installed and on PATH."""
    return shutil.which('ffmpeg') is not None


def _probe_duration(source: str) -> Optional[float]:
    """Best-effort video duration via ffprobe."""
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', source],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _download_to_tmp(url: str) -> str:
    """Download *url* to a NamedTemporaryFile and return its path."""
    suffix = Path(url.split('?')[0]).suffix or '.mp4'
    fd, path = tempfile.mkstemp(suffix=suffix, prefix='intelli_video_')
    os.close(fd)
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    with opener.open(url, timeout=60) as resp, open(path, 'wb') as fh:
        fh.write(resp.read())
    return path


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def extract_frames(
    source: str,
    n_frames: int = 5,
    quality: int = 3,
) -> List[FrameResult]:
    """Extract *n_frames* evenly-spaced frames from *source*.

    Parameters
    ----------
    source:
        HTTP/HTTPS URL or local file path to the video.
    n_frames:
        Number of frames to extract (clamped 1-20).
    quality:
        JPEG quality scale for ffmpeg -q:v (1 = best, 31 = worst).
        Default 3 gives ~85 % JPEG comparable quality.

    Returns a list of :class:`FrameResult` dicts.

    Raises
    ------
    RuntimeError
        If ffmpeg is not installed or the extraction fails.
    """
    if not ffmpeg_available():
        raise RuntimeError(
            'ffmpeg is not installed. Install it via your package manager '
            '(e.g. sudo apt install ffmpeg) to use video frame analysis.'
        )

    n_frames = max(1, min(int(n_frames), 20))
    quality = max(1, min(int(quality), 31))

    tmp_download: Optional[str] = None
    try:
        if source.startswith(('http://', 'https://')):
            tmp_download = _download_to_tmp(source)
            video_path = tmp_download
        else:
            video_path = source
            if not Path(video_path).exists():
                raise FileNotFoundError(f'Video file not found: {video_path}')

        duration = _probe_duration(video_path)

        # Build ffmpeg filter: select frames at evenly-spaced timestamps
        with tempfile.TemporaryDirectory(prefix='intelli_frames_') as tmpdir:
            out_pattern = os.path.join(tmpdir, 'frame_%03d.jpg')

            if duration and duration > 0:
                # Use select filter for exact timestamps
                interval = duration / (n_frames + 1)
                select_expr = '+'.join(
                    f'eq(t,{interval * (i + 1):.3f})' for i in range(n_frames)
                )
                # Round timestamps to close values
                # Simpler: fps-based approach — calc fps to yield exactly n_frames
                fps = n_frames / duration
                cmd = [
                    'ffmpeg', '-i', video_path,
                    '-vf', f'fps={fps:.6f}',
                    '-q:v', str(quality),
                    '-vframes', str(n_frames),
                    '-f', 'image2',
                    out_pattern,
                    '-y', '-loglevel', 'error',
                ]
            else:
                # Unknown duration — spread across whole file with fps hack
                cmd = [
                    'ffmpeg', '-i', video_path,
                    '-vf', f'fps=1/{max(1, 10 // n_frames)}',
                    '-q:v', str(quality),
                    '-vframes', str(n_frames),
                    '-f', 'image2',
                    out_pattern,
                    '-y', '-loglevel', 'error',
                ]

            subprocess.run(cmd, check=True, capture_output=True, timeout=120)

            frames: List[FrameResult] = []
            frame_files = sorted(Path(tmpdir).glob('frame_*.jpg'))
            total = len(frame_files)
            for idx, fp in enumerate(frame_files):
                raw = fp.read_bytes()
                ts = (duration / (total + 1) * (idx + 1)) if duration else float(idx)
                frames.append(FrameResult(
                    frame=idx + 1,
                    timestamp_s=round(ts, 2),
                    b64=base64.b64encode(raw).decode(),
                ))
            return frames

    finally:
        if tmp_download and os.path.exists(tmp_download):
            os.unlink(tmp_download)


# ---------------------------------------------------------------------------
# Vision-model integration
# ---------------------------------------------------------------------------

def describe_video(
    source: str,
    n_frames: int = 5,
    provider: str = '',
    model: str = '',
    prompt: str = '',
) -> str:
    """Extract frames and send them to a vision-capable LLM for description.

    Parameters
    ----------
    source:
        Video URL or local path.
    n_frames:
        Number of frames to extract (1-20).
    provider:
        LLM provider name (defaults to the active provider).
    model:
        Model name override.
    prompt:
        Custom instruction appended to the analysis request.

    Returns the model's textual description.
    """
    import sys as _sys
    _gw = os.path.dirname(os.path.dirname(__file__))
    if _gw not in _sys.path:
        _sys.path.insert(0, _gw)

    from providers.adapters import get_adapter, available_providers

    prov = provider or (available_providers()[0] if available_providers() else 'openai')
    adpt = get_adapter(prov)

    # Extract frames
    try:
        frames = extract_frames(source, n_frames=n_frames)
    except Exception as exc:
        return f'[ERROR] Frame extraction failed: {exc}'

    if not frames:
        return '[ERROR] No frames extracted from video.'

    # Build multimodal message content
    instructions = (
        prompt
        or 'Describe what is happening in this video based on the sampled frames. '
           'Briefly explain the scene, people, objects, and any notable actions.'
    )
    content: List[Dict[str, Any]] = [
        {'type': 'text', 'text': f'I have extracted {len(frames)} frames from a video. {instructions}'},
    ]
    for fr in frames:
        content.append({
            'type': 'image_url',
            'image_url': {'url': f'data:image/jpeg;base64,{fr["b64"]}'},
        })

    messages = [{'role': 'user', 'content': content}]

    try:
        resp = adpt.chat(messages, model=model, max_tokens=1024, temperature=0.3)
        return resp.get('content', '').strip() or '(model returned no content)'
    except Exception as exc:
        return f'[ERROR] Vision model failed: {exc}'

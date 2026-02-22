#!/usr/bin/env python3
"""log_shipper.py — audit.log tail + SIEM forwarder.

Watches the Intelli agent-gateway audit log, batches JSONL entries,
and POSTs them to an external SIEM / log-aggregation endpoint.

Usage
-----
    python scripts/log_shipper.py

Environment variables
---------------------
INTELLI_SIEM_URL
    Required. HTTP(S) endpoint to POST batches to.
    Example: https://my-siem.example.com/api/v1/logs

INTELLI_SIEM_BATCH_SIZE
    Number of log entries per POST request. Default: 50.

INTELLI_SIEM_INTERVAL_SECS
    Poll interval in seconds. Default: 10.

INTELLI_AUDIT_LOG
    Path to the audit log file.
    Default: <repo-root>/agent-gateway/audit.log

INTELLI_SIEM_TOKEN
    Optional Bearer token for the SIEM endpoint.
    Sent as ``Authorization: Bearer <token>`` when set.

INTELLI_SIEM_RETRIES
    Maximum POST retries per batch on transient errors. Default: 3.

INTELLI_SIEM_RETRY_DELAY
    Seconds to wait between retries. Default: 2.

Behaviour
---------
* Starts reading from the *end* of the existing file so historical
  entries are not re-shipped on first run.
* Raw lines that are not valid JSON are wrapped as ``{"raw": "<line>"}``.
* Batches are shipped in NDJSON format (one JSON object per line,
  ``Content-Type: application/x-ndjson``).
* Retries up to ``INTELLI_SIEM_RETRIES`` times per batch on HTTP ≥ 500
  or network errors, with ``INTELLI_SIEM_RETRY_DELAY`` seconds between
  attempts.
* Exits cleanly on SIGINT / KeyboardInterrupt.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

SIEM_URL: str = os.environ.get("INTELLI_SIEM_URL", "")
BATCH_SIZE: int = int(os.environ.get("INTELLI_SIEM_BATCH_SIZE", "50"))
INTERVAL: float = float(os.environ.get("INTELLI_SIEM_INTERVAL_SECS", "10"))
SIEM_TOKEN: str = os.environ.get("INTELLI_SIEM_TOKEN", "")
MAX_RETRIES: int = int(os.environ.get("INTELLI_SIEM_RETRIES", "3"))
RETRY_DELAY: float = float(os.environ.get("INTELLI_SIEM_RETRY_DELAY", "2"))

_DEFAULT_LOG = (
    Path(__file__).resolve().parent.parent / "agent-gateway" / "audit.log"
)
AUDIT_LOG: Path = Path(os.environ.get("INTELLI_AUDIT_LOG", str(_DEFAULT_LOG)))


# ── Delivery ──────────────────────────────────────────────────────────────────

def _build_headers(body: bytes) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/x-ndjson",
        "Content-Length": str(len(body)),
        "User-Agent": "intelli-log-shipper/1.0",
    }
    if SIEM_TOKEN:
        headers["Authorization"] = f"Bearer {SIEM_TOKEN}"
    return headers


def _ship(batch: list[dict]) -> bool:  # noqa: C901
    """POST *batch* to SIEM_URL.  Retries on 5xx / network errors.

    Returns True when the batch was accepted (HTTP 2xx).
    """
    body = ("\n".join(json.dumps(e) for e in batch) + "\n").encode("utf-8")
    headers = _build_headers(body)

    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(
            SIEM_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 300:
                    return True
                _warn(
                    f"SIEM returned HTTP {resp.status} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                if resp.status < 500:
                    # Client error — no point retrying
                    return False
        except urllib.error.HTTPError as e:
            _warn(
                f"HTTP error {e.code}: {e.reason} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            if e.code < 500:
                return False
        except urllib.error.URLError as e:
            _warn(f"Network error: {e.reason} (attempt {attempt}/{MAX_RETRIES})")

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)

    return False


# ── Utilities ─────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"[log_shipper] {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"[log_shipper] WARNING: {msg}", file=sys.stderr, flush=True)


def _parse_line(line: str) -> dict:
    """Parse a JSONL line; wrap raw text on failure."""
    stripped = line.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"raw": stripped}


# ── Main loop ─────────────────────────────────────────────────────────────────

def tail_and_ship() -> None:
    """Continuously tail ``AUDIT_LOG`` and ship new entries in batches."""
    if not SIEM_URL:
        sys.exit(
            "[log_shipper] ERROR: INTELLI_SIEM_URL is not set — exiting.\n"
            "Set it to the HTTP(S) endpoint to POST batches to."
        )

    _info(
        f"watching {AUDIT_LOG}\n"
        f"  → {SIEM_URL}\n"
        f"  batch={BATCH_SIZE}  interval={INTERVAL}s  retries={MAX_RETRIES}"
    )

    # Start from the *end* of the existing file to avoid re-shipping history
    pos: int = AUDIT_LOG.stat().st_size if AUDIT_LOG.exists() else 0

    shipped_total = 0
    try:
        while True:
            time.sleep(INTERVAL)

            if not AUDIT_LOG.exists():
                continue

            # Handle log rotation: if file shrank, reset to beginning
            try:
                current_size = AUDIT_LOG.stat().st_size
            except OSError:
                continue
            if current_size < pos:
                _info("Log file appears to have been rotated — resetting position.")
                pos = 0

            # Read new lines
            new_entries: list[dict] = []
            try:
                with AUDIT_LOG.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    for raw_line in fh:
                        entry = _parse_line(raw_line)
                        if entry:
                            new_entries.append(entry)
                    pos = fh.tell()
            except OSError as e:
                _warn(f"Read error: {e}")
                continue

            if not new_entries:
                continue

            # Ship in BATCH_SIZE chunks
            failed = 0
            for i in range(0, len(new_entries), BATCH_SIZE):
                batch = new_entries[i : i + BATCH_SIZE]
                ok = _ship(batch)
                if ok:
                    shipped_total += len(batch)
                    _info(f"shipped {len(batch)} entr{'y' if len(batch)==1 else 'ies'} (total: {shipped_total})")
                else:
                    failed += len(batch)
                    _warn(f"failed to ship {len(batch)} entr{'y' if len(batch)==1 else 'ies'}")

    except KeyboardInterrupt:
        _info(f"Stopped. Total entries shipped: {shipped_total}")


if __name__ == "__main__":
    tail_and_ship()

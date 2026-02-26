#!/usr/bin/env bash
# start.sh — Set up the environment and launch Intelli browser (dev mode)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATEWAY_DIR="$SCRIPT_DIR/../agent-gateway"
VENV_DIR="$GATEWAY_DIR/.venv"
REQUIREMENTS="$GATEWAY_DIR/requirements.txt"
ICON_OUT="$SCRIPT_DIR/assets/icon.png"

echo "[intelli] Browser-shell root : $SCRIPT_DIR"
echo "[intelli] Gateway dir        : $GATEWAY_DIR"

# ── 1. Python venv ──────────────────────────────────────────────────────────
if [ ! -x "$VENV_DIR/bin/python3" ]; then
  echo "[intelli] Creating Python venv at $VENV_DIR …"
  python3 -m venv "$VENV_DIR"
fi

echo "[intelli] Installing / verifying Python dependencies …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$REQUIREMENTS"
echo "[intelli] Python deps OK  ($("$VENV_DIR/bin/python3" --version))"

# ── 2. Node.js / npm deps ───────────────────────────────────────────────────
cd "$SCRIPT_DIR"

if [ ! -d node_modules ]; then
  echo "[intelli] Installing npm dependencies …"
  npm install
fi

# ── 3. Generate placeholder icon (first run only) ───────────────────────────
if [ ! -f "$ICON_OUT" ]; then
  echo "[intelli] Generating placeholder icon …"
  node generate-icon.js
fi

# ── 4. Launch Electron ───────────────────────────────────────────────────────
echo "[intelli] Starting Intelli browser …"
exec npx electron .

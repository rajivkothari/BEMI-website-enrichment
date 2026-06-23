#!/usr/bin/env bash
# Bullseye website-enrichment web app launcher (macOS / Linux).
# Run:  ./start-web.sh
set -e
cd "$(dirname "$0")"
PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv .venv
fi

# Ensure dependencies are installed (also covers a venv missing packages).
if ! "$PY" -c "import streamlit" 2>/dev/null; then
  echo "[setup] Installing dependencies (may take a minute)..."
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -r requirements.txt
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "[setup] Created .env - add your GOOGLE_MAPS_API_KEY to look up missing websites."
fi

echo "[run] Starting the Bullseye web app...  (press Ctrl+C to stop)"
exec "$PY" -m streamlit run app.py

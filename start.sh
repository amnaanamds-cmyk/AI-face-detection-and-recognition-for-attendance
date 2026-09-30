#!/usr/bin/env bash
# One-command start on Linux / macOS:  ./start.sh        (add --lan for classroom devices)
set -e
cd "$(dirname "$0")"

# Setup is complete only when this marker exists (a half-finished .venv is rebuilt).
if [ ! -f .venv/setup-complete.txt ]; then
  PY=$(command -v python3 || command -v python || true)
  if [ -z "$PY" ]; then
    echo "ERROR: Python 3.10+ not found. Install it (e.g. sudo apt install python3 python3-venv) and try again."
    exit 1
  fi
  if ! "$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
    echo "ERROR: Python 3.10 or newer is required (found $("$PY" --version))."
    exit 1
  fi
  echo "First-time setup (needs internet, takes 3-10 minutes)..."
  rm -rf .venv
  "$PY" -m venv .venv || { echo "ERROR: could not create .venv (on Ubuntu: sudo apt install python3-venv)"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  echo ok > .venv/setup-complete.txt
fi
exec .venv/bin/python run.py "$@"

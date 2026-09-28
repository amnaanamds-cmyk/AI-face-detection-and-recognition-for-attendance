#!/usr/bin/env bash
# One-command start on Linux / macOS:  ./start.sh        (add --lan for classroom devices)
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python run.py "$@"

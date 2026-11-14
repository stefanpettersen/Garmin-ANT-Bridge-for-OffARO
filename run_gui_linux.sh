#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "ERROR: .venv is missing. Run: python3 -m venv .venv" >&2
    echo "Then run: .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
fi
exec .venv/bin/python garmin_gui.py

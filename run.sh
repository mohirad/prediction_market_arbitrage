#!/usr/bin/env bash
# One-command local setup + run: creates the venv if missing, installs
# deps, seeds config from the example files on first run, executes the
# test suite, then starts the interactive divergence-scanner app and
# opens it in a browser. Safe to re-run -- every step is idempotent.
# Leaves the server running in the foreground; press Ctrl+C to stop it.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "==> creating venv"
  python3 -m venv .venv
fi
source .venv/bin/activate
export PYTHONPATH="$PWD/src"

echo "==> installing dependencies"
pip install -q --upgrade pip
pip install -q -r requirements.txt

[ -f .env ] || cp .env.example .env
[ -f config/watchlist.yaml ] || cp config/watchlist.example.yaml config/watchlist.yaml

echo "==> running tests"
python -m pytest -q

echo "==> starting the divergence scanner app (Ctrl+C to stop)"
python scripts/serve_dashboard.py

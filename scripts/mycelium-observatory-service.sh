#!/usr/bin/env bash
set -euo pipefail

# launchd wrapper — explicit context using venv outside restricted paths
export PYTHONPATH="$HOME/Documents/mycelium"
exec "$HOME/.hermes/myceliumd/venv/bin/python3" -m uvicorn web.backend.app:app --host 127.0.0.1 --port 8421

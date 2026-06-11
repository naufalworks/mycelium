#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/Documents/mycelium"
export PYTHONPATH="$HOME/Documents/mycelium"
exec python3 -m uvicorn web.backend.app:app --reload --host 127.0.0.1 --port 8421

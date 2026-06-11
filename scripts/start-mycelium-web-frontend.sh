#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/Documents/mycelium/web/frontend"
exec npm run dev -- --host 127.0.0.1 --port 8420

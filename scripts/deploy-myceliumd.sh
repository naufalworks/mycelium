#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/install-myceliumd.sh"

echo
echo "--- launchd stderr ---"
cat "$HOME/.hermes/myceliumd/launchd.stderr.log" 2>/dev/null || true
echo "--- launchd stdout ---"
cat "$HOME/.hermes/myceliumd/launchd.stdout.log" 2>/dev/null || true
echo "--- daemon state ---"
cat "$HOME/.hermes/myceliumd/state.json" 2>/dev/null || true
echo "--- runtime verify ---"
/usr/bin/python3 "$HOME/.hermes/myceliumd/runtime/scripts/mycelium.py" verify || true

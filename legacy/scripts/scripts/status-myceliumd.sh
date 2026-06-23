#!/usr/bin/env bash
set -euo pipefail

LABEL="gui/$(id -u)/com.naufal.myceliumd"

echo "--- launchctl ---"
launchctl print "$LABEL" 2>/dev/null | sed -n '1,80p' || echo "not loaded"
echo "--- stderr ---"
cat "$HOME/.hermes/myceliumd/launchd.stderr.log" 2>/dev/null || true
echo "--- stdout ---"
cat "$HOME/.hermes/myceliumd/launchd.stdout.log" 2>/dev/null || true
echo "--- state ---"
cat "$HOME/.hermes/myceliumd/state.json" 2>/dev/null || true

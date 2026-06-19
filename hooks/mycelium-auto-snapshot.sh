#!/usr/bin/env bash
# mycelium-auto-snapshot.sh
#
# Hook: automatically create a context snapshot + extract facts
# on session close or crash.
#
# Usage options:
#   1. Cron:    */30 * * * * /path/to/mycelium-auto-snapshot.sh
#   2. Claude Code hook: set in .claude/settings.json as onHangup
#   3. Manual:  ./mycelium-auto-snapshot.sh
#
# Checks if the brain has new entries since last snapshot,
# and if so, runs mycelium snapshot.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAST_SNAP_FILE="$ROOT/.last_snapshot_turn"
LOG_FILE="$ROOT/log.jsonl"
PYTHON="python3"

if ! command -v $PYTHON &>/dev/null; then
    PYTHON="python3"
fi

# Get current turn count from brain
CURRENT_TURN=$($PYTHON -c "
import json
with open('$LOG_FILE') as f:
    last = None
    for line in f:
        if line.strip():
            last = json.loads(line)
    print(last.get('turn', 0) if last else 0)
" 2>/dev/null || echo "0")

# Get last snapshot turn
LAST_SNAP=$(cat "$LAST_SNAP_FILE" 2>/dev/null || echo "0")

# Only snapshot if there are new entries (minimum 3)
DIFF=$((CURRENT_TURN - LAST_SNAP))
if [ "$DIFF" -ge 3 ]; then
    echo "[mycelium] $DIFF new turns since last snapshot → running snapshot..."
    $PYTHON "$ROOT/scripts/mycelium.py" snapshot 2>&1 | tail -1
    echo "$CURRENT_TURN" > "$LAST_SNAP_FILE"

    # Also run lightweight compaction
    $PYTHON "$ROOT/scripts/mycelium.py" compact 2>&1 | tail -1
else
    echo "[mycelium] No new entries (diff=$DIFF), skipping snapshot"
fi

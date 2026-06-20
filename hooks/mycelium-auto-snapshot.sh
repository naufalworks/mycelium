#!/usr/bin/env bash
# mycelium-auto-snapshot.sh
#
# Event-based: called AFTER each brain append (no cron).
# Compares current turn vs last processed turn.
# If ≥3 new turns → snapshot + compact.
#
# Wire into your flow:
#   After brain append: bash hooks/mycelium-auto-snapshot.sh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAST_FILE="$ROOT/.last_snapshot_turn"
LOG_FILE="$ROOT/log.jsonl"

CURRENT=$("python3" -c "
import json
with open('$LOG_FILE') as f:
    last = None
    for line in f:
        if line.strip():
            last = json.loads(line)
    print(last.get('turn', 0) if last else 0)
" 2>/dev/null || echo "0")

LAST=$(cat "$LAST_FILE" 2>/dev/null || echo "0")
DIFF=$((CURRENT - LAST))

if [ "$DIFF" -ge 3 ]; then
    echo "[mycelium] $DIFF new turns → snapshot + compact..."
    python3 "$ROOT/scripts/mycelium.py" snapshot 2>&1 | tail -1
    python3 "$ROOT/scripts/mycelium.py" compact 2>&1 | tail -1
    echo "$CURRENT" > "$LAST_FILE"

    # Speculative cache: pre-compute predictions for next likely questions
    curl -s -X POST http://127.0.0.1:8443/api/cache/precompute \
        -H "Content-Type: application/json" \
        -d '{"threshold": 0.4}' > /dev/null 2>&1 || true
fi

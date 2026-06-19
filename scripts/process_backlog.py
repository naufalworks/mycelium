#!/usr/bin/env python3
"""
Backlog processor — extract basic snapshots from all existing sessions.

Strategy:
  - Entity extraction for all sessions (fast, deterministic, no LLM)
  - LLM extraction only for new sessions (via auto-snapshot hook)
  - This is a one-time catch-up; incremental processing is event-driven
"""

import json, sys, time
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "log.jsonl"
LAST_SNAP_FILE = ROOT / ".last_snapshot_turn"

sys.path.insert(0, str(ROOT / "scripts"))
from mycelium_lib import extract_entities
from mycelium_memory import create_snapshot, get_snapshot, insert_fact, full_compact, fact_stats


def load_all_entries():
    if not LOG.exists():
        print("log.jsonl not found")
        return []
    entries = []
    with open(LOG) as f:
        for line in f:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def group_by_session(entries):
    sessions = defaultdict(list)
    for e in entries:
        sessions[e.get("session", "orphan")].append(e)
    for sid in sessions:
        sessions[sid].sort(key=lambda x: x.get("turn", 0))
    return sessions


def main():
    print("Mycelium Backlog (entity-based, no LLM)")
    print("=" * 50)

    entries = load_all_entries()
    sessions = group_by_session(entries)

    targets = []
    for sid, sess in sorted(sessions.items(),
                              key=lambda x: x[1][-1].get("turn", 0), reverse=True):
        if len(sess) < 3 or sid == "mycelium-auto":
            continue
        if get_snapshot(sid):
            continue
        targets.append((sid, sess))

    print(f"  Total sessions: {len(sessions)}, remaining: {len(targets)}")
    if not targets:
        print("  All caught up!")
        return

    processed = 0
    start = time.time()

    for sid, sess in targets:
        last_hash = sess[-1].get("hash", "")
        text = " ".join(e.get("user", "") + " " + e.get("assistant", "")
                        for e in sess[-20:])
        ents = extract_entities(text)

        create_snapshot(
            session_id=sid,
            summary=f"{len(sess)} turns — {', '.join(ents[:5])}",
            topics=ents[:10],
            entities=ents[:15],
            turn_count=len(sess),
            last_turn_hash=last_hash,
        )
        processed += 1

    # Marker
    if entries:
        with open(str(LAST_SNAP_FILE), "w") as f:
            f.write(str(entries[-1].get("turn", 0)))

    elapsed = time.time() - start
    print(f"  Processed {processed} sessions in {elapsed:.0f}s")

    full_compact()
    stats = fact_stats()
    print(f"  Total facts: {stats['total_facts']}, snapshots: {stats['total_snapshots']}")


if __name__ == "__main__":
    main()

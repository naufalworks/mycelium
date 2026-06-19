#!/usr/bin/env python3
"""
Backlog processor — extract memory facts from ALL existing brain sessions.

Runs once to catch up, then event-based auto-snapshot handles new sessions.
Raw brain (log.jsonl) is never modified — only reads.

Usage:
  python3 scripts/process_backlog.py          # process all sessions
  python3 scripts/process_backlog.py --recent  # last 5 sessions only
"""

import json, sys, time
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "log.jsonl"
LAST_SNAP_FILE = ROOT / ".last_snapshot_turn"

sys.path.insert(0, str(ROOT / "scripts"))
from mycelium_memory import insert_fact, create_snapshot


def load_all_entries():
    """Load and group log entries by session."""
    if not LOG.exists():
        print(f"❌ log.jsonl not found at {LOG}")
        return []

    entries = []
    with open(LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def group_by_session(entries):
    """Group entries by session_id."""
    sessions = defaultdict(list)
    for e in entries:
        sid = e.get("session", "orphan")
        sessions[sid].append(e)

    # Sort sessions by turn
    for sid in sessions:
        sessions[sid].sort(key=lambda x: x.get("turn", 0))

    return sessions


def process_session(sid, session_entries, skip_llm=False):
    """Process a single session: create snapshot + extract facts."""
    if len(session_entries) < 3:
        return False  # too short

    texts = [json.dumps(e) for e in session_entries]
    last_turn = session_entries[-1]
    last_hash = last_turn.get("hash", "")

    print(f"  [{sid[:30]:30s}] {len(session_entries):4d} turns", end="")

    if skip_llm:
        # Basic: no LLM, just store session metadata
        from mycelium_lib import extract_entities
        all_text = " ".join(
            e.get("user", "") + " " + e.get("assistant", "")
            for e in session_entries[-10:]
        )
        ents = extract_entities(all_text)
        create_snapshot(
            session_id=sid,
            summary=f"Session with {len(session_entries)} turns",
            topics=ents[:8],
            entities=ents[:12],
            turn_count=len(session_entries),
            last_turn_hash=last_hash,
        )
        print(" ✓ (basic)")
        return True

    # LLM-powered extraction
    try:
        from mycelium_llm import summarize_session, extract_facts

        summary = summarize_session(texts, sid)
        if summary:
            create_snapshot(
                session_id=sid,
                summary=summary.get("summary", ""),
                topics=summary.get("topics", []),
                decisions=summary.get("decisions", []),
                entities=summary.get("entities", []),
                credentials=summary.get("credentials", []),
                turn_count=len(session_entries),
                last_turn_hash=last_hash,
            )
            print(" snapshot ✓", end="")
        else:
            print(" snapshot ✗", end="")

        # Extract and store facts
        facts = extract_facts(texts, sid)
        if facts:
            for f in facts:
                insert_fact(
                    entity=f.get("entity", "unknown"),
                    attribute=f.get("attribute", "value"),
                    value=str(f.get("value", "")),
                    fact_type=f.get("fact_type", "fact"),
                    confidence=float(f.get("confidence", 0.5)),
                    source_session=sid,
                    entropy=float(f.get("entropy", 0.5)),
                )
            print(f" facts={len(facts)} ✓")
        else:
            print(" facts=0")
        return True

    except ImportError as e:
        print(f" ✗ (LLM unavailable: {e})")
        return False
    except Exception as e:
        print(f" ✗ (error: {e})")
        return False


def main():
    print("🍄 Mycelium Backlog Processor")
    print("=" * 55)
    print(f"  This processes EXISTING sessions into memory facts.")
    print(f"  Raw brain (log.jsonl) is READ ONLY — never modified.")
    print()

    recent_only = "--recent" in sys.argv

    entries = load_all_entries()
    if not entries:
        print("❌ No entries found")
        return

    sessions = group_by_session(entries)
    total_sessions = len(sessions)
    total_turns = len(entries)

    print(f"  Brain: {total_turns} turns across {total_sessions} sessions")
    print()

    # Check what's already been processed
    try:
        from mycelium_memory import last_snapshot
        last = last_snapshot()
        if last:
            print(f"  Already processed: {last.get('session_id')} "
                  f"({last.get('turn_count', 0)} turns)")
    except Exception:
        pass

    # Order: most recent first (most useful)
    sorted_sessions = sorted(sessions.items(),
                              key=lambda x: x[1][-1].get("turn", 0),
                              reverse=True)

    if recent_only:
        sorted_sessions = sorted_sessions[:5]
        print(f"\n  Processing last 5 sessions (--recent)...")
    else:
        print(f"\n  Processing all {len(sorted_sessions)} sessions...")
        print(f"  ⚠️  This calls the LLM per session — may take a while.")
        print(f"  Ctrl+C to stop, re-run resumes where it left off.")

    print()

    processed = skipped = 0
    start_time = time.time()

    for sid, session_entries in sorted_sessions:
        # Skip mycelium-auto sessions (internal noise)
        if sid == "mycelium-auto":
            skipped += 1
            continue

        # Skip already-processed
        try:
            from mycelium_memory import get_snapshot
            if get_snapshot(sid):
                skipped += 1
                continue
        except Exception:
            pass

        ok = process_session(sid, session_entries)
        if ok:
            processed += 1
        else:
            skipped += 1

        # Small delay between LLM calls
        if processed % 3 == 0 and processed > 0:
            time.sleep(0.5)

    elapsed = time.time() - start_time

    print()
    print(f"\n  ✅ Done in {elapsed:.0f}s")
    print(f"     Sessions processed: {processed}")
    print(f"     Skipped:            {skipped}")

    # Update last turn marker so auto-snapshot picks up from here
    if entries:
        last_turn = entries[-1].get("turn", 0)
        with open(str(LAST_SNAP_FILE), "w") as f:
            f.write(str(last_turn))
        print(f"     Last turn marker:   {last_turn}")

    # Run compaction
    print("\n  Running compaction...")
    from mycelium_memory import full_compact
    full_compact()

    print()
    from mycelium_memory import fact_stats
    stats = fact_stats()
    print(f"  Total facts:     {stats['total_facts']}")
    print(f"  Total snapshots: {stats['total_snapshots']}")


if __name__ == "__main__":
    main()

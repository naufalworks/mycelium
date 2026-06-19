#!/usr/bin/env python3
"""
append.py — single-turn append for Mycelium v2.

Appends one turn to log.jsonl.
Auto-extracts entities, classifies tier, computes hash chain.
Incremental index update (no full rebuild).

Changes (v2-optimize):
  - Shared constants from mycelium_lib.py (single source of truth)
  - Incremental index update instead of full O(n) rebuild
  - Seek-based _load_last (O(1) vs O(n))
  - Always-on evolution detection (no flag needed)

Usage:
  append.py [--session NAME] [--type TYPE] [--finding JSON] "user text" "assistant text"

Types: talk (default), finding, decision, idea, dead-end, gardener, tech_verdict
"""
import argparse, fcntl, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

# Import shared lib (same directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import (
    MYCELIUM, LOG, INDEX, extract_entities, classify_tier,
    compute_hash, load_last_entry, update_index, init_index,
)
from evolution import detect_corrections, log_failure


def _detect_and_log_corrections(user_text: str, session: str) -> None:
    """Always-on evolution: scan user text for correction signals, log if found.
    Non-blocking — failures are silently swallowed so append is never blocked."""
    try:
        signals = detect_corrections(user_text)
        if signals:
            for s in signals:
                log_failure(
                    session=session,
                    category=s["category"],
                    user_msg=user_text[:200],
                    correction=user_text[:200],
                )
            print(f"🧬 Evolution: detected {len(signals)} correction signal(s)")
    except Exception:
        pass  # non-critical


def main():
    ap = argparse.ArgumentParser(description="Append one turn to Mycelium log.")
    ap.add_argument("--session", "-s", default="default", help="Session name (kebab-case)")
    ap.add_argument("--type", "-t", default="talk",
                    choices=["talk", "finding", "decision", "idea", "dead-end", "gardener", "tech_verdict"])
    ap.add_argument("--verdict", "-V", help="JSON string for tech_verdict object")
    ap.add_argument("--finding", "-f", help="JSON string for finding object")
    ap.add_argument("--no-index", action="store_true", help="Skip SQLite index update (faster)")
    ap.add_argument("--watch-user-msg", action="store_true",
                    help="(kept for compat, always-on now) Detect correction signals")
    ap.add_argument("user", help="User message (condensed)")
    ap.add_argument("assistant", help="Assistant response (condensed)")
    args = ap.parse_args()

    # ── Evolution: always-on detection ──
    _detect_and_log_corrections(args.user, args.session)

    # ── Load prev hash via seek (O(1)) ──
    last = load_last_entry()
    prev_hash = last["hash"] if last else ""
    turn = (last["turn"] + 1) if last else 1

    entry = {
        "turn": turn,
        "type": args.type,
        "session": args.session,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "",
        "entities": [],
        "user": args.user,
        "assistant": args.assistant,
        "prev_hash": prev_hash,
        "hash": "",
    }

    if args.type == "finding" and args.finding:
        try:
            entry["finding"] = json.loads(args.finding)
        except json.JSONDecodeError as e:
            print(f"Invalid --finding JSON: {e}", file=sys.stderr)
            sys.exit(1)

    if args.type == "tech_verdict" and args.verdict:
        try:
            entry["verdict"] = json.loads(args.verdict)
        except json.JSONDecodeError as e:
            print(f"Invalid --verdict JSON: {e}", file=sys.stderr)
            sys.exit(1)

    if args.type == "dead-end":
        entry["attempt"] = args.user
        entry["result"] = args.assistant

    entry["tier"] = classify_tier(entry)
    entry["entities"] = extract_entities(args.user + " " + args.assistant)
    entry["hash"] = compute_hash(entry, prev_hash)

    # ── Append single line — O(1) with file locking ──
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    # ── Incremental index update — O(1) instead of O(n) full rebuild ──
    if not args.no_index:
        update_index(entry)

    # ── Update attention table ──
    TIER_SCORES = {"S": 1.0, "A": 0.7, "B": 0.4, "C": 0.1}
    if not args.no_index:
        aconn = init_index()
        now_str = entry["ts"]
        score = TIER_SCORES.get(entry["tier"], 0.4)
        aconn.execute(
            "INSERT OR REPLACE INTO attention (turn, score, hit_count, last_referenced, first_seen) VALUES (?,?,?,?,?)",
            (turn, score, 1, now_str, now_str),
        )
        aconn.commit()
        aconn.close()

    print(f"✅ Turn {turn} appended [{entry['tier']}] {args.session}: {args.type}")

    # Event trigger: non-blocking snapshot check after append
    import subprocess, threading
    def _auto_snapshot():
        try:
            hook = Path(__file__).resolve().parent.parent / "hooks" / "mycelium-auto-snapshot.sh"
            subprocess.run(["bash", str(hook)], capture_output=True, timeout=30)
        except Exception:
            pass
    threading.Thread(target=_auto_snapshot, daemon=True).start()

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
mycelium_compact.py — Condition-based compaction for mycelium.

Orchestrates full maintenance cycle:
  1. Record before state
  2. Apply attention decay (if AttentionTracker available)
  3. Flush L0 → L1 (if over L0_MAX threshold)
  4. Compact L1 → L2 (if over L1_MAX threshold)
  5. Rebuild Bloom filter from all entries
  6. Re-extract entity graph edges
  7. Verify hash chain integrity
  8. Record after state, compute savings

CLI:
  mycelium compact           — run if over thresholds
  mycelium compact --dry-run — show what would happen
  mycelium compact --force   — compact regardless of thresholds
  mycelium compact --stats   — show layer stats only
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mycelium_lib import MYCELIUM, INDEX, load_log, compute_hash, init_index
from mycelium_lsm import MyceliumLSM, L0_MAX, L1_MAX

# Optional: AttentionTracker (may not exist yet)
try:
    from mycelium_attention import AttentionTracker
except ImportError:
    AttentionTracker = None  # type: ignore[assignment,misc]

# Optional: Bloom + Graph
try:
    from mycelium_bloom import MyceliumBloom
except ImportError:
    MyceliumBloom = None  # type: ignore[assignment,misc]

try:
    from mycelium_graph import EntityGraph
except ImportError:
    EntityGraph = None  # type: ignore[assignment,misc]


# ── Helpers ─────────────────────────────────────────────────

def _bytes_fmt(n: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _collect_all_entries(lsm: MyceliumLSM) -> list[dict]:
    """Gather all entries across L0, L1, L2 sorted by turn."""
    lsm._ensure_loaded()
    all_entries: dict[int, dict] = {}
    for e in lsm.l0.to_list():
        all_entries[e.get("turn", 0)] = e
    for seg in lsm.l1._discover():
        for e in seg.entries():
            t = e.get("turn", 0)
            if t not in all_entries:
                all_entries[t] = e
    for s in lsm.l2._discover():
        for e in s.entries():
            t = e.get("turn", 0)
            if t not in all_entries:
                all_entries[t] = e
    return [all_entries[t] for t in sorted(all_entries.keys())]


# ── Main compact function ───────────────────────────────────

def compact(
    force: bool = False,
    dry_run: bool = False,
    base_path: Path | str | None = None,
    lsm: MyceliumLSM | None = None,
) -> dict:
    """
    Run full maintenance compaction cycle.

    Args:
        force: compact even if under thresholds
        dry_run: report what would happen, no mutations
        base_path: override MYCELIUM root (for testing)
        lsm: optional pre-initialized MyceliumLSM (for testing / shared state)

    Returns:
        Stats dict with before/after state + savings
    """
    if lsm is None:
        lsm = MyceliumLSM(base_path)
    lsm._ensure_loaded()

    # ── Before state ────────────────────────────────────────
    before = lsm.stats()
    before_state = {
        "l0_entries": before["l0_entries"],
        "l1_segments": before["l1_segments"],
        "l1_entries": before["l1_entries"],
        "l2_summaries": before["l2_summaries"],
        "total_entries": before["total_entries"],
        "total_bytes": before["total_size_bytes"],
    }

    if dry_run:
        return _dry_run_plan(lsm, before_state, force)

    steps: list[str] = []
    skipped: list[str] = []

    # ── Step 1: Attention decay ─────────────────────────────
    if AttentionTracker is not None:
        try:
            attn = AttentionTracker(base_path=base_path)
            attn.decay_all()
            steps.append("attention_decay")
        except Exception:
            skipped.append("attention_decay")
    else:
        skipped.append("attention_decay")

    # ── Step 2: Flush L0 → L1 ──────────────────────────────
    l0_count = lsm.l0.count()
    needs_flush = l0_count > L0_MAX
    if force or needs_flush:
        result = lsm.flush()
        flushed = result.get("flushed", 0)
        if flushed > 0:
            steps.append(f"L0→L1: flushed {flushed} entries")
        else:
            skipped.append("L0→L1: nothing to flush")
    else:
        skipped.append(f"L0→L1: {l0_count} < {L0_MAX} (under threshold)")

    # ── Step 3: Compact L1 → L2 ────────────────────────────
    l1_count = lsm.l1.segment_count()
    needs_compact = lsm.l1.needs_compaction()
    if force or needs_compact:
        result = lsm.compact()
        compacted = result.get("compacted", 0)
        if compacted > 0:
            steps.append(
                f"L1→L2: compacted {compacted} segments, "
                f"{result.get('entries_summarized', 0)} entries summarized"
            )
        else:
            skipped.append("L1→L2: nothing to compact")
    else:
        skipped.append(f"L1→L2: {l1_count} segments < {L1_MAX} (under threshold)")

    # ── Step 4: Rebuild Bloom filter ────────────────────────
    if MyceliumBloom is not None:
        try:
            all_entries = _collect_all_entries(lsm)
            bloom = MyceliumBloom(capacity=max(len(all_entries) * 2, 1000), name="entities")
            for entry in all_entries:
                for ent in entry.get("entities", []):
                    bloom.add_entity(ent)
            bloom_path = Path(base_path or MYCELIUM) / ".bloom_entities"
            bloom.save(bloom_path)
            bloom.save_to_db(Path(base_path or MYCELIUM) / "index.db")
            steps.append(f"bloom: rebuilt {bloom.count()} entries")
        except Exception:
            skipped.append("bloom_rebuild")
    else:
        skipped.append("bloom_rebuild")

    # ── Step 5: Re-extract entity graph ─────────────────────
    if EntityGraph is not None:
        try:
            all_entries = _collect_all_entries(lsm)
            graph = EntityGraph(db_path=Path(base_path or MYCELIUM) / "index.db")
            graph.build_from_log()
            edge_count = graph.count()
            graph.close()
            steps.append(f"graph: {edge_count} edges")
        except Exception:
            skipped.append("graph_rebuild")
    else:
        skipped.append("graph_rebuild")

    # ── Step 6: Verify hash chain ───────────────────────────
    integrity = lsm.verify_integrity()
    if integrity.get("valid", False):
        steps.append(f"hash_chain: valid ({integrity.get('entries', 0)} entries)")
    else:
        steps.append("hash_chain: BROKEN — tampering detected")

    # ── After state ─────────────────────────────────────────
    after = lsm.stats()
    after_state = {
        "l0_entries": after["l0_entries"],
        "l1_segments": after["l1_segments"],
        "l1_entries": after["l1_entries"],
        "l2_summaries": after["l2_summaries"],
        "total_entries": after["total_entries"],
        "total_bytes": after["total_size_bytes"],
    }

    bytes_before = before_state["total_bytes"]
    bytes_after = after_state["total_bytes"]
    savings = bytes_before - bytes_after
    pct = (savings / bytes_before * 100) if bytes_before > 0 else 0.0

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
        "force": force,
        "before": before_state,
        "after": after_state,
        "savings_bytes": savings,
        "savings_pct": round(pct, 1),
        "savings_human": _bytes_fmt(savings),
        "steps": steps,
        "skipped": skipped,
        "integrity": integrity,
    }


# ── Dry run planner ─────────────────────────────────────────

def _dry_run_plan(lsm: MyceliumLSM, before: dict, force: bool) -> dict:
    """Compute what compact() WOULD do without mutating."""
    actions: list[str] = []

    # L0 → L1
    l0_count = before["l0_entries"]
    if force or l0_count > L0_MAX:
        flush_count = max(0, l0_count - max(L0_MAX // 2, 10))
        actions.append(f"L0→L1: would flush ~{flush_count} entries")
    else:
        actions.append(f"L0→L1: skip ({l0_count} < {L0_MAX})")

    # L1 → L2
    l1_segments = before["l1_segments"]
    needs_compact = lsm.l1.needs_compaction()
    if force or needs_compact:
        compact_count = max(0, l1_segments - L1_MAX // 2)
        if compact_count == 0:
            compact_count = min(3, l1_segments)
        actions.append(f"L1→L2: would compact ~{compact_count} segments")
    else:
        actions.append(f"L1→L2: skip ({l1_segments} < {L1_MAX})")

    actions.append("bloom: would rebuild from all entries")
    actions.append("graph: would re-extract edges")
    actions.append("hash_chain: would verify integrity")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "force": force,
        "before": before,
        "actions": actions,
    }


# ── CLI ─────────────────────────────────────────────────────

def main():
    """CLI interface for mycelium compact."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Mycelium condition-based compaction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Default: compact only when thresholds exceeded.\n"
               "Use --force for maintenance. --dry-run to preview.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true",
                       help="Show what would happen without doing it")
    group.add_argument("--force", action="store_true",
                       help="Compact even if under thresholds")
    group.add_argument("--stats", action="store_true",
                       help="Show layer stats only (no compaction)")
    parser.add_argument("--base", type=str, default=None,
                        help="Override mycelium root directory")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()
    base = Path(args.base) if args.base else None

    if args.stats:
        lsm = MyceliumLSM(base)
        s = lsm.stats()
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print("═══ Mycelium Layer Stats ═══")
            print(f"  L0 (Hot):  {s['l0_entries']} entries  ({_bytes_fmt(s['l0_size_bytes'])})")
            print(f"  L1 (Warm): {s['l1_segments']} segments, {s['l1_entries']} entries  ({_bytes_fmt(s['l1_size_bytes'])})")
            print(f"  L2 (Cold): {s['l2_summaries']} summaries, {s['l2_entries']} entries  ({_bytes_fmt(s['l2_size_bytes'])})")
            print(f"  ─────────────────────────")
            print(f"  Total:     {s['total_entries']} entries  ({_bytes_fmt(s['total_size_bytes'])})")
            print(f"  Thresholds: L0>{s['l0_max']} → flush, L1>{s['l1_max']} → compact")
        return

    result = compact(
        force=args.force,
        dry_run=args.dry_run,
        base_path=base,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    # Pretty output
    mode = "DRY RUN" if result["dry_run"] else ("FORCED" if result.get("force") else "compact")
    print(f"═══ Mycelium Compaction ({mode}) ═══")
    print()

    before = result.get("before", {})
    print(f"  Before:")
    print(f"    L0: {before.get('l0_entries', '?')} entries")
    print(f"    L1: {before.get('l1_segments', '?')} segments ({before.get('l1_entries', '?')} entries)")
    print(f"    L2: {before.get('l2_summaries', '?')} summaries")
    print(f"    Size: {_bytes_fmt(before.get('total_bytes', 0))}")

    if result["dry_run"]:
        print()
        print("  Actions (planned):")
        for a in result.get("actions", []):
            print(f"    → {a}")
    else:
        after = result.get("after", {})
        print()
        print(f"  After:")
        print(f"    L0: {after.get('l0_entries', '?')} entries")
        print(f"    L1: {after.get('l1_segments', '?')} segments ({after.get('l1_entries', '?')} entries)")
        print(f"    L2: {after.get('l2_summaries', '?')} summaries")
        print(f"    Size: {_bytes_fmt(after.get('total_bytes', 0))}")

        savings = result.get("savings_bytes", 0)
        if savings > 0:
            print()
            print(f"  Savings: {result.get('savings_human', '0 B')} ({result.get('savings_pct', 0)}%)")

        print()
        print("  Steps:")
        for s in result.get("steps", []):
            print(f"    ✓ {s}")
        for s in result.get("skipped", []):
            print(f"    ○ {s}")

        integrity = result.get("integrity", {})
        if not integrity.get("valid", True):
            print()
            print("  ⚠ WARNING: Hash chain integrity FAILED")


if __name__ == "__main__":
    main()

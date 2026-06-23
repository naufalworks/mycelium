#!/usr/bin/env python3
"""
mycelium_snapshot.py — Copy-on-Write session snapshots for time-travel.

Two stores:
  SnapshotStore — full point-in-time snapshots with metadata
  DeltaStore    — COW deltas (only changed fields between snapshots)

Design:
  - Snapshots are lightweight: metadata + LSM state pointers (not data copies)
  - Deltas store only changed fields, enabling efficient chain replay
  - Snapshots stored as individual JSON files under MYCELIUM/snapshots/
  - Deltas stored under MYCELIUM/snapshots/deltas/

Usage (library):
    from mycelium_snapshot import SnapshotStore, DeltaStore
    ss = SnapshotStore()
    snap_id = ss.create("session-name", lsm_state)
    snap = ss.load(snap_id)
    all_snaps = ss.list_all()

Usage (CLI):
    python3 mycelium_snapshot.py create --session test --lsm-state '{}'
    python3 mycelium_snapshot.py list
    python3 mycelium_snapshot.py load <snap_id>
    python3 mycelium_snapshot.py diff <snap1> <snap2>
    python3 mycelium_snapshot.py latest
    python3 mycelium_snapshot.py delete <snap_id>
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import MYCELIUM


# ── Snapshot Store ─────────────────────────────────────────────

class SnapshotStore:
    """Point-in-time snapshots of mycelium state.

    Snapshots capture metadata about LSM layers, bloom filter,
    entity graph, and negation index — not full data copies.
    """

    def __init__(self, base_path: str | Path | None = None):
        self.base = Path(base_path or MYCELIUM) / "snapshots"
        self.base.mkdir(parents=True, exist_ok=True)

    def create(self, session: str, lsm_state: dict) -> str:
        """Create a snapshot from LSM state dict.

        Args:
            session: session name
            lsm_state: dict with keys like l0_turns, l1_segments,
                       l2_summaries, total_entries, total_size_bytes,
                       bloom_entities, graph_edges, negations

        Returns:
            snap_id string
        """
        ts = time.time()
        ts_int = int(ts)
        snap_id = f"snap_{session}_{ts_int}"

        snap = {
            "snap_id": snap_id,
            "session": session,
            "ts": datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "l0_turns": lsm_state.get("l0_turns", []),
            "l1_segments": lsm_state.get("l1_segments", []),
            "l2_summaries": lsm_state.get("l2_summaries", []),
            "total_entries": lsm_state.get("total_entries", 0),
            "total_size_bytes": lsm_state.get("total_size_bytes", 0),
            "bloom_entities": lsm_state.get("bloom_entities", 0),
            "graph_edges": lsm_state.get("graph_edges", 0),
            "negations": lsm_state.get("negations", 0),
        }

        snap_path = self.base / f"{snap_id}.json"
        snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False))
        return snap_id

    def load(self, snap_id: str) -> dict | None:
        """Load a snapshot by ID. Returns None if not found."""
        snap_path = self.base / f"{snap_id}.json"
        if not snap_path.exists():
            return None
        try:
            return json.loads(snap_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def list_all(self) -> list[dict]:
        """List all snapshots with metadata, sorted by timestamp."""
        snaps = []
        for f in sorted(self.base.glob("snap_*.json")):
            try:
                data = json.loads(f.read_text())
                snaps.append(data)
            except (json.JSONDecodeError, OSError):
                continue
        snaps.sort(key=lambda s: (s.get("ts", ""), s.get("snap_id", "")))
        return snaps

    def diff(self, snap1: str, snap2: str) -> dict:
        """Show differences between two snapshots.

        Returns dict with changed fields and their old/new values.
        """
        s1 = self.load(snap1)
        s2 = self.load(snap2)
        if s1 is None or s2 is None:
            missing = [sid for sid, s in [(snap1, s1), (snap2, s2)] if s is None]
            return {"error": f"Snapshot(s) not found: {missing}"}

        changes = {}
        # Compare all tracked fields
        tracked = [
            "l0_turns", "l1_segments", "l2_summaries",
            "total_entries", "total_size_bytes",
            "bloom_entities", "graph_edges", "negations",
        ]
        for key in tracked:
            v1 = s1.get(key)
            v2 = s2.get(key)
            if v1 != v2:
                changes[key] = {"old": v1, "new": v2}

        return {
            "snap1": snap1,
            "snap2": snap2,
            "ts1": s1.get("ts"),
            "ts2": s2.get("ts"),
            "changes": changes,
            "changed_count": len(changes),
        }

    def latest(self) -> dict | None:
        """Return the most recent snapshot, or None."""
        snaps = self.list_all()
        return snaps[-1] if snaps else None

    def delete(self, snap_id: str) -> bool:
        """Delete a snapshot. Returns True if deleted, False if not found."""
        snap_path = self.base / f"{snap_id}.json"
        if snap_path.exists():
            snap_path.unlink()
            # Also delete associated delta if exists
            delta_path = self.base / "deltas" / f"delta_{snap_id}.json"
            if delta_path.exists():
                delta_path.unlink()
            return True
        return False


# ── Delta Store (COW) ──────────────────────────────────────────

class DeltaStore:
    """Copy-on-Write delta storage.

    Only stores changed fields between snapshots, enabling
    efficient chain replay to reconstruct any point in time.
    """

    def __init__(self, base_path: str | Path | None = None):
        self.base = Path(base_path or MYCELIUM) / "snapshots" / "deltas"
        self.base.mkdir(parents=True, exist_ok=True)

    def compute_delta(self, prev: dict, current: dict) -> dict:
        """Compute only changed fields between two states.

        Args:
            prev: previous state dict
            current: current state dict

        Returns:
            Dict mapping changed field names to {old, new} values.
        """
        delta = {}
        all_keys = set(list(prev.keys()) + list(current.keys()))
        for key in all_keys:
            old_val = prev.get(key)
            new_val = current.get(key)
            if old_val != new_val:
                delta[key] = {"old": old_val, "new": new_val}
        return delta

    def store_delta(self, snap_id: str, delta: dict) -> None:
        """Store a delta for a snapshot."""
        delta_path = self.base / f"delta_{snap_id}.json"
        delta_path.write_text(json.dumps(delta, indent=2, ensure_ascii=False))

    def load_delta(self, snap_id: str) -> dict | None:
        """Load delta for a snapshot. Returns None if not found."""
        delta_path = self.base / f"delta_{snap_id}.json"
        if not delta_path.exists():
            return None
        try:
            return json.loads(delta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def reconstruct(self, base_snap: str, delta_chain: list) -> dict:
        """Reconstruct state by replaying deltas from a base snapshot.

        Args:
            base_snap: snap_id of the base (full) snapshot
            delta_chain: list of snap_ids whose deltas to apply in order

        Returns:
            Reconstructed state dict, or partial result on error.
        """
        # Load base snapshot
        store = SnapshotStore(self.base.parent.parent)
        base = store.load(base_snap)
        if base is None:
            return {"error": f"Base snapshot not found: {base_snap}"}

        # Start from base state (strip metadata fields)
        state = self._extract_state(base)

        # Replay deltas in order
        errors = []
        for snap_id in delta_chain:
            delta = self.load_delta(snap_id)
            if delta is None:
                errors.append(f"Delta not found: {snap_id}")
                continue
            for key, change in delta.items():
                if key in ("snap_id", "session", "ts"):
                    continue  # skip metadata
                if isinstance(change, dict) and "new" in change:
                    state[key] = change["new"]

        result = dict(state)
        if errors:
            result["_errors"] = errors
        return result

    @staticmethod
    def _extract_state(snap: dict) -> dict:
        """Extract state fields from a full snapshot (strip metadata)."""
        return {
            "l0_turns": snap.get("l0_turns", []),
            "l1_segments": snap.get("l1_segments", []),
            "l2_summaries": snap.get("l2_summaries", []),
            "total_entries": snap.get("total_entries", 0),
            "total_size_bytes": snap.get("total_size_bytes", 0),
            "bloom_entities": snap.get("bloom_entities", 0),
            "graph_edges": snap.get("graph_edges", 0),
            "negations": snap.get("negations", 0),
        }


# ── CLI ─────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Mycelium COW Snapshots")
    sub = parser.add_subparsers(dest="cmd")

    # create
    p_create = sub.add_parser("create", help="Create snapshot")
    p_create.add_argument("--session", required=True, help="Session name")
    p_create.add_argument("--lsm-state", default="{}",
                          help="JSON string of LSM state dict")

    # list
    sub.add_parser("list", help="List all snapshots")

    # load
    p_load = sub.add_parser("load", help="Load a snapshot")
    p_load.add_argument("snap_id", help="Snapshot ID")

    # diff
    p_diff = sub.add_parser("diff", help="Diff two snapshots")
    p_diff.add_argument("snap1", help="First snapshot ID")
    p_diff.add_argument("snap2", help="Second snapshot ID")

    # latest
    sub.add_parser("latest", help="Show most recent snapshot")

    # delete
    p_del = sub.add_parser("delete", help="Delete a snapshot")
    p_del.add_argument("snap_id", help="Snapshot ID")

    # delta (sub)
    p_delta = sub.add_parser("delta", help="COW delta operations")
    p_delta.add_argument("delta_cmd", choices=["compute", "store", "load", "reconstruct"],
                         help="Delta operation")
    p_delta.add_argument("--base", help="Base snap ID (for compute/reconstruct)")
    p_delta.add_argument("--current", help="Current snap ID (for compute)")
    p_delta.add_argument("--snap-id", help="Snap ID for store/load")
    p_delta.add_argument("--chain", nargs="*", default=[],
                         help="Delta chain snap IDs (for reconstruct)")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return

    ss = SnapshotStore()
    ds = DeltaStore()

    if args.cmd == "create":
        lsm_state = json.loads(args.lsm_state)
        snap_id = ss.create(args.session, lsm_state)
        print(f"Created: {snap_id}")

    elif args.cmd == "list":
        snaps = ss.list_all()
        if not snaps:
            print("No snapshots found.")
        for s in snaps:
            print(f"  {s['snap_id']}  session={s['session']}  "
                  f"ts={s['ts']}  entries={s.get('total_entries', 0)}")

    elif args.cmd == "load":
        snap = ss.load(args.snap_id)
        if snap:
            print(json.dumps(snap, indent=2))
        else:
            print(f"Snapshot not found: {args.snap_id}")

    elif args.cmd == "diff":
        d = ss.diff(args.snap1, args.snap2)
        print(json.dumps(d, indent=2))

    elif args.cmd == "latest":
        snap = ss.latest()
        if snap:
            print(json.dumps(snap, indent=2))
        else:
            print("No snapshots found.")

    elif args.cmd == "delete":
        if ss.delete(args.snap_id):
            print(f"Deleted: {args.snap_id}")
        else:
            print(f"Not found: {args.snap_id}")

    elif args.cmd == "delta":
        if args.delta_cmd == "compute":
            s1 = ss.load(args.base)
            s2 = ss.load(args.current)
            if not s1 or not s2:
                print("Snapshot(s) not found")
                return
            delta = ds.compute_delta(s1, s2)
            print(json.dumps(delta, indent=2))

        elif args.delta_cmd == "store":
            if not args.snap_id or not args.base or not args.current:
                print("--snap-id, --base, --current required for store")
                return
            s1 = ss.load(args.base)
            s2 = ss.load(args.current)
            if not s1 or not s2:
                print("Snapshot(s) not found")
                return
            delta = ds.compute_delta(s1, s2)
            ds.store_delta(args.snap_id, delta)
            print(f"Stored delta for {args.snap_id}")

        elif args.delta_cmd == "load":
            if not args.snap_id:
                print("--snap-id required")
                return
            delta = ds.load_delta(args.snap_id)
            if delta:
                print(json.dumps(delta, indent=2))
            else:
                print(f"Delta not found: {args.snap_id}")

        elif args.delta_cmd == "reconstruct":
            if not args.base:
                print("--base required for reconstruct")
                return
            state = ds.reconstruct(args.base, args.chain)
            print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()

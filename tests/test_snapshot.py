#!/usr/bin/env python3
"""Tests for mycelium_snapshot.py — COW snapshot system."""

import json, os, sys, tempfile, shutil
from pathlib import Path

import pytest

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from mycelium_snapshot import SnapshotStore, DeltaStore


# ── Fixtures ───────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def snap_store(tmp_dir):
    """SnapshotStore pointed at a temp directory."""
    return SnapshotStore(tmp_dir)


@pytest.fixture
def delta_store(tmp_dir):
    """DeltaStore pointed at a temp directory."""
    return DeltaStore(tmp_dir)


@pytest.fixture
def state_a():
    """Sample LSM state A."""
    return {
        "l0_turns": [600, 601, 602],
        "l1_segments": ["seg_000500_000599.jsonl.gz"],
        "l2_summaries": ["sum_000000_000100.jsonl.gz"],
        "total_entries": 650,
        "total_size_bytes": 100000,
        "bloom_entities": 900,
        "graph_edges": 40,
        "negations": 2,
    }


@pytest.fixture
def state_b():
    """Sample LSM state B — evolved from A."""
    return {
        "l0_turns": [620, 621, 622],
        "l1_segments": [
            "seg_000500_000599.jsonl.gz",
            "seg_000600_000619.jsonl.gz",
        ],
        "l2_summaries": ["sum_000000_000100.jsonl.gz"],
        "total_entries": 669,
        "total_size_bytes": 108113,
        "bloom_entities": 977,
        "graph_edges": 45,
        "negations": 3,
    }


# ── SnapshotStore Tests ────────────────────────────────────────

class TestSnapshotStore:
    def test_create_load_roundtrip(self, snap_store, state_a):
        """Create snapshot → load it back → data matches."""
        snap_id = snap_store.create("test-session", state_a)
        assert snap_id.startswith("snap_test-session_")

        loaded = snap_store.load(snap_id)
        assert loaded is not None
        assert loaded["snap_id"] == snap_id
        assert loaded["session"] == "test-session"
        assert loaded["total_entries"] == 650
        assert loaded["l0_turns"] == [600, 601, 602]
        assert loaded["bloom_entities"] == 900

    def test_list_all(self, snap_store, state_a, state_b):
        """Multiple snapshots appear in list, sorted by timestamp."""
        id1 = snap_store.create("session-1", state_a)
        id2 = snap_store.create("session-2", state_b)

        snaps = snap_store.list_all()
        assert len(snaps) == 2
        # Sorted by timestamp ascending
        assert snaps[0]["session"] == "session-1"
        assert snaps[1]["session"] == "session-2"
        assert snaps[0]["snap_id"] == id1
        assert snaps[1]["snap_id"] == id2

    def test_list_all_empty(self, snap_store):
        """Empty store returns empty list."""
        assert snap_store.list_all() == []

    def test_latest_returns_most_recent(self, snap_store, state_a, state_b):
        """latest() returns the snapshot with highest timestamp."""
        id1 = snap_store.create("older", state_a)
        id2 = snap_store.create("newer", state_b)

        latest = snap_store.latest()
        assert latest is not None
        assert latest["snap_id"] == id2
        assert latest["session"] == "newer"

    def test_latest_empty(self, snap_store):
        """latest() returns None when no snapshots exist."""
        assert snap_store.latest() is None

    def test_delete(self, snap_store, state_a):
        """Delete removes snapshot from disk."""
        snap_id = snap_store.create("to-delete", state_a)
        assert snap_store.load(snap_id) is not None

        result = snap_store.delete(snap_id)
        assert result is True
        assert snap_store.load(snap_id) is None
        assert snap_store.list_all() == []

    def test_delete_nonexistent(self, snap_store):
        """Deleting non-existent snapshot returns False."""
        assert snap_store.delete("snap_nobody_0000000000") is False

    def test_load_nonexistent(self, snap_store):
        """Loading non-existent snapshot returns None."""
        assert snap_store.load("snap_nope_0000000000") is None

    def test_diff_shows_changes(self, snap_store, state_a, state_b):
        """Diff between two snapshots identifies changed fields."""
        id_a = snap_store.create("session-a", state_a)
        id_b = snap_store.create("session-b", state_b)

        d = snap_store.diff(id_a, id_b)
        assert d["changed_count"] > 0
        assert "total_entries" in d["changes"]
        assert d["changes"]["total_entries"]["old"] == 650
        assert d["changes"]["total_entries"]["new"] == 669
        assert "bloom_entities" in d["changes"]
        assert d["changes"]["graph_edges"]["old"] == 40
        assert d["changes"]["graph_edges"]["new"] == 45

    def test_diff_no_changes(self, snap_store, state_a):
        """Diff of identical states shows zero changes."""
        id1 = snap_store.create("s1", state_a)
        id2 = snap_store.create("s2", state_a)

        d = snap_store.diff(id1, id2)
        assert d["changed_count"] == 0
        assert d["changes"] == {}

    def test_diff_missing_snapshot(self, snap_store, state_a):
        """Diff with non-existent snapshot returns error."""
        id1 = snap_store.create("exists", state_a)
        d = snap_store.diff(id1, "snap_nope_0000000000")
        assert "error" in d


# ── DeltaStore Tests ──────────────────────────────────────────

class TestDeltaStore:
    def test_delta_computation(self, delta_store, state_a, state_b):
        """compute_delta captures only changed fields."""
        delta = delta_store.compute_delta(state_a, state_b)

        # Changed fields should be present
        assert "total_entries" in delta
        assert delta["total_entries"]["old"] == 650
        assert delta["total_entries"]["new"] == 669
        assert "bloom_entities" in delta
        assert "l0_turns" in delta
        assert "l1_segments" in delta

        # Unchanged fields should NOT be in delta
        assert "l2_summaries" not in delta

    def test_store_load_delta(self, delta_store):
        """Store and load delta roundtrip."""
        delta = {"total_entries": {"old": 100, "new": 200}}
        delta_store.store_delta("snap_test_123", delta)

        loaded = delta_store.load_delta("snap_test_123")
        assert loaded is not None
        assert loaded["total_entries"]["old"] == 100
        assert loaded["total_entries"]["new"] == 200

    def test_load_delta_nonexistent(self, delta_store):
        """Loading non-existent delta returns None."""
        assert delta_store.load_delta("snap_nope_000") is None

    def test_reconstruct_from_deltas(self, tmp_dir, state_a, state_b):
        """Reconstruct state by replaying delta chain."""
        ss = SnapshotStore(tmp_dir)
        ds = DeltaStore(tmp_dir)

        # Create base snapshot
        base_id = ss.create("base", state_a)

        # Create evolved snapshot
        evolved_id = ss.create("evolved", state_b)

        # Compute and store delta
        delta = ds.compute_delta(state_a, state_b)
        ds.store_delta(evolved_id, delta)

        # Reconstruct — single delta in chain
        reconstructed = ds.reconstruct(base_id, [evolved_id])

        assert reconstructed["total_entries"] == 669
        assert reconstructed["bloom_entities"] == 977
        assert reconstructed["graph_edges"] == 45
        assert reconstructed["negations"] == 3

    def test_reconstruct_multi_step(self, tmp_dir, state_a):
        """Reconstruct with multiple delta steps."""
        ss = SnapshotStore(tmp_dir)
        ds = DeltaStore(tmp_dir)

        base_id = ss.create("base", state_a)

        # Step 1: add some entries
        state_mid = dict(state_a)
        state_mid["total_entries"] = 680
        state_mid["bloom_entities"] = 950
        mid_id = ss.create("mid", state_mid)
        d1 = ds.compute_delta(state_a, state_mid)
        ds.store_delta(mid_id, d1)

        # Step 2: add more entries
        state_final = dict(state_mid)
        state_final["total_entries"] = 700
        state_final["negations"] = 5
        final_id = ss.create("final", state_final)
        d2 = ds.compute_delta(state_mid, state_final)
        ds.store_delta(final_id, d2)

        # Reconstruct through chain
        result = ds.reconstruct(base_id, [mid_id, final_id])
        assert result["total_entries"] == 700
        assert result["negations"] == 5
        assert result["bloom_entities"] == 950  # from mid, not overwritten by final

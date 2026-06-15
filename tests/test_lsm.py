#!/usr/bin/env python3
"""Tests for mycelium_lsm.py — LSM-tree memory layer."""

import json, gzip, os, sys, tempfile, shutil
from pathlib import Path

import pytest

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from mycelium_lsm import (
    L0Layer, L1Layer, L2Layer, L1Segment, L2Summary,
    MyceliumLSM, make_summary, L0_MAX, L1_SEGMENT_SIZE,
)


# ── Fixtures ───────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def sample_entries():
    """Generate 20 sample entries for testing."""
    entries = []
    for i in range(20):
        entries.append({
            "turn": i + 1,
            "tier": "S" if i < 3 else "A" if i < 6 else "B",
            "type": "finding" if i < 3 else "idea" if i < 6 else "talk",
            "session": f"test-session-{i // 5}",
            "ts": f"2026-06-15T{10 + i}:00:00Z",
            "entities": [f"entity-{i % 3}", f"entity-{(i + 1) % 3}"],
            "user": f"User message {i}: what about topic-{i}?",
            "assistant": f"Assistant response {i}: here is the answer about topic-{i}.",
            "prev_hash": f"prev_{i:04d}",
            "hash": f"hash_{i:04d}",
        })
    return entries


# ── L0 Tests ────────────────────────────────────────────────

class TestL0Layer:
    def test_put_and_get(self):
        l0 = L0Layer()
        e = {"turn": 1, "user": "test"}
        l0.put(e)
        assert l0.get(1) == e
        assert l0.get(999) is None

    def test_count(self):
        l0 = L0Layer()
        assert l0.count() == 0
        l0.put({"turn": 1})
        l0.put({"turn": 2})
        assert l0.count() == 2

    def test_needs_flush(self):
        l0 = L0Layer(max_entries=3)
        l0.put({"turn": 1})
        assert not l0.needs_flush()
        l0.put({"turn": 2})
        l0.put({"turn": 3})
        l0.put({"turn": 4})  # exceeds max
        assert l0.needs_flush()

    def test_flush_candidates(self):
        l0 = L0Layer(max_entries=5)
        for i in range(10):
            l0.put({"turn": i + 1, "data": f"entry_{i}"})
        flushed = l0.flush_candidates(5)
        assert len(flushed) == 5
        assert flushed[0]["turn"] == 1  # oldest first
        assert l0.count() == 5  # remaining

    def test_to_list_sorted(self):
        l0 = L0Layer()
        l0.put({"turn": 3})
        l0.put({"turn": 1})
        l0.put({"turn": 2})
        result = l0.to_list()
        assert [e["turn"] for e in result] == [1, 2, 3]


# ── L1 Tests ────────────────────────────────────────────────

class TestL1Layer:
    def test_write_and_read(self, tmp_dir):
        l1 = L1Layer(tmp_dir)
        entries = [{"turn": i, "data": f"e{i}"} for i in range(5)]
        l1.write_segment(entries)
        assert l1.segment_count() == 1
        e = l1.get(3)
        assert e is not None
        assert e["data"] == "e3"

    def test_multiple_segments(self, tmp_dir):
        l1 = L1Layer(tmp_dir)
        for seg in range(3):
            entries = [{"turn": seg * 10 + i} for i in range(5)]
            l1.write_segment(entries)
        assert l1.segment_count() == 3

    def test_total_entries(self, tmp_dir):
        l1 = L1Layer(tmp_dir)
        l1.write_segment([{"turn": i} for i in range(10)])
        l1.write_segment([{"turn": i} for i in range(10, 25)])
        assert l1.total_entries() == 25

    def test_all_turns(self, tmp_dir):
        l1 = L1Layer(tmp_dir)
        l1.write_segment([{"turn": 1}, {"turn": 5}])
        l1.write_segment([{"turn": 10}, {"turn": 15}])
        assert l1.all_turns() == {1, 5, 10, 15}

    def test_needs_compaction(self, tmp_dir):
        l1 = L1Layer(tmp_dir, max_segments=2)
        l1.write_segment([{"turn": 1}])
        l1.write_segment([{"turn": 2}])
        assert not l1.needs_compaction()
        l1.write_segment([{"turn": 3}])
        assert l1.needs_compaction()

    def test_compression(self, tmp_dir):
        l1 = L1Layer(tmp_dir)
        entries = [{"turn": i, "user": "test message " * 10} for i in range(10)]
        l1.write_segment(entries)
        seg = l1._discover()[0]
        # Verify it's gzipped
        with gzip.open(seg.path, "rt") as f:
            data = f.read()
        assert len(data) > 0
        assert json.loads(data.split("\n")[0])["turn"] == 0


# ── L2 Tests ────────────────────────────────────────────────

class TestL2Layer:
    def test_write_and_read(self, tmp_dir):
        l2 = L2Layer(tmp_dir)
        summaries = [{"turn": i, "summary": f"s{i}"} for i in range(5)]
        l2.write_summary(summaries)
        assert l2.summary_count() == 1
        assert l2.total_entries() == 5

    def test_get_by_turn(self, tmp_dir):
        l2 = L2Layer(tmp_dir)
        l2.write_summary([{"turn": 42, "summary": "test"}])
        e = l2.get(42)
        assert e is not None
        assert e["summary"] == "test"
        assert l2.get(99) is None


# ── Summary Tests ──────────────────────────────────────────

class TestMakeSummary:
    def test_basic_summary(self):
        entry = {
            "turn": 5,
            "tier": "S",
            "type": "finding",
            "session": "test",
            "ts": "2026-06-15T10:00:00Z",
            "entities": ["grav", "python"],
            "user": "Fix the grav shim issue",
            "assistant": "Running health check on grav shim...",
            "finding": {"type": "sqli", "target": "admin.com", "severity": "critical"},
        }
        s = make_summary(entry)
        assert s["turn"] == 5
        assert s["tier"] == "S"
        assert s["entities"] == ["grav", "python"]
        assert "Fix the grav" in s["summary"]
        assert s["finding"]["type"] == "sqli"
        assert s["hash"] == ""

    def test_summary_preserves_hash(self):
        entry = {"turn": 1, "prev_hash": "abc", "hash": "def"}
        s = make_summary(entry)
        assert s["prev_hash"] == "abc"
        assert s["hash"] == "def"


# ── Full LSM Tests ─────────────────────────────────────────

class TestMyceliumLSM:
    def test_append_and_get(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        e = {"turn": 1, "user": "test", "assistant": "ok"}
        lsm.append(e)
        assert lsm.get(1)["user"] == "test"

    def test_auto_flush(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        for i in range(L0_MAX + 10):
            lsm.append({"turn": i + 1, "user": f"msg_{i}"})
        stats = lsm.stats()
        assert stats["l0_entries"] <= L0_MAX
        assert stats["l1_segments"] >= 1

    def test_load_from_jsonl(self, tmp_dir, sample_entries):
        log_path = tmp_dir / "log.jsonl"
        with open(log_path, "w") as f:
            for e in sample_entries:
                f.write(json.dumps(e) + "\n")

        lsm = MyceliumLSM(tmp_dir)
        result = lsm.load_from_jsonl(log_path)
        assert result["loaded"] == 20
        assert result["l0"] == min(20, L0_MAX)
        # Older entries in L1
        stats = lsm.stats()
        assert stats["l1_segments"] >= 1 or stats["l0_entries"] == 20

    def test_stats(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        lsm.append({"turn": 1, "user": "hi"})
        s = lsm.stats()
        assert "l0_entries" in s
        assert "l1_segments" in s
        assert "total_entries" in s
        assert s["l0_entries"] == 1

    def test_verify_integrity(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        lsm.append({"turn": 1, "hash": "abc", "prev_hash": ""})
        lsm.append({"turn": 2, "hash": "def", "prev_hash": "abc"})
        result = lsm.verify_integrity()
        assert result["valid"]
        assert result["entries"] == 2

    def test_flush_and_compact(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        # Add enough entries to flush
        for i in range(L0_MAX + 5):
            lsm.append({"turn": i + 1, "user": f"msg_{i}"})
        # Flush
        flush_result = lsm.flush()
        assert flush_result["flushed"] >= 0
        # Verify L1 has data
        assert lsm.stats()["l1_segments"] >= 1

    def test_get_from_l1(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        # Fill L0 and flush
        for i in range(L0_MAX + 5):
            lsm.append({"turn": i + 1, "user": f"msg_{i}"})
        lsm.flush()
        # Turn 1 should be in L1 now
        e = lsm.get(1)
        assert e is not None
        assert e["user"] == "msg_0"


# ── Edge Cases ─────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_lsm(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        assert lsm.get(1) is None
        s = lsm.stats()
        assert s["total_entries"] == 0

    def test_append_after_flush(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        for i in range(L0_MAX + 5):
            lsm.append({"turn": i + 1})
        lsm.flush()
        # Can still append
        lsm.append({"turn": 999, "user": "after flush"})
        assert lsm.get(999)["user"] == "after flush"

    def test_large_entry(self, tmp_dir):
        lsm = MyceliumLSM(tmp_dir)
        big_entry = {"turn": 1, "user": "x" * 10000, "assistant": "y" * 10000}
        lsm.append(big_entry)
        e = lsm.get(1)
        assert len(e["user"]) == 10000

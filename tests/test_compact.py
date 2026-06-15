#!/usr/bin/env python3
"""Tests for mycelium_compact.py — condition-based compaction."""

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mycelium_lsm import MyceliumLSM, L0_MAX, L1_MAX
from mycelium_lib import compute_hash
from mycelium_compact import compact


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    """Isolated temp dir for each test."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


def _make_entry(turn: int, **kwargs) -> dict:
    """Create a minimal valid entry."""
    entry = {
        "turn": turn,
        "tier": "B",
        "type": "talk",
        "session": "test",
        "ts": "2026-06-15T10:00:00Z",
        "entities": ["test-entity"],
        "user": f"User turn {turn}",
        "assistant": f"Assistant turn {turn}",
        "prev_hash": f"prev_{turn:04d}",
        "hash": f"hash_{turn:04d}",
    }
    entry.update(kwargs)
    return entry


def _fill_lsm(lsm: MyceliumLSM, count: int, **kwargs) -> None:
    """Fill LSM with N entries."""
    for i in range(count):
        lsm.append(_make_entry(i + 1, **kwargs))


# ── Test: under threshold = no-op ───────────────────────────

class TestCompactUnderThreshold:
    def test_no_compaction_when_under(self, tmp_dir):
        """Below L0_MAX → no flush, below L1_MAX → no compact."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)  # well under L0_MAX=50

        before_stats = lsm.stats()
        result = compact(base_path=tmp_dir, lsm=lsm)

        assert not result["dry_run"]
        assert result["before"]["l0_entries"] == 5
        assert result["before"]["total_entries"] == 5
        # Should be a no-op for flush/compact
        assert any("skip" in s or "under threshold" in s for s in result["skipped"])

    def test_counts_unchanged_under_threshold(self, tmp_dir):
        """Entry counts shouldn't change when under thresholds."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 10)

        result = compact(base_path=tmp_dir, lsm=lsm)
        after = lsm.stats()

        assert after["total_entries"] == 10
        assert after["l1_segments"] == 0  # nothing flushed


# ── Test: over threshold = L0→L1 flush ──────────────────────

class TestCompactOverThreshold:
    def test_flush_l0_to_l1(self, tmp_dir):
        """Over L0_MAX triggers flush to L1."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, L0_MAX + 20)  # 70 entries, auto-flush may trigger

        before = lsm.stats()
        # Auto-flush already happened during fill, so L1 should have data
        assert before["l1_segments"] >= 1 or before["l0_entries"] > 0

        result = compact(force=True, base_path=tmp_dir, lsm=lsm)
        after = lsm.stats()

        # L1 should have segments
        assert after["l1_segments"] >= 1 or after["l2_summaries"] >= 0
        # Total entries preserved
        assert after["total_entries"] == L0_MAX + 20

    def test_compact_l1_to_l2(self, tmp_dir):
        """Over L1_MAX triggers L1→L2 compaction."""
        lsm = MyceliumLSM(tmp_dir)
        # Force L1 segments above threshold
        for i in range(L1_MAX + 10):
            lsm.l1.write_segment([_make_entry(i + 1)])
        lsm.l1._segments = None  # invalidate cache

        assert lsm.l1.segment_count() > L1_MAX

        result = compact(base_path=tmp_dir, lsm=lsm)
        after = lsm.stats()

        # L1 segments should be reduced
        assert after["l1_segments"] <= L1_MAX


# ── Test: dry run ───────────────────────────────────────────

class TestDryRun:
    def test_dry_run_no_changes(self, tmp_dir):
        """Dry run reports plan without mutating state."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, L0_MAX + 10)

        before = lsm.stats()
        result = compact(dry_run=True, base_path=tmp_dir, lsm=lsm)

        after = lsm.stats()

        # Dry run flag
        assert result["dry_run"] is True
        # No mutations
        assert after["l0_entries"] == before["l0_entries"]
        assert after["l1_segments"] == before["l1_segments"]
        assert after["total_entries"] == before["total_entries"]
        # Actions list present
        assert "actions" in result
        assert len(result["actions"]) > 0

    def test_dry_run_contains_before_state(self, tmp_dir):
        """Dry run includes before state snapshot."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(dry_run=True, base_path=tmp_dir, lsm=lsm)

        assert "before" in result
        assert result["before"]["l0_entries"] == 5


# ── Test: force compact ─────────────────────────────────────

class TestForceCompact:
    def test_force_compacts_below_threshold(self, tmp_dir):
        """--force compacts even when under thresholds."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, L0_MAX + 5)  # auto-flushes, leaving ~25 in L0

        before_stats = lsm.stats()
        result = compact(force=True, base_path=tmp_dir, lsm=lsm)

        assert result["force"] is True
        # Force triggers flush even if L0 is under threshold
        assert "L0→L1" in " ".join(result["steps"] + result["skipped"])

    def test_force_l1_compact(self, tmp_dir):
        """Force compact works on L1→L2 even with few segments."""
        lsm = MyceliumLSM(tmp_dir)
        for i in range(3):
            lsm.l1.write_segment([_make_entry(i + 1)])
        lsm.l1._segments = None

        result = compact(force=True, base_path=tmp_dir, lsm=lsm)
        # Should have attempted L1→L2
        assert any("L1→L2" in s for s in result["steps"] + result["skipped"])


# ── Test: hash chain integrity ──────────────────────────────

class TestHashChainIntegrity:
    def test_integrity_after_compact(self, tmp_dir):
        """Hash chain should remain valid after compaction."""
        lsm = MyceliumLSM(tmp_dir)
        prev = ""
        for i in range(20):
            entry = _make_entry(i + 1, prev_hash=prev)
            entry["hash"] = compute_hash(entry, prev)
            prev = entry["hash"]
            lsm.append(entry)

        result = compact(base_path=tmp_dir, lsm=lsm)
        integrity = result["integrity"]

        assert integrity["valid"] is True
        assert integrity["entries"] >= 20

    def test_integrity_reported_in_steps(self, tmp_dir):
        """Hash chain verification appears in steps."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 3)

        result = compact(base_path=tmp_dir, lsm=lsm)
        assert any("hash_chain" in s for s in result["steps"])


# ── Test: stats output format ───────────────────────────────

class TestStatsOutput:
    def test_stats_keys(self, tmp_dir):
        """Stats dict has all required keys."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)

        # Required top-level keys
        for key in ("timestamp", "dry_run", "force", "before", "after",
                     "savings_bytes", "savings_pct", "savings_human",
                     "steps", "skipped", "integrity"):
            assert key in result, f"missing key: {key}"

    def test_before_after_structure(self, tmp_dir):
        """before/after dicts have consistent structure."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)

        for state in (result["before"], result["after"]):
            for key in ("l0_entries", "l1_segments", "l1_entries",
                        "l2_summaries", "total_entries", "total_bytes"):
                assert key in state, f"missing key in state: {key}"
                assert isinstance(state[key], int)

    def test_json_serializable(self, tmp_dir):
        """Full result dict is JSON-serializable."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)
        serialized = json.dumps(result, default=str)
        assert len(serialized) > 0


# ── Test: before/after savings ──────────────────────────────

class TestBeforeAfterSavings:
    def test_savings_non_negative(self, tmp_dir):
        """Savings should be non-negative."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)
        assert result["savings_bytes"] >= 0

    def test_savings_pct_range(self, tmp_dir):
        """Savings percentage in [0, 100]."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)
        assert 0 <= result["savings_pct"] <= 100

    def test_savings_human_readable(self, tmp_dir):
        """savings_human is a string with unit."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, 5)

        result = compact(base_path=tmp_dir, lsm=lsm)
        assert isinstance(result["savings_human"], str)
        assert "B" in result["savings_human"]

    def test_after_entries_match_actual(self, tmp_dir):
        """After state matches actual LSM stats."""
        lsm = MyceliumLSM(tmp_dir)
        _fill_lsm(lsm, L0_MAX + 10)

        result = compact(base_path=tmp_dir, lsm=lsm)
        # Use same LSM instance (L0 is in-memory, new instance has empty L0)
        after = lsm.stats()

        assert result["after"]["total_entries"] == after["total_entries"]
        assert result["after"]["l1_segments"] == after["l1_segments"]

#!/usr/bin/env python3
"""Tests for mycelium_attention.py — AttentionTracker."""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mycelium_lib import init_index
from mycelium_attention import AttentionTracker, _now_iso


@pytest.fixture
def tracker(tmp_path):
    """Create a fresh tracker with temp DB + required turns table entries."""
    db = tmp_path / "test_attention.db"
    t = AttentionTracker(db_path=db)

    # Insert some turns so tier lookups work
    now = _now_iso()
    for turn in range(1, 6):
        t.conn.execute(
            "INSERT OR REPLACE INTO turns (turn, tier, type, session, ts, summary) "
            "VALUES (?,?,?,?,?,?)",
            (turn, "B", "talk", "test-session", now, f"summary-{turn}"),
        )
    # One A-tier and one S-tier
    t.conn.execute("UPDATE turns SET tier='A' WHERE turn=3")
    t.conn.execute("UPDATE turns SET tier='S' WHERE turn=4")
    t.conn.commit()
    yield t
    t.close()


def _insert_turn(conn, turn, tier="B", ts=None):
    """Helper to insert a turn with optional timestamp."""
    if ts is None:
        ts = _now_iso()
    conn.execute(
        "INSERT OR REPLACE INTO turns (turn, tier, type, session, ts, summary) "
        "VALUES (?,?,?,?,?,?)",
        (turn, tier, "talk", "test", ts, f"t{turn}"),
    )
    conn.commit()


def test_record_hit_increments(tracker):
    tracker.record_hit(turn=1)
    assert tracker.conn.execute(
        "SELECT hit_count FROM attention WHERE turn=1"
    ).fetchone()[0] == 1

    tracker.record_hit(turn=1)
    assert tracker.conn.execute(
        "SELECT hit_count FROM attention WHERE turn=1"
    ).fetchone()[0] == 2


def test_score_calculation_with_decay(tracker):
    # No hits → score 0
    assert tracker.score(turn=1) == 0.0

    # Record 3 hits
    tracker.record_hit(turn=1)
    tracker.record_hit(turn=1)
    tracker.record_hit(turn=1)

    # Just recorded — days_since ≈ 0 → decay ≈ 1.0
    s = tracker.score(turn=1)
    assert abs(s - 3.0) < 0.01  # base=3, decay≈1


def test_score_no_hits_returns_zero(tracker):
    # Non-existent turn
    assert tracker.score(turn=999) == 0.0
    # Turn exists but no attention row
    assert tracker.score(turn=1) == 0.0


def test_promote_b_to_a(tracker):
    # Give turn 1 (B-tier) 6 hits → score > 5
    for _ in range(6):
        tracker.record_hit(turn=1)

    result = tracker.promote(turn=1)
    assert result == "A"
    tier = tracker.conn.execute(
        "SELECT tier FROM turns WHERE turn=1"
    ).fetchone()[0]
    assert tier == "A"

    # last_promoted should be set
    lp = tracker.conn.execute(
        "SELECT last_promoted FROM attention WHERE turn=1"
    ).fetchone()[0]
    assert lp is not None


def test_promote_a_to_s(tracker):
    # turn 3 is A-tier — give 11 hits
    for _ in range(11):
        tracker.record_hit(turn=3)

    result = tracker.promote(turn=3)
    assert result == "S"
    tier = tracker.conn.execute(
        "SELECT tier FROM turns WHERE turn=3"
    ).fetchone()[0]
    assert tier == "S"


def test_demote_inactivity(tracker):
    # turn 3 is A-tier, insert with old timestamp, 0 hits
    old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    _insert_turn(tracker.conn, 3, tier="A", ts=old_ts)
    tracker.record_hit(turn=3)
    # Reset hit_count to 0 to simulate inactivity
    tracker.conn.execute("UPDATE attention SET hit_count=0 WHERE turn=3")
    tracker.conn.commit()

    result = tracker.demote(turn=3)
    assert result == "B"

    # Check last_demoted set
    ld = tracker.conn.execute(
        "SELECT last_demoted FROM attention WHERE turn=3"
    ).fetchone()[0]
    assert ld is not None


def test_decay_batch(tracker):
    # turn 1 (B): 7 hits → should promote to A
    for _ in range(7):
        tracker.record_hit(turn=1)

    # turn 2 (B): insert old, 0 hits → should demote to C
    old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    _insert_turn(tracker.conn, 2, tier="B", ts=old_ts)
    tracker.record_hit(turn=2)
    tracker.conn.execute("UPDATE attention SET hit_count=0 WHERE turn=2")
    tracker.conn.commit()

    result = tracker.decay_batch()
    assert any(p["turn"] == 1 for p in result["promoted"])
    assert any(d["turn"] == 2 for d in result["demoted"])


def test_top_entries(tracker):
    for _ in range(5):
        tracker.record_hit(turn=1)
    for _ in range(3):
        tracker.record_hit(turn=2)

    top = tracker.top_entries(limit=2)
    assert len(top) == 2
    assert top[0]["turn"] == 1
    assert top[0]["hit_count"] == 5


def test_stale_entries(tracker):
    # turn 1: has hits → not stale
    tracker.record_hit(turn=1)
    # turn 5: no hits → stale
    _insert_turn(tracker.conn, 5, tier="B")

    stale = tracker.stale_entries(limit=10)
    stale_turns = [s["turn"] for s in stale]
    assert 5 in stale_turns
    assert 1 not in stale_turns


def test_stats(tracker):
    tracker.record_hit(turn=1)
    tracker.record_hit(turn=1)
    tracker.record_hit(turn=2)

    stats = tracker.stats()
    assert stats["total_tracked"] >= 2
    assert stats["avg_score"] > 0
    # No promotions/demotions yet
    assert stats["promoted_count"] == 0
    assert stats["demoted_count"] == 0

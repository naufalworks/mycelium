#!/usr/bin/env python3
"""Tests for migrate_v3.py — flat JSONL to LSM migration tool."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from migrate_v3 import migrate, rollback, status
from mycelium_lib import load_log, compute_hash, extract_entities


# ── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


def _gen_entries(n=100):
    """Generate n sample log entries with proper hash chain."""
    entries = []
    prev_hash = ""
    sessions = ["alpha-session", "beta-session", "gamma-session"]
    for i in range(n):
        tier = "S" if i % 20 == 0 else "A" if i % 10 == 0 else "B"
        typ = "finding" if i % 20 == 0 else "idea" if i % 10 == 0 else "talk"
        session = sessions[i % len(sessions)]
        user = f"User message {i}: what about topic-{i} with python and git?"
        assistant = f"Assistant response {i}: here is info about topic-{i} using curl."

        entry = {
            "turn": i + 1,
            "tier": tier,
            "type": typ,
            "session": session,
            "ts": f"2026-06-15T{10 + (i % 12):02d}:{i % 60:02d}:00Z",
            "entities": extract_entities(user + " " + assistant),
            "user": user,
            "assistant": assistant,
            "prev_hash": prev_hash,
        }
        if typ == "finding":
            entry["finding"] = {
                "type": "SQLi",
                "target": f"target-{i}.example.com",
                "severity": "critical" if i % 40 == 0 else "high",
            }
        entry["hash"] = compute_hash(entry, prev_hash)
        prev_hash = entry["hash"]
        entries.append(entry)
    return entries


def _write_log(entries, path):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


@pytest.fixture
def sample_log(tmp_dir):
    """Create a 100-entry log.jsonl in a temp dir."""
    entries = _gen_entries(100)
    log_path = tmp_dir / "log.jsonl"
    _write_log(entries, log_path)
    return tmp_dir


# ── Test: migrate 100-entry sample log ──────────────────────

def test_migrate_sample_log(sample_log):
    result = migrate(log_path=sample_log / "log.jsonl",
                     base_path=sample_log, backup=False)
    assert result["entries_read"] == 100
    assert result["status"] == "complete"
    assert result["lsm_load"]["loaded"] == 100
    assert result["lsm_stats"]["total_entries"] == 100
    assert result["bloom_entities_added"] > 0
    assert result["graph_edges"] > 0
    assert result["integrity"]["valid"] is True


# ── Test: zero data loss across LSM layers ──────────────────

def test_zero_data_loss(sample_log):
    result = migrate(log_path=sample_log / "log.jsonl",
                     base_path=sample_log, backup=False)

    # Migration loaded all 100 entries
    assert result["entries_read"] == 100
    assert result["lsm_load"]["loaded"] == 100
    assert result["lsm_stats"]["l0_entries"] + result["lsm_stats"]["l1_entries"] == 100

    # L1 has the older entries on disk (turns 1-50)
    from mycelium_lsm import MyceliumLSM
    lsm = MyceliumLSM(sample_log)
    l1_turns = lsm.l1.all_turns()
    assert len(l1_turns) == 50

    # Verify older entries in L1
    for turn in range(1, 51):
        e = lsm.l1.get(turn)
        assert e is not None, f"Turn {turn} missing from L1"
        assert e["turn"] == turn


# ── Test: hash chain integrity after migration ──────────────

def test_hash_chain_integrity(sample_log):
    result = migrate(log_path=sample_log / "log.jsonl",
                     base_path=sample_log, backup=False)
    assert result["integrity"]["valid"] is True
    assert result["integrity"]["entries"] == 100

    # Also verify directly via LSM
    from mycelium_lsm import MyceliumLSM
    lsm = MyceliumLSM(sample_log)
    integrity = lsm.verify_integrity()
    assert integrity["valid"] is True


# ── Test: rollback restores original state ──────────────────

def test_rollback_restores(sample_log):
    # Save original log state
    original_entries = load_log(sample_log / "log.jsonl")
    assert len(original_entries) == 100

    # Run migration WITH backup
    result = migrate(log_path=sample_log / "log.jsonl",
                     base_path=sample_log, backup=True)
    assert result["entries_read"] == 100

    # Verify LSM artifacts exist
    assert (sample_log / "l1").exists() or (sample_log / "l2").exists()

    # Get backup path from result
    backup_file = result["backup"]

    # Run rollback
    rb = rollback(backup_file, base_path=sample_log)
    assert rb["status"] == "rolled_back"
    assert rb["integrity"]["valid"] is True

    # Verify LSM artifacts removed
    assert not (sample_log / "l1").exists()
    assert not (sample_log / "l2").exists()

    # Verify log restored
    restored_entries = load_log(sample_log / "log.jsonl")
    assert len(restored_entries) == len(original_entries)

    # Verify content preserved
    for orig, rest in zip(original_entries, restored_entries):
        assert orig["turn"] == rest["turn"]
        assert orig["hash"] == rest["hash"]


# ── Test: bloom filter works after migration ────────────────

def test_bloom_accuracy(sample_log):
    migrate(log_path=sample_log / "log.jsonl",
            base_path=sample_log, backup=False)

    from mycelium_bloom import MyceliumBloom
    bloom = MyceliumBloom.load(sample_log / ".bloom_entities", name="entities")

    # Entities that ARE in the log
    assert bloom.check("python") is True
    assert bloom.check("git") is True
    assert bloom.check("curl") is True

    # Random entity that is NOT in the log — may false-positive (bloom filter)
    # but the ones above must be present (no false negatives)
    stats = bloom.stats()
    assert stats["elements"] > 0
    assert stats["bits"] > 0


# ── Test: entity graph has edges after migration ────────────

def test_graph_completeness(sample_log):
    migrate(log_path=sample_log / "log.jsonl",
            base_path=sample_log, backup=False)

    from mycelium_graph import EntityGraph
    graph = EntityGraph(db_path=sample_log / "index.db")
    count = graph.count()
    assert count > 0, "Entity graph should have edges after migration"

    # Check that co-occur edges exist (entries have 2+ entities each)
    # python and git co-occur in every entry
    rels = graph.query_entity("python")
    assert len(rels) > 0
    graph.close()


# ── Test: idempotent — running migrate twice doesn't break ──

def test_idempotent(sample_log):
    # First migration
    r1 = migrate(log_path=sample_log / "log.jsonl",
                 base_path=sample_log, backup=False)
    assert r1["entries_read"] == 100
    assert r1["status"] == "complete"

    # Second migration on same data
    r2 = migrate(log_path=sample_log / "log.jsonl",
                 base_path=sample_log, backup=False)
    assert r2["entries_read"] == 100
    assert r2["status"] == "complete"
    assert r2["integrity"]["valid"] is True

    # L1 still has entries (on disk, survived double migration)
    from mycelium_lsm import MyceliumLSM
    lsm = MyceliumLSM(sample_log)
    for turn in range(1, 51):
        e = lsm.l1.get(turn)
        assert e is not None, f"Turn {turn} missing from L1 after double migration"


# ── Test: status returns expected keys ──────────────────────

def test_status_returns_keys(sample_log):
    migrate(log_path=sample_log / "log.jsonl",
            base_path=sample_log, backup=False)

    s = status(base_path=sample_log)
    assert "log_entries" in s
    assert s["log_entries"] == 100
    assert "bloom_files" in s
    assert "db_tables" in s
    assert "turns_count" in s
    assert s["turns_count"] == 100

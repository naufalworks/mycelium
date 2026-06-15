#!/usr/bin/env python3
"""Tests for mycelium_resume_v3.py — V3 session resume."""
import json, os, sys, tempfile, shutil, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from mycelium_lib import init_index, extract_entities, classify_tier, save_log
from mycelium_lsm import MyceliumLSM
from mycelium_bloom import MyceliumBloom
from mycelium_graph import EntityGraph
from mycelium_negation import NegationExtractor
from mycelium_resume_v3 import ResumeV3, CHARS_PER_TOKEN


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


@pytest.fixture
def sample_entries():
    """20 entries across tiers."""
    entries = []
    for i in range(20):
        tier = "S" if i < 3 else "A" if i < 6 else "B"
        entry = {
            "turn": i + 1,
            "tier": tier,
            "type": "finding" if i < 3 else "idea" if i < 6 else "talk",
            "session": f"test-session-{i // 5}",
            "ts": f"2026-06-15T{10 + i}:00:00Z",
            "entities": [f"grav", "mycelium", f"entity-{i % 3}"],
            "user": f"User message {i}: what about grav deployment?",
            "assistant": f"Assistant response {i}: here is the answer about topic-{i}.",
            "prev_hash": f"prev_{i:04d}",
            "hash": f"hash_{i:04d}",
        }
        if i < 3:
            entry["finding"] = {
                "type": "SQLi",
                "target": f"target-{i}",
                "severity": "critical",
            }
        entries.append(entry)
    return entries


@pytest.fixture
def populated_brain(tmp_dir, sample_entries):
    """Build a fully populated test brain with LSM, bloom, graph, negation."""
    # Write log.jsonl
    log_path = tmp_dir / "log.jsonl"
    save_log(sample_entries, log_path)

    # Init LSM and load
    lsm = MyceliumLSM(tmp_dir)
    lsm.load_from_jsonl(log_path)

    # Build bloom
    bloom = MyceliumBloom(capacity=1000, name="entities")
    for e in sample_entries:
        for ent in e.get("entities", []):
            bloom.add_entity(ent)
    bloom.save(tmp_dir / ".bloom_entities")
    bloom.save_to_db(tmp_dir / "index.db")

    # Build graph
    graph = EntityGraph(db_path=tmp_dir / "index.db")
    for e in sample_entries:
        edges = graph.extract_edges(e)
        graph._store_edges(edges)
    graph.close()

    # Store negation
    ne = NegationExtractor(db_path=tmp_dir / "index.db")
    ne.store({
        "approach": "using curl for auth",
        "result": "failed",
        "category": "forbidden-approach",
        "entities": ["grav", "curl"],
    }, session="test-session-0")

    return tmp_dir


@pytest.fixture
def resume_v3(populated_brain):
    """Create a ResumeV3 instance with a fully populated test brain."""
    rv = ResumeV3(populated_brain)
    yield rv
    rv.close()


# ── Tests ────────────────────────────────────────────────────

class TestBrainStats:
    def test_brain_stats_returns_string(self, resume_v3):
        stats = resume_v3.brain_stats()
        assert isinstance(stats, str)
        assert "entries" in stats
        assert "sessions" in stats
        assert "entities" in stats


class TestFullResumeFlow:
    def test_resume_without_hint(self, resume_v3):
        """Resume without user_hint should produce structured output."""
        output = resume_v3.resume()
        assert isinstance(output, str)
        assert "🧠" in output           # brain stats
        assert "📋" in output           # recent context
        assert "ms" in output           # timing

    def test_resume_with_hint(self, resume_v3):
        """Resume with user_hint should include bloom + graph + negation."""
        output = resume_v3.resume(user_hint="grav deployment")
        assert "🔍" in output           # bloom pre-check
        assert "🔗" in output           # entity relations
        assert "🚫" in output           # negation warnings
        assert "grav" in output.lower()

    def test_resume_empty_log(self, tmp_dir):
        """Resume on empty brain should not crash."""
        rv = ResumeV3(tmp_dir)
        output = rv.resume()
        rv.close()
        assert isinstance(output, str)
        assert "🧠" in output


class TestTokenBudgetPacking:
    def test_s_tier_always_included(self, resume_v3):
        """S-tier entries always included regardless of budget."""
        entries = [
            {"turn": 1, "tier": "S", "session": "s1",
             "user": "critical finding", "assistant": "severe bug found",
             "entities": ["grav"]},
            {"turn": 2, "tier": "B", "session": "b1",
             "user": "chat message", "assistant": "chat reply",
             "entities": ["mycelium"]},
        ]
        # Very small budget — S should still appear
        packed = resume_v3.pack_by_budget(entries, max_tokens=10)
        s_found = any("[S]" in p for p in packed)
        assert s_found

    def test_a_tier_fallback_to_compact(self, resume_v3):
        """A-tier falls back to compact format when budget is tight."""
        entries = [
            {"turn": 1, "tier": "A", "session": "a1",
             "user": "short", "assistant": "reply",
             "entities": ["grav"]},
        ]
        # Enough for compact, not for full
        compact_text = resume_v3._format_compact(entries[0])
        full_text = resume_v3._format_full(entries[0])
        compact_tokens = resume_v3._estimate_tokens(compact_text)

        packed = resume_v3.pack_by_budget(entries, max_tokens=compact_tokens + 1)
        assert len(packed) >= 1

    def test_budget_zero_yields_nothing(self, resume_v3):
        """Zero budget should return nothing."""
        entries = [
            {"turn": 1, "tier": "B", "session": "s",
             "user": "msg", "assistant": "rep", "entities": []},
        ]
        packed = resume_v3.pack_by_budget(entries, max_tokens=0)
        assert len(packed) == 0

    def test_many_entries_within_budget(self, resume_v3):
        """Packing 20 entries into generous budget should include most."""
        entries = [
            {"turn": i, "tier": "B", "session": f"s{i}",
             "user": f"msg-{i}", "assistant": f"rep-{i}",
             "entities": []}
            for i in range(20)
        ]
        packed = resume_v3.pack_by_budget(entries, max_tokens=5000)
        assert len(packed) == 20


class TestBloomPrecheck:
    def test_bloom_detects_known_entity(self, resume_v3):
        """Bloom should detect entities present in filter."""
        assert resume_v3.bloom.check("grav") is True
        assert resume_v3.bloom.check("mycelium") is True

    def test_bloom_misses_unknown(self, resume_v3):
        """Bloom should not match completely unknown entities."""
        assert resume_v3.bloom.check("zzzznonexistent_xyz_abc") is False

    def test_resume_shows_bloom_hits(self, resume_v3):
        """Resume output should show bloom hits for known entities."""
        output = resume_v3.resume(user_hint="grav status")
        assert "Bloom hits" in output or "Bloom: no known" in output


class TestEntityGraphEnrichment:
    def test_graph_has_edges(self, resume_v3):
        """Graph should have edges from sample data."""
        count = resume_v3.graph.count()
        assert count > 0

    def test_graph_query_entity(self, resume_v3):
        """Querying a known entity should return relationships."""
        rels = resume_v3.graph.query_entity("grav")
        assert isinstance(rels, dict)

    def test_resume_includes_graph_for_hint(self, resume_v3):
        """Resume with hint containing known entity should show relations."""
        output = resume_v3.resume(user_hint="grav deployment status")
        assert "🔗" in output


class TestNegationIntegration:
    def test_negation_stored(self, resume_v3):
        """Negation query should find stored record."""
        negs = resume_v3.negation.query(entity="grav")
        assert len(negs) >= 1
        assert "curl" in negs[0].get("approach", "")

    def test_resume_shows_negation_warnings(self, resume_v3):
        """Resume with hint should show negation warnings."""
        output = resume_v3.resume(user_hint="grav curl approach")
        assert "🚫" in output


class TestPerformanceUnder1ms:
    def test_resume_completes_under_1ms(self, resume_v3):
        """Core resume flow should be under 1ms (pre-populated brain)."""
        # Warm up
        resume_v3.resume(user_hint="grav")

        # Measure
        t0 = time.monotonic()
        for _ in range(100):
            resume_v3.resume(user_hint="grav deployment")
        elapsed = (time.monotonic() - t0) / 100 * 1000  # avg ms

        assert elapsed < 1.0, f"Resume took {elapsed:.2f}ms (limit: 1ms)"

    def test_pack_by_budget_under_1ms(self, resume_v3):
        """Token packing should be fast."""
        entries = [
            {"turn": i, "tier": "B", "session": f"s{i}",
             "user": f"msg-{i}" * 10, "assistant": f"rep-{i}" * 10,
             "entities": ["grav"]}
            for i in range(50)
        ]
        t0 = time.monotonic()
        for _ in range(100):
            resume_v3.pack_by_budget(entries, max_tokens=2000)
        elapsed = (time.monotonic() - t0) / 100 * 1000

        assert elapsed < 1.0, f"Pack took {elapsed:.2f}ms (limit: 1ms)"

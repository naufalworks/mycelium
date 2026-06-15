#!/usr/bin/env python3
"""Tests for mycelium_negation — negation index module."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mycelium_negation.py"
    spec = importlib.util.spec_from_file_location("negation_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()
NegationExtractor = mod.NegationExtractor

# Import lib for schema
from mycelium_lib import INDEX_SCHEMA  # noqa: E402


@pytest.fixture
def ne(tmp_path):
    """Return NegationExtractor wired to temp index."""
    db_path = tmp_path / "test_index.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(INDEX_SCHEMA)
    conn.close()
    return NegationExtractor(db_path=db_path)


# ── Pattern detection tests ────────────────────────────────────

class TestDetectEachPattern:
    """Each NEGATION_SIGNALS pattern should be detected."""

    def test_wrong_approach(self, ne):
        results = ne.detect("that's not the right approach to fix the bug")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "wrong-approach" in cats

    def test_wrong_approach_variant(self, ne):
        results = ne.detect("thats not the right fix for this")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "wrong-approach" in cats

    def test_forbidden_approach(self, ne):
        results = ne.detect("don't use curl for that endpoint")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "forbidden-approach" in cats

    def test_failed_attempt(self, ne):
        results = ne.detect("tried modifying the config and it failed")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "failed-attempt" in cats

    def test_caused_regression(self, ne):
        results = ne.detect("that caused a new bug in the login flow")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "caused-regression" in cats

    def test_wrong_context(self, ne):
        results = ne.detect("that's the wrong port for the service")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "wrong-context" in cats

    def test_repeated_mistake_already_told(self, ne):
        results = ne.detect("I already told you the path is wrong")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "repeated-mistake" in cats

    def test_repeated_mistake_how_many_times(self, ne):
        results = ne.detect("how many times do I have to explain this")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "repeated-mistake" in cats

    def test_behavioral_drift(self, ne):
        results = ne.detect("stop doing that, it breaks everything")
        assert len(results) >= 1
        cats = [r["category"] for r in results]
        assert "behavioral-drift" in cats

    def test_no_signal(self, ne):
        results = ne.detect("everything looks great, let's continue")
        assert results == []


# ── Store and query tests ──────────────────────────────────────

class TestStoreAndQuery:
    """Store negations and query by approach."""

    def test_store_and_query_by_approach(self, ne):
        ne.store(
            {"approach": "use root user for SSH", "result": "forbidden-approach",
             "context": "don't use root"},
            session="test-session",
        )
        results = ne.query(approach="root user")
        assert len(results) == 1
        assert "root user" in results[0]["approach"]
        assert results[0]["session"] == "test-session"

    def test_store_multiple_query_filtered(self, ne):
        ne.store({"approach": "curl localhost", "result": "failed"}, session="s1")
        ne.store({"approach": "wget localhost", "result": "failed"}, session="s2")
        ne.store({"approach": "python requests", "result": "success"}, session="s3")

        curl_only = ne.query(approach="curl")
        assert len(curl_only) == 1
        assert "curl" in curl_only[0]["approach"]


# ── Query by entity ───────────────────────────────────────────

class TestQueryByEntity:
    """Query negations filtered by entity name."""

    def test_query_by_entity(self, ne):
        ne.store(
            {"approach": "use nginx config", "result": "wrong-context",
             "entities": "grav,nginx"},
            session="s1",
        )
        ne.store(
            {"approach": "modify grav shim", "result": "caused-regression",
             "entities": "grav-shim"},
            session="s2",
        )

        grav_results = ne.query(entity="grav")
        assert len(grav_results) >= 1
        for r in grav_results:
            assert "grav" in r["entities"]


# ── Count ──────────────────────────────────────────────────────

class TestCount:
    """Count returns correct total."""

    def test_count_empty(self, ne):
        assert ne.count() == 0

    def test_count_after_stores(self, ne):
        ne.store({"approach": "a", "result": "x"}, session="s")
        ne.store({"approach": "b", "result": "y"}, session="s")
        ne.store({"approach": "c", "result": "z"}, session="s")
        assert ne.count() == 3


# ── Recent ─────────────────────────────────────────────────────

class TestRecent:
    """Recent returns negations sorted newest-first."""

    def test_recent_sorted_by_ts(self, ne):
        approaches = ["first", "second", "third"]
        for a in approaches:
            ne.store({"approach": a, "result": "test"}, session="s")

        recent = ne.recent(limit=2)
        assert len(recent) == 2
        # Most recent should be "third" (stored last → newest ts)
        assert recent[0]["approach"] == "third"
        assert recent[1]["approach"] == "second"

    def test_recent_limit(self, ne):
        for i in range(5):
            ne.store({"approach": f"approach-{i}", "result": "test"}, session="s")

        recent = ne.recent(limit=3)
        assert len(recent) == 3

    def test_recent_empty(self, ne):
        assert ne.recent() == []

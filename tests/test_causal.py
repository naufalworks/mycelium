#!/usr/bin/env python3
"""Tests for mycelium_causal — Causal Chain DAG."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import sqlite3

import pytest

# Add scripts/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from mycelium_causal import CausalExtractor
from mycelium_lib import init_index


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test."""
    return tmp_path / "test_index.db"


@pytest.fixture
def ce(db):
    """CausalExtractor with temp DB."""
    return CausalExtractor(db_path=db)


# ── Sample entries ────────────────────────────────────────────

def _finding(turn, session="s1", severity="critical", detail="", entities=None):
    return {
        "turn": turn,
        "type": "finding",
        "session": session,
        "ts": f"2026-01-01T00:00:{turn:02d}",
        "entities": entities or ["test-entity"],
        "user": "Found a bug in test-entity",
        "assistant": "Acknowledged",
        "finding": {"type": "SQLi", "target": "test-entity", "severity": severity, "detail": detail},
    }


def _decision(turn, session="s1", entities=None, extra_text=""):
    return {
        "turn": turn,
        "type": "decision",
        "session": session,
        "ts": f"2026-01-01T00:00:{turn:02d}",
        "entities": entities or ["test-entity"],
        "user": f"Decided to fix the bug in test-entity{extra_text}",
        "assistant": "Applied fix",
    }


def _idea(turn, session="s1", entities=None, extra_text=""):
    return {
        "turn": turn,
        "type": "idea",
        "session": session,
        "ts": f"2026-01-01T00:00:{turn:02d}",
        "entities": entities or ["test-entity"],
        "user": f"Idea: use test-entity{extra_text}",
        "assistant": "Interesting idea",
    }


# ── Tests ─────────────────────────────────────────────────────

class TestCausedEdge:
    """finding → decision → CAUSED edge"""

    def test_caused_edge(self, ce):
        prev = _finding(1)
        cur = _decision(2)
        edges = ce.extract_edges(cur, [prev])
        assert len(edges) >= 1
        e = edges[0]
        assert e["edge_type"] == "CAUSED"
        assert e["source_turn"] == 1
        assert e["target_turn"] == 2
        assert 0.0 < e["confidence"] <= 1.0


class TestResolvedEdge:
    """decision → finding(severity=resolved) → RESOLVED edge"""

    def test_resolved_edge(self, ce):
        prev = _decision(3)
        cur = _finding(4, severity="resolved", detail="bug is resolved now")
        edges = ce.extract_edges(cur, [prev])
        resolved = [e for e in edges if e["edge_type"] == "RESOLVED"]
        assert len(resolved) >= 1
        e = resolved[0]
        assert e["source_turn"] == 3
        assert e["target_turn"] == 4


class TestRegressedEdge:
    """decision → finding(text=regression/broke) → REGRESSED edge"""

    def test_regressed_edge(self, ce):
        prev = _decision(5)
        cur = _finding(6, severity="high", detail="this broke the auth system regression")
        edges = ce.extract_edges(cur, [prev])
        regressed = [e for e in edges if e["edge_type"] == "REGRESSED"]
        assert len(regressed) >= 1
        e = regressed[0]
        assert e["source_turn"] == 5
        assert e["target_turn"] == 6


class TestTraceCause:
    """Chain of 3 turns → trace returns [3, 2, 1]"""

    def test_trace_cause(self, ce):
        # Build chain: turn 1 → 2 → 3
        for edge in [
            {"source_turn": 1, "target_turn": 2, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""},
            {"source_turn": 2, "target_turn": 3, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""},
        ]:
            ce.store_edge(edge)

        chain = ce.trace_cause(3)
        assert chain == [3, 2, 1]


class TestTraceEffect:
    """Forward trace works"""

    def test_trace_effect(self, ce):
        for edge in [
            {"source_turn": 1, "target_turn": 2, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""},
            {"source_turn": 2, "target_turn": 3, "edge_type": "RESOLVED", "confidence": 0.9, "session": "s1", "ts": ""},
        ]:
            ce.store_edge(edge)

        chain = ce.trace_effect(1)
        assert chain == [1, 2, 3]


class TestRegressions:
    """Returns all REGRESSED edges"""

    def test_regressions(self, ce):
        ce.store_edge({"source_turn": 1, "target_turn": 2, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""})
        ce.store_edge({"source_turn": 3, "target_turn": 4, "edge_type": "REGRESSED", "confidence": 0.9, "session": "s1", "ts": ""})
        ce.store_edge({"source_turn": 5, "target_turn": 6, "edge_type": "REGRESSED", "confidence": 0.8, "session": "s2", "ts": ""})

        regs = ce.regressions()
        assert len(regs) == 2
        assert all(r["edge_type"] == "REGRESSED" for r in regs)


class TestMaxDepth:
    """Trace stops at max_depth"""

    def test_max_depth(self, ce):
        # Build chain: 1→2→3→4→5→6→7→8
        for i in range(1, 8):
            ce.store_edge({
                "source_turn": i, "target_turn": i + 1,
                "edge_type": "CAUSED", "confidence": 0.9,
                "session": "s1", "ts": "",
            })

        chain = ce.trace_cause(8, max_depth=3)
        assert len(chain) == 4  # 8, 7, 6, 5
        assert chain == [8, 7, 6, 5]


class TestCount:
    """Correct edge count"""

    def test_count(self, ce):
        assert ce.count() == 0
        ce.store_edge({"source_turn": 1, "target_turn": 2, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""})
        assert ce.count() == 1
        ce.store_edge({"source_turn": 2, "target_turn": 3, "edge_type": "RESOLVED", "confidence": 0.7, "session": "s1", "ts": ""})
        assert ce.count() == 2


class TestBuildFromLog:
    """Builds from existing log.jsonl"""

    def test_build_from_log(self, ce, tmp_path):
        log_path = tmp_path / "log.jsonl"
        entries = [
            _finding(1),
            _decision(2),
            _finding(3, severity="resolved", detail="all good now"),
        ]
        with open(log_path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        count = ce.build_from_log(log_path)
        assert count >= 1  # At least CAUSED from finding→decision
        # Verify edges stored
        rows = ce.conn.execute("SELECT * FROM causal_edges").fetchall()
        assert len(rows) >= 1


class TestGetChain:
    """get_chain returns edges between specified turns"""

    def test_get_chain(self, ce):
        ce.store_edge({"source_turn": 1, "target_turn": 2, "edge_type": "CAUSED", "confidence": 0.9, "session": "s1", "ts": ""})
        ce.store_edge({"source_turn": 3, "target_turn": 4, "edge_type": "RESOLVED", "confidence": 0.7, "session": "s1", "ts": ""})

        chain = ce.get_chain([1, 2])
        assert len(chain) == 1
        assert chain[0]["source_turn"] == 1
        assert chain[0]["target_turn"] == 2


class TestSupersededEdge:
    """idea → idea (same entities) → SUPERSEDED"""

    def test_superseded_edge(self, ce):
        prev = _idea(1, entities=["mycelium", "hermes"])
        cur = _idea(2, entities=["mycelium", "hermes"], extra_text=" but better")
        edges = ce.extract_edges(cur, [prev])
        superseded = [e for e in edges if e["edge_type"] == "SUPERSEDED"]
        assert len(superseded) >= 1
        assert superseded[0]["source_turn"] == 1
        assert superseded[0]["target_turn"] == 2


class TestDeploysEdge:
    """decision → decision(deploy keyword) → DEPLOYS"""

    def test_deploys_edge(self, ce):
        prev = _decision(1, extra_text=" for production")
        cur = _decision(2, extra_text=" deployed to production")
        edges = ce.extract_edges(cur, [prev])
        deploys = [e for e in edges if e["edge_type"] == "DEPLOYS"]
        assert len(deploys) >= 1
        assert deploys[0]["source_turn"] == 1
        assert deploys[0]["target_turn"] == 2


class TestConfidenceScoring:
    """Confidence scoring rules"""

    def test_close_same_session(self, ce):
        prev = _finding(1)
        cur = _decision(2)  # gap=1, same session
        edges = ce.extract_edges(cur, [prev])
        assert edges[0]["confidence"] >= 0.9

    def test_medium_same_session(self, ce):
        prev = _finding(1)
        cur = _decision(8)  # gap=7, same session
        edges = ce.extract_edges(cur, [prev])
        assert 0.6 <= edges[0]["confidence"] <= 0.8

    def test_different_session_shared_entities(self, ce):
        prev = _finding(1, session="s1")
        cur = _finding(2, session="s2")  # different session
        prev_type = prev["type"]
        cur_type = cur["type"]
        # These won't create a CAUSED edge (both are findings), but
        # we can test the _confidence method directly
        conf = ce._confidence(False, 1, {"mycelium"}, "test mycelium", "test mycelium")
        assert conf == 0.6  # 0.5 base + 0.1 shared keywords

    def test_shared_keywords_boost(self, ce):
        prev = _finding(1)
        cur = _decision(2)
        edges = ce.extract_edges(cur, [prev])
        # Both mention "test-entity" → shared keywords → boost applied
        assert edges[0]["confidence"] >= 0.9


class TestNoEdgesForDistantTurns:
    """Turns > 10 apart produce no edges"""

    def test_distant(self, ce):
        prev = _finding(1)
        cur = _decision(12)  # gap=11
        edges = ce.extract_edges(cur, [prev])
        assert len(edges) == 0


class TestNoEdgesForDifferentSessions:
    """Different sessions don't create CAUSED/RESOLVED/etc. edges"""

    def test_different_sessions(self, ce):
        prev = _finding(1, session="s1")
        cur = _decision(2, session="s2")
        edges = ce.extract_edges(cur, [prev])
        # No same-session edge types
        same_session_types = {e["edge_type"] for e in edges} & {"CAUSED", "RESOLVED", "REGRESSED", "DEPLOYS"}
        assert len(same_session_types) == 0

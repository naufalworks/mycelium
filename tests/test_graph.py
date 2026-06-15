#!/usr/bin/env python3
"""Tests for EntityGraph — entity relationship graph module."""
import os
import sys
import sqlite3
import tempfile
import pytest

# Ensure scripts/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from mycelium_graph import EntityGraph
from mycelium_lib import LOG


@pytest.fixture
def graph():
    """Temp-DB graph for isolation."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    g = EntityGraph(db_path=tmp.name)
    yield g
    g.close()
    os.unlink(tmp.name)


@pytest.fixture
def log_graph():
    """Graph built from real log.jsonl."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    g = EntityGraph(db_path=tmp.name)
    g.build_from_log(LOG)
    yield g
    g.close()
    os.unlink(tmp.name)


# ── Edge extraction ────────────────────────────────────────────

def test_cooccur_extraction(graph):
    """Two entities in same turn → co-occur edge."""
    entry = {
        "turn": 1,
        "entities": ["hermes", "grav"],
        "user": "hey", "assistant": "hi",
        "session": "test", "ts": "2025-01-01",
    }
    edges = graph.extract_edges(entry)
    co = [e for e in edges if e["edge_type"] == "co-occur"]
    assert len(co) == 1
    assert co[0]["source"] == "grav"
    assert co[0]["target"] == "hermes"


def test_cooccur_three_entities(graph):
    """Three entities → 3 co-occur edges (combinations of 2)."""
    entry = {
        "turn": 1,
        "entities": ["grav", "hermes", "mycelium"],
        "user": "", "assistant": "",
        "session": "test", "ts": "",
    }
    edges = graph.extract_edges(entry)
    co = [e for e in edges if e["edge_type"] == "co-occur"]
    assert len(co) == 3  # C(3,2) = 3


def test_resolves_detection(graph):
    """'fixed grav' → resolves edge."""
    entry = {
        "turn": 1,
        "entities": ["grav"],
        "user": "", "assistant": "Fixed grav shim, working now.",
        "session": "test", "ts": "",
    }
    edges = graph.extract_edges(entry)
    resolves = [e for e in edges if e["edge_type"] == "resolves"]
    assert len(resolves) >= 1
    assert resolves[0]["target"] == "grav"


def test_requires_detection(graph):
    """'depends on grav' → requires edge."""
    entry = {
        "turn": 1,
        "entities": ["grav"],
        "user": "mycelium depends on grav",
        "assistant": "ok",
        "session": "test", "ts": "",
    }
    edges = graph.extract_edges(entry)
    req = [e for e in edges if e["edge_type"] == "requires"]
    assert len(req) >= 1
    assert req[0]["target"] == "grav"


def test_deploys_detection(graph):
    """'installed hermes' → deploys edge."""
    entry = {
        "turn": 1,
        "entities": ["hermes"],
        "user": "ok", "assistant": "Installed hermes agent on the vps.",
        "session": "test", "ts": "",
    }
    edges = graph.extract_edges(entry)
    deploys = [e for e in edges if e["edge_type"] == "deploys"]
    assert len(deploys) >= 1
    assert deploys[0]["target"] == "hermes"


def test_affects_detection(graph):
    """'broke grav' → affects edge."""
    entry = {
        "turn": 1,
        "entities": ["grav"],
        "user": "", "assistant": "Updated grav config, broke something.",
        "session": "test", "ts": "",
    }
    edges = graph.extract_edges(entry)
    affects = [e for e in edges if e["edge_type"] == "affects"]
    assert len(affects) >= 1
    assert affects[0]["target"] == "grav"


# ── Storage + query ────────────────────────────────────────────

def test_store_and_query(graph):
    """Store edge → query returns it."""
    graph.store_edge("grav", "hermes", "co-occur", 1, "test", "")
    rels = graph.query_entity("grav")
    assert "co-occur" in rels
    assert ("hermes", 1) in rels["co-occur"]


def test_store_and_query_reverse(graph):
    """Query target entity finds the edge too."""
    graph.store_edge("grav", "hermes", "co-occur", 1, "test", "")
    rels = graph.query_entity("hermes")
    assert "co-occur" in rels
    assert ("grav", 1) in rels["co-occur"]


def test_weight_increment(graph):
    """Same edge twice → weight=2."""
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    row = graph.conn.execute(
        "SELECT weight FROM entity_edges WHERE source='a' AND target='b'"
    ).fetchone()
    assert row[0] == 2


# ── Neighbors ──────────────────────────────────────────────────

def test_neighbors(graph):
    """Entity with 2 connections → neighbors returns both."""
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    graph.store_edge("a", "c", "co-occur", 1, "", "")
    nb = graph.neighbors("a")
    assert nb == {"b", "c"}


def test_neighbors_depth2(graph):
    """BFS depth=2 picks up indirect neighbors."""
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    graph.store_edge("b", "c", "co-occur", 2, "", "")
    nb_d1 = graph.neighbors("a", depth=1)
    assert "b" in nb_d1
    assert "c" not in nb_d1  # not reached at depth 1
    nb_d2 = graph.neighbors("a", depth=2)
    assert "c" in nb_d2


# ── Top entities ───────────────────────────────────────────────

def test_top_entities(graph):
    """Multiple edges → sorted by connection count."""
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    graph.store_edge("a", "c", "co-occur", 2, "", "")
    graph.store_edge("a", "d", "co-occur", 3, "", "")
    graph.store_edge("x", "y", "co-occur", 1, "", "")
    top = graph.top_entities(3)
    assert top[0][0] == "a"
    assert top[0][1] == 3  # source in 3 edges
    # x and y tied at 1 each
    assert {top[1][0], top[2][0]} == {"x", "y"}


# ── Count ──────────────────────────────────────────────────────

def test_count(graph):
    """Returns correct edge count."""
    assert graph.count() == 0
    graph.store_edge("a", "b", "co-occur", 1, "", "")
    graph.store_edge("b", "c", "co-occur", 2, "", "")
    assert graph.count() == 2


# ── Build from log ─────────────────────────────────────────────

def test_build_from_log(log_graph):
    """Builds from existing log.jsonl, creates edges."""
    c = log_graph.count()
    assert c > 0  # real log has multiple turns with shared entities


def test_build_clears_old(log_graph):
    """Building again clears old edges first."""
    c1 = log_graph.count()
    log_graph.build_from_log(LOG)
    c2 = log_graph.count()
    assert c1 == c2  # same data → same count


# ── Multiple edge types for same entity pair ───────────────────

def test_multiple_edge_types(graph):
    """Same entities, different edge types coexist."""
    graph.store_edge("grav", "hermes", "co-occur", 1, "", "")
    graph.store_edge("grav", "hermes", "affects", 1, "", "")
    rels = graph.query_entity("grav")
    assert "co-occur" in rels
    assert "affects" in rels

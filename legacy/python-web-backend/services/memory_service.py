"""
Memory service — serves semantic memory data to the dashboard.
Provides JSON API endpoints consumed by memory_dashboard.html.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

from .status_service import load_entries


def _get_index_path() -> Path:
    """Resolve index.db path."""
    p = Path(__file__).resolve().parents[3] / "index.db"
    if p.exists():
        return p
    # Runtime fallback
    from os import environ
    home = Path.home()
    candidates = [
        home / ".hermes/myceliumd/runtime/index.db",
        Path.cwd() / "index.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    return p


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a read query on the index DB and return rows as dicts."""
    path = _get_index_path()
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── API handlers ────────────────────────────────────────────


def handle_stats(params: dict) -> dict:
    """GET /api/memory/stats"""
    facts = _query("""
        SELECT fact_type, COUNT(*) as c FROM memory_facts GROUP BY fact_type
    """)
    tiers = _query("""
        SELECT tier, COUNT(*) as c FROM memory_facts GROUP BY tier
    """)
    snap_count = _query("SELECT COUNT(*) as c FROM context_snapshots")
    total = _query("SELECT COUNT(*) as c FROM memory_facts")

    by_type = {r["fact_type"]: r["c"] for r in facts}
    by_tier = {f"tier_{r['tier']}": r["c"] for r in tiers}

    return {
        "total_facts": total[0]["c"] if total else 0,
        "total_snapshots": snap_count[0]["c"] if snap_count else 0,
        "by_type": by_type,
        "by_tier": by_tier,
    }


def handle_facts(params: dict) -> dict:
    """GET /api/memory/facts?type=credential&limit=30"""
    ftype = params.get("type", "")
    limit = int(params.get("limit", 30))

    if ftype:
        rows = _query(
            "SELECT * FROM memory_facts WHERE fact_type=? ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (ftype, limit),
        )
    else:
        rows = _query(
            "SELECT * FROM memory_facts ORDER BY tier ASC, confidence DESC LIMIT ?",
            (limit,),
        )
    return {"facts": rows}


def handle_recall(params: dict) -> dict:
    """GET /api/memory/recall?q=metabase api key"""
    import re
    q = params.get("q", "").strip()
    if not q:
        return {"facts": []}

    words = [w.lower() for w in re.findall(r"\w+", q) if len(w) > 2]
    if not words:
        return {"facts": []}

    conditions = " OR ".join(
        ["(entity LIKE ? OR attribute LIKE ? OR value LIKE ?)" for _ in words]
    )
    query_params = []
    for w in words:
        query_params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])

    rows = _query(
        f"SELECT * FROM memory_facts WHERE {conditions} ORDER BY confidence DESC, tier ASC LIMIT 15",
        tuple(query_params),
    )
    return {"facts": rows, "query": q}


def handle_patterns(params: dict) -> dict:
    """GET /api/memory/patterns — most frequent entities in facts."""
    rows = _query("""
        SELECT entity, COUNT(*) as c, MAX(updated_at) as last_seen
        FROM memory_facts
        GROUP BY entity
        ORDER BY c DESC
        LIMIT 20
    """)
    return {
        "patterns": [
            {"entity": r["entity"], "count": r["c"], "last_seen": r["last_seen"]}
            for r in rows
        ]
    }


def handle_snapshots(params: dict) -> dict:
    """GET /api/memory/snapshots — context timeline."""
    rows = _query("""
        SELECT session_id, summary, topics, decisions, entities, credentials,
               turn_count, created_at
        FROM context_snapshots
        ORDER BY created_at DESC
        LIMIT 20
    """)
    snapshots = []
    for r in rows:
        d = dict(r)
        for field in ("topics", "decisions", "entities", "credentials"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
        snapshots.append(d)
    return {"snapshots": snapshots}

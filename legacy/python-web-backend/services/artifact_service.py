"""
Artifact service — bridges Python web backend to Go artifact store.
All artifact operations are handled by Go SQLite reads from index.db.
"""

import json
import sqlite3
from pathlib import Path


def _get_index() -> Path:
    p = Path(__file__).resolve().parents[3] / "index.db"
    if p.exists():
        return p
    home = Path.home()
    for c in [home / ".hermes/myceliumd/runtime/index.db", Path.cwd() / "index.db"]:
        if c.exists():
            return c
    return p


def _query(sql: str, params: tuple = ()) -> list[dict]:
    path = _get_index()
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


def handle_stats(params: dict) -> dict:
    """GET /api/artifacts/stats"""
    total = _query("SELECT COUNT(*) as c FROM artifacts")
    tokens = _query("SELECT COALESCE(SUM(token_cost),0) as c FROM artifacts")
    by_type = _query("SELECT type, COUNT(*) as c FROM artifacts GROUP BY type ORDER BY c DESC")

    return {
        "total": total[0]["c"] if total else 0,
        "total_tokens": tokens[0]["c"] if tokens else 0,
        "total_cost": (tokens[0]["c"] if tokens else 0) * 3.0 / 1_000_000,
        "by_type": {r["type"]: r["c"] for r in by_type},
    }


def handle_list(params: dict) -> dict:
    """GET /api/artifacts?type=&limit=25&offset=0"""
    atype = params.get("type", "")
    limit = int(params.get("limit", 25))
    offset = int(params.get("offset", 0))

    if atype:
        rows = _query(
            "SELECT id, type, COALESCE(name,'') as name, created_at, COALESCE(token_cost,0) as cost "
            "FROM artifacts WHERE type=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (atype, limit, offset),
        )
    else:
        rows = _query(
            "SELECT id, type, COALESCE(name,'') as name, created_at, COALESCE(token_cost,0) as cost "
            "FROM artifacts ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return {"artifacts": rows}


def handle_query(params: dict) -> dict:
    """POST /api/artifacts/query"""
    sql = params.get("sql", "").strip()
    if not sql:
        return {"error": "missing sql"}
    if not sql.upper().startswith("SELECT"):
        return {"error": "only SELECT queries allowed"}

    path = _get_index()
    if not path.exists():
        return {"error": "index.db not found"}

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(r) for r in cur.fetchall()]
        return {"columns": cols, "rows": rows}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def handle_get(params: dict) -> dict:
    """GET /api/artifacts/{id}"""
    aid = params.get("id", "")
    rows = _query("SELECT * FROM artifacts WHERE id=?", (aid,))
    if not rows:
        return {"error": "not found"}
    return {"artifact": rows[0]}

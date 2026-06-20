"""Tasks + Cache API routes — proxies to Go packages via SQLite."""

import json
import sqlite3
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(tags=["tasks", "cache"])


def _get_index() -> Path:
    p = Path(__file__).resolve().parents[3] / "index.db"
    if p.exists():
        return p
    return Path.home() / ".hermes/myceliumd/runtime/index.db"


def _query(sql: str, params: tuple = ()) -> list[dict]:
    path = _get_index()
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        return []
    finally:
        conn.close()


# ── Tasks ──────────────────────────────────────────────


@router.get("/api/tasks")
def api_tasks(status: str = "", limit: int = 20):
    rows = _query(
        "SELECT id, prompt, status, COALESCE(result_artifact,'') as result_artifact, "
        "COALESCE(error_msg,'') as error_msg, created_at, updated_at FROM tasks "
        "WHERE (? = '' OR status = ?) ORDER BY created_at DESC LIMIT ?",
        (status, status, limit),
    )
    return {"tasks": rows}


@router.get("/api/tasks/{task_id}")
def api_task_get(task_id: str):
    rows = _query(
        "SELECT id, prompt, status, COALESCE(result_artifact,'') as result_artifact, "
        "COALESCE(error_msg,'') as error_msg, created_at, updated_at, "
        "COALESCE(started_at,'') as started_at, COALESCE(completed_at,'') as completed_at "
        "FROM tasks WHERE id=?",
        (task_id,),
    )
    if not rows:
        return {"error": "not found"}
    return {"task": rows[0]}


# ── Cache ──────────────────────────────────────────────


@router.get("/api/cache/stats")
def api_cache_stats():
    rows = _query("SELECT COUNT(*) as c FROM artifacts WHERE type='speculative'")
    total = rows[0]["c"] if rows else 0
    return {"cached_entries": total, "max_entries": 100}

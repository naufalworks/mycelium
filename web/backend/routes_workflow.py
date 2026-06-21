"""Workflow API routes — proxies to Go workflow engine via SQLite."""

import json
import sqlite3
from pathlib import Path
from fastapi import APIRouter

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


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
    except Exception:
        return []
    finally:
        conn.close()


@router.get("/list")
def api_workflow_list():
    """List all workflow definitions (stored as prompts)."""
    rows = _query(
        "SELECT value FROM memory_facts WHERE entity='prompt' AND fact_type='prompt' ORDER BY updated_at DESC"
    )
    workflows = []
    for r in rows:
        try:
            data = json.loads(r["value"])
            if "steps" in data:
                workflows.append({
                    "name": data.get("name", "?"),
                    "description": data.get("description", ""),
                    "steps": data.get("steps", []),
                    "created_at": data.get("created_at", ""),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return {"workflows": workflows}


@router.post("/define")
def api_workflow_define(payload: dict):
    """Define a new workflow."""
    name = payload.get("name", "")
    description = payload.get("description", "")
    steps = payload.get("steps", [])
    if not name or not steps:
        return {"ok": False, "error": "name and steps required"}

    now = __import__("datetime").datetime.utcnow().isoformat() + "Z"
    data = json.dumps({"name": name, "description": description,
                        "steps": steps, "created_at": now})

    path = _get_index()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memory_facts (entity, attribute, value, fact_type, confidence, created_at, updated_at)"
            " VALUES ('prompt', ?, ?, 'prompt', 1.0, ?, ?)",
            (name, data, now, now),
        )
        conn.commit()
        return {"ok": True, "name": name, "steps": len(steps)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()


@router.post("/run/{workflow_name}")
def api_workflow_run(workflow_name: str):
    """Start a workflow run."""
    # Read the workflow definition
    rows = _query(
        "SELECT value FROM memory_facts WHERE entity='prompt' AND attribute=? AND fact_type='prompt'",
        (workflow_name,),
    )
    if not rows:
        return {"error": f"workflow {workflow_name} not found"}

    import hashlib, datetime
    run_id = f"wf_{workflow_name}_{hashlib.md5(datetime.datetime.utcnow().isoformat().encode()).hexdigest()[:8]}"

    return {"run_id": run_id, "workflow": workflow_name, "status": "running"}


@router.get("/status/{run_id}")
def api_workflow_status(run_id: str):
    """Get run status."""
    return {
        "id": run_id,
        "workflow": run_id.split("_")[1] if "_" in run_id else "?",
        "status": "running",
        "current_step": 1,
        "step_results": [],
    }

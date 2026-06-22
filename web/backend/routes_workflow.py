"""Workflow API routes — define, run, and track workflows.

Workflow definitions stored in memory_facts (entity='prompt').
Workflow runs stored in memory_facts (entity='workflow_run', attribute=run_id).
Step results stored in artifacts (type='workflow_step').

Run execution happens in a background thread so the CLI can poll for progress.
"""

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


def _get_index() -> Path:
    p = Path(__file__).resolve().parents[3] / "index.db"
    if p.exists():
        return p
    return Path.home() / ".hermes/myceliumd/runtime/index.db"


def _ensure_table():
    """Ensure workflow_runs table exists for faster queries."""
    path = _get_index()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY,
                workflow_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                current_step INTEGER NOT NULL DEFAULT 0,
                total_steps INTEGER NOT NULL DEFAULT 0,
                steps_json TEXT NOT NULL DEFAULT '[]',
                results_json TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


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


def _execute(sql: str, params: tuple = ()) -> bool:
    path = _get_index()
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        print(f"[workflow] db error: {e}")
        return False
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── List defined workflows ──────────────────────────────


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
                    "step_count": len(data.get("steps", [])),
                    "created_at": data.get("created_at", ""),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return {"workflows": workflows}


# ── Define a new workflow ───────────────────────────────


@router.post("/define")
def api_workflow_define(payload: dict):
    """Define a new workflow."""
    name = payload.get("name", "")
    description = payload.get("description", "")
    steps = payload.get("steps", [])
    if not name or not steps:
        return {"ok": False, "error": "name and steps required"}

    now = _now()
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


# ── Background step execution ───────────────────────────


def _exec_step(cmd: str, step_name: str, timeout: int = 60) -> dict:
    """Execute a single workflow step (shell command) and return result."""
    import subprocess
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "name": step_name,
            "status": "passed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-500:] if proc.stdout else "",
            "stderr": proc.stderr[-500:] if proc.stderr else "",
            "duration_s": 0,  # filled in by caller
        }
    except subprocess.TimeoutExpired:
        return {
            "name": step_name,
            "status": "failed",
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timeout after {timeout}s",
            "duration_s": timeout,
        }
    except Exception as e:
        return {
            "name": step_name,
            "status": "failed",
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "duration_s": 0,
        }


def _run_workflow_background(run_id: str, workflow: dict):
    """Execute workflow steps in background, updating the DB as we go."""
    steps = workflow.get("steps", [])
    total = len(steps)
    results = []
    now = _now()

    for i, step in enumerate(steps):
        step_name = step.get("name", f"step-{i}")
        step_cmd = step.get("cmd", step.get("prompt", f"echo '{step_name}'"))

        # Update status: current step
        _execute(
            "UPDATE workflow_runs SET current_step=?, status='running', updated_at=? WHERE id=?",
            (i + 1, _now(), run_id),
        )

        # Execute
        import time
        t0 = time.time()
        result = _exec_step(step_cmd, step_name)
        elapsed = round(time.time() - t0, 1)
        result["duration_s"] = elapsed
        results.append(result)

        # Update step result
        _execute(
            "UPDATE workflow_runs SET results_json=?, updated_at=? WHERE id=?",
            (json.dumps(results), _now(), run_id),
        )

        # Stop on failure if step says so
        if result["status"] == "failed" and step.get("stop_on_fail", True):
            _execute(
                "UPDATE workflow_runs SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?",
                (f"Step '{step_name}' failed", _now(), _now(), run_id),
            )
            return

    # All steps passed
    _execute(
        "UPDATE workflow_runs SET status='done', current_step=?, completed_at=?, updated_at=? WHERE id=?",
        (total, _now(), _now(), run_id),
    )


# ── Run a workflow ──────────────────────────────────────


@router.post("/run/{workflow_name}")
def api_workflow_run(workflow_name: str):
    """Start a workflow run. Returns immediately; execution happens in background."""
    _ensure_table()

    # Read workflow definition
    rows = _query(
        "SELECT value FROM memory_facts WHERE entity='prompt' AND attribute=? AND fact_type='prompt'",
        (workflow_name,),
    )
    if not rows:
        return {"error": f"workflow {workflow_name} not found"}

    try:
        workflow = json.loads(rows[0]["value"])
    except (json.JSONDecodeError, KeyError):
        return {"error": f"invalid workflow definition for '{workflow_name}'"}

    steps = workflow.get("steps", [])
    if not steps:
        return {"error": f"workflow '{workflow_name}' has no steps"}

    run_id = f"wf_{workflow_name}_{uuid.uuid4().hex[:8]}"
    now = _now()

    # Create run record
    _execute(
        "INSERT INTO workflow_runs (id, workflow_name, status, current_step, total_steps, steps_json, results_json, created_at, updated_at)"
        " VALUES (?, ?, 'running', 0, ?, ?, '[]', ?, ?)",
        (run_id, workflow_name, len(steps), json.dumps(steps), now, now),
    )

    # Launch background execution
    t = threading.Thread(
        target=_run_workflow_background,
        args=(run_id, workflow),
        daemon=True,
    )
    t.start()

    return {
        "run_id": run_id,
        "workflow": workflow_name,
        "status": "running",
        "total_steps": len(steps),
    }


# ── List all runs ────────────────────────────────────────


@router.get("/runs")
def api_workflow_runs(limit: int = 20):
    """List all workflow runs, newest first."""
    _ensure_table()
    rows = _query(
        "SELECT id, workflow_name, status, current_step, total_steps, error, created_at, updated_at, completed_at"
        " FROM workflow_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return {"runs": rows}


# ── Get run status ───────────────────────────────────────


@router.get("/status/{run_id}")
def api_workflow_status(run_id: str):
    """Get run status with step-by-step results."""
    _ensure_table()
    rows = _query(
        "SELECT * FROM workflow_runs WHERE id=?",
        (run_id,),
    )
    if not rows:
        return {"error": f"run {run_id} not found"}

    r = rows[0]
    steps = json.loads(r.get("steps_json", "[]"))
    results = json.loads(r.get("results_json", "[]"))

    return {
        "id": r["id"],
        "workflow": r["workflow_name"],
        "status": r["status"],
        "current_step": r["current_step"],
        "total_steps": r["total_steps"],
        "steps": steps,
        "step_results": results,
        "error": r.get("error", ""),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "completed_at": r.get("completed_at", ""),
    }


# ── Get run log ──────────────────────────────────────────


@router.get("/log/{run_id}")
def api_workflow_log(run_id: str):
    """Get the chronological log of a workflow run."""
    rows = _query(
        "SELECT * FROM workflow_runs WHERE id=?", (run_id,)
    )
    if not rows:
        return {"error": f"run {run_id} not found"}

    r = rows[0]
    results = json.loads(r.get("results_json", "[]"))
    steps = json.loads(r.get("steps_json", "[]"))

    log_entries = []
    for i, step in enumerate(steps):
        result = results[i] if i < len(results) else {"name": step.get("name", f"step-{i}"), "status": "pending"}
        log_entries.append({
            "step": i + 1,
            "name": result.get("name", step.get("name", f"step-{i}")),
            "status": result.get("status", "pending"),
            "exit_code": result.get("exit_code"),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "duration_s": result.get("duration_s", 0),
        })

    return {
        "run_id": run_id,
        "workflow": r["workflow_name"],
        "status": r["status"],
        "log": log_entries,
    }


# ── Stop a running workflow ──────────────────────────────


@router.post("/stop/{run_id}")
def api_workflow_stop(run_id: str):
    """Mark a running workflow as stopped."""
    _execute(
        "UPDATE workflow_runs SET status='stopped', completed_at=?, updated_at=? WHERE id=? AND status='running'",
        (_now(), _now(), run_id),
    )
    return {"ok": True, "run_id": run_id, "status": "stopped"}

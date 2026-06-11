from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .services.backup_service import (
    create_snapshot,
    dry_run_import,
    export_snapshot,
    list_backups,
    migrate_dry_run,
    migrate_execute,
    restore_snapshot,
    verify_snapshot,
)
from .services.status_service import (
    get_connections,
    get_daemon_state,
    get_findings,
    get_session_detail,
    get_status,
    get_stream,
)
from .services.recall_service import recall
from .services.verify_service import run_verify

app = FastAPI(title="Mycelium Web", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8420",
        "http://localhost:8420",
        "http://127.0.0.1:8421",
        "http://localhost:8421",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def api_status():
    return get_status()


@app.get("/api/daemon")
def api_daemon():
    return get_daemon_state()


@app.post("/api/verify")
def api_verify():
    return run_verify()


@app.get("/api/stream")
def api_stream(
    limit: int = 100,
    session: Optional[str] = None,
    tier: Optional[str] = None,
    item_type: Optional[str] = None,
    entity: Optional[str] = None,
    q: Optional[str] = None,
):
    return get_stream(limit=limit, session=session, tier=tier, item_type=item_type, entity=entity, q=q)


@app.get("/api/sessions")
def api_sessions():
    return {"items": get_status()["recent_sessions"]}


@app.get("/api/sessions/{session_name}")
def api_session_detail(session_name: str):
    return get_session_detail(session_name)


@app.get("/api/connections")
def api_connections(limit: int = 80):
    return get_connections(limit=limit)


@app.get("/api/findings")
def api_findings():
    return get_findings()


@app.get("/api/recall")
def api_recall(q: str, limit: int = 12):
    return recall(q, limit=limit)


@app.get("/api/backups")
def api_backups():
    return list_backups()


@app.post("/api/backups/create")
def api_backup_create():
    snapshot = create_snapshot()
    return {"ok": True, "message": "snapshot created", "data": snapshot}


@app.post("/api/backups/verify")
def api_backup_verify(payload: dict):
    return verify_snapshot(payload.get("path", ""))


@app.post("/api/backups/export")
def api_backup_export(payload: dict):
    return export_snapshot(payload.get("path", ""))


@app.post("/api/import/dry-run")
def api_import_dry_run(payload: dict):
    return dry_run_import(payload.get("path", ""), payload.get("target_root"))


@app.post("/api/import/restore")
def api_import_restore(payload: dict):
    return restore_snapshot(
        payload.get("path", ""),
        payload.get("target_root"),
        bool(payload.get("overwrite", False)),
    )


@app.post("/api/migrate/dry-run")
def api_migrate_dry_run(payload: dict):
    return migrate_dry_run(payload.get("target_root", ""))


@app.post("/api/migrate/execute")
def api_migrate_execute(payload: dict):
    return migrate_execute(
        payload.get("target_root", ""),
        bool(payload.get("overwrite", False)),
    )


@app.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

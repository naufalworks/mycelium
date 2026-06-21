"""Fallback route — must be included LAST to avoid catching API routes."""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["fallback"])

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend"


@router.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    memory_html = FRONTEND_SRC / "memory_dashboard.html"
    if full_path == "memory_dashboard.html" and memory_html.exists():
        return FileResponse(memory_html)
    artifact_html = FRONTEND_SRC / "artifact_dashboard.html"
    if full_path == "artifact_dashboard.html" and artifact_html.exists():
        return FileResponse(artifact_html)
    global_html = FRONTEND_SRC / "global.html"
    if full_path == "global.html" and global_html.exists():
        return FileResponse(global_html)
    v3_pages = {"v3_dashboard.html", "v3_graph.html", "v3_negations.html", "v3_causal.html"}
    if full_path in v3_pages:
        src_file = FRONTEND_SRC / full_path
        if src_file.exists():
            return FileResponse(src_file)
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

"""Fallback route — serves the SPA for all non-API paths.
Legacy HTML pages still served individually for now.
"""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["fallback"])

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend"

LEGACY_PAGES = {
    "memory_dashboard.html": FRONTEND_SRC / "memory_dashboard.html",
    "artifact_dashboard.html": FRONTEND_SRC / "artifact_dashboard.html",
    "global.html": FRONTEND_SRC / "global.html",
    "v3_dashboard.html": FRONTEND_SRC / "v3_dashboard.html",
    "v3_graph.html": FRONTEND_SRC / "v3_graph.html",
    "v3_negations.html": FRONTEND_SRC / "v3_negations.html",
    "v3_causal.html": FRONTEND_SRC / "v3_causal.html",
}


@router.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    # Serve legacy HTML pages if they exist
    if full_path in LEGACY_PAGES:
        src = LEGACY_PAGES[full_path]
        if src.exists():
            return FileResponse(src)
    # Otherwise serve the SPA
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

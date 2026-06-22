"""Fallback route — serves the SPA for all non-API paths.
All legacy paths (v3/*, memory_dashboard, artifact_dashboard, etc.)
redirect to the unified SPA at the root.
"""

from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter(tags=["fallback"])

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

# Legacy paths that should redirect to the unified SPA
LEGACY_PATHS = {
    "memory_dashboard.html",
    "artifact_dashboard.html",
    "global.html",
    "v3_dashboard.html",
    "v3_graph.html",
    "v3_negations.html",
    "v3_causal.html",
}


@router.get("/{full_path:path}")
def frontend_fallback(full_path: str, request: Request):
    # Redirect legacy standalone pages to the SPA root
    if full_path in LEGACY_PATHS:
        return RedirectResponse(url="/")

    # Serve the SPA for all other paths (client-side routing)
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

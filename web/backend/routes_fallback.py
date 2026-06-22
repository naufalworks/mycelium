"""Fallback route — serves the SPA for all non-API paths.
Legacy HTML pages still served individually for now.
"""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["fallback"])

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend"


@router.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

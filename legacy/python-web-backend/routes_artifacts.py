"""Artifact API routes for the mycelium web backend."""

from fastapi import APIRouter
from .services.artifact_service import (
    handle_stats as artifact_stats,
    handle_list as artifact_list,
    handle_query as artifact_query,
    handle_get as artifact_get,
)

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/stats")
def api_artifact_stats():
    try:
        return artifact_stats({})
    except Exception as e:
        return {"error": str(e)}


@router.get("")
def api_artifact_list(type: str = "", limit: int = 25, offset: int = 0):
    try:
        return artifact_list({"type": type, "limit": limit, "offset": offset})
    except Exception as e:
        return {"artifacts": [], "error": str(e)}


@router.post("/query")
def api_artifact_query(payload: dict):
    try:
        return artifact_query({"sql": payload.get("sql", "")})
    except Exception as e:
        return {"error": str(e)}


@router.get("/{artifact_id}")
def api_artifact_get(artifact_id: str):
    try:
        return artifact_get({"id": artifact_id})
    except Exception as e:
        return {"error": str(e)}

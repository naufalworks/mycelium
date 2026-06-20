"""Prompts API routes for the mycelium web backend."""

import json
import urllib.request
from fastapi import APIRouter

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


def _call_go(path: str, data: dict = None):
    """Call the Go prompt endpoint on the mycelium-proxy (:8443)."""
    url = f"http://127.0.0.1:8443/api/prompts/{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


@router.get("/list")
def api_prompts_list():
    result = _call_go("list")
    return result or {"prompts": []}


@router.post("/define")
def api_prompts_define(payload: dict):
    result = _call_go("define", payload)
    return result or {"ok": False, "error": "prompt service unavailable"}


@router.post("/run")
def api_prompts_run(payload: dict):
    result = _call_go("run", payload)
    return result or {"error": "prompt service unavailable"}

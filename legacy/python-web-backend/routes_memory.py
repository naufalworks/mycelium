"""Memory API routes for the mycelium web backend."""

import json
from fastapi import APIRouter
from .services.memory_service import (
    handle_stats as memory_stats,
    handle_facts as memory_facts,
    handle_recall as memory_recall,
    handle_patterns as memory_patterns,
    handle_snapshots as memory_snapshots,
)

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/stats")
def api_memory_stats():
    try:
        return memory_stats({})
    except Exception as e:
        return {"error": str(e)}


@router.get("/facts")
def api_memory_facts(type: str = "", limit: int = 30):
    try:
        return memory_facts({"type": type, "limit": limit})
    except Exception as e:
        return {"facts": [], "error": str(e)}


@router.get("/recall")
def api_memory_recall(q: str = ""):
    try:
        return memory_recall({"q": q})
    except Exception as e:
        return {"facts": [], "error": str(e)}


@router.get("/patterns")
def api_memory_patterns():
    try:
        return memory_patterns({})
    except Exception as e:
        return {"patterns": [], "error": str(e)}


@router.get("/snapshots")
def api_memory_snapshots():
    try:
        return memory_snapshots({})
    except Exception as e:
        return {"snapshots": [], "error": str(e)}


@router.get("/infer")
def api_memory_infer():
    try:
        from mycelium_inference import infer_patterns
        insights = infer_patterns()
        return insights
    except Exception as e:
        return {"error": str(e), "note": "inference unavailable"}


@router.post("/extract")
def api_memory_extract(payload: dict):
    """Hippocampus: real-time fact extraction from a single exchange."""
    try:
        user = payload.get("user", "")
        assistant = payload.get("assistant", "")
        session = payload.get("session", "unknown")
        if not user or not assistant:
            return {"ok": False, "reason": "empty exchange"}

        texts = [json.dumps({"user": user, "assistant": assistant})]

        from mycelium_llm import extract_facts
        facts = extract_facts(texts, session)

        stored = 0
        if facts:
            from mycelium_memory import insert_fact
            for f in facts:
                insert_fact(
                    entity=f.get("entity", "unknown"),
                    attribute=f.get("attribute", "value"),
                    value=str(f.get("value", "")),
                    fact_type=f.get("fact_type", "fact"),
                    confidence=float(f.get("confidence", 0.5)),
                    source_session=session,
                    entropy=float(f.get("entropy", 0.5)),
                )
                stored += 1

        return {"ok": True, "facts_extracted": stored, "session": session}
    except Exception as e:
        return {"ok": False, "error": str(e)}

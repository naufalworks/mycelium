"""Core API routes for mycelium web backend."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from fastapi.responses import FileResponse

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
    get_findings,
    get_status,
    get_session_detail,
    get_stream,
    load_entries,
    recent_sessions,
)
from .services.recall_service import (
    recall,
    record_feedback,
    list_thread_cards,
)

router = APIRouter(tags=["core"])

# Static file paths for frontend fallback
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend"

# ── Health & Status ─────────────────────────────────────────

@router.get("/api/health")
def api_health():
    return {"ok": True}

@router.get("/api/status")
def api_status():
    return get_status()

@router.get("/api/daemon")
def api_daemon():
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:20151/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}

@router.post("/api/verify")
def api_verify():
    try:
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "scripts.mycelium", "verify"],
            capture_output=True, text=True, timeout=30,
        )
        return {"ok": r.returncode == 0, "output": r.stdout.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@router.get("/api/stream")
def api_stream():
    try:
        return get_stream()
    except Exception:
        return {"stream": []}

@router.get("/api/sessions")
def api_sessions():
    try:
        entries = load_entries()
        n = len(entries) if entries else 0
        return {"session_count": n, "entry_count": n}
    except Exception as e:
        return {"error": str(e)}

@router.get("/api/sessions/{session_name}")
def api_session(session_name: str):
    try:
        return get_session_detail(session_name)
    except Exception as e:
        return {"session": session_name, "entries": [], "error": str(e)}

@router.get("/api/connections")
def api_connections():
    return get_connections()

@router.get("/api/findings")
def api_findings():
    return get_findings()

@router.get("/api/recall")
def api_recall(q: str, limit: int = 12):
    return recall(q, limit=limit)

@router.post("/api/recall/feedback")
def api_recall_feedback(payload: dict):
    return record_feedback(
        payload.get("query", ""),
        payload.get("action", ""),
        payload.get("note", ""),
    )

@router.get("/api/threads")
def api_threads():
    return list_thread_cards()

@router.get("/api/backups")
def api_backups():
    return list_backups()

@router.post("/api/backups/create")
def api_backup_create():
    snap = create_snapshot()
    return {"ok": True, "message": "snapshot created", "data": snap}

@router.post("/api/backups/verify")
def api_backup_verify(payload: dict):
    return verify_snapshot(payload.get("path", ""))

@router.post("/api/backups/export")
def api_backup_export(payload: dict):
    return export_snapshot(payload.get("path", ""))

@router.post("/api/import/dry-run")
def api_import_dry_run(payload: dict):
    return dry_run_import(payload.get("path", ""))

@router.post("/api/import/restore")
def api_import_restore(payload: dict):
    return restore_snapshot(payload.get("path", ""), payload.get("source", ""))

@router.post("/api/migrate/dry-run")
def api_migrate_dry_run(payload: dict):
    return migrate_dry_run(payload.get("target_root", ""))

@router.post("/api/migrate/execute")
def api_migrate_execute(payload: dict):
    return migrate_execute(
        payload.get("target_root", ""),
        bool(payload.get("overwrite", False)),
    )

# ── v3 endpoints ─────────────────────────────────────────

@router.get("/api/graph/entity/{entity}")
def api_graph_entity(entity: str):
    try:
        from mycelium_graph import EntityGraph
        g = EntityGraph()
        rels = g.query_entity(entity)
        return {"entity": entity, "relations": rels}
    except Exception as e:
        return {"entity": entity, "relations": [], "error": str(e)}

@router.get("/api/graph/edges")
def api_graph_edges():
    try:
        from mycelium_graph import EntityGraph
        g = EntityGraph()
        entities = g.top_entities(20)
        return {"entities": [{"name": n, "count": c} for n, c in entities]}
    except Exception as e:
        return {"entities": [], "total_edges": 0, "error": str(e)}

@router.get("/api/negations")
def api_negations(approach: Optional[str] = None, entity: Optional[str] = None):
    try:
        from mycelium_negation import NegationExtractor
        ne = NegationExtractor()
        results = ne.query(approach=approach, entity=entity) if (approach or entity) else ne.recent(50)
        total = ne.count()
        return {"negations": results, "total": total}
    except Exception as e:
        return {"negations": [], "total": 0, "error": str(e)}

@router.get("/api/causal/trace/{turn}")
def api_causal_trace(turn: int):
    try:
        from mycelium_causal import CausalExtractor
        ce = CausalExtractor()
        chain = ce.trace_cause(turn)
        edges = ce.get_chain(chain) if len(chain) > 1 else []
        ce.close()
        return {"start_turn": turn, "chain": chain, "edges": edges}
    except Exception as e:
        return {"start_turn": turn, "chain": [], "edges": [], "error": str(e)}

@router.get("/api/causal/regressions")
def api_causal_regressions():
    try:
        from mycelium_causal import CausalExtractor
        ce = CausalExtractor()
        regs = ce.get_regressions()
        ce.close()
        return {"regressions": regs}
    except Exception as e:
        return {"regressions": [], "error": str(e)}

@router.get("/api/bloom/check/{entity}")
def api_bloom_check(entity: str):
    try:
        from mycelium_bloom import MyceliumBloom, MYCELIUM
        bloom = MyceliumBloom.load(MYCELIUM / ".bloom_entities", name="entities")
        found = bloom.check(entity)
        return {"entity": entity, "found": found}
    except Exception as e:
        return {"entity": entity, "found": False, "error": str(e)}

@router.get("/api/bloom/stats")
def api_bloom_stats():
    try:
        from mycelium_bloom import MyceliumBloom
        bloom = MyceliumBloom.load_from_db(name="entities")
        return bloom.stats()
    except Exception as e:
        return {"error": str(e), "hint": "Run: python3 scripts/mycelium_bloom.py build"}

@router.get("/api/attention/top")
def api_attention_top(limit: int = 15):
    try:
        from mycelium_attention import AttentionTracker
        t = AttentionTracker()
        entries = t.top_entries(limit)
        stats = t.stats()
        t.close()
        return {"entries": entries, "stats": stats}
    except Exception as e:
        return {"entries": [], "stats": {}, "error": str(e)}

@router.get("/api/attention/stale")
def api_attention_stale(limit: int = 15):
    try:
        from mycelium_attention import AttentionTracker
        t = AttentionTracker()
        entries = t.stale_entries(limit)
        t.close()
        return {"entries": entries}
    except Exception as e:
        return {"entries": [], "error": str(e)}

@router.get("/api/lsm/stats")
def api_lsm_stats():
    try:
        from mycelium_lsm import MyceliumLSM
        lsm = MyceliumLSM()
        return lsm.stats()
    except Exception as e:
        return {"error": str(e)}

# ── End of core routes. Catch-all frontend fallback is in routes_fallback.py.

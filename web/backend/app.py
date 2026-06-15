from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Add scripts/ to path so v3 modules can be imported
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

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
from .services.recall_service import list_thread_cards, recall, record_feedback
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


@app.post("/api/recall/feedback")
def api_recall_feedback(payload: dict):
    return record_feedback(payload.get("query", ""), payload.get("action", ""), payload.get("note", ""))


@app.get("/api/threads")
def api_threads():
    return list_thread_cards()


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


# ── v3 endpoints ─────────────────────────────────────────────

@app.get("/api/graph/entity/{entity}")
def api_graph_entity(entity: str):
    """Entity relationships grouped by edge type."""
    try:
        from mycelium_graph import EntityGraph
        g = EntityGraph()
        rels = g.query_entity(entity)
        g.close()
        return {"entity": entity, "relations": rels}
    except Exception as e:
        return {"entity": entity, "relations": {}, "error": str(e)}


@app.get("/api/graph/top")
def api_graph_top(limit: int = 15):
    """Top entities by connection count."""
    try:
        from mycelium_graph import EntityGraph
        g = EntityGraph()
        entities = g.top_entities(limit)
        count = g.count()
        g.close()
        return {"entities": [{"name": n, "count": c} for n, c in entities], "total_edges": count}
    except Exception as e:
        return {"entities": [], "total_edges": 0, "error": str(e)}


@app.get("/api/negations")
def api_negations(approach: Optional[str] = None, entity: Optional[str] = None):
    """List negations with optional filters."""
    try:
        from mycelium_negation import NegationExtractor
        ne = NegationExtractor()
        if approach or entity:
            results = ne.query(approach=approach, entity=entity)
        else:
            results = ne.recent(50)
        total = ne.count()
        return {"negations": results, "total": total}
    except Exception as e:
        return {"negations": [], "total": 0, "error": str(e)}


@app.get("/api/causal/trace/{turn}")
def api_causal_trace(turn: int):
    """Trace cause chain backwards from turn."""
    try:
        from mycelium_causal import CausalExtractor
        ce = CausalExtractor()
        chain = ce.trace_cause(turn)
        edges = ce.get_chain(chain) if len(chain) > 1 else []
        ce.close()
        return {"start_turn": turn, "chain": chain, "edges": edges}
    except Exception as e:
        return {"start_turn": turn, "chain": [], "edges": [], "error": str(e)}


@app.get("/api/causal/regressions")
def api_causal_regressions():
    """List all regressions."""
    try:
        from mycelium_causal import CausalExtractor
        ce = CausalExtractor()
        regs = ce.regressions()
        ce.close()
        return {"regressions": regs}
    except Exception as e:
        return {"regressions": [], "error": str(e)}


@app.get("/api/bloom/check/{entity}")
def api_bloom_check(entity: str):
    """Check entity membership in bloom filter."""
    try:
        from mycelium_bloom import MyceliumBloom, MYCELIUM
        try:
            bloom = MyceliumBloom.load(MYCELIUM / ".bloom_entities", name="entities")
        except FileNotFoundError:
            bloom = MyceliumBloom.load_from_db(name="entities")
        found = bloom.check(entity)
        return {"entity": entity, "found": found, "hint": "possible (bloom filter)" if found else "definitely not present"}
    except Exception as e:
        return {"entity": entity, "found": False, "error": str(e)}


@app.get("/api/bloom/stats")
def api_bloom_stats():
    """Bloom filter statistics."""
    try:
        from mycelium_bloom import MyceliumBloom
        bloom = MyceliumBloom.load_from_db(name="entities")
        return bloom.stats()
    except Exception as e:
        return {"error": str(e), "hint": "Run: python3 scripts/mycelium_bloom.py build"}


@app.get("/api/attention/top")
def api_attention_top(limit: int = 15):
    """Most-attended entries."""
    try:
        from mycelium_attention import AttentionTracker
        t = AttentionTracker()
        entries = t.top_entries(limit)
        stats = t.stats()
        t.close()
        return {"entries": entries, "stats": stats}
    except Exception as e:
        return {"entries": [], "stats": {}, "error": str(e)}


@app.get("/api/attention/stale")
def api_attention_stale(limit: int = 15):
    """Never-referenced entries."""
    try:
        from mycelium_attention import AttentionTracker
        t = AttentionTracker()
        entries = t.stale_entries(limit)
        t.close()
        return {"entries": entries}
    except Exception as e:
        return {"entries": [], "error": str(e)}


@app.get("/api/lsm/stats")
def api_lsm_stats():
    """LSM layer stats."""
    try:
        from mycelium_lsm import MyceliumLSM
        lsm = MyceliumLSM()
        return lsm.stats()
    except Exception as e:
        return {"error": str(e)}


@app.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

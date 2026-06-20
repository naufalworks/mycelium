from __future__ import annotations

import json, sys
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
from .services.memory_service import (
    handle_stats as memory_stats,
    handle_facts as memory_facts,
    handle_recall as memory_recall,
    handle_patterns as memory_patterns,
    handle_snapshots as memory_snapshots,
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
FRONTEND_SRC = Path(__file__).resolve().parents[1] / "frontend"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
if FRONTEND_SRC.exists():
    app.mount("/src", StaticFiles(directory=FRONTEND_SRC / "src"), name="src")


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


# ── Semantic Memory API ────────────────────────────────────

@app.get("/api/memory/stats")
def api_memory_stats():
    try:
        return memory_stats({})
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/memory/facts")
def api_memory_facts(type: str = "", limit: int = 30):
    try:
        return memory_facts({"type": type, "limit": limit})
    except Exception as e:
        return {"facts": [], "error": str(e)}

@app.get("/api/memory/recall")
def api_memory_recall(q: str = ""):
    try:
        return memory_recall({"q": q})
    except Exception as e:
        return {"facts": [], "error": str(e)}

@app.get("/api/memory/patterns")
def api_memory_patterns():
    try:
        return memory_patterns({})
    except Exception as e:
        return {"patterns": [], "error": str(e)}

@app.get("/api/memory/snapshots")
def api_memory_snapshots():
    try:
        return memory_snapshots({})
    except Exception as e:
        return {"snapshots": [], "error": str(e)}

@app.get("/api/memory/infer")
def api_memory_infer():
    """Cross-session pattern inference — cached per call."""
    try:
        from mycelium_inference import infer_patterns
        insights = infer_patterns()
        return insights
    except Exception as e:
        return {"error": str(e), "note": "inference unavailable"}


@app.post("/api/memory/extract")
def api_memory_extract(payload: dict):
    """Hippocampus: real-time fact extraction from a single exchange.
    Called by Meshgate after each response. Non-blocking.

    Payload: {"user": "...", "assistant": "...", "session": "..."}
    """
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


# ── Reader API ────────────────────────────────────────────


@app.get("/api/reader/fetch")
def api_reader_fetch(url: str = ""):
    """Fetch and extract clean content from a URL.
    Calls the Go reader tool (compiled into mycelium-proxy).
    Falls back to basic requests + readability if Go endpoint unavailable.
    """
    if not url:
        return {"error": "url parameter required"}

    try:
        # Try Go reader endpoint first (runs on :8443)
        import urllib.request, json
        req = urllib.request.Request(
            f"http://127.0.0.1:8443/api/reader/fetch?url={urllib.parse.quote(url)}",
            headers={"User-Agent": "mycelium/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            return data
    except Exception:
        pass

    # Fallback: basic extraction using readability if available
    try:
        import requests
        from readability import Document
        resp = requests.get(url, timeout=15, headers={"User-Agent": "mycelium/1.0"})
        doc = Document(resp.text)
        return {
            "title": doc.title(),
            "content": doc.summary(),
            "url": url,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Prompts API ───────────────────────────────────────────


def _call_go_prompt_endpoint(path: str, data: dict = None):
    """Call the Go prompt endpoint on the mycelium-proxy."""
    import urllib.request, json

    url = f"http://127.0.0.1:8443/api/prompts/{path}"
    if data:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


@app.get("/api/prompts/list")
def api_prompts_list():
    """List compiled prompts."""
    result = _call_go_prompt_endpoint("list")
    if result:
        return result
    return {"prompts": []}


@app.post("/api/prompts/define")
def api_prompts_define(payload: dict):
    """Define a compiled prompt."""
    result = _call_go_prompt_endpoint("define", payload)
    if result:
        return result
    return {"ok": False, "error": "prompt service unavailable"}


@app.post("/api/prompts/run")
def api_prompts_run(payload: dict):
    """Run a compiled prompt."""
    result = _call_go_prompt_endpoint("run", payload)
    if result:
        return result
    return {"error": "prompt service unavailable"}


@app.get("/{full_path:path}")
def frontend_fallback(full_path: str):
    # Serve memory dashboard from source if exists
    memory_html = FRONTEND_SRC / "memory_dashboard.html"
    if full_path == "memory_dashboard.html" and memory_html.exists():
        return FileResponse(memory_html)
    # Serve v3 HTML pages from source if not found in dist
    v3_pages = {"v3_dashboard.html", "v3_graph.html", "v3_negations.html", "v3_causal.html"}
    if full_path in v3_pages:
        src_file = FRONTEND_SRC / full_path
        if src_file.exists():
            return FileResponse(src_file)
    # Serve built frontend
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"ok": False, "message": "frontend build missing", "hint": "run: cd web/frontend && npm run build"}

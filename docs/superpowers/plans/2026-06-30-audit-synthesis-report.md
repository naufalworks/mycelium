# Audit Synthesis: Proxy, Brain, and System Health

**Date:** 2026-06-30
**Source:** Three parallel audits of mycelium-proxy, mycelium-core/storage, and system runtime
**Status:** Cross-referenced and reconciled

---

## 1. What's Working (keep as-is)

### Core infrastructure
| Component | Detail | Observed by |
|-----------|--------|-------------|
| mycelium-daemon (PID 55559) | Running at 4.1 MB RSS, 0% CPU, launchd-managed with KeepAlive | Agent 3 |
| mycelium-server (PID 5711) | Running at 7.3 MB RSS on port 8421 | Agent 3 |
| mycelium-proxy (PID 31944) | Running at 6 MB RSS on port 8443 | Agents 1, 3 |
| SQLite database | `PRAGMA integrity_check` passes; WAL mode functional | Agent 2 |
| tantivy search index | Updating independently (newest segment 2026-06-30 14:06, 3.2 MB) | Agent 2 |
| SSE streaming endpoint | Active in server with broadcast channel capacity 1024 | Agent 3 |

### Data layer
| Component | Detail | Observed by |
|-----------|--------|-------------|
| Entries 1..1866 hash chain | Valid linear chain (prev_hash -> hash pointers match) | Agent 2 |
| Entries 11868..11881 hash chain | Valid linear chain | Agent 2 |
| Brain processing up to turn 11020 | Atoms (452K), edges (1.35M), positions (3.07M) populated | Agent 2 |
| Entity registry | 100 entries with realistic project/file/service entities | Agent 2 |
| Schema v1 | Correctly initialized | Agent 2 |
| Snapshots | 91 safety snapshots created correctly by chain monitor | Agent 2 |
| Chain monitor | Correctly detects broken segments; creates snapshots before repair | Agent 2 |

### Proxy routing
| Component | Detail | Observed by |
|-----------|--------|-------------|
| Routing logic | `/v1/messages` -> intercept_and_forward, `/v1/chat/completions` -> handle_openai, others -> passthrough | Agent 1 |
| Path normalization | Strips duplicate `/v1/` prefix correctly | Agent 1 |
| Upstream connectivity | port 9099 (meshgate) is reachable and responding | Agent 1 |
| Empty request passthrough | Returns upstream's 401 correctly | Agent 1 |
| Request body parsing | User message extraction, session ID extraction work | Agent 1 |
| `handle_openai` | Correctly routes to `extract_openai_response` | Agent 1 |
| Test requests | Reach upstream and return 200 with valid model responses | Agent 1 |

### Launchd / service management
| Component | Detail | Observed by |
|-----------|--------|-------------|
| `com.naufal.mycelium-rust` | PID 55559, running OK with KeepAlive + ThrottleInterval 5s | Agent 3 |

---

## 2. What's Broken — needs fixing

### P0 — System-halting or data-loss

| # | Issue | File/Module | Details | Action |
|---|-------|-------------|---------|--------|
| 1 | **LLMAgent mutex deadlock** | `crates/mycelium-core/src/self_healing/llm_agent.rs:156-165` | `conn_guard` held across `dispatch_tool()` call; tool handler (`verify_hash_chain`) re-locks `self.conn()`. `std::sync::Mutex` is not recursive — same thread deadlocks on itself. Locks all brain processing and HTTP API. | Drop `conn_guard` before calling `dispatch_tool()`, or lock per-tool inside the tool handler. |
| 2 | **Proxy extractor bug: Anthropic responses not parsed** | `crates/mycelium-proxy/src/lib.rs:280` | `intercept_and_forward` calls `extract_openai_response()` for all traffic, but Anthropic-format responses have `content:[{type:text,text:...}]` structure, not `/choices/0/message/content`. Assistant message always empty. | Change to `extract_assistant_response()` for the Anthropic route. |
| 3 | **Conversation logging dead** | `crates/mycelium-proxy/src/lib.rs:283` (via `log_conversation`) | Direct consequence of P0#2: `log_conversation` has early return `if user_msg.is_empty() || assistant_msg.is_empty() { return; }`. Zero entries in `entries` table despite requests. | Fix P0#2 first. Then confirm `entries.turn` increments sequentially. |
| 4 | **Memory injection is a no-op** | `memory_facts` table | Table has exactly 1 row: an e2e test entry. The proxy code for memory injection is correct, but there are no facts to inject. | Either populate `memory_facts` from entity registry (100 entities exist) or implement fact extraction from conversation entries. |

### P1 — Feature-breaking or ongoing damage

| # | Issue | File/Module | Details | Action |
|---|-------|-------------|---------|--------|
| 5 | **9141 broken hash-chain entries** | `entries` table, segment 1867..11867 | All 9141 entries share a SINGLE duplicate `prev_hash` (`433b12ac60da89ef`) — classic batch-import or fan-out corruption. The chain is non-linear but the data itself is intact. | Decide: repair (set correct prev_hash for each entry) or accept as-is. The broken chain blocks LLM-driven repair but does not prevent reading entries. |
| 6 | **Brain daemon frozen** | Brain processing loop | LLMAgent deadlock (P0#1) stalls the entire brain daemon. 5 pending work items (turns 11021-11025). Atoms/edges/positions not decaying. | Fix P0#1 first; brain processing resumes automatically. |
| 7 | **Proxy restart-looping** | mycelium-proxy | 7+ restarts recorded in daemon log. Likely caused by upstream connection failures. | Investigate restart cause. Check if it's the extractor bug causing upstream errors, or an independent issue. |
| 8 | **mycelium-web crate does not compile** | `crates/mycelium-web/src/views/sidebar.rs` | Sidebar uses leptos/leptos_router but Cargo.toml lacks these dependencies. Entire `lib.rs` is a comment stub. | Add leptos deps or strip from workspace. |
| 9 | **`MYCELIUM_LLM_URL` env var misleading** | Configuration | Env var set to `http://localhost:8080/v1/messages` but code hardcodes `format!("{}/v1/messages", config.upstream_url)` using port 9099. The env var has no effect. | Remove or document the env var. |
| 10 | **Skills cortex enabled but empty** | `skills.yaml` | Runtime warning: "Cortex enabled but no skills loaded". Skills matching is on but the YAML file has no entries. | Populate `skills.yaml` or disable cortex skill matching. |

### P2 — Degraded but non-blocking

| # | Issue | File/Module | Details | Action |
|---|-------|-------------|---------|--------|
| 11 | **`context_snippets` table empty** | Database | Write-time snippets path never produces output. No snippets exist despite 11025 entries. | Investigate the write path; likely never calls the insert. |
| 12 | **`artifacts` table empty** | Database | No artifacts recorded despite entries. | Either the artifact extraction path is missing or never triggered. |
| 13 | **MemoryView stub** | `crates/mycelium-web/src/views/memory.rs` | 16-line placeholder with no data fetching. No way to browse entries/facts from the web UI. | Implement real memory view, or a ConversationView/HistoryView. |
| 14 | **`verify_hash_chain` loads ALL entries** | `crates/mycelium-core/src/storage.rs:537-560` | Loads 11K entries into memory, then for each calls `get_entry()` which locks a separate connection. Long critical section; deadlock-prone. | Use a single SQL join query instead. |
| 15 | **Proxy upstream model mismatch** | Configuration | Proxy configured for `minimax-m3` model but env says `nemotron-3-ultra-fp4`. Upstream blocks `gpt-4o-mini`. | Align model names across configs. |

---

## 3. What's Dead Code — remove or archive

| # | Item | Location | Size/State | Action |
|---|------|----------|------------|--------|
| 1 | **`mycelium-web` crate** | `crates/mycelium-web/` | Has sidebar views that don't compile; `lib.rs` is a comment stub. 5 views are dead scaffolding. | Strip from workspace or add leptos deps and repair. |
| 2 | **`leptos_axum` workspace dependency** | `/workspace/Cargo.toml` | Listed as workspace dependency, zero actual use across all crates (only in comments). | Remove from workspace. |
| 3 | **`exproxy/rtk` design spec** | `docs/exproxy/` or similar | 40 KB of design specification with zero Rust code implementing it. | Either implement or archive. |
| 4 | **`mycelium-stream` crate** | `crates/mycelium-stream/` | Full Leptos WASM implementation — orphaned in workspace, not invoked by any binary. | Strip from workspace or connect to a binary. |
| 5 | **`mycelium-mcp` crate** | `crates/mycelium-mcp/` | Compiled binary on disk, never invoked by daemon, server, or proxy. Fully standalone. | Integrate or strip. |
| 6 | **Observatory service** | Launchd plist | Python FastAPI replaced by Rust server months ago. Plist registered but not loaded. | Remove plist. |
| 7 | **`com.naufal.companion` launchd service** | Launchd plist | Exit code 1 (failed to start). Dead registration. | Remove plist. |
| 8 | **`com.naufal.myceliumd` (Python) launchd plist** | Launchd plist | Not loaded, replaced by Rust daemon. | Remove plist. |
| 9 | **Python `myceliumd.py` daemon** | `bin/myceliumd.py` | 165 recurring `ModuleNotFoundError` for missing `'web'` module, 173 `BrokenPipeError`. Replaced by Rust daemon but file not cleaned up. | Remove the file and any references. |

---

## 4. What's Bloat — overengineered for current needs

| # | Item | Problem | Assessment |
|---|------|---------|------------|
| 1 | **Self-healing LLMAgent** | Full LLM-driven chain repair system — but it immediately deadlocks, requires a working proxy (which also has critical bugs), and the hash chain break is structural (9141 entries sharing same prev_hash) that no LLM can fix without manual intervention. The agent cannot run in its current state. | The deadlock fix (P0#1) is required before this can be evaluated. However, consider whether a simpler repair strategy (re-compute prev_hash locally, no LLM) would suffice for the known fan-out corruption. |
| 2 | **90+ database snapshots** | 91 snapshots exist for rollback. The chain monitor creates a snapshot before every repair attempt. With the repair loop retrying and deadlocking, this generates many identical snapshots. | Consider a dedup or max-snapshots policy (keep last N). |
| 3 | **Separate `mycelium-stream`, `mycelium-mcp`, `mycelium-web` crates** | Three crates for frontend/streaming/MCP that are all either uncompilable, orphaned, or standalone. A single web-serving binary could replace all three. | Consolidate or remove. |
| 4 | **58 MB search index** | tantivy index at 58 MB for 11K entries. A simple SQLite FTS5 index would be smaller and simpler. | Evaluate whether the tantivy complexity is justified by search quality gains. |
| 5 | **Complex hash chain verification** | `verify_hash_chain` loads all entries, then does N separate `get_entry()` calls. A single SQL window function (LAG over prev_hash) would be O(N) in the database, not O(N) in the application with 2N lock acquisitions. | Simplify to a SQL query. |

---

## 5. What's Mystery — we don't know if it works

| # | Question | Why unknown | How to resolve |
|---|----------|-------------|----------------|
| 1 | **Has conversation logging ever worked?** | `entries` table is empty. Could be brand new feature or long-broken. No git history of successful logging. | Fix P0#2 (extractor bug), send test request, verify entries table. |
| 2 | **Is upstream meshgate at :9099 the intended LLM target?** | Env var says port 8080, code uses 9099. Comment says LLM_URL is "intentionally ignored." Which port is correct? | Check deployment config, or clarify intent. |
| 3 | **Was `memory_facts` ever populated by real extraction?** | Only 1 row exists (an e2e test). The entity registry has 100 entries with real-looking data, but none promoted to `memory_facts`. | Check entity-to-fact promotion code path. |
| 4 | **Does memory injection work when facts exist?** | Code path for injection is correct, but `memory_facts` is empty so injection never fires. | Seed a fact, send a matching query, confirm `<mycelium-facts>` block appears in forwarded body. |
| 5 | **Does `context_snippets` generation work?** | Table is empty. The write-time path may have a bug or may never have been triggered. | Add test coverage to the snippets write path. |
| 6 | **Does artifact generation work?** | `artifacts` table is empty. Unclear if the feature is alpha, incomplete, or broken. | Check if artifact extraction code exists and is wired up. |
| 7 | **Why did the hash chain break at turn 1867?** | 9141 entries share the same `prev_hash` — classic fan-out corruption. Cause unknown (batch import? bug in writer?). | Review git history around the time turn 1867 was written. The entry data is intact; only the chain pointers are wrong. |
| 8 | **Is the Vite dev server (port 5174) serving the real UI?** | Agent 3 mentions React frontend via Vite on port 5174, but the mycelium-server also serves on 8421. Are these the same UI or different? | Confirm UI deployment model. |
| 9 | **Does Cortex recall mode work end-to-end?** | Mode is active (GraphTraversal), model claude-sonnet-4-20250514 is configured, but the proxy has extractor bugs and `context_snippets` is empty. | Fix P0#2 first, then test recall with a real query. |

---

## Root-Cause Dependency Map

```
P0#1: LLMAgent deadlock
  └─ blocks ╶→ P1#6: Brain daemon frozen
              └─ blocks ╶→ P1#1: Cannot repair broken hash chain
              
P0#2: Proxy extractor bug (wrong function called)
  └─ causes  ╶→ P0#3: Conversation logging dead
              ╶→ P0#4: Memory injection is no-op (user msg never logged, no entry to cross-reference)
              ╶→ P2#1: context_snippets empty (no entry to snippet-ize)
              ╶→ P1#7: Proxy restart-looping (maybe — needs confirmation)
```

**Fix order:** P0#1 first (unblocks the brain), then P0#2 (unblocks conversation logging, memory, snippets).

---

## Stats Summary

| Metric | Value |
|--------|-------|
| Database entries | 11,025 (turns 1..11881, with gaps at broken segment) |
| Broken chain entries | 9,141 (segment 1867..11867) |
| Intact chain entries | ~1,884 (segments 1..1866 + 11868..11881) |
| Atoms | 452,471 |
| Edges | 1,351,588 |
| Positions | 3,067,915 |
| Entity registry | 100 |
| Memory facts | 1 (e2e test only) |
| Context snippets | 0 |
| Artifacts | 0 |
| Pending brain work | 5 (turns 11021-11025) |
| Snapshots | 91 |
| Dead crates to remove | 3-4 (mycelium-web, mycelium-stream, mycelium-mcp, possibly leptos_axum) |
| Dead launchd plists | 3 (companion, python daemon, observatory) |
| Dead Python files | 1 (myceliumd.py) |
| Dead design specs | 1 (exproxy/rtk) |

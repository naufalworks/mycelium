# Mycelium — Feature Inventory & Architecture

**Date:** 2026-06-30
**Status:** Living Document

## Running Services

```
meshgate     8080  Go upstream proxy → Kimchi API
mycelium-server 8421  Rust API server + Svelte frontend
mycelium-proxy  8443  Rust memory proxy (intercepts LLM calls)
mycelium daemon —    Process manager (launchd-controlled)
```

## Crate Structure

```
mycelium-core/     Core types, storage, brain, hot_graph, recall, self_healing
mycelium-server/   REST API + server-side SSE stream + static file serving
mycelium-proxy/    LLM proxy: memory injection, conversation logging
mycelium-app/      CLI (daemon, status, brain, search, recall commands)
mycelium-mcp/      MCP protocol server
mycelium-web/      Web frontend scaffold (Leptos — placeholder)
mycelium-stream/   WebSocket streaming canvas
```

---

## Feature List

### 1. Memory Proxy (Port 8443) — ACTIVE

**What it does:** Intercepts Anthropic `/v1/messages` and OpenAI `/v1/chat/completions`, searches SQLite for matching memory facts, injects as system context, logs the conversation to the database.

**Implementation:** `crates/mycelium-proxy/src/lib.rs` → `intercept_and_forward()`, `handle_openai()`

**Status:** ✅ Working (stripped: no LLM calls, pure SQLite search_facts)
**Memory injection:** ✅ `search_facts()` → `build_facts_block()` → inject into `"system"` field
**Conversation logging:** ✅ `log_conversation()` → writes to `entries` + `pending_brain_work`
**Model fallback:** No — delegated to meshgate upstream
**Format translation:** No — delegated to meshgate upstream
**Cache:** No

### 2. REST API (Port 8421) — ACTIVE

**What it does:** Serves the web UI and API for querying/deleting memory. 18 routes total.

**Routes:**

| Endpoint | Method | What |
|---|---|---|
| `/api/health` | GET | Basic health check |
| `/api/status` | GET | Full brain stats (atoms, edges, entries, sessions) |
| `/api/config` | GET | Server configuration |
| `/api/stream` | GET | SSE event stream (live broadcast) |
| `/api/sessions` | GET | List all sessions |
| `/api/sessions/{name}` | GET | Get session detail |
| `/api/entries` | GET | List entries |
| `/api/entries/{turn}` | GET | Get entry |
| `/api/memory/facts` | GET/POST | List/create memory facts |
| `/api/memory/facts/{id}` | DELETE | Delete fact |
| `/api/memory/snapshots` | GET/POST | List/create snapshots |
| `/api/memory/snapshots/{id}` | DELETE | Delete snapshot |
| `/api/daemon` | GET | Daemon status |
| `/api/artifacts` | GET | List artifacts |
| `/api/search` | GET | Full-text search |
| `/api/backups` | GET/POST | List/create backups |
| `/api/verify` | GET | Hash chain verification |
| `/api/brain/status` | GET | Atom/edge/position/pending counts |
| `/api/brain/atoms` | GET | Atom list |
| `/api/brain/heat` | GET | HotGraph metrics + top atoms |

**Status:** ✅ All active
**SSE stream:** ✅ `broadcast::Sender<String>` — used for live events. Currently unused by frontend.

### 3. Brain Daemon (In-process, Server-side) — ACTIVE

**What it does:** Background task that processes `pending_brain_work` entries, consolidates them into atoms, edges, and positions in the atom graph.

**Trigger:** `tokio::sync::Notify` wake signal (sent after every `write_entry()` call)
**IDLE:** 60-second safety timeout if no notify fires
**Batch size:** 20 entries per cycle
**Effect:** Extracts atom phrases → upserts into `atoms` table → records positions → builds adjacency edges → seeds HotGraph

**Status:** ✅ Working
**Edge building:** ✅ W=1 direct + W=2 local + W=2.5 bridge edges
**Stop words:** ✅ 20 seeded, additional auto-detection
**Consolidated:** ~450k atoms, ~1.34M edges in database

### 4. HotGraph (In-Memory Heat Cache) — ACTIVE

**What it does:** In-memory heat-governed atom cache. Atoms with `heat > EVICT_THRESHOLD` live in a `HashMap`. Heat spreads along edges when atoms are accessed. Cold atoms evict to SQLite.

**Constants:** `DECAY_RATE=0.95`, `SPREAD_FACTOR=0.5`, `EVICT_THRESHOLD=0.1`, `PROMOTE_THRESHOLD=0.3`

**Operations:** `seed()` → new atoms from consolidation, `bump()` → query match, `tick_decay()` → spread+decay+evict every ~60s, `get()` → L1 lookup

**Status:** ✅ Working
**Promotion counter:** ✅ Fixed (was 0, now tracks correctly)
**Spread formula:** ✅ Fixed (importance-based, not heat-based — prevents exponential growth)
**Debug endpoint:** ✅ `/api/brain/heat`

### 5. Recall (Graph Traversal) — ACTIVE

**What it does:** Takes query atoms, traverses the atom graph to find related clusters, returns ranked memory positions.

**Implementation:** `crates/mycelium-core/src/recall.rs` → `traverse()`

**Integration:** Called from `proxy` during memory injection (stripped: no LLM query parser, uses raw user message)

**Status:** ✅ Working (simplified)
**Heat bump:** ✅ `bump()` on matched atoms in `traverse()`
**Max clusters:** 5, max neighbors: 5

### 6. Self-Healing Chain Repair (In-memory, LLM-driven) — BUILT, NOT ACTIVE

**What it does:** Detects broken hash chains via `verify_hash_chain()`, spawns a constrained LLM agent (kimi-k2.7 / minimax-m3) that repairs `prev_hash`/`hash` fields, writes git-trackable audit files.

**Modules:** `crates/mycelium-core/src/self_healing/` — 8 files:
- `mod.rs` — public API
- `chain_monitor.rs` — detects new breaks
- `safety.rs` — snapshot, rollback, whitelist
- `llm_provider.rs` — kimi/minimax client + circuit breaker
- `llm_agent.rs` — repair agent loop
- `tools.rs` — 6 LLM tools (read+write)
- `audit.rs` — bugfixes/ writer
- `policy.rs` — policy.md/safety.md loader

**Wired into:** Brain daemon decay cycle (auto-detects on tick, spawns repair)

**Status:** ⚠️ Built, tests pass (20 integration tests). Not triggered in production yet.
**Reason:** Kimchi API returns empty content for the LLM agent (needs working upstream).

### 7. Connection Pooling — ACTIVE

**What it does:** Two SQLite connections instead of single `Mutex<Connection>`:
- `write_conn: tokio::sync::Mutex<Connection>` — serialized writes
- `read_conn: parking_lot::Mutex<Connection>` — dedicated read connection

**Status:** ✅ Working
**WAL mode:** ✅ Enabled (concurrent readers + single writer)
**get_entry hot path:** ✅ Uses `read_conn`, cache-bypassing reads don't block writes

### 8. Event-Driven Notify (Brain Daemon Wake) — ACTIVE

**What it does:** `tokio::sync::Notify` signal after `write_entry()` → brain daemon wakes immediately instead of 5-second poll.

**Status:** ✅ Working (was the first Approach A fix)
**60s safety timeout:** ✅ Catches dangling entries

### 9. Daemon Health Monitoring — ACTIVE

**What it does:** `mycelium daemon` manages server + proxy as child processes. Uses `tokio::process::Child::wait()` in a `tokio::select!` loop for event-driven health monitoring.

**Restart strategy:** Circuit breaker (1s min interval, 10-failure cap)

**Status:** ✅ Working (was the Approach A daemon fix)

### 10. Web UI (Port 8421 Static Serve) — ACTIVE

**What it does:** Svelte frontend at `web/frontend/dist/` served by `mycelium-server` via `ServeDir` fallback.

**Status:** ✅ Working (accessed at http://127.0.0.1:8421/)
**SSE feed ready:** ✅ `/api/stream` broadcasts events — not yet consumed by UI

### 11. Model Fallback — DELEGATED TO MESHGATE

**What it does:** When kimi-k2.7 returns empty content, try minimax-m3, then kimi-k2.6.

**Implementation:** Was in exproxy (removed). Now delegated to meshgate's smart routing.

**Status:** ⚠️ Relies on meshgate's internal routing. Kimchi API credits/availability determine actual failover behavior.

### 12. Format Translation (Anthropic ↔ OpenAI) — ACTIVE IN PROXY

**What it does:** Converts Anthropic `/v1/messages` format to OpenAI `/v1/chat/completions` for meshgate, converts responses back.

**Implementation:** `crates/mycelium-proxy/src/proxy.rs` → intercept, convert messages, forward, convert response

**Status:** ✅ Working (stripped down, basic text + tool blocks)

### 13. Cache (moka, entry-level) — ACTIVE

**What it does:** In-memory TTL cache for entries (5 min), memory facts (2 min), session context (30s), artifacts (60s).

**Status:** ✅ Working (unchanged)

### 14. Hash Chain (Tamper-Evident Log) — ACTIVE

**What it does:** Each entry links to predecessor via `prev_hash`. SHA-256 signed. `mycelium verify` checks integrity.

**Status:** ⚠️ 83% of current entries have broken chains (pre-existing migration bug). Self-healing repair built but not triggered.

### 15. HotGraph metrics (when server restarted fresh)

**Status:** ✅ Showing correct values: hot_count, promotions, evictions, etc.

---

## Potential Dead Code / Low-Value Code

| Feature | Status | Notes |
|---|---|---|
| `cortex` module | Not compiled (removed from imports) | Was query parser enhancement |
| `context_synthesizer` | Not compiled | Was LLM synthesis |
| `run_recall_pipeline` | Removed | Was 3-stage recall (parser → traverse → synthesis) |
| `process_openai` memory injection | Skipped for Kimchi models | Now empty context → no injection |
| Self-healing agent | Built, not active | Needs working LLM upstream |
| mycelium-mcp crate | Exists | Status unknown, untested |
| mycelium-stream crate | Exists | Status unknown, untested |

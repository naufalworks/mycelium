# Mycelium Changelog

## 2026-06-23

### 🛡️ Stability & Reliability
- **Marked `resp.Body.Close` as `defer`** in `hippocampusExtract` — prevents Goroutine leak when the response body is not closed on early return
- **Added panic recovery** to `hippocampusExtract` goroutine — background fact extraction no longer crashes the entire proxy on unexpected errors
- **Checked `os.MkdirAll` error** in `brain.New` — returns a proper error instead of silently ignoring directory creation failure
- **Propagated request context** to upstream calls — client disconnects now cancel the upstream request, freeing resources early
- **Added hippocampus worker semaphore** (max 5 concurrent extractors, drops when saturated) — prevents goroutine pile-up under heavy proxy load
- **Made SQLite connections persistent** in brain (`indexDB()`) and cache (`db()`) — lazy singleton via `sync.Once` instead of open/close per call, reducing GC pressure under load

## 2026-06-22

### 🔧 Proxy Reliability Fix
- Fixed **528% CPU spike** caused by connection leaks — added connection pooling (`MaxIdleConns: 50`, `IdleConnTimeout: 90s`), server timeouts (`ReadTimeout`, `WriteTimeout`, `IdleTimeout`), and concurrency limiter (max 20 parallel requests)
- Added **launchd auto-start** with `KeepAlive` — proxy auto-restarts on crash and boots on login
- Added panic recovery and rate limiting (returns 429 on overload)

### 🧠 Workflow Engine
- **Real execution** — workflow `run` endpoint actually executes steps in background, stores state in `workflow_runs` table
- **Live progress** — CLI shows live spinner with per-step checkmarks, durations, and stdout/stderr
- **Go engine** — `ProgressCallback`, step timeout, duration tracking
- **Web UI** — new Workflows tab with Library, Runs, and Run Detail views

---

## 2026-06-21

### 🧠 Workflow System
- **Go workflow engine** — define structured multi-step workflows with verification criteria, stopping conditions, and artifact-backed audit trail
- **Async task queue** — background LLM task processing with status polling
- **Speculative cache** — predicts next questions, pre-computes answers, serves instantly on hit

### 📄 Documentation
- Complete rewrite of `README.md` and `AGENTS.md`
- Fixed LSM zstd compression error
- Fixed speculative cache auto-trigger wiring

---

## 2026-06-20

### 🧠 Memory Layer (Semantic)
- **Semantic memory** — entropy-weighted fact storage with LLM-based extraction
- **Continuous decay model** — facts degrade over time unless reinforced
- **Cross-session inference** — LLM reads all snapshots, discovers patterns & gaps
- **Event-based snapshot trigger** + backlog processor for missed turns
- **Hippocampus + Anti-Memory** — real-time fact extraction from every exchange, verified facts injected into context (integrated with Meshgate)

### 🎨 Artifact Layer
- **Go artifact storage** — `go/pkg/artifacts` with proxy interceptor
- **Artifact dashboard** + API routes
- **MCP artifact tools** — `artifact_run`, `artifact_get`, `artifact_query`, `artifact_ls`

### 🌐 Web Frontend
- **Complete redesign** — dark theme, settings tab with MCP setup + proxy activation guide
- **Meaningful graph view** — session→entity relationship map
- **Fixed scrollable graph**, session list truncation

### 🔧 Proxy & Reader
- **Go Reader + Prompts packages** in mycelium-proxy
- Proxy intercepts **all API paths**, supports configurable upstream (meshgate)
- **SSE parser** supports both Anthropic and OpenAI streaming formats

### 🔧 Daemon
- **Split-brain fix** — replaced paths_service with Go daemon `/health` probe
- **Session-start context loader** with dedup, FTS search, MCP store tool

---

## 2026-06-19

### 🚀 Platform Migration
- **Full Go migration** of core hot path (brain, proxy, MCP, daemon)
- **Unified CLI** (`mycelium` binary) — status, verify, search, resume, precheck, backup/restore
- **Daemon** — safety-net importer from Hermes state.db with health endpoint
- **Backup/restore system** with archive management
- **Zero-to-hero install** — `make install-daemon` sets up everything
- **MCP server** — Claude Desktop integration for querying mycelium

### 🧩 Evolution Engine
- In-process self-patching system
- Pattern detection, bloom filters, causal tracing


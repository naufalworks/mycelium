# 🍄 Mycelium — Permanent Memory for AI

A permanent, structured memory system for AI conversations. Three memory tiers + predictive caching + async task processing.

## Architecture

```
mycelium/
├── log.jsonl                        ← The raw brain (append-only, never deleted)
├── index.db                         ← SQLite: facts, artifacts, prompts, tasks, cache
├── scripts/                         ← Python CLI + services
├── go/pkg/{brain,artifacts,cache,
│           prompts,reader,tasks}/   ← Go packages (hot path)
├── web/backend/                     ← FastAPI web backend (:8421)
├── web/frontend/                    ← Dashboards + React SPA
└── go/cmd/{proxy,mcp,mycelium}/    ← Go binaries
```

## Three Memory Tiers

| Tier | Size | Examples | Access |
|---|---|---|---|
| **memory_facts** | Tiny | `metabase.api_key = mb_xxx`, `postgres.version = 16` | `mycelium recall`, MCP `search` |
| **artifacts** | Large | Expense reports, generated files, SQL results | `mycelium artifact query`, MCP `artifact_get` |
| **prompts** | Templates | `extract-invoice`, `summarize-report` | `mycelium prompt define`, MCP `artifact_run` |

## Predictive Features

| Feature | What | CLI Command |
|---|---|---|
| **Async Tasks** | Queue long-running LLM operations, poll results later | `mycelium task` |
| **Speculative Cache** | Predicts next questions, pre-computes answers, serves instantly on hit | `mycelium cache` |
| **Hippocampus** | Real-time fact extraction from every exchange (via proxy) | Automatic via Meshgate |
| **Anti-Memory** | Injects verified facts into context before each LLM call | Automatic via Meshgate |
| **Cross-Session Inference** | LLM reads all snapshots → discovers patterns & gaps | `mycelium infer` |

## CLI Commands

```
  status              Brain stats
  resume              Smart session resume
  verify              Integrity chain check
  search <query>      Search log + index
  fact                Manage memory facts (list, add, search, stats)
  recall <question>   Semantic recall via natural language
  snapshot            Create context snapshot of last session (LLM-powered)
  context             Show last session context + hot facts
  compact             Entropy-weighted memory compaction
  infer               Cross-session pattern inference
  read <url>          Fetch and extract clean content from URL
  prompt              Manage compiled prompts (define, list, run)
  artifact            Manage structured artifacts (run, get, query, ls)
  task                Manage async tasks (create, status, list)
  cache               Manage speculative cache (stats, clear)
  workflow            Define, run, and track structured workflows
```

## MCP Tools (available to Claude Code)

| Tool | What it does |
|---|---|
| `search` | Search permanent memory for past findings |
| `recall` | Smart session recall with context |
| `store` | Store a new entry in permanent memory |
| `artifact_run` | Execute a compiled prompt, store result as artifact |
| `artifact_get` | Retrieve a stored artifact by ID |
| `artifact_query` | Run SQL SELECT over stored artifacts |
| `artifact_ls` | List stored artifacts by type |

## Architecture Details

### Memory Decay Model
Facts decay by an entropy-weighted exponential function:
```
R = confidence × e^(-λ × days) × (1 + entropy × η)
```
High-entropy (surprising) facts survive longer. Low-entropy (mundane) facts decay faster.

### Event-Based Snapshot
No cron. Every `mycelium append` triggers a non-blocking check: if enough new entries accumulated, snapshot + compact automatically.

### Proxy Flow
```
Claude Code → :8443 (mycelium-proxy) → :8080 (meshgate) → upstream LLM
```
Proxy handles: Anti-Memory injection, Hippocampus extraction, Reader tool, Cache lookup.

## Dashboards

| URL | What |
|---|---|
| `/` | React SPA (main dashboard) |
| `/memory_dashboard.html` | Facts, credentials, decisions, patterns |
| `/artifact_dashboard.html` | Artifacts, SQL query runner |
| `/v3_dashboard.html` | Legacy v3 views |

## Quick Start

```bash
mycelium status                   # Brain stats
mycelium verify                   # Integrity check
mycelium recall "api key"         # Semantic recall (fast)
mycelium snapshot                 # Capture last session
mycelium context                  # Show last context
mycelium infer                    # Cross-session patterns
mycelium read https://example.com # Fetch clean content
```

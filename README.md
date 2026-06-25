# 🍄 Mycelium

**Permanent memory for AI agents.** Rust rewrite, replacing the original Go+Python system.

## Architecture

```
mycelium daemon  (background supervisor, auto-restart on crash)
  ├── server   :8421  →  REST API (Axum)
  └── proxy    :8443  →  LLM API proxy with memory injection
       │
       └── upstream →  Meshgate / Anthropic API
```

## Quick Start

```bash
# Build
cargo build --release

# Migrate existing data from old log.jsonl
./target/release/mycelium migrate

# Start daemon (background with auto-restart)
./target/release/mycelium daemon-start

# Check status
./target/release/mycelium daemon-status

# Install for auto-start on boot (macOS launchd)
./target/release/mycelium daemon-install
```

## CLI

```
status       Brain stats (entries, sessions, tiers)
search <q>   Full-text search across memory
verify       Hash chain integrity check
resume       Recent context for session
fact         Memory fact CRUD (list/search/add/delete)
snapshot     Context snapshots (list/create)
backup       Full backup (tar.gz)
migrate      Import old log.jsonl → mycelium.db
precheck     Health checks
start/stop   Start/stop server + proxy
daemon       Background supervisor with auto-restart
daemon-install  launchd auto-start on boot
```

## API (`:8421`)

`/api/health` `/api/status` `/api/search?q=` `/api/sessions`
`/api/memory/facts` `/api/memory/snapshots`
`/api/daemon` `/api/verify` `/api/config`

## Proxy (`:8443`)

Intercepts `/v1/messages` (Anthropic) and `/v1/chat/completions` (OpenAI) to:
1. Inject memory context from the knowledge graph
2. Log conversations with SHA-256 hash chain
3. A1/A2 session context loading + dedup

## Graph-Guided Recall

The proxy uses a **graph traversal engine** over the Hebbian Crystal Brain for memory recall instead of traditional text search.

**How it works:**

```
natural question → query parser (LLM, ~200 tokens)
                → atoms (e.g. "proxy", "change secret")
                → graph traversal (SQL on atoms/edges tables, sub-ms)
                → context synthesis (LLM or template)
                → <mycelium-context> block injected into system prompt
```

The brain stores every conversation as **atoms** (important phrases) connected by **weighted edges** (co-occurrence). Recall traverses these edges to find related memories — no embedding search, no raw text scanning.

**Three recall layers:**

| Layer | Description | Tokens | Latency |
|-------|-------------|--------|---------|
| Query Parser | Decomposes your question into atom phrases + intent | ~200 | ~300ms |
| Graph Traversal | Pure SQL on indexed brain tables | 0 | <1ms |
| Context Synthesis | Builds `<mycelium-context>` block | 0–20K | ~500ms |

**Two modes** (env var `MYCELIUM_RECALL_MODE`):

| Mode | Description |
|------|-------------|
| `graph` (default) | Brain graph traversal — the new recall system |
| `legacy` | Old `search_facts` SQL LIKE query (deprecated) |

**Configuration (environment variables):**

| Variable | Default | Description |
|----------|---------|-------------|
| `MYCELIUM_RECALL_MODE` | `graph` | `graph` or `legacy` |
| `MYCELIUM_MODEL` | `claude-sonnet-4-20250514` | Model for query parser + synthesizer |
| `MYCELIUM_LLM_URL` | `{upstream_url}/v1/messages` | API endpoint for recall LLM calls |
| `MYCELIUM_UPSTREAM_API_KEY` | `""` | API key for upstream LLM |

**Debugging** — set `RUST_LOG=debug` to see recall pipeline traces:

```
🧠 Recall pipeline: processing "what did we do with the proxy?"
  Query parser: 2 atoms, intent=Relational
  Traversal: 5 clusters in 443ms
  ✅ Recall context generated in 1200ms (LLM synthesis)
```

**Logs:** Proxy logs at `$MYCELIUM_ROOT/daemon/proxy.log`.

## Data

Single `mycelium.db` (SQLite, WAL mode) replacing old `log.jsonl` + `index.db` split.

## Project Structure

```
crates/
  mycelium-core/     Storage engine, cache, tantivy search, types
  mycelium-app/      CLI binary (clap, 16+ commands)
  mycelium-server/   REST API server (Axum)
  mycelium-proxy/    LLM API proxy (Axum)
  mycelium-web/      Leptos frontend (scaffold)
legacy/
  go/                Old Go packages (archived)
  scripts/           Old Python scripts (archived)
  python-web-backend/ Old FastAPI backend (archived)
web/frontend/        Legacy React SPA
```

## Development

```bash
cargo build --release   # Build all
cargo test             # Run tests
./target/release/mycelium <command>
```

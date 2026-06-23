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

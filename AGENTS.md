# Mycelium — AI Agent Instructions

Mycelium is a permanent memory system with three tiers: facts (tiny), artifacts (structured), prompts (templates). It also has predictive caching, async task processing, and a web dashboard.

## Available MCP Tools

| Tool | When to use |
|---|---|
| `search` | User asks about past conversations, decisions, or findings |
| `recall` | User needs context from a previous session |
| `store` | User says something important to remember permanently |
| `artifact_run` | User says "run prompt X" — executes a compiled prompt, stores result |
| `artifact_get` | User asks for a stored artifact by ID |
| `artifact_query` | User asks about aggregated data (totals, counts, trends) |
| `artifact_ls` | User asks what artifacts exist |

## CLI Commands Reference

```bash
# Memory
mycelium recall "what is the api key"   # Instant semantic recall
mycelium fact list --type credential     # Credential grid
mycelium fact list --type decision       # Decision log
mycelium fact list --type idea           # Idea board
mycelium context                         # Last session context

# Prompts
mycelium prompt define extract-invoice --template "..." --output-schema "{...}"
mycelium prompt run extract-invoice '{"text":"..."}'

# Artifacts
mycelium artifact query "SELECT type, COUNT(*) FROM artifacts GROUP BY type"

# Tasks (async)
mycelium task list --status pending
mycelium task status task_abc123

# Cache (predictive)
mycelium cache stats

# Analysis
mycelium infer                            # Cross-session pattern inference
mycelium compact                          # Entropy-weighted compaction
mycelium read https://example.com         # Fetch clean content
```

## Infrastructure

| Service | Port | Binary | What |
|---|---|---|---|
| Web backend | 8421 | — | FastAPI + dashboards |
| LLM proxy | 8443 | `mycelium-proxy` | Anti-Memory, Hippocampus, Cache |
| Meshgate | 8080 | `meshgate` | Upstream routing |
| MCP | stdio | `mycelium-mcp` | Tools for Claude Code |
| Daemon | 20151 | `myceliumd` | Background health |

## Three Memory Tiers

1. **memory_facts** — tiny key-value facts (credentials, decisions, preferences). Fast recall, SQL-backed.
2. **artifacts** — large structured outputs (expense reports, generated files). SQL-queryable.
3. **prompts** — versioned templates with input/output schema validation. Produces artifacts.

## Key Principles

- Raw brain (`log.jsonl`) is append-only and never modified.
- All derived data (facts, artifacts, snapshots) is stored in `index.db`.
- Memory decays by entropy-weight: surprising facts survive, mundane facts fade.
- Snapshot is event-based (triggered by append), not cron-based.
- Proxy intercepts every LLM call for context injection and fact extraction.

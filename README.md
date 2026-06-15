# 🍄 Mycelium — Your Persistent Brain v3

Every conversation, every finding, every idea — saved forever with integrity chain,
importance tiers, entity indexing, and LSM-tree storage. Never loses data. Never deleted — only archived.
Any AI agent opens the repo and knows exactly what to do (see `AGENTS.md`).

**v3 upgrade: LSM-tree storage, 85x faster resume, Bloom filter, entity graph, negation index, causal chains, attention decay.**

## Architecture

```
mycelium/
├── README.md                ← this file
├── AGENTS.md                ← AI agent instructions
├── log.jsonl                ← the brain (flat JSONL, backward compatible)
├── index.db                 ← SQLite index (entities, findings, edges, etc.)
├── BENCHMARK_V3.md          ← performance benchmarks
├── archive/                 ← backups (never deleted)
├── l1/                      ← v3: LSM compressed segments (gzip)
├── l2/                      ← v3: LSM summaries (gzip)
├── snapshots/               ← v3: COW session snapshots
├── objects/                 ← v3: content-addressed object store
├── scripts/
│   ├── mycelium_lib.py      ← shared library (paths, entities, tier, hash, index)
│   ├── mycelium.py          ← CLI (status, resume, search, verify, archive, compact)
│   ├── append.py            ← single-turn append (incremental index, always-on evolution)
│   ├── myceliumd.py         ← safety-net daemon (watches Hermes state.db)
│   ├── precheck.py          ← pre-flight health gate (mandatory)
│   ├── mycelium-start       ← session-start wrapper
│   │
│   │  ── v3 Core ──
│   ├── mycelium_lsm.py        ← LSM-tree memory (L0 hot/L1 warm/L2 cold)
│   ├── mycelium_bloom.py      ← Bloom filter (O(1) entity membership)
│   ├── mycelium_graph.py      ← Entity relationship graph (5 edge types)
│   ├── mycelium_negation.py   ← Negation index (what doesn't work)
│   ├── mycelium_causal.py     ← Causal chain DAG (cause→effect tracking)
│   ├── mycelium_attention.py  ← Attention decay (exponential, 14d half-life)
│   ├── mycelium_resume_v3.py  ← V3 resume (LSM + Bloom + Graph + Negation)
│   ├── mycelium_compact.py    ← Condition-based compaction (NOT cron)
│   ├── mycelium_snapshot.py   ← COW snapshots + delta compression
│   ├── object_store.py        ← Content-addressed dedup store
│   ├── zstd_compress.py       ← Zstd trained dictionary compression
│   ├── migrate_v3.py          ← Migration tool (flat → LSM, backup/rollback)
│   ├── benchmark_v3.py        ← Benchmark suite
│   ├── benchmark_report.py    ← Benchmark report generator
│   │
│   │  ── v2 Features ──
│   ├── evolution.py         ← Self-Evolution Engine
│   ├── detect-patterns.py   ← Skill Garden
│   ├── branch.py            ← Conversation Tree
│   └── findings.py          ← Vuln Hunter Notebook
│
├── web/
│   ├── backend/app.py       ← FastAPI backend (26 + 10 v3 endpoints)
│   └── frontend/
│       ├── v3_dashboard.html   ← v3 overview dashboard
│       ├── v3_graph.html       ← Entity graph viewer
│       ├── v3_negations.html   ← Negation feed
│       └── v3_causal.html      ← Causal chain viewer
│
├── tests/                   ← 228 tests
│   ├── test_lsm.py          ← 25 tests
│   ├── test_bloom.py        ← 16 tests
│   ├── test_negation.py     ← 18 tests
│   ├── test_graph.py        ← 16 tests
│   ├── test_causal.py       ← 18 tests
│   ├── test_attention.py    ← 10 tests
│   ├── test_resume_v3.py    ← 18 tests
│   ├── test_compact.py      ← 17 tests
│   ├── test_snapshot.py     ← 16 tests
│   ├── test_object_store.py ← 15 tests
│   ├── test_zstd.py         ← 14 tests
│   └── test_migration.py    ← 8 tests
│
├── evolution/               ← self-evolution data (gitignored)
├── branches/                ← conversation branches
└── garden/                  ← skill auto-grow
```

---

## v3: LSM-Tree Memory

The v3 architecture replaces flat JSONL scanning with a three-tier LSM-tree:

```
┌─────────────────────────────────────────────────┐
│  L0 (Hot)    │  Last 50 entries, in-memory dict  │  O(1) lookup
│──────────────│───────────────────────────────────│
│  L1 (Warm)   │  Compressed JSONL segments (gzip)  │  Condition-based flush
│──────────────│───────────────────────────────────│
│  L2 (Cold)   │  Summaries + entity tags           │  Auto-compact
└─────────────────────────────────────────────────┘
```

**Condition-based compaction (NOT cron):**
- L0 > 50 entries → flush to L1
- L1 > 500 segments → compact to L2
- Manual: `python3 scripts/mycelium_compact.py`
- Like LLM context compression — compress when full, not on a timer

### Performance

| Metric | v2 | v3 | Improvement |
|--------|-----|-----|-------------|
| Resume speed | ~1.9ms | 0.02ms | **85.9x faster** |
| Storage size | 367KB | 108KB | **3.4x smaller** |
| Bloom check | full scan | 2.1μs | instant |
| Graph query | N/A | 0.12ms | new capability |
| Negation check | N/A | 0.34ms | new capability |

Run benchmarks: `python3 scripts/benchmark_v3.py`

### v3 Subsystems

**Bloom Filter** — O(1) entity membership. 977 entities, 12KB filter, 1% false positive rate. Pre-check before expensive lookups.

**Entity Graph** — 5 relationship types: co-occur, resolves, requires, deploys, affects. 1,224 edges tracked. Query any entity's relationships.

**Negation Index** — 8 regex patterns detecting what doesn't work: wrong-approach, forbidden-approach, failed-attempt, caused-regression, wrong-context, repeated-mistake, behavioral-drift.

**Causal Chain DAG** — 5 edge types: CAUSED, RESOLVED, REGRESSED, SUPERSEDED, DEPLOYS. Trace cause chains, find regressions, confidence scoring.

**Attention Decay** — Exponential decay (14-day half-life). Auto-promote (B→A→S) and demote (A→B→C) based on usage. Frequently-used entries float up.

**Resume V3** — Uses all subsystems: Bloom pre-check → L0 entries → tier-priority packing → entity graph enrichment → negation warnings. Token-budget aware.

**Compact** — 8-step maintenance cycle: decay → flush → compact → bloom rebuild → graph rebuild → verify. Condition-based triggers.

**Content-Addressed Store** — SHA256 dedup. Same content = same hash = no duplicate storage. Ref counting for garbage collection.

**COW Snapshots** — Point-in-time state capture with delta compression. Time-travel to any snapshot state.

**Zstd Compression** — Trained dictionary compression for 10-20x ratio (with gzip fallback).

---

## How it works

Every turn (your prompt + my response) → appended to `log.jsonl` immediately.
**v3 upgrades:**
- **LSM-tree** — three-tier storage with condition-based compaction
- **Bloom filter** — O(1) entity membership checks
- **Entity graph** — relationship tracking across turns
- **Negation index** — remember what doesn't work
- **Causal chains** — cause→effect tracking with confidence
- **Attention decay** — self-optimizing memory (use it or lose it)
- **Integrity chain** — each entry hashes the previous, tampering detectable
- **Archival** — old sessions → summary + `archive/` (never deleted)

Plain JSONL. One object per line. Readable by anything.

| Tool | How to query |
|------|-------------|
| Terminal | `cat`, `grep`, `tail`, `less` |
| VS Code | Open `log.jsonl` |
| Hermes / Claude Code / Codex | Reads `AGENTS.md` for instructions, then `log.jsonl` for context |
| Any AI agent | Start here: `AGENTS.md` |
| Any script | Read JSONL with any language |

**Quick recall:**
```bash
tail -5 log.jsonl                  # what were we doing
grep '"finding"' log.jsonl         # all security findings
grep '"type":"SQLi"' log.jsonl     # specific vuln type
grep '"decision"' log.jsonl        # all decisions made
wc -l log.jsonl                    # total brain size
```

---

## v3 CLI Commands

```bash
# Resume with all v3 subsystems
python3 scripts/mycelium_resume_v3.py --hint "grav"

# Compact (condition-based, not cron)
python3 scripts/mycelium_compact.py
python3 scripts/mycelium_compact.py --dry-run
python3 scripts/mycelium_compact.py --stats

# Migration from v2
python3 scripts/migrate_v3.py migrate
python3 scripts/migrate_v3.py rollback --backup-path archive/pre-v3-migration-XXX.tar.gz
python3 scripts/migrate_v3.py status

# Benchmarks
python3 scripts/benchmark_v3.py
python3 scripts/benchmark_report.py

# Bloom filter
python3 scripts/mycelium_bloom.py check "grav"
python3 scripts/mycelium_bloom.py stats

# Entity graph
python3 scripts/mycelium_graph.py top 10
python3 scripts/mycelium_graph.py query grav

# Negation index
python3 scripts/mycelium_negation.py query --approach curl
python3 scripts/mycelium_negation.py recent

# Causal chain
python3 scripts/mycelium_causal.py trace-cause 42
python3 scripts/mycelium_causal.py regressions

# Attention
# (integrated into mycelium.py subcommands)
```

---

## Web UI (v3)

Local dashboard at `http://127.0.0.1:8421/`

New v3 pages:
- `v3_dashboard.html` — LSM layers, bloom stats, attention heatmap
- `v3_graph.html` — Entity relationship viewer
- `v3_negations.html` — What doesn't work feed
- `v3_causal.html` — Causal chain tracer

10 new API endpoints:
- `/api/graph/entity/{entity}` — entity relationships
- `/api/graph/top` — top entities
- `/api/negations` — negation list
- `/api/causal/trace/{turn}` — cause chain
- `/api/causal/regressions` — regressions
- `/api/bloom/check/{entity}` — bloom check
- `/api/bloom/stats` — bloom stats
- `/api/attention/top` — most-attended entries
- `/api/attention/stale` — never-referenced entries
- `/api/lsm/stats` — LSM layer stats

---

## Tests

228 tests covering all v3 subsystems:

```bash
cd ~/Documents/mycelium
python3 -m pytest tests/ -v          # run all
python3 -m pytest tests/test_lsm.py  # LSM only
python3 -m pytest tests/ -q          # quick summary
```

---

## Migration

v3 is backward compatible — `log.jsonl` stays as the source of truth.
Migration builds LSM layers from the existing log:

```bash
python3 scripts/migrate_v3.py migrate          # build LSM + indexes
python3 scripts/migrate_v3.py status            # check migration state
python3 scripts/migrate_v3.py rollback ...      # undo if needed
```

---

## myceliumd runtime install

Development source stays in `~/Documents/mycelium`.
Installed launchd/runtime lives in `~/.hermes/myceliumd/runtime` to avoid macOS TCC blocking `~/Documents`.

Quick commands:

```bash
make install-daemon
make deploy-daemon
make status-daemon
make test-daemon-offline
make test-daemon-e2e
```

---

## Security

- **Local only.** No network, no cloud, no upload.
- **Same protection as your Mac:** FileVault, login password.
- **You own it.** No vendor lock, no weird DB format.
- **Tamper-evident.** Append-only log — nothing deleted, only appended.
- **Hash chain.** Each entry hashes the previous — tampering detectable.
- **Zero data loss.** LSM compression never deletes — only compresses.

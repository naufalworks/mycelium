# Mycelium — AI Agent Instructions v3

Mycelium is a **persistent brain** — LSM-tree storage with integrity chain,
tiered importance, entity extraction, Bloom filter, entity graph, negation index,
and smart session resume. Survives crashes, session loss, and `/new`.

## Quick start

```bash
python3 scripts/mycelium.py status          # Brain stats
python3 scripts/mycelium.py resume          # Smart session resume
python3 scripts/mycelium.py verify          # Integrity chain check
python3 scripts/mycelium.py search <query>  # Search log + index
python3 scripts/mycelium.py compact         # Condition-based maintenance
python3 scripts/mycelium_compact.py --stats # Layer stats (L0/L1/L2)
python3 scripts/detect-patterns.py          # Skill Garden pattern scan
python3 scripts/branch.py list              # List branches
python3 scripts/findings.py list            # List findings
python3 scripts/findings.py report          # Generate report
```

## v3 Core Commands

```bash
# Resume (uses LSM + Bloom + Graph + Negation)
python3 scripts/mycelium_resume_v3.py --hint "grav"

# Compact (condition-based, NOT cron)
python3 scripts/mycelium_compact.py              # auto-flush if over thresholds
python3 scripts/mycelium_compact.py --dry-run    # preview
python3 scripts/mycelium_compact.py --stats      # layer stats

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
```

## Architecture

```
log.jsonl          ← flat JSONL (backward compatible)
index.db           ← SQLite (entities, findings, edges, negations, causal, attention)
l1/                ← LSM compressed segments (gzip)
l2/                ← LSM summaries (gzip)
snapshots/         ← COW session snapshots
objects/           ← content-addressed object store
```

All scripts import shared constants from `mycelium_lib.py` — single source of truth.
Paths resolve dynamically via `Path(__file__)`.

## Log format v2 (tiered + integrity chain)

```jsonl
{"turn": 1, "tier": "S", "type": "finding", "session": "acme-pentest",
 "ts": "ISO_TIMESTAMP", "entities": ["acme.com", "sqli"],
 "user": "...", "assistant": "...",
 "finding": {"type": "SQLi", "target": "admin.acme.com", "severity": "critical"},
 "prev_hash": "", "hash": "a1b2c3d4e5f6g7h8"}
```

### Tier rules

| Tier | Types |
|------|-------|
| S | finding (critical/high), decision, gardener (sprout) |
| A | idea, finding (medium/low) |
| B | talk (default) |
| C | dead-end, pruned branch |

## Agent behavior

### On session start
```bash
bash scripts/mycelium-start
```
This runs: precheck → resume → pattern detection → evolution load.
Inject output into response context.

### After every response
```bash
python3 scripts/append.py \
  --session <kebab-name> --type <talk|finding|decision|idea> \
  "<condensed user>" "<condensed assistant>"
```
Auto-calculates: turn number, tier, entities, prev_hash, hash. Takes <1s.

### V3 resume flow
1. Brain stats (entries, sessions, entities)
2. Bloom pre-check (O(1) entity membership)
3. Load L0 entries (last 50, full text)
4. Tier-priority filter (S → A → B)
5. Token-budget packing
6. Entity graph enrichment
7. Negation warnings

### Condition-based maintenance
NOT cron-based. Triggers:
- L0 > 50 entries → flush to L1
- L1 > 500 segments → compact to L2
- `python3 scripts/mycelium_compact.py` (manual)

### Integrity verification
```bash
python3 scripts/mycelium.py verify
```
Checks prev_hash chain across all LSM layers.

## Phase 1 — Skill Garden

```bash
python3 scripts/detect-patterns.py
```
Short-circuit optimization — stops at threshold.

## Phase 2 — Conversation Tree

```bash
python3 scripts/branch.py create|merge|prune|diff|list <name>
```

## Phase 3 — Vuln Hunter Notebook

```bash
python3 scripts/findings.py list|by-target|by-type|report|stats
```

## Tests

```bash
python3 -m pytest tests/ -v    # 228 tests
python3 -m pytest tests/ -q    # quick summary
```

## Security

- **Local only.** No network, no cloud, no upload.
- **Integrity chain.** Each entry hashes the previous. Tampering detectable.
- **Append-only.** Nothing deleted — only appended or archived.
- **Zero data loss.** LSM compression never deletes — only compresses.
- **Hash chain.** Tamper-evident across all LSM layers.

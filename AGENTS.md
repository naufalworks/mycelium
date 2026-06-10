# Mycelium — AI Agent Instructions v2

Mycelium is a **persistent brain** — an append-only log with integrity chain,
tiered importance, entity extraction, and smart session resume. Survives
crashes, session loss, and `/new`.

## Quick start

```bash
python3 scripts/mycelium.py status          # Brain stats
python3 scripts/mycelium.py resume          # Smart session resume
python3 scripts/mycelium.py verify          # Integrity chain check
python3 scripts/mycelium.py search <query>  # Search log + index
python3 scripts/mycelium.py archive [days]  # Compact old sessions (never deletes)
python3 scripts/detect-patterns.py          # Skill Garden pattern scan
python3 scripts/branch.py list              # List branches
python3 scripts/findings.py list            # List findings
python3 scripts/findings.py report          # Generate report
```

## Project structure

```
mycelium/
├── AGENTS.md                  ← this file — AI agent instructions
├── README.md
├── .gitignore
├── log.jsonl                  ← THE BRAIN — v2 tiered, hashed, entity-indexed (gitignored)
├── index.db                   ← SQLite index for fast queries (gitignored)
├── archive/                   ← compacted old sessions (gitignored, NEVER deleted)
├── scripts/
│   ├── mycelium.py            ← NEW unified CLI (status, resume, search, verify, archive)
│   ├── detect-patterns.py     ← Phase 1: Skill Garden
│   ├── branch.py              ← Phase 2: Conversation Tree
│   └── findings.py            ← Phase 3: Vuln Hunter Notebook
├── branches/                  ← conversation branches (gitignored)
└── garden/                    ← skill garden state (gitignored)
```

## Log format v2 (tiered + integrity chain)

```jsonl
{"turn": 1, "tier": "S", "type": "finding", "session": "acme-pentest",
 "ts": "ISO_TIMESTAMP", "entities": ["acme.com", "sqli"],
 "user": "...", "assistant": "...",
 "finding": {"type": "SQLi", "target": "admin.acme.com", "severity": "critical"},
 "prev_hash": "", "hash": "a1b2c3d4e5f6g7h8"}

{"turn": 2, "tier": "B", "type": "talk", "session": "brainstorm",
 "entities": ["mycelium", "grav"],
 "prev_hash": "a1b2c3d4e5f6g7h8", "hash": "i9j0k1l2m3n4o5p6"}
```

### New fields

| Field | Meaning |
|-------|---------|
| `tier` | S=critical (findings, decisions), A=important (ideas), B=normal (talk), C=noise (dead-ends) |
| `entities` | Auto-extracted: projects, tools, domains, ports |
| `prev_hash` | SHA256 of previous entry (chain integrity) |
| `hash` | SHA256 of this entry excluding the `hash` field |

### Tier rules (applied during append)

- **S** — finding (critical/high), decision, gardener (skill sprouted)
- **A** — idea, finding (medium/low)
- **B** — talk (default)
- **C** — dead-end, pruned branch

## Agent behavior v2

### On session start
```bash
python3 scripts/mycelium.py resume
```
This gives you a structured summary: last session, garden seeds, recent S-tier entries, latest critical finding, active branches. Use this instead of raw `tail` — denser signal, less context waste.

### On every session end
```python
# Append turn with ALL v2 fields:
entry = {
    "turn": next_turn,
    "tier": classify_tier(e),       # S/A/B/C based on content
    "type": "finding|talk|etc",
    "session": "current-session",
    "ts": now,
    "entities": extract_entities(user_msg + assistant_msg),  # auto-extracted
    "user": abbreviated_user_msg,
    "assistant": abbreviated_response,
    "finding": {...} or None,        # if type==finding
    "prev_hash": last_entry["hash"],
}
entry["hash"] = compute_hash(entry, entry["prev_hash"])
append_to_log(entry)
```

### Smart resume injection
When resuming, inject the `resume` output NOT raw JSONL tail. It includes:
- Last session name + turn count
- Brain stats (22 turns, 10.2 KB, v2)
- Garden seeds approaching threshold
- Active branches
- Recent S-tier entries (decisions, critical findings)
- Latest critical finding
- Patterns near 3x threshold (skill offer candidates)

### Integrity verification
Periodically:
```bash
python3 scripts/mycelium.py verify
```
This checks the prev_hash chain. If broken → tampering detected.

### Log compaction
When log exceeds 500 turns, archive old sessions:
```bash
python3 scripts/mycelium.py archive 30   # compact sessions >30 days
```
Archived entries go to `archive/log.YYYYMM.session.jsonl` — NEVER deleted.
Main log gets a summary entry pointing to the archive file.
You can still `grep` the archive files.

## Phase 1 — Skill Garden

```bash
python3 scripts/detect-patterns.py
```
Now with short-circuit optimization — stops counting at threshold. Scales to 1M+ turns.

## Phase 2 — Conversation Tree

```bash
python3 scripts/branch.py create <name>
python3 scripts/branch.py merge <name>
python3 scripts/branch.py prune <name>
python3 scripts/branch.py diff <name>
python3 scripts/branch.py list
```

When branching, append to BOTH main log and branch file.

## Phase 3 — Vuln Hunter Notebook

```bash
python3 scripts/findings.py list
python3 scripts/findings.py by-target <name>
python3 scripts/findings.py by-type <type>
python3 scripts/findings.py report [target]
python3 scripts/findings.py stats
```

Findings now indexed in SQLite — queries are instant regardless of log size.

## Security v2

- **Local only.** No network, no cloud, no upload.
- **Integrity chain.** Each entry hashes the previous. Tampering is detectable.
- **Append-only.** Nothing deleted — only appended or archived.
- **Archive never deletes.** Old sessions compacted into summaries, raw data preserved in `archive/`.

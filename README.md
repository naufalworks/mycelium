# 🍄 Mycelium — Your Persistent Brain v2

Every conversation, every finding, every idea — saved forever with integrity chain,
importance tiers, and entity indexing. Never loses data. Never deleted — only archived.
Any AI agent opens the repo and knows exactly what to do (see `AGENTS.md`).

**One project. Three features. Zero new infra. v2 upgrades: integrity, speed, recall.**

```
mycelium/
├── README.md              ← project overview
├── AGENTS.md              ← AI agent instructions (Hermes, Claude Code, etc.)
├── log.jsonl              ← the brain — v2 tiered + hashed + entities
├── index.db               ← SQLite index (auto-built, queries)
├── archive/               ← compacted old sessions (never deleted)
├── scripts/
│   ├── mycelium.py        ← unified CLI (status, resume, search, verify, archive)
│   ├── detect-patterns.py ← Phase 1: Skill Garden
│   ├── branch.py          ← Phase 2: Conversation Tree
│   └── findings.py        ← Phase 3: Vuln Hunter Notebook
├── branches/              ← conversation branches (Phase 2)
└── garden/                ← skill auto-grow (Phase 1)
    ├── patterns.json      ← recurring patterns detected
    └── state.json         ← offered/accepted/dormant skills
```

---

## How it works

Every turn (your prompt + my response) → appended to `log.jsonl` immediately.
**v2 upgrades:**
- **Tiered** (S/A/B/C) — important entries surface first, noise sinks
- **Entity-indexed** — projects, tools, domains extracted per turn, stored in SQLite
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

## Structured log format

```jsonl
{"turn": 1, "type": "talk", "session": "session-name", "ts": "ISO_TIMESTAMP",
 "user": "...", "assistant": "..."}

{"turn": 2, "type": "finding", "session": "acme-pentest", "ts": "...",
 "user": "...", "assistant": "...",
 "finding": {"type": "SQLi", "target": "admin.acme.com", "severity": "critical"}}
```

| Field | What |
|-------|------|
| `type` | `talk`, `finding`, `idea`, `decision`, `branch`, `dead-end`, `gardener` |
| `session` | Short kebab-case name (auto-set per session) |
| `finding` | (optional) Structured vuln data |
| `branch` | (optional) Branch name if branching |

---

## Three features

### Phase 1 — Skill Garden 🪴 ✅ (ACTIVE)
Patterns detected from real use → skill offered at 3x threshold.
Detection script: `scripts/detect-patterns.py`. Currently tracking 8 patterns.
**3 seeds planted** (grav-health, grav-pool, page-radar each at 1/3).
No auto-creation — always asks permission first.

### Phase 2 — Conversation Tree 🌿 ✅ (ACTIVE)
`branch [name]` → explore without losing main thread.
`merge [name]` → pull useful findings in.
`prune [name]` → delete dead ends.
`diff [name]` → compare paths.
`branches` → list all.
Branch manager: `scripts/branch.py`

### Phase 3 — Vuln Hunter's Notebook 🎯 ✅ (ACTIVE)
Tagged findings with type/target/severity/evidence/remediation.
CLI: `scripts/findings.py` — list, by-target, by-type, by-severity, report, dead-ends, stats, timeline.
`findings report [target]` → markdown report skeleton ready for client.
Dead-end logging so you never retry failed approaches.

---

## Security

- **Local only.** No network, no cloud, no upload.
- **Same protection as your Mac:** FileVault, login password.
- **You own it.** No vendor lock, no weird DB format.
- **Tamper-evident.** Append-only log — nothing deleted, only appended.

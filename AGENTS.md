# Mycelium — AI Agent Instructions

Mycelium is a **persistent brain** — an append-only log that survives crashes,
session loss, and `/new`. Every conversation turn is saved as structured JSONL.

## Quick start

```bash
python3 scripts/detect-patterns.py   # Scan log → detect skill-worthy patterns
python3 scripts/branch.py list        # List conversation branches
python3 scripts/findings.py list      # List all security findings
python3 scripts/findings.py stats     # Summary across all engagements
```

## Project structure

```
mycelium/
├── README.md                  ← full docs
├── AGENTS.md                  ← this file — AI agent instructions
├── .gitignore
├── log.jsonl                  ← THE BRAIN — every turn ever (gitignored)
├── scripts/
│   ├── detect-patterns.py     ← Phase 1: Skill Garden
│   ├── branch.py              ← Phase 2: Conversation Tree
│   └── findings.py            ← Phase 3: Vuln Hunter Notebook
├── branches/                  ← conversation branches (gitignored)
└── garden/                    ← skill garden state (gitignored)
    ├── patterns.json
    └── state.json
```

## Log format (JSONL)

Every line is one turn. One JSON object per line.

```jsonl
{"turn": 1, "type": "talk", "session": "session-name", "ts": "ISO_TIMESTAMP",
 "user": "...", "assistant": "..."}

{"turn": 2, "type": "finding", "session": "acme-pentest", "ts": "...",
 "user": "...", "assistant": "...",
 "finding": {"type": "SQLi", "target": "admin.acme.com", "severity": "critical",
             "endpoint": "/login", "parameter": "uid", "detail": "...",
             "evidence": "curl ...", "remediation": "parameterized queries"}}

{"turn": 3, "type": "dead-end", "session": "acme-pentest", "ts": "...",
 "attempt": "what was tried", "result": "why it didn't work"}

{"turn": 4, "type": "branch", "session": "session-name", "ts": "...",
 "branch": "branch-name", "branch_action": "create|merge|prune|dead-end|active"}
```

### Types

| Type | Meaning |
|------|---------|
| `talk` | Normal conversation (default) |
| `finding` | Security finding with structured data |
| `idea` | New concept, brainstorm |
| `decision` | A decision made |
| `branch` | Branch event (create/merge/prune) |
| `dead-end` | Approach tried and failed |
| `gardener` | Skill garden event |

## Phase 1 — Skill Garden

Patterns detected from real log entries → skill offered at 3x threshold.

```bash
python3 scripts/detect-patterns.py
```

Output shows each pattern with count/threshold. When a pattern hits 3x,
`new_offers` tells the agent which skills to offer the user.

Rules defined in the script as `PATTERN_RULES` dict. Each has:
- `match` lambda — what to look for in log entries
- `threshold` — count needed before offering (default 3)
- `skill_hint` — what the skill would automate

## Phase 2 — Conversation Tree

Branch exploration without losing the main thread.

```bash
python3 scripts/branch.py create <name>   # Start a branch
python3 scripts/branch.py list            # List all branches
python3 scripts/branch.py status <name>   # Branch stats
python3 scripts/branch.py diff <name>     # Compare with main
python3 scripts/branch.py merge <name>    # Merge useful turns to main
python3 scripts/branch.py prune <name>    # Delete dead-end branch
```

When a branch is active, every turn is logged to **both** main log and branch file.

## Phase 3 — Vuln Hunter Notebook

Tagged security findings with full structured data.

```bash
python3 scripts/findings.py list                    # All findings
python3 scripts/findings.py by-target acme.com      # Per target
python3 scripts/findings.py by-type SQLi             # Per vuln type
python3 scripts/findings.py by-severity critical     # Per severity level
python3 scripts/findings.py report                   # Full report (markdown)
python3 scripts/findings.py report acme.com          # Per-target report
python3 scripts/findings.py dead-ends                # Failed attempts
python3 scripts/findings.py stats                    # Summary
python3 scripts/findings.py timeline                 # Chronological view
```

## Agent behavior

1. **On session start:** Read last 5-10 lines of `log.jsonl` to resume context
2. **After every response:** Append structured turn to `log.jsonl`
3. **On every session:** Run `detect-patterns.py` — check for skill offers
4. **When user branches:** Dual-log to main + branch file
5. **When user hunts vulns:** Tag every finding with structured `finding` object
6. **Dead-ends matter:** Log them — prevent retrying failed approaches

## Security

- Local only. No network, no cloud, no upload.
- Same protection as the host OS (macOS FileVault, login password)
- Append-only log — nothing deleted, only appended (tamper-evident)

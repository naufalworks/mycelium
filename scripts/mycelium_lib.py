#!/usr/bin/env python3
"""
Shared library for Mycelium scripts.

Single source of truth for:
  - Path resolution (dynamic — works from source or runtime)
  - KNOWN_ENTITIES + ENTITY_PATTERNS (no more divergence between scripts)
  - TIER_RULES + tier classification
  - Hash chain computation
  - Entity extraction
  - Index schema init + incremental update

Usage (from any script in scripts/):
  from mycelium_lib import MYCELIUM, LOG, INDEX, extract_entities, classify_tier, compute_hash
"""
from __future__ import annotations

import hashlib, json, os, re, sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone

# ── Dynamic path resolution ──────────────────────────────────
# scripts/ is always one level under the mycelium root.
# Works from ~/Documents/mycelium/scripts/ AND ~/.hermes/myceliumd/runtime/scripts/
SCRIPT_DIR = Path(__file__).resolve().parent
MYCELIUM = SCRIPT_DIR.parent
LOG = MYCELIUM / "log.jsonl"
INDEX = MYCELIUM / "index.db"
ARCHIVE = MYCELIUM / "archive"
BRANCHES = MYCELIUM / "branches"
GARDEN = MYCELIUM / "garden"
EVOLUTION_DIR = MYCELIUM / "evolution"


# ── Entity extraction ────────────────────────────────────────
# Merged from append.py (33) + mycelium.py (39) — union of both.
KNOWN_ENTITIES = {
    "grav", "grav-shim", "antigravity", "macro-gift-770k4", "gen-lang-client-0558595692",
    "mycelium", "memgit",
    "page-radar", "page radar",
    "companion",
    "hermes", "hermes agent",
    "claude code", "codex",
    "sqlite", "jsonl", "json",
    "curl", "python", "bash", "git", "gh", "grep", "tail",
    "sql", "sqli", "xss", "ssrf", "lfi", "idor",
    "vpn", "vps", "launchd", "cron",
}

ENTITY_PATTERNS = [
    (r'https?://([^/\s"]+)', lambda m: m.group(1)),
    (r'(?:^|\s)([\w-]+\.[\w-]{2,})(?:\s|$)', lambda m: m.group(1)),
    (r'/v\d+/[\w/-]+', lambda m: m.group(0)),
    (r'port\s*:?\s*(\d{4,5})', lambda m: f"port-{m.group(1)}"),
]


def extract_entities(text: str) -> list:
    """Extract entities from text — known names + patterns."""
    if not text:
        return []
    tl = text.lower()
    entities = set()
    for ent in KNOWN_ENTITIES:
        if ent in tl:
            entities.add(ent)
    for pat, fn in ENTITY_PATTERNS:
        for m in re.finditer(pat, tl):
            entities.add(fn(m))
    return sorted(entities)


# ── Tier classification ──────────────────────────────────────

TIER_RULES = [
    ("S", lambda e: e.get("type") == "finding" and e.get("finding", {}).get("severity") in ("critical", "high")),
    ("S", lambda e: e.get("type") == "gardener" and e.get("action") == "sprout"),
    ("S", lambda e: e.get("type") == "decision"),
    ("S", lambda e: e.get("type") == "tech_verdict"),
    ("A", lambda e: e.get("type") == "idea"),
    ("A", lambda e: e.get("type") == "finding"),
    ("B", lambda e: e.get("type") == "talk"),
    ("C", lambda e: e.get("type") in ("dead-end", "branch") and e.get("branch_action") in ("prune", "dead-end")),
]
DEFAULT_TIER = "B"


def classify_tier(entry: dict) -> str:
    """Classify an entry into S/A/B/C tier based on type + content."""
    for tier, check in TIER_RULES:
        if check(entry):
            return tier
    return DEFAULT_TIER


# ── Hash chain ───────────────────────────────────────────────

def compute_hash(entry: dict, prev_hash: str = "") -> str:
    """Chain hash: SHA256 of (prev_hash + canonical_json). Excludes 'hash' from entry."""
    e = {k: v for k, v in entry.items() if k != "hash"}
    raw = json.dumps(e, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + raw).encode()).hexdigest()[:16]


# ── Log I/O ──────────────────────────────────────────────────

def load_log(path=None) -> list:
    """Load all entries from a JSONL log file."""
    path = path or LOG
    if not path.exists():
        return []
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def load_last_entry(path=None) -> dict | None:
    """Read only the last entry — O(1) via seek, O(n) fallback."""
    path = path or LOG
    if not path.exists():
        return None
    size = path.stat().st_size
    if size == 0:
        return None
    # Fast path: read last 8KB (entries avg 536B, max ~1.1KB — 8KB is safe)
    with open(path, "rb") as f:
        f.seek(max(0, size - 8192))
        chunk = f.read().decode("utf-8", errors="replace")
        lines = chunk.strip().split("\n")
        if lines and lines[-1]:
            try:
                return json.loads(lines[-1])
            except json.JSONDecodeError:
                pass  # fall through to slow path
    # Fallback: full iteration
    with open(path) as f:
        last = None
        for line in f:
            if line.strip():
                last = line
    return json.loads(last) if last else None


def save_log(entries: list, path=None) -> None:
    """Write entries to a JSONL file (full overwrite)."""
    path = path or LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    path.write_text(content)


# ── Index (SQLite) ───────────────────────────────────────────

INDEX_SCHEMA = """
    CREATE TABLE IF NOT EXISTS turns (
        turn INTEGER PRIMARY KEY, tier TEXT, type TEXT,
        session TEXT, ts TEXT, summary TEXT);
    CREATE TABLE IF NOT EXISTS entities (
        turn INTEGER, entity TEXT,
        FOREIGN KEY(turn) REFERENCES turns(turn));
    CREATE TABLE IF NOT EXISTS findings (
        turn INTEGER PRIMARY KEY, target TEXT, ftype TEXT, severity TEXT,
        FOREIGN KEY(turn) REFERENCES turns(turn));
    CREATE INDEX IF NOT EXISTS idx_entities ON entities(entity);
    CREATE INDEX IF NOT EXISTS idx_findings_target ON findings(target);
    CREATE INDEX IF NOT EXISTS idx_findings_type ON findings(ftype);
    CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session);
    CREATE INDEX IF NOT EXISTS idx_turns_tier ON turns(tier);
"""


def init_index(path=None) -> sqlite3.Connection:
    """Create index schema if missing, return connection."""
    path = path or INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(INDEX_SCHEMA)
    return conn


def update_index(entry: dict, path=None) -> None:
    """Incremental insert of one entry into the index. O(1)."""
    conn = init_index(path)
    turn = entry.get("turn", 0)
    tier = entry.get("tier", classify_tier(entry))
    typ = entry.get("type", "talk")
    session = entry.get("session", "?")
    ts = entry.get("ts", "?")
    user = entry.get("user", "")
    assistant = entry.get("assistant", "")
    summary = (user[:80] + " \u2192 " + assistant[:80])[:240]

    conn.execute(
        "INSERT OR REPLACE INTO turns (turn, tier, type, session, ts, summary) VALUES (?,?,?,?,?,?)",
        (turn, tier, typ, session, ts, summary)
    )
    # Clear old entities for this turn (in case of re-insert)
    conn.execute("DELETE FROM entities WHERE turn=?", (turn,))
    for ent in entry.get("entities", extract_entities(user + " " + assistant)):
        conn.execute("INSERT INTO entities (turn, entity) VALUES (?, ?)", (turn, ent))

    finding = entry.get("finding")
    if finding:
        conn.execute(
            "INSERT OR REPLACE INTO findings (turn, target, ftype, severity) VALUES (?,?,?,?)",
            (turn, finding.get("target", "unknown"),
             finding.get("type", "unknown"),
             finding.get("severity", "info"))
        )
    else:
        conn.execute("DELETE FROM findings WHERE turn=?", (turn,))

    conn.commit()
    conn.close()


def rebuild_index(entries=None, path=None) -> tuple:
    """Full index rebuild from log. Used for recovery/reindex only."""
    if entries is None:
        entries = load_log()
    conn = init_index(path)
    conn.execute("DELETE FROM turns")
    conn.execute("DELETE FROM entities")
    conn.execute("DELETE FROM findings")

    for e in entries:
        turn = e.get("turn", 0)
        tier = e.get("tier", classify_tier(e))
        typ = e.get("type", "talk")
        session = e.get("session", "?")
        ts = e.get("ts", "?")
        user = e.get("user", "")
        assistant = e.get("assistant", "")
        summary = (user[:80] + " \u2192 " + assistant[:80])[:240]

        conn.execute(
            "INSERT OR REPLACE INTO turns (turn, tier, type, session, ts, summary) VALUES (?,?,?,?,?,?)",
            (turn, tier, typ, session, ts, summary)
        )
        entities = e.get("entities", extract_entities(user + " " + assistant))
        for ent in entities:
            conn.execute("INSERT INTO entities (turn, entity) VALUES (?, ?)", (turn, ent))

        if typ == "finding":
            finding = e.get("finding") or {}
            detail = finding.get("detail") or finding.get("result")
            if detail is not None and "detail" not in finding:
                finding = {**finding, "detail": detail}
            conn.execute(
                "INSERT OR REPLACE INTO findings (turn, target, ftype, severity) VALUES (?,?,?,?)",
                (turn, finding.get("target") or "unknown",
                 finding.get("type") or "unknown",
                 finding.get("severity") or "info")
            )

    conn.commit()
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    ent_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()
    return turn_count, ent_count

#!/usr/bin/env python3
"""
🍄 append.py — single-turn append for Mycelium v2.

Appends one turn to log.jsonl (O(1) line write, no full re-read).
Auto-extracts entities, classifies tier, computes hash chain.
Rebuilds SQLite index.

Usage:
  append.py [--session NAME] [--type TYPE] [--finding JSON] "user text" "assistant text"

Types: talk (default), finding, decision, idea, dead-end, gardener
"""
import argparse, hashlib, json, os, re, sqlite3, sys
from pathlib import Path
from datetime import datetime, timezone

MYCELIUM = Path.home() / "Documents/mycelium"
LOG = MYCELIUM / "log.jsonl"
INDEX = MYCELIUM / "index.db"

# ── Entity extraction ──
KNOWN_ENTITIES = {
    "grav", "grav-shim", "antigravity", "macro-gift-770k4", "gen-lang-client-0558595692",
    "mycelium", "memgit", "page-radar", "page radar", "companion",
    "hermes", "hermes agent", "claude code", "codex",
    "sqlite", "jsonl", "json",
    "curl", "python", "bash", "git", "gh", "grep", "tail",
    "sql", "sqli", "xss", "ssrf", "lfi", "idor", "vpn", "vps",
    "launchd", "cron",
}
ENTITY_PATTERNS = [
    (r'https?://([^/\s"]+)', lambda m: m.group(1)),
    (r'(?:^|\s)([\w-]+\.[\w-]{2,})(?:\s|$)', lambda m: m.group(1)),
    (r'/v\d+/[\w/-]+', lambda m: m.group(0)),
    (r'port\s*:?\s*(\d{4,5})', lambda m: f"port-{m.group(1)}"),
]

TIER_RULES = [
    ("S", lambda e: e.get("type") == "finding" and e.get("finding", {}).get("severity") in ("critical", "high")),
    ("S", lambda e: e.get("type") == "gardener" and e.get("action") == "sprout"),
    ("S", lambda e: e.get("type") == "decision"),
    ("A", lambda e: e.get("type") == "idea"),
    ("A", lambda e: e.get("type") == "finding"),
    ("B", lambda e: e.get("type") == "talk"),
    ("C", lambda e: e.get("type") in ("dead-end", "branch") and e.get("branch_action") in ("prune", "dead-end")),
]


def _load_last():
    """Read only the last line of log.jsonl — O(1)."""
    if not LOG.exists():
        return None
    with open(LOG) as f:
        line = None
        for line in f:
            pass
        if line and line.strip():
            return json.loads(line)
    return None


def _extract_entities(text):
    if not text:
        return []
    tl = text.lower()
    es = set()
    for ent in KNOWN_ENTITIES:
        if ent in tl:
            es.add(ent)
    for pat, fn in ENTITY_PATTERNS:
        for m in re.finditer(pat, tl):
            es.add(fn(m))
    return sorted(es)


def _classify_tier(entry):
    for tier, check in TIER_RULES:
        if check(entry):
            return tier
    return "B"


def _compute_hash(entry, prev_hash=""):
    e = {k: v for k, v in entry.items() if k != "hash"}
    raw = json.dumps(e, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + raw).encode()).hexdigest()[:16]


def _rebuild_index():
    """Full SQLite index rebuild. Cheap for <10K turns."""
    if not LOG.exists():
        return
    conn = sqlite3.connect(str(INDEX))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS turns (
            turn INTEGER PRIMARY KEY, tier TEXT, type TEXT,
            session TEXT, ts TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS entities (
            turn INTEGER, entity TEXT,
            FOREIGN KEY(turn) REFERENCES turns(turn));
        CREATE TABLE IF NOT EXISTS findings (
            turn INTEGER PRIMARY KEY, target TEXT, ftype TEXT, severity TEXT,
            FOREIGN KEY(turn) REFERENCES turns(turn));
        DELETE FROM turns; DELETE FROM entities; DELETE FROM findings;
    """)
    with open(LOG) as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            turn = e.get("turn", 0)
            tier = e.get("tier", _classify_tier(e))
            typ = e.get("type", "talk")
            session = e.get("session", "?")
            ts = e.get("ts", "?")
            user = e.get("user", "")
            assistant = e.get("assistant", "")
            summary = (user[:80] + " → " + assistant[:80])[:240]
            conn.execute("INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?,?)",
                         (turn, tier, typ, session, ts, summary))
            for ent in e.get("entities", []):
                conn.execute("INSERT INTO entities (turn, entity) VALUES (?, ?)", (turn, ent))
            f = e.get("finding")
            if f:
                conn.execute("INSERT OR REPLACE INTO findings VALUES (?,?,?,?)",
                             (turn, f.get("target", "?"), f.get("type", "?"), f.get("severity", "?")))
    conn.commit()
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Append one turn to Mycelium log.")
    ap.add_argument("--session", "-s", default="default", help="Session name (kebab-case)")
    ap.add_argument("--type", "-t", default="talk",
                    choices=["talk", "finding", "decision", "idea", "dead-end", "gardener"])
    ap.add_argument("--finding", "-f", help="JSON string for finding object")
    ap.add_argument("--no-index", action="store_true", help="Skip SQLite index rebuild (faster)")
    ap.add_argument("user", help="User message (condensed)")
    ap.add_argument("assistant", help="Assistant response (condensed)")
    args = ap.parse_args()

    last = _load_last()
    prev_hash = last["hash"] if last else ""
    turn = (last["turn"] + 1) if last else 1

    entry = {
        "turn": turn,
        "type": args.type,
        "session": args.session,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tier": "",
        "entities": [],
        "user": args.user,
        "assistant": args.assistant,
        "prev_hash": prev_hash,
        "hash": "",
    }

    if args.type == "finding" and args.finding:
        try:
            entry["finding"] = json.loads(args.finding)
        except json.JSONDecodeError as e:
            print(f"✗ Invalid --finding JSON: {e}", file=sys.stderr)
            sys.exit(1)

    if args.type == "dead-end":
        entry["attempt"] = args.user
        entry["result"] = args.assistant

    entry["tier"] = _classify_tier(entry)
    entry["entities"] = _extract_entities(args.user + " " + args.assistant)
    entry["hash"] = _compute_hash(entry, prev_hash)

    # Append single line — O(1)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

    if not args.no_index:
        _rebuild_index()

    print(f"✅ Turn {turn} appended [{entry['tier']}] {args.session}: {args.type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

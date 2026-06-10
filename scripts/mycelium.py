#!/usr/bin/env python3
"""
🍄 Mycelium — unified CLI v2.

Commands:
  status          — brain stats summary
  resume          — smart session resume (for AI agent injection)
  verify          — check integrity chain
  reindex         — rebuild SQLite index from log
  archive         — compact old sessions (never deletes)
  search <query>  — search across log + index
  migrate         — upgrade existing log to v2 format
"""
import json, hashlib, re, sqlite3, sys, shutil, datetime, textwrap
from pathlib import Path
from collections import defaultdict, Counter

MYCELIUM = Path.home() / "Documents/mycelium"
LOG = MYCELIUM / "log.jsonl"
ARCHIVE = MYCELIUM / "archive"
INDEX = MYCELIUM / "index.db"
SCRIPTS = MYCELIUM / "scripts"

# ─── Tier rules ──────────────────────────────────────────────
TIER_RULES = {
    "S": lambda e: (
        e.get("type") == "finding" and e.get("finding", {}).get("severity") in ("critical", "high")
    ) or (
        e.get("type") == "gardener" and e.get("action") == "sprout"
    ) or (
        e.get("type") == "decision"
    ),
    "A": lambda e: (
        e.get("type") == "idea"
    ) or (
        e.get("type") == "finding"
    ),
    "B": lambda e: (
        e.get("type") == "talk"
    ),
    "C": lambda e: (
        e.get("type") in ("dead-end", "branch") and e.get("branch_action") in ("prune", "dead-end")
    ),
}
DEFAULT_TIER = "B"

# ─── Entity extraction ───────────────────────────────────────
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
    "vpn", "vps",
}
ENTITY_PATTERNS = [
    (r'https?://([^/\s"]+)', lambda m: m.group(1)),           # URLs
    (r'(?:^|\s)([\w-]+\.[\w-]{2,})(?:\s|$)', lambda m: m.group(1)),  # domains
    (r'/v\d+/[\w/-]+', lambda m: m.group(0)),                  # API paths
    (r'port\s*:?\s*(\d{4,5})', lambda m: f"port-{m.group(1)}"),  # port numbers
]


def load_log(path=None):
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


def save_log(entries, path=None):
    path = path or LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    path.write_text(content)


def compute_hash(entry, prev_hash=""):
    """Chain hash: SHA256 of (prev_hash + canonical_json). Excludes 'hash' from entry."""
    e = {k: v for k, v in entry.items() if k != "hash"}
    raw = json.dumps(e, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((prev_hash + raw).encode()).hexdigest()[:16]


def extract_entities(text):
    """Extract entities from text — known names + patterns."""
    if not text:
        return []
    text_lower = text.lower()
    entities = set()
    for ent in KNOWN_ENTITIES:
        if ent in text_lower:
            entities.add(ent)
    for pattern, extractor in ENTITY_PATTERNS:
        for m in re.finditer(pattern, text_lower):
            entities.add(extractor(m))
    return sorted(entities)


def classify_tier(entry):
    for tier, checker in TIER_RULES.items():
        if checker(entry):
            return tier
    return DEFAULT_TIER


# ─── Index (SQLite) ──────────────────────────────────────────
def init_index():
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(INDEX))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turns (
            turn INTEGER PRIMARY KEY,
            tier TEXT,
            type TEXT,
            session TEXT,
            ts TEXT,
            summary TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            turn INTEGER,
            entity TEXT,
            FOREIGN KEY(turn) REFERENCES turns(turn)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            turn INTEGER PRIMARY KEY,
            target TEXT,
            ftype TEXT,
            severity TEXT,
            FOREIGN KEY(turn) REFERENCES turns(turn)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities ON entities(entity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_target ON findings(target)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_type ON findings(ftype)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_tier ON turns(tier)")
    return conn


def rebuild_index(entries=None):
    if entries is None:
        entries = load_log()
    conn = init_index()
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
        summary = (user[:80] + " → " + assistant[:80])[:240]

        conn.execute(
            "INSERT OR REPLACE INTO turns (turn, tier, type, session, ts, summary) VALUES (?,?,?,?,?,?)",
            (turn, tier, typ, session, ts, summary)
        )

        # Entities
        entities = e.get("entities", extract_entities(user + " " + assistant))
        for ent in entities:
            conn.execute("INSERT INTO entities (turn, entity) VALUES (?, ?)", (turn, ent))

        # Findings
        finding = e.get("finding")
        if finding:
            conn.execute(
                "INSERT OR REPLACE INTO findings (turn, target, ftype, severity) VALUES (?,?,?,?)",
                (turn, finding.get("target", "?"), finding.get("type", "?"), finding.get("severity", "?"))
            )

    conn.commit()
    turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    ent_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()
    return turn_count, ent_count


# ─── Migration to v2 ─────────────────────────────────────────
def migrate():
    entries = load_log()
    if not entries:
        print("No entries to migrate.")
        return

    # Check if already v2
    if entries[0].get("tier"):
        print("Already v2 format. Nothing to migrate.")
        return

    migrated = []
    prev_hash = ""
    for e in entries:
        e["tier"] = classify_tier(e)
        e["entities"] = extract_entities(e.get("user", "") + " " + e.get("assistant", ""))
        e["prev_hash"] = prev_hash
        e["hash"] = compute_hash(e, prev_hash)
        prev_hash = e["hash"]
        migrated.append(e)

    save_log(migrated)

    # Rebuild index
    t, et = rebuild_index(migrated)
    print(f"Migrated {len(migrated)} turns to v2.")
    print(f"Index rebuilt: {t} turns, {et} entities.")

    # Archive dir
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    print(f"Archive dir ready: {ARCHIVE}")
    return migrated


# ─── Status ──────────────────────────────────────────────────
def cmd_status():
    entries = load_log()
    if not entries:
        print("🍄 Mycelium: empty brain.")
        return

    v2 = entries[0].get("tier", False)
    ts_first = entries[0].get("ts", "?")[:10]
    ts_last = entries[-1].get("ts", "?")[:10]
    types = Counter(e.get("type", "?") for e in entries)
    tiers = Counter(e.get("tier", classify_tier(e)) for e in entries)
    sessions = len(set(e.get("session", "?") for e in entries))
    total_bytes = sum(len(json.dumps(e, ensure_ascii=False)) for e in entries)
    findings = [e for e in entries if e.get("type") == "finding"]

    # Entities
    all_entities = Counter()
    for e in entries:
        for ent in e.get("entities", extract_entities(e.get("user", "") + " " + e.get("assistant", ""))):
            all_entities[ent] += 1

    print(f"🍄 Mycelium — Brain Status")
    print(f"{'='*50}")
    print(f"  Turns:      {len(entries)}")
    print(f"  Size:       {total_bytes/1024:.1f} KB")
    print(f"  Format:     {'v2 (tiered + hashed)' if v2 else 'v1 (flat)'}")
    print(f"  Sessions:   {sessions}")
    print(f"  Date range: {ts_first} → {ts_last}")
    print()
    print(f"  By type:")
    for t in ["finding", "decision", "idea", "talk", "gardener", "dead-end", "branch"]:
        if types.get(t):
            print(f"    {t:12s} {types[t]}")
    print()
    print(f"  By tier:")
    for t in ["S", "A", "B", "C"]:
        if tiers.get(t):
            print(f"    {t:3s}  {tiers[t]} turns")
    if findings:
        print()
        sevs = Counter(f.get("finding", {}).get("severity", "?") for f in findings)
        print(f"  Findings: {len(findings)}")
        for s in ["critical", "high", "medium", "low"]:
            if sevs.get(s):
                print(f"    {s:10s} {sevs[s]}")
    if all_entities:
        print()
        print(f"  Top entities:")
        for ent, cnt in all_entities.most_common(10):
            print(f"    {ent:25s} {cnt}x")
    print()

    # Archive stats
    if ARCHIVE.exists():
        archive_files = sorted(ARCHIVE.glob("log.jsonl.*"))
        if archive_files:
            archive_turns = sum(len(load_log(f)) for f in archive_files)
            print(f"  Archived: {len(archive_files)} file(s), ~{archive_turns} turns")
    print()


def cmd_resume():
    """Smart resume — structured summary, S-tier focus, garden state."""
    entries = load_log()
    if not entries:
        print("🍄 Mycelium brain empty. Starting fresh.")
        return

    # Current session context
    last = entries[-1]
    last_session = last.get("session", "?")
    last_ts = last.get("ts", "?")[:16]

    # Count sessions back to last
    current_session = last_session
    sessions_back = 0
    for e in reversed(entries):
        if e.get("session") != current_session:
            break
        sessions_back += 1

    # S-tier entries (last 5)
    s_tiers = [e for e in entries if e.get("tier", classify_tier(e)) == "S"]
    recent_s = s_tiers[-5:] if len(s_tiers) > 5 else s_tiers

    # Garden state
    garden_file = MYCELIUM / "garden" / "patterns.json"
    garden_seeds = []
    if garden_file.exists():
        try:
            garden = json.loads(garden_file.read_text())
            for p in garden.get("patterns", []):
                if p.get("count", 0) > 0:
                    bar = "#" * min(p["count"], 10)
                    garden_seeds.append(f"{p['id']} {bar} {p['count']}/{p['threshold']}")
        except Exception:
            pass

    # Active branches
    branches = list((MYCELIUM / "branches").glob("*.jsonl")) if (MYCELIUM / "branches").exists() else []

    # Health stats
    total = len(entries)
    log_size = f"{sum(len(json.dumps(e)) for e in entries)/1024:.1f} KB"

    print(f"🍄 Mycelium Resume")
    print(f"{'='*50}")
    print(f"  Last session: {last_session} ({sessions_back} turns ago)")
    print(f"  Last activity: {last_ts}")
    print(f"  Brain: {total} turns | {log_size} | created {entries[0].get('ts','?')[:10]}")
    print()

    if garden_seeds:
        print(f"  🌱 Garden seeds:")
        for s in garden_seeds:
            print(f"     {s}")
        print()

    if branches:
        print(f"  🌿 Active branches: {len(branches)}")
        for b in branches:
            print(f"     {b.stem}")
        print()

    if recent_s:
        print(f"  ★ Recent S-tier entries:")
        for e in recent_s[-3:]:
            typ = e.get("type", "?")
            user = e.get("user", "")[:70]
            print(f"     [{typ:10s}] {user}")
        print()

    # Latest finding
    findings = [e for e in entries if e.get("type") == "finding" and e.get("finding", {}).get("severity") in ("critical", "high")]
    if findings:
        latest = findings[-1]
        f = latest.get("finding", {})
        print(f"  ⚠ Latest critical finding:")
        print(f"     {f.get('type')} on {f.get('target')} — {f.get('detail','')[:60]}")
        print()

    # Next actions / pattern hints
    if garden_seeds:
        near_threshold = [s for s in garden_seeds if any(f"/{p}" in s for p in ["2/3"])]
        if near_threshold:
            print(f"  💡 Patterns near threshold — ask user if they want a skill.")
            print()

    print(f"  {'─'*50}")
    print(f"  Full log: cat ~/Documents/mycelium/log.jsonl")
    print(f"  CLI:     python3 ~/Documents/mycelium/scripts/mycelium.py status")


# ─── Integrity verify ────────────────────────────────────────
def cmd_verify():
    entries = load_log()
    if not entries:
        print("No entries to verify.")
        return

    if not entries[0].get("hash"):
        print("v1 format — no integrity chain. Run `mycelium migrate` to upgrade.")
        return

    errors = 0
    prev_hash = ""
    for i, e in enumerate(entries):
        stored_hash = e.get("hash", "")
        stored_prev = e.get("prev_hash", "")
        expected_hash = compute_hash(e, stored_prev)

        if stored_prev != prev_hash:
            print(f"✗ Turn {e['turn']}: prev_hash mismatch (expected {prev_hash[:8]}, got {stored_prev[:8]})")
            errors += 1
        if stored_hash != expected_hash:
            print(f"✗ Turn {e['turn']}: hash mismatch (expected {expected_hash}, got {stored_hash})")
            errors += 1

        prev_hash = stored_hash

    if errors == 0:
        print(f"✅ Integrity chain valid — {len(entries)} turns, all hashes match.")
    else:
        print(f"✗ {errors} integrity error(s) found.")


# ─── Archive (compaction) ────────────────────────────────────
def cmd_archive(days=30):
    entries = load_log()
    if not entries:
        print("No entries to archive.")
        return

    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    active = []
    archive_batches = defaultdict(list)  # session -> entries

    for e in entries:
        ts_str = e.get("ts", "")
        try:
            ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            ts = datetime.datetime.utcnow()

        if ts < cutoff:
            archive_batches[e.get("session", "orphan")].append(e)
        else:
            active.append(e)

    if not archive_batches:
        print(f"No sessions older than {days} days to archive.")
        return

    ARCHIVE.mkdir(parents=True, exist_ok=True)

    old_turns = 0
    for session, batch in archive_batches.items():
        # Archive raw entries
        month = batch[0].get("ts", "unknown")[:7]
        archive_path = ARCHIVE / f"log.{month}.{session}.jsonl"
        save_log(batch, archive_path)

        # Replace with summary entry in main log
        types = Counter(e.get("type", "?") for e in batch)
        findings = [e for e in batch if e.get("type") == "finding"]
        entities = Counter()
        for e in batch:
            for ent in e.get("entities", extract_entities(e.get("user", "") + " " + e.get("assistant", ""))):
                entities[ent] += 1

        summary = {
            "turn": len(active) + 1,
            "type": "gardener",
            "tier": "B",
            "session": f"archived-{session}",
            "ts": batch[0].get("ts", "?"),
            "entities": [e for e, _ in entities.most_common(5)],
            "user": f"[ARCHIVED] Session '{session}' — {len(batch)} turns, {len(findings)} findings",
            "assistant": f"Archived to {archive_path.name}. Types: {dict(types)}. Entities: {', '.join(e for e,_ in entities.most_common(5))}",
            "prev_hash": active[-1]["hash"] if active else "",
        }
        summary["hash"] = compute_hash(summary, summary["prev_hash"])
        active.append(summary)
        old_turns += len(batch)

    save_log(active)
    rebuild_index(active)

    print(f"Archived {old_turns} turns from {len(archive_batches)} session(s).")
    print(f"Active log: {len(active)} turns (including {len(archive_batches)} summary entries).")
    print(f"Raw archives in: {ARCHIVE}/")
    print(f"Nothing deleted — ever. Archives available for grep: `grep ... archive/*`")


# ─── Search ──────────────────────────────────────────────────
def cmd_search(query):
    conn = init_index()
    cur = conn.execute(
        "SELECT turn, tier, type, session, ts, summary FROM turns WHERE ts LIKE ? OR session LIKE ? OR summary LIKE ? ORDER BY turn DESC LIMIT 20",
        (f"%{query}%", f"%{query}%", f"%{query}%")
    )
    results = cur.fetchall()

    # Also search entities
    cur2 = conn.execute(
        "SELECT t.turn, t.tier, t.type, t.session, t.ts, t.summary FROM turns t JOIN entities e ON t.turn=e.turn WHERE e.entity LIKE ? ORDER BY t.turn DESC LIMIT 10",
        (f"%{query}%",)
    )
    results.extend(cur2.fetchall())

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r[0] not in seen:
            seen.add(r[0])
            unique.append(r)

    conn.close()

    if not unique:
        print(f"No results for '{query}'.")
        return

    print(f"Search results for '{query}':")
    print(f"{'Turn':>5} {'Tier':4s} {'Type':12s} {'Session':25s} {'Summary'}")
    print("-" * 90)
    for r in unique:
        turn, tier, typ, session, ts, summary = r
        print(f"{turn:>5} {tier:4s} {typ:12s} {session[:25]:25s} {(summary or '')[:60]}")


# ─── Main dispatcher ────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(textwrap.dedent("""\
            🍄 Mycelium — unified CLI

            Commands:
              status              Brain stats
              resume              Smart session resume
              verify              Integrity chain check
              reindex             Rebuild SQLite index
              archive [days=30]   Compact old sessions, archive raw entries
              search <query>      Search log + index
              migrate             Upgrade log to v2 format
        """))
        return

    cmd = sys.argv[1]

    if cmd == "migrate":
        migrate()
    elif cmd == "status":
        cmd_status()
    elif cmd == "resume":
        cmd_resume()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "reindex":
        entries = load_log()
        t, et = rebuild_index(entries)
        print(f"Rebuilt index: {t} turns, {et} entities.")
    elif cmd == "archive":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        cmd_archive(days)
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("Usage: mycelium search <query>")
            return
        cmd_search(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

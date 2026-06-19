#!/usr/bin/env python3
"""
Mycelium — unified CLI v2.

Commands:
  status          — brain stats summary
  resume          — smart session resume (for AI agent injection)
  verify          — check integrity chain
  reindex         — rebuild SQLite index from log
  archive         — compact old sessions (never deletes)
  search <query>  — search across log + index
  migrate         — upgrade existing log to v2 format

Changes (v2-optimize):
  - Shared constants/functions from mycelium_lib.py
  - No more duplicated entity/tier/hash logic
"""
import json, sys, datetime, textwrap
from pathlib import Path
from collections import defaultdict, Counter

# Bootstrap: add source root to sys.path for paths_service import
SOURCE_PARENT = Path(__file__).resolve().parents[1]
if str(SOURCE_PARENT) not in sys.path:
    sys.path.insert(0, str(SOURCE_PARENT))

# Import shared lib (same directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import (
    MYCELIUM, LOG, INDEX, ARCHIVE, BRANCHES,
    extract_entities, classify_tier, compute_hash,
    load_log, save_log, init_index, rebuild_index,
    DEFAULT_TIER,
)

# Import semantic memory layer
from mycelium_memory import (
    init_tables, insert_fact, recall_facts, search_facts,
    create_snapshot, get_snapshot, last_snapshot, fact_stats
)

# Daemon health check (replaced Python web.backend after Go migration)
DAEMON_URL = "http://127.0.0.1:20151"


def daemon_health():
    """Check Go myceliumd health via /health endpoint."""
    try:
        import urllib.request, json
        req = urllib.request.Request(f"{DAEMON_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            if data.get("ok"):
                return []
            return [{"message": f"Daemon unhealthy: {data}"}]
    except Exception as e:
        return [{"message": f"Cannot reach myceliumd at {DAEMON_URL}: {e}"}]


def print_daemon_health():
    warnings = daemon_health()
    if not warnings:
        return
    print("MYCELIUM DAEMON WARNING", file=sys.stderr)
    for warning in warnings:
        print(f"  {warning['message']}", file=sys.stderr)


# ─── Migration to v2 ─────────────────────────────────────────
def migrate():
    entries = load_log()
    if not entries:
        print("No entries to migrate.")
        return

    if entries[0].get("tier"):
        print("Already v2 format. Nothing to migrate.")
        return

    prev_hash = ""
    for e in entries:
        e["tier"] = classify_tier(e)
        e["entities"] = extract_entities(e.get("user", "") + " " + e.get("assistant", ""))
        e["prev_hash"] = prev_hash
        e["hash"] = compute_hash(e, prev_hash)
        prev_hash = e["hash"]

    save_log(entries)

    t, et = rebuild_index(entries)
    print(f"Migrated {len(entries)} turns to v2.")
    print(f"Index rebuilt: {t} turns, {et} entities.")

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    print(f"Archive dir ready: {ARCHIVE}")
    return entries


# ─── Status ──────────────────────────────────────────────────
def cmd_status():
    entries = load_log()
    if not entries:
        print("Mycelium: empty brain.")
        return

    v2 = entries[0].get("tier", False)
    ts_first = entries[0].get("ts", "?")[:10]
    ts_last = entries[-1].get("ts", "?")[:10]
    types = Counter(e.get("type", "?") for e in entries)
    tiers = Counter(e.get("tier", classify_tier(e)) for e in entries)
    sessions = len(set(e.get("session", "?") for e in entries))
    total_bytes = sum(len(json.dumps(e, ensure_ascii=False)) for e in entries)
    findings = [e for e in entries if e.get("type") == "finding"]

    all_entities = Counter()
    for e in entries:
        for ent in e.get("entities", extract_entities(e.get("user", "") + " " + e.get("assistant", ""))):
            all_entities[ent] += 1

    print(f"MyC Mycelium — Brain Status")
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

    if ARCHIVE.exists():
        archive_files = sorted(ARCHIVE.glob("log.jsonl.*"))
        if archive_files:
            archive_turns = sum(len(load_log(f)) for f in archive_files)
            print(f"  Archived: {len(archive_files)} file(s), ~{archive_turns} turns")
    print()


# ─── Resume ──────────────────────────────────────────────────
def cmd_resume():
    """Smart resume — structured summary, S-tier focus, garden state."""
    entries = load_log()
    if not entries:
        print("Mycelium brain empty. Starting fresh.")
        return

    last = entries[-1]
    last_session = last.get("session", "?")
    last_ts = last.get("ts", "?")[:16]

    current_session = last_session
    sessions_back = 0
    for e in reversed(entries):
        if e.get("session") != current_session:
            break
        sessions_back += 1

    s_tiers = [e for e in entries if e.get("tier", classify_tier(e)) == "S"]
    recent_s = s_tiers[-5:] if len(s_tiers) > 5 else s_tiers

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

    branches = list(BRANCHES.glob("*.jsonl")) if BRANCHES.exists() else []

    total = len(entries)
    log_size = f"{sum(len(json.dumps(e)) for e in entries)/1024:.1f} KB"

    print(f"Mycelium Resume")
    print(f"{'='*50}")
    print(f"  Last session: {last_session} ({sessions_back} turns ago)")
    print(f"  Last activity: {last_ts}")
    print(f"  Brain: {total} turns | {log_size} | created {entries[0].get('ts','?')[:10]}")
    print()

    if garden_seeds:
        print(f"  Garden seeds:")
        for s in garden_seeds:
            print(f"     {s}")
        print()

    if branches:
        print(f"  Active branches: {len(branches)}")
        for b in branches:
            print(f"     {b.stem}")
        print()

    if recent_s:
        print(f"  Recent S-tier entries:")
        for e in recent_s[-3:]:
            typ = e.get("type", "?")
            user = e.get("user", "")[:70]
            print(f"     [{typ:10s}] {user}")
        print()

    findings = [e for e in entries if e.get("type") == "finding" and e.get("finding", {}).get("severity") in ("critical", "high")]
    if findings:
        latest = findings[-1]
        f = latest.get("finding", {})
        print(f"  Latest critical finding:")
        print(f"     {f.get('type')} on {f.get('target')} — {f.get('detail','')[:60]}")
        print()

    if garden_seeds:
        near_threshold = [s for s in garden_seeds if any(f"/{p}" in s for p in ["2/3"])]
        if near_threshold:
            print(f"  Patterns near threshold — ask user if they want a skill.")
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
    archive_batches = defaultdict(list)

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
        month = batch[0].get("ts", "unknown")[:7]
        archive_path = ARCHIVE / f"log.{month}.{session}.jsonl"
        save_log(batch, archive_path)

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

    cur2 = conn.execute(
        "SELECT t.turn, t.tier, t.type, t.session, t.ts, t.summary FROM turns t JOIN entities e ON t.turn=e.turn WHERE e.entity LIKE ? ORDER BY t.turn DESC LIMIT 10",
        (f"%{query}%",)
    )
    results.extend(cur2.fetchall())

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


# ── Semantic Memory commands ──────────────────────────────

def _init_memory():
    """Ensure memory tables exist."""
    init_tables()


def _cmd_fact():
    """Manage memory facts.
    Usage: mycelium fact list [--type credential|decision|idea|preference]
           mycelium fact add <entity> <attribute> <value> [--type fact]
           mycelium fact search <query>
           mycelium fact stats
    """
    _init_memory()

    if len(sys.argv) < 3:
        print("Usage:")
        print("  mycelium fact list [--type <type>]")
        print("  mycelium fact add <entity> <attr> <value> [--type <type>]")
        print("  mycelium fact search <query>")
        print("  mycelium fact stats")
        return

    sub = sys.argv[2]

    if sub == "stats":
        stats = fact_stats()
        print("🧠 Memory Fact Stats")
        print("-" * 40)
        print(f"  Total facts:    {stats['total_facts']}")
        print(f"  By type:")
        for t, c in sorted(stats["by_type"].items()):
            print(f"    {t}: {c}")
        print(f"  By tier:")
        for t, c in sorted(stats["by_tier"].items()):
            print(f"    {t}: {c}")
        print(f"  Snapshots:      {stats['total_snapshots']}")
        return

    if sub == "list":
        ftype = None
        if "--type" in sys.argv:
            ftype = sys.argv[sys.argv.index("--type") + 1]
        facts = recall_facts(fact_type=ftype, limit=30)
        if not facts:
            print("No facts found.")
            return
        print(f"{'Type':14s} {'Entity':20s} {'Attribute':20s} {'Value':40s} {'Conf':5s} {'Tier'}")
        print("-" * 105)
        for f in facts:
            val = f["value"][:38]
            print(f"{f['fact_type']:14s} {f['entity'][:20]:20s} {f['attribute'][:20]:20s} "
                  f"{val:40s} {f['confidence']:.2f}  T{f['tier']}")
        return

    if sub == "search":
        if len(sys.argv) < 4:
            print("Usage: mycelium fact search <query>")
            return
        query = sys.argv[3]
        facts = search_facts(query)
        if not facts:
            print(f"No facts matching '{query}'.")
            return
        print(f"{'Type':14s} {'Entity':20s} {'Attribute':20s} {'Value':40s}")
        print("-" * 100)
        for f in facts:
            val = f["value"][:38]
            print(f"{f['fact_type']:14s} {f['entity'][:20]:20s} {f['attribute'][:20]:20s} {val:40s}")
        return

    if sub == "add":
        if len(sys.argv) < 6:
            print("Usage: mycelium fact add <entity> <attribute> <value> [--type fact]")
            return
        entity, attr, value = sys.argv[3], sys.argv[4], sys.argv[5]
        ftype = "fact"
        if "--type" in sys.argv:
            ftype = sys.argv[sys.argv.index("--type") + 1]
        ok = insert_fact(entity, attr, value, fact_type=ftype)
        print(f"{'✅' if ok else '📌'} fact {'inserted' if ok else 'updated'}: {entity}.{attr} = {value[:60]}")
        return

    print(f"Unknown fact subcommand: {sub}")


def _cmd_recall():
    """Recall facts from semantic memory using natural language.
    Usage: mycelium recall <question>
    """
    _init_memory()

    if len(sys.argv) < 3:
        print("Usage: mycelium recall <question>")
        print("Example: mycelium recall what is the metabase api key")
        return

    question = " ".join(sys.argv[2:])

    # Try LLM-powered query translation
    try:
        from mycelium_llm import query_to_sql
        sql = query_to_sql(question)
    except ImportError:
        sql = None

    if sql:
        import sqlite3
        from mycelium_lib import INDEX
        try:
            db = sqlite3.connect(str(INDEX))
            db.row_factory = sqlite3.Row
            rows = db.execute(sql).fetchall()
            db.close()
            if rows:
                print(f"🔍 {question}")
                print("-" * 60)
                for r in rows:
                    d = dict(r)
                    print(f"  [{d.get('fact_type','?')}] {d.get('entity','')}.{d.get('attribute','')} = {d.get('value','')}")
                    if d.get('confidence'):
                        print(f"       confidence={d['confidence']:.2f}  tier=T{d.get('tier','?')}  session={d.get('source_session','')}")
                return
        except Exception:
            pass

    # Fallback: direct fact search with word-level matching
    import re
    words = [w.lower() for w in re.findall(r'\w+', question) if len(w) > 2]
    facts = []
    if words:
        from mycelium_lib import INDEX
        import sqlite3
        db = sqlite3.connect(str(INDEX))
        db.row_factory = sqlite3.Row
        # Search for any matching word across entity/attribute/value
        conditions = " OR ".join(["(entity LIKE ? OR attribute LIKE ? OR value LIKE ?)" for _ in words])
        params = []
        for w in words:
            params.extend([f"%{w}%", f"%{w}%", f"%{w}%"])
        try:
            rows = db.execute(f"""
                SELECT * FROM memory_facts
                WHERE {conditions}
                ORDER BY confidence DESC, tier ASC, updated_at DESC
                LIMIT 15
            """, params).fetchall()
            facts = [dict(r) for r in rows]
        except Exception:
            pass
        db.close()

    if facts:
        print(f"🔍 {question}")
        print("-" * 60)
        for f in facts[:10]:
            print(f"  [{f['fact_type']}] {f['entity']}.{f['attribute']} = {f['value'][:80]}")
        return

    # Last fallback: full brain search
    print(f"⚠️ No facts found for '{question}'. Try mycelium search '{question}' for brain log search.")


def _cmd_snapshot():
    """Create a context snapshot of a session.
    Usage: mycelium snapshot [--session <session_id>]
           mycelium snapshot list
    """
    _init_memory()

    if len(sys.argv) >= 3 and sys.argv[2] == "list":
        import sqlite3
        from mycelium_lib import INDEX
        db = sqlite3.connect(str(INDEX))
        rows = db.execute("""
            SELECT session_id, summary, created_at, turn_count
            FROM context_snapshots ORDER BY created_at DESC LIMIT 20
        """).fetchall()
        db.close()
        if not rows:
            print("No snapshots yet.")
            return
        print(f"{'Session':30s} {'Turns':6s} {'Summary'}")
        print("-" * 90)
        for r in rows:
            print(f"{r[0][:30]:30s} {str(r[3] or 0):6s} {(r[1] or '')[:50]}")
        return

    # Generate snapshot from recent log entries
    from mycelium_lib import load_log
    entries = load_log()

    # Get last non-trivial session
    sessions = {}
    for e in entries:
        sid = e.get("session", "unknown")
        if sid not in sessions:
            sessions[sid] = []
        sessions[sid].append(e)

    # Find the most recent session with > 2 entries
    target_session = None
    target_entries = []
    for sid in reversed(list(sessions.keys())):
        if len(sessions[sid]) > 2 and sid != "mycelium-auto":
            target_session = sid
            target_entries = sessions[sid]
            break

    if not target_session:
        print("No significant session found for snapshot.")
        return

    # Try LLM-powered summary
    try:
        from mycelium_llm import summarize_session, extract_facts
        texts = [json.dumps(e) for e in target_entries]

        summary = summarize_session(texts, target_session)
        if summary:
            snapshot_data = {
                "session_id": target_session,
                "summary": summary.get("summary", ""),
                "topics": summary.get("topics", []),
                "decisions": summary.get("decisions", []),
                "entities": summary.get("entities", []),
                "credentials": summary.get("credentials", []),
                "turn_count": len(target_entries),
                "last_turn_hash": target_entries[-1].get("hash", ""),
            }
            create_snapshot(**snapshot_data)
            print(f"✅ Snapshot created for {target_session}")
            print(f"   Summary: {summary.get('summary', '')[:80]}")

            # Also extract facts
            for fact in extract_facts(texts, target_session):
                insert_fact(
                    entity=fact.get("entity", "unknown"),
                    attribute=fact.get("attribute", "value"),
                    value=str(fact.get("value", "")),
                    fact_type=fact.get("fact_type", "fact"),
                    confidence=float(fact.get("confidence", 0.5)),
                    source_session=target_session,
                    entropy=float(fact.get("entropy", 0.5)),
                )
            print(f"   Facts extracted: ✓")
            return
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️ LLM summary failed: {e}")

    # Fallback: basic snapshot without LLM
    from mycelium_lib import extract_entities
    all_text = " ".join(e.get("user", "") + " " + e.get("assistant", "") for e in target_entries[-10:])
    ents = extract_entities(all_text)
    create_snapshot(
        session_id=target_session,
        summary=f"Session with {len(target_entries)} turns",
        topics=ents[:8],
        entities=ents[:12],
        turn_count=len(target_entries),
        last_turn_hash=target_entries[-1].get("hash", ""),
    )
    print(f"📝 Basic snapshot created for {target_session} ({len(target_entries)} turns)")


def _cmd_context():
    """Show last session context.
    Usage: mycelium context
    """
    _init_memory()

    snap = last_snapshot()
    if not snap:
        print("No context snapshots yet. Run 'mycelium snapshot' first.")
        return

    print("🧠 Last Session Context")
    print("=" * 60)
    print(f"  Session:    {snap.get('session_id')}")
    print(f"  Summary:    {snap.get('summary', '')}")
    print(f"  Turns:      {snap.get('turn_count', 0)}")
    print(f"  Created:    {snap.get('created_at', '')}")

    topics = snap.get("topics", [])
    if topics:
        print(f"\n  Topics:     {', '.join(topics[:8])}")

    decisions = snap.get("decisions", [])
    if decisions:
        print(f"\n  Decisions:")
        for d in decisions:
            print(f"    • {d}")

    credentials = snap.get("credentials", [])
    if credentials:
        print(f"\n  Credentials:")
        for c in credentials:
            svc = c.get("service", c.get("entity", "?"))
            typ = c.get("type", "?")
            val = c.get("value", "?")
            print(f"    • {svc} ({typ}): {val}")

    # Show hot-tier facts
    facts = recall_facts(tier=0, limit=8)
    if facts:
        print(f"\n  Hot facts:")
        for f in facts:
            print(f"    [{f['fact_type']}] {f['entity']}.{f['attribute']} = {str(f['value'])[:60]}")

    print(f"\n  Run 'mycelium recall <question>' to query facts")


def _cmd_infer():
    """Run cross-session pattern inference.
    Usage: mycelium infer
    """
    try:
        from mycelium_inference import infer_patterns, print_insights
        insights = infer_patterns()
        print_insights(insights)
    except ImportError:
        print("Inference engine not available.")
    except Exception as e:
        import traceback
        print(f"Inference error: {e}")
        traceback.print_exc()


# ─── Main dispatcher ────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(textwrap.dedent("""\
            Mycelium — unified CLI

            Commands:
              status              Brain stats
              resume              Smart session resume
              verify              Integrity chain check
              reindex             Rebuild SQLite index
              archive [days=30]   Compact old sessions, archive raw entries
              search <query>      Search log + index
              migrate             Upgrade log to v2 format

  Memory:
              fact                Manage memory facts (list, add, search, stats)
              recall <question>   Semantic recall via natural language
              snapshot            Create context snapshot of last session
              context             Show last session context + hot facts
              compact             Entropy-weighted memory compaction
              infer               Cross-session pattern inference
        """))
        return

    cmd = sys.argv[1]
    print_daemon_health()

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

    # ── Semantic Memory subcommands ──
    elif cmd == "fact":
        _cmd_fact()
    elif cmd == "recall":
        _cmd_recall()
    elif cmd == "snapshot":
        _cmd_snapshot()
    elif cmd == "context":
        _cmd_context()
    elif cmd == "compact":
        from mycelium_memory import full_compact
        full_compact()
    elif cmd == "infer":
        _cmd_infer()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

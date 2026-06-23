#!/usr/bin/env python3
"""
🧬 evolution.py — LLM Self-Evolution Engine

Watches for agent failure patterns, generates prompt patches,
injects them into future sessions. The agent gets smarter over time
without fine-tuning.

Subcommands:
  watch    <message>           Detect correction signals in user message
  log      <args>              Log a failure event
  cluster                      Group failures into patterns
  generate [--pattern NAME]    Generate prompt patches from patterns
  load                         Return active patches for session injection
  evaluate [--batch]           Check if patches are working, retire/reinforce
  status                       Dashboard: patches, failures, patterns

Data files:
  evolution/failures.jsonl     Raw failure events
  evolution/patches.jsonl      Generated + tracked patches
  evolution/stats.db           SQLite for fast pattern queries
"""
from __future__ import annotations

import argparse, json, os, re, sqlite3, sys, time, uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import MYCELIUM, EVOLUTION_DIR as EVO_DIR
FAILURES_LOG = EVO_DIR / "failures.jsonl"
PATCHES_LOG = EVO_DIR / "patches.jsonl"
STATS_DB = EVO_DIR / "stats.db"

# ── Correction Signal Patterns (pre-compiled) ──

CORRECTION_PATTERNS = [
    # Explicit corrections
    (re.compile(r"\bthat'?s\s+(?:wrong|incorrect|not right)\b", re.I), "explicit-correction"),
    (re.compile(r"\bno[,.]?\s+(?:that|this|it)\s+(?:is|'s)\s+wrong\b", re.I), "explicit-correction"),
    (re.compile(r"\bincorrect\b", re.I), "explicit-correction"),
    (re.compile(r"\bthat'?s\s+not\s+(?:correct|right|how)\b", re.I), "explicit-correction"),
    # Memory failures
    (re.compile(r"\byou\s+forgot\b", re.I), "memory-failure"),
    (re.compile(r"\balready\s+(?:told|said|mentioned)\b", re.I), "memory-failure"),
    (re.compile(r"\bI\s+(?:already|just)\s+(?:told|said|mentioned)\b", re.I), "memory-failure"),
    (re.compile(r"\bwhy\s+(?:didn'?t|don'?t)\s+you\s+(?:remember|check|do)\b", re.I), "memory-failure"),
    (re.compile(r"\byou(?:'re|\s+are)\s+not\s+(?:doing|following|checking|listening)\b", re.I), "memory-failure"),
    (re.compile(r"\bagain\b.*\b(?:forgot|missed|skipped)\b", re.I), "memory-failure"),
    # Behavioral drift
    (re.compile(r"\bI\s+said\s+(?:don'?t|no|stop|not)\b", re.I), "behavioral-drift"),
    (re.compile(r"\b(?:stop|quit)\s+(?:doing|making|using)\b", re.I), "behavioral-drift"),
    (re.compile(r"\bhow\s+many\s+times\b", re.I), "behavioral-drift"),
    # Repeated asks
    (re.compile(r"\bI\s+(?:just|already)\s+asked\b", re.I), "repeated-ask"),
    (re.compile(r"\basked\s+(?:you|this)\s+(?:already|before)\b", re.I), "repeated-ask"),
    # Tool/process failures
    (re.compile(r"\bthat(?:'s|\s+is)\s+not\s+(?:the\s+)?(?:right\s+)?(?:port|url|path|file|endpoint)\b", re.I), "wrong-context"),
    (re.compile(r"\bwrong\s+(?:port|url|path|file|endpoint)\b", re.I), "wrong-context"),
    (re.compile(r"\bcheck\s+(?:the\s+)?(?:mycelium|append|daemon)\b", re.I), "append-discipline"),
    (re.compile(r"\bmycelium\b.*\b(?:running|alive|working|status)\b", re.I), "append-discipline"),
]

# Category → description mapping
CATEGORY_DESCRIPTIONS = {
    "explicit-correction": "Agent stated something incorrect",
    "memory-failure": "Agent forgot information user already provided",
    "behavioral-drift": "Agent doing what it was told not to",
    "repeated-ask": "User had to ask the same thing twice",
    "wrong-context": "Agent used wrong port/path/URL/endpoint",
    "append-discipline": "Agent forgot mycelium append or precheck",
}

PATCH_TEMPLATES = {
    "explicit-correction": "Verify factual claims before stating them. If unsure, say 'I'm not certain' instead of asserting.",
    "memory-failure": "Check mycelium resume and conversation context before responding. If user mentioned something before, reference it — don't make them repeat.",
    "behavioral-drift": "Re-read user constraints at session start. If user said 'don't do X', maintain that constraint throughout the session.",
    "repeated-ask": "When user asks something, give a complete answer the first time. If they ask again, you missed something — re-read their original message.",
    "wrong-context": "Don't assume ports, URLs, paths, or endpoints. Verify from config, mycelium memory, or ask the user. Wrong assumptions waste time.",
    "append-discipline": "ALWAYS run `python3 ~/Documents/mycelium/scripts/append.py` after every meaningful response. Run precheck.py at session start. No exceptions.",
}


def _ensure_dirs():
    """Lazy init — create evolution dir only when needed."""
    EVO_DIR.mkdir(parents=True, exist_ok=True)


def detect_corrections(message: str) -> list:
    """Scan a user message for correction signals."""
    seen = set()
    unique = []
    for compiled_pat, category in CORRECTION_PATTERNS:
        if category not in seen and compiled_pat.search(message):
            seen.add(category)
            unique.append({"pattern": compiled_pat.pattern, "category": category})
    return unique


# ── Failure Logger ──

def log_failure(session: str, category: str, user_msg: str, correction: str, context: str = "") -> dict:
    """Append a failure event to failures.jsonl."""
    _ensure_dirs()
    entry = {
        "id": f"f-{uuid.uuid4().hex[:8]}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "category": category,
        "user_msg": user_msg[:300],
        "correction": correction[:300],
        "context": context[:200],
    }
    with open(FAILURES_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    _update_stats_db(entry)
    return entry


def _get_stats_db():
    """Get SQLite connection, creating tables if needed."""
    _ensure_dirs()
    conn = sqlite3.connect(str(STATS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            id TEXT PRIMARY KEY, ts TEXT, session TEXT,
            category TEXT, user_msg TEXT, correction TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns (
            category TEXT PRIMARY KEY, hit_count INTEGER DEFAULT 0,
            first_seen TEXT, last_seen TEXT, patch_id TEXT
        )
    """)
    conn.commit()
    return conn


def _update_stats_db(entry: dict):
    """Update SQLite stats for fast pattern queries."""
    conn = _get_stats_db()
    conn.execute("""
        INSERT OR IGNORE INTO failures (id, ts, session, category, user_msg, correction)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entry["id"], entry["ts"], entry["session"], entry["category"],
          entry["user_msg"], entry["correction"]))
    conn.execute("""
        INSERT INTO patterns (category, hit_count, first_seen, last_seen)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(category) DO UPDATE SET
            hit_count = hit_count + 1,
            last_seen = excluded.last_seen
    """, (entry["category"], entry["ts"], entry["ts"]))
    conn.commit()
    conn.close()


# ── Pattern Clusterer ──

def cluster_patterns() -> list:
    """Group failures by category and return pattern summary."""
    if not STATS_DB.exists():
        return []
    conn = sqlite3.connect(str(STATS_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT category, hit_count, first_seen, last_seen, patch_id
        FROM patterns ORDER BY hit_count DESC
    """).fetchall()
    conn.close()
    patterns = []
    for r in rows:
        patterns.append({
            "category": r["category"],
            "hit_count": r["hit_count"],
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "patch_id": r["patch_id"],
            "description": CATEGORY_DESCRIPTIONS.get(r["category"], "Unknown pattern"),
            "threshold": 2,
            "at_threshold": r["hit_count"] >= 2,
        })
    return patterns


# ── Patch Generator ──

def _load_patches() -> list:
    """Load all patches from patches.jsonl."""
    if not PATCHES_LOG.exists():
        return []
    patches = []
    for line in PATCHES_LOG.read_text().strip().split("\n"):
        if line.strip():
            patches.append(json.loads(line))
    return patches


def _save_patches(patches: list):
    """Atomically overwrite patches.jsonl (write tmp + rename)."""
    _ensure_dirs()
    tmp = PATCHES_LOG.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for p in patches:
            f.write(json.dumps(p) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.rename(str(tmp), str(PATCHES_LOG))


def generate_patch(category: str, force: bool = False) -> dict | None:
    """Generate a prompt patch for a pattern category."""
    patterns = cluster_patterns()
    pattern = next((p for p in patterns if p["category"] == category), None)
    if not pattern:
        return None
    if not force and pattern["hit_count"] < 2:
        return None  # not at threshold yet

    # Check if patch already exists for this category
    existing = _load_patches()
    for p in existing:
        if p["pattern"] == category and p["status"] == "active":
            # strengthen existing patch
            p["hit_count"] = pattern["hit_count"]
            p["last_seen"] = pattern["last_seen"]
            p["strength"] = _escalate_strength(p["strength"])
            _save_patches(existing)
            return p

    # Generate new patch
    patch = {
        "id": f"patch-{uuid.uuid4().hex[:8]}",
        "pattern": category,
        "constraint": PATCH_TEMPLATES.get(category, f"Avoid repeating {category} failures."),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "hit_count": pattern["hit_count"],
        "last_seen": pattern["last_seen"],
        "status": "active",
        "clean_sessions": 0,
        "strength": "soft",
    }
    existing.append(patch)
    _save_patches(existing)

    # Link patch to pattern in stats db
    if STATS_DB.exists():
        conn = sqlite3.connect(str(STATS_DB))
        conn.execute("UPDATE patterns SET patch_id = ? WHERE category = ?",
                     (patch["id"], category))
        conn.commit()
        conn.close()

    return patch


def _escalate_strength(current: str) -> str:
    """soft → hard → critical"""
    if current == "soft":
        return "hard"
    if current == "hard":
        return "critical"
    return "critical"


# ── Patch Loader ──

def load_patches() -> str:
    """Return active patches formatted for session injection."""
    patches = _load_patches()
    active = [p for p in patches if p["status"] == "active"]
    if not active:
        return ""

    # Sort: critical first, then hard, then soft
    strength_order = {"critical": 0, "hard": 1, "soft": 2}
    active.sort(key=lambda p: strength_order.get(p["strength"], 3))

    lines = ["🧬 Active Evolution Patches:"]
    for p in active:
        icon = {"critical": "🔴", "hard": "🟡", "soft": "🟢"}.get(p["strength"], "⚪")
        lines.append(f"  {icon} [{p['strength']}] {p['constraint']}")
    return "\n".join(lines)


# ── Evaluator ──

def _count_recent_failures(days: int = 7) -> Counter:
    """Count failures per category within the last N days."""
    counts = Counter()
    if not FAILURES_LOG.exists():
        return counts
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for line in FAILURES_LOG.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if ts >= cutoff:
                counts[entry["category"]] += 1
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return counts


def evaluate(session: str = "", batch: bool = False) -> dict:
    """Check if patches are working. Retire after 5 clean sessions, reinforce on recurrence."""
    patches = _load_patches()
    if not patches:
        return {"message": "no patches to evaluate"}

    recent_failures = _count_recent_failures(days=7)

    results = []
    patches_to_generate = []

    for p in patches:
        if p["status"] != "active":
            continue

        category = p["pattern"]
        recent_hits = recent_failures.get(category, 0)

        if recent_hits == 0:
            # No recurrence — increment clean sessions
            p["clean_sessions"] = p.get("clean_sessions", 0) + 1
            if p["clean_sessions"] >= 5:
                p["status"] = "retired"
                results.append(f"  ✅ {category}: retired after 5 clean sessions")
            else:
                results.append(f"  🟢 {category}: {p['clean_sessions']}/5 clean sessions")
        else:
            # Recurrence — reset clean count, escalate
            p["clean_sessions"] = 0
            old_strength = p["strength"]
            p["strength"] = _escalate_strength(p["strength"])
            if old_strength != p["strength"]:
                results.append(f"  🔺 {category}: escalated {old_strength} → {p['strength']}")
            else:
                results.append(f"  🔴 {category}: still at max strength ({p['strength']})")

    # Save patches first (evaluate changes)
    _save_patches(patches)

    # Now generate patches for new patterns at threshold
    # (generate_patch reloads patches internally, safe after our save)
    patterns = cluster_patterns()
    for pat in patterns:
        if pat["at_threshold"] and not pat.get("patch_id"):
            patch = generate_patch(pat["category"])
            if patch:
                results.append(f"  🆕 {pat['category']}: new patch generated (threshold reached)")

    return {"evaluations": results, "patch_count": len(patches)}


# ── Status Dashboard ──

def status() -> str:
    """Show evolution engine state at a glance."""
    patches = _load_patches()
    patterns = cluster_patterns()
    active = [p for p in patches if p["status"] == "active"]
    retired = [p for p in patches if p["status"] == "retired"]

    # Recent failures (actual last 24h)
    recent_count = 0
    recent_cats = Counter()
    if FAILURES_LOG.exists():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for line in FAILURES_LOG.read_text().strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["ts"])
                if ts >= cutoff:
                    recent_count += 1
                    recent_cats[entry["category"]] += 1
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

    output = []
    output.append("🧬 Evolution Engine Status")
    output.append("─" * 40)

    # Active patches
    output.append(f"\nActive patches: {len(active)}")
    if active:
        strength_order = {"critical": 0, "hard": 1, "soft": 2}
        active.sort(key=lambda p: strength_order.get(p["strength"], 3))
        for p in active:
            icon = {"critical": "🔴", "hard": "🟡", "soft": "🟢"}.get(p["strength"], "⚪")
            clean = p.get("clean_sessions", 0)
            constraint = p['constraint']
            if len(constraint) > 60:
                constraint = constraint[:60] + "..."
            output.append(f"  {icon} [{p['strength']}] {p['pattern']} — {constraint} ({clean}/5 clean)")

    # Retired patches
    if retired:
        output.append(f"\nRetired patches: {len(retired)}")
        for p in retired:
            output.append(f"  ✓ {p['pattern']}")

    # Recent failures
    output.append(f"\nRecent failures (24h): {recent_count}")
    for cat, count in recent_cats.most_common(5):
        output.append(f"  - {cat}: {count} hit{'s' if count > 1 else ''}")

    # Patterns approaching threshold
    approaching = [p for p in patterns if p["hit_count"] > 0 and not p["at_threshold"]]
    if approaching:
        output.append("\nPatterns approaching threshold:")
        for p in approaching:
            output.append(f"  {p['category']}: {p['hit_count']}/{p['threshold']}")

    return "\n".join(output)


# ── CLI ──

def main():
    # Ensure dirs only on CLI invocation, not import
    _ensure_dirs()

    parser = argparse.ArgumentParser(description="🧬 LLM Self-Evolution Engine")
    sub = parser.add_subparsers(dest="command")

    # watch
    p_watch = sub.add_parser("watch", help="Detect correction signals in message")
    p_watch.add_argument("message", help="User message to scan")

    # log
    p_log = sub.add_parser("log", help="Log a failure event")
    p_log.add_argument("--session", required=True)
    p_log.add_argument("--category", required=True)
    p_log.add_argument("--user", required=True, help="User message")
    p_log.add_argument("--correction", required=True, help="What user corrected")
    p_log.add_argument("--context", default="", help="Additional context")

    # cluster
    sub.add_parser("cluster", help="Group failures into patterns")

    # generate
    p_gen = sub.add_parser("generate", help="Generate prompt patches")
    p_gen.add_argument("--pattern", help="Specific category to patch")
    p_gen.add_argument("--force", action="store_true", help="Force even below threshold")

    # load
    sub.add_parser("load", help="Load active patches for injection")

    # evaluate
    p_eval = sub.add_parser("evaluate", help="Evaluate patch effectiveness")
    p_eval.add_argument("--session", default="")
    p_eval.add_argument("--batch", action="store_true")

    # status
    sub.add_parser("status", help="Dashboard")

    args = parser.parse_args()

    if args.command == "watch":
        signals = detect_corrections(args.message)
        result = {
            "detected": len(signals) > 0,
            "signals": signals,
            "categories": [s["category"] for s in signals],
        }
        print(json.dumps(result, indent=2))

    elif args.command == "log":
        entry = log_failure(args.session, args.category, args.user, args.correction, args.context)
        print(f"✅ Failure logged: {entry['id']} [{args.category}]")

    elif args.command == "cluster":
        patterns = cluster_patterns()
        if not patterns:
            print("No patterns yet.")
        else:
            print("📊 Failure Patterns")
            print("─" * 40)
            for p in patterns:
                icon = "🔴" if p["at_threshold"] else "⚪"
                print(f"  {icon} {p['category']}: {p['hit_count']}/{p['threshold']} — {p['description']}")
            print(f"\n---MACHINE---")
            print(json.dumps(patterns))

    elif args.command == "generate":
        if args.pattern:
            patch = generate_patch(args.pattern, force=args.force)
            if patch:
                print(f"✅ Patch generated/updated: {patch['id']}")
                print(json.dumps(patch, indent=2))
            else:
                print(f"Pattern '{args.pattern}' not at threshold yet. Use --force to override.")
        else:
            patterns = cluster_patterns()
            generated = 0
            for p in patterns:
                if p["at_threshold"]:
                    patch = generate_patch(p["category"])
                    if patch:
                        generated += 1
                        print(f"✅ {p['category']}: patch {patch['id']} ({patch['strength']})")
            if generated == 0:
                print("No patterns at threshold yet.")

    elif args.command == "load":
        output = load_patches()
        if output:
            print(output)
        else:
            print("No active patches.")

    elif args.command == "evaluate":
        result = evaluate(session=args.session, batch=args.batch)
        if result.get("evaluations"):
            print("🧬 Patch Evaluation")
            print("─" * 40)
            for line in result["evaluations"]:
                print(line)
        else:
            print(result.get("message", "Nothing to evaluate."))

    elif args.command == "status":
        print(status())

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

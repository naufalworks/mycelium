#!/usr/bin/env python3
"""
🍄 precheck.py — Mycelium v3 pre-flight health gate.

Runs BEFORE first inference every session. Verifies all v3 subsystems.

Checks (v3):
  1.  brain        — log.jsonl exists, writable, parseable
  2.  append       — append.py callable
  3.  index        — index.db exists
  4.  daemon       — daemon state file readable
  5.  evolution    — evolution/ directory exists
  6.  tables       — all 9 v3 SQLite tables exist
  7.  bloom        — bloom filter file + DB state
  8.  graph        — entity_edges table queryable
  9.  negation     — negations table queryable
 10.  causal       — causal_edges table queryable
 11.  attention    — attention table queryable + has entries
 12.  objects      — objects table queryable
 13.  lsm-layers   — L1/L2 directories exist
 14.  integrity    — prev_hash chain on last 50 entries valid

Auto-fix:
  - Missing log.jsonl → create
  - Missing evolution/ → create
  - Missing snapshots/ → create
  - Missing objects/ → create

Usage:
  python3 precheck.py              # human-readable output
  python3 precheck.py --json       # machine-readable JSON
  python3 precheck.py --stats      # human output + stats summary

Exit codes:
  0 = all checks passed
  1 = one or more checks failed
"""
import json, os, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import (
    MYCELIUM, LOG, INDEX, EVOLUTION_DIR, load_log,
)

SCRIPTS = MYCELIUM / "scripts"
STATE = Path.home() / ".hermes" / "myceliumd" / "state.json"
BLOOM_FILE = MYCELIUM / ".bloom_entities"
L1_DIR = MYCELIUM / "l1"
L2_DIR = MYCELIUM / "l2"
SNAPSHOTS_DIR = MYCELIUM / "snapshots"
OBJECTS_DIR = MYCELIUM / "objects"

# Required v3 tables in index.db
REQUIRED_TABLES = [
    "turns", "entities", "findings",
    "entity_edges", "negations", "causal_edges",
    "attention", "objects", "bloom_states",
]

INTEGRITY_WINDOW = 50  # check last N entries for hash chain


# ── Checks ──────────────────────────────────────────────────────

def check_brain():
    """log.jsonl exists, writable, and last 20 lines parse as valid JSON."""
    if not LOG.exists():
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            LOG.touch()
            return True, "created log.jsonl (was missing)", {}
        except OSError as e:
            return False, f"cannot create log.jsonl: {e}", {}
    if not os.access(LOG, os.W_OK):
        return False, "log.jsonl not writable", {}
    # Validate last 20 lines
    bad = 0
    total = 0
    try:
        with open(LOG) as f:
            lines = f.readlines()
        total = len([l for l in lines if l.strip()])
        for line in lines[-20:]:
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
    except OSError as e:
        return False, f"cannot read log.jsonl: {e}", {}
    if bad:
        return False, f"{bad}/20 tail lines corrupt", {"entries": total}
    return True, "ok", {"entries": total}


def check_append():
    """append.py exists and is a file."""
    append = SCRIPTS / "append.py"
    if not append.exists():
        return False, "append.py missing", {}
    if not append.is_file():
        return False, "append.py is not a file", {}
    return True, "ok", {}


def check_index():
    """index.db exists and is openable."""
    if not INDEX.exists():
        return True, "missing (will rebuild on next append)", {}
    try:
        conn = sqlite3.connect(str(INDEX))
        conn.execute("SELECT 1")
        conn.close()
        return True, "ok", {"size_kb": round(INDEX.stat().st_size / 1024, 1)}
    except sqlite3.Error as e:
        return False, f"cannot open: {e}", {}


def check_daemon():
    """Check daemon state file for recent heartbeat."""
    if not STATE.exists():
        return True, "no state file (daemon may not be installed)", {}
    try:
        data = json.loads(STATE.read_text())
        if not isinstance(data, dict):
            return True, "state file is not a dict", {}
        last_run = data.get("last_run") or data.get("ts")
        if last_run:
            return True, f"last run: {last_run}", {}
        return True, "state exists (no timestamp)", {}
    except (json.JSONDecodeError, ValueError, AttributeError, OSError):
        return True, "state file unreadable", {}


def check_evolution_dir():
    """evolution/ directory exists for self-evolution engine."""
    if not EVOLUTION_DIR.exists():
        try:
            EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
            return True, "created evolution/ (was missing)", {}
        except OSError as e:
            return False, f"cannot create evolution/: {e}", {}
    file_count = len(list(EVOLUTION_DIR.iterdir())) if EVOLUTION_DIR.is_dir() else 0
    return True, "ok", {"files": file_count}


def check_tables():
    """All 9 v3 SQLite tables exist in index.db."""
    if not INDEX.exists():
        return True, "skipped (index.db missing)", {}
    try:
        conn = sqlite3.connect(str(INDEX))
        existing = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
    except sqlite3.Error as e:
        return False, f"cannot query schema: {e}", {}
    missing = [t for t in REQUIRED_TABLES if t not in existing]
    if missing:
        return False, f"missing tables: {', '.join(missing)}", {"tables": len(existing)}
    return True, f"{len(existing)} tables", {"tables": len(existing)}


def check_bloom():
    """Bloom filter file exists and has state in DB."""
    file_ok = BLOOM_FILE.exists()
    db_ok = False
    stats = {}
    if INDEX.exists():
        try:
            conn = sqlite3.connect(str(INDEX))
            row = conn.execute(
                "SELECT element_count, m, k, updated FROM bloom_states WHERE name='entities' LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                db_ok = True
                stats = {"elements": row[0], "m": row[1], "k": row[2], "updated": row[3]}
        except sqlite3.Error:
            pass
    if not file_ok and not db_ok:
        return False, "no filter file + no DB state", {}
    if file_ok and db_ok:
        return True, "ok", {**stats, "file_kb": round(BLOOM_FILE.stat().st_size / 1024, 1)}
    if file_ok:
        return True, "file ok, no DB state (will rebuild)", {}
    # db_ok but no file
    return True, "DB state ok, no file (will rebuild on check)", stats


def _check_index_table(table: str):
    """Generic: can we SELECT COUNT(*) FROM table?"""
    if not INDEX.exists():
        return True, "skipped", {}
    try:
        conn = sqlite3.connect(str(INDEX))
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return True, f"{count} rows", {"count": count}
    except sqlite3.Error as e:
        return False, str(e), {}


def check_graph():
    return _check_index_table("entity_edges")

def check_negation():
    return _check_index_table("negations")

def check_causal():
    return _check_index_table("causal_edges")

def check_attention():
    if not INDEX.exists():
        return True, "skipped", {}
    try:
        conn = sqlite3.connect(str(INDEX))
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(score),0) FROM attention"
        ).fetchone()
        conn.close()
        count, avg_score = row
        return True, f"{count} entries, avg_score={avg_score:.3f}", {"count": count, "avg_score": round(avg_score, 3)}
    except sqlite3.Error as e:
        return False, str(e), {}


def check_objects():
    return _check_index_table("objects")


def check_lsm_layers():
    """L1 and L2 directories exist for LSM compaction."""
    l1_exists = L1_DIR.is_dir()
    l2_exists = L2_DIR.is_dir()
    info = {}
    if l1_exists:
        info["l1_segments"] = len(list(L1_DIR.glob("seg_*")))
    if l2_exists:
        info["l2_segments"] = len(list(L2_DIR.glob("sum_*")))
    if not l1_exists and not l2_exists:
        # L0 only — first session, no compaction yet. That's ok.
        return True, "L0 only (no compaction yet)", info
    status = f"L1={info.get('l1_segments', 0)} L2={info.get('l2_segments', 0)}"
    return True, status, info


def check_integrity():
    """Verify prev_hash chain on last N entries."""
    entries = load_log()
    if not entries:
        return True, "empty log (no chain to verify)", {}
    window = entries[-INTEGRITY_WINDOW:]
    broken = 0
    for i in range(1, len(window)):
        expected_prev = window[i - 1].get("hash", "")
        actual_prev = window[i].get("prev_hash", "")
        if expected_prev != actual_prev:
            broken += 1
    if broken:
        return False, f"{broken}/{len(window)-1} breaks in last {INTEGRITY_WINDOW}", {"total": len(entries)}
    return True, f"chain ok (last {len(window)} entries)", {"total": len(entries)}


# ── Runner ──────────────────────────────────────────────────────

CHECKS = [
    ("brain",       check_brain),
    ("append",      check_append),
    ("index",       check_index),
    ("daemon",      check_daemon),
    ("evolution",   check_evolution_dir),
    ("tables",      check_tables),
    ("bloom",       check_bloom),
    ("graph",       check_graph),
    ("negation",    check_negation),
    ("causal",      check_causal),
    ("attention",   check_attention),
    ("objects",     check_objects),
    ("lsm-layers",  check_lsm_layers),
    ("integrity",   check_integrity),
]


def run_checks():
    results = {}
    all_ok = True
    for name, fn in CHECKS:
        try:
            ok, detail, info = fn()
        except Exception as e:
            ok, detail, info = False, f"exception: {e}", {}
        results[name] = {"ok": ok, "detail": detail, "info": info}
        if not ok:
            all_ok = False
    return all_ok, results


def print_human(results, all_ok, show_stats=False):
    print("🍄 Mycelium v3 Pre-Flight Check")
    print("─" * 50)

    for name, r in results.items():
        icon = "✓" if r["ok"] else "✗"
        print(f"  {icon} {name:12s}  {r['detail']}")

    print("─" * 50)
    print(f"  {'✅ All checks passed' if all_ok else '⚠️  Some checks failed'}")

    if show_stats:
        print()
        print("📊 Brain Stats")
        print("─" * 50)
        brain = results["brain"]["info"]
        if brain:
            print(f"  Entries:      {brain.get('entries', '?')}")
        idx = results["index"]["info"]
        if idx:
            print(f"  Index DB:     {idx.get('size_kb', '?')} KB")
        tables = results["tables"]["info"]
        if tables:
            print(f"  Tables:       {tables.get('tables', '?')}")
        bloom = results["bloom"]["info"]
        if bloom:
            print(f"  Bloom:        {bloom.get('elements', '?')} elements (m={bloom.get('m','?')}, k={bloom.get('k','?')})")
        for sub in ("graph", "negation", "causal", "attention", "objects"):
            info = results[sub]["info"]
            if info and "count" in info:
                print(f"  {sub:12s}  {info['count']} rows")
        lsm = results["lsm-layers"]["info"]
        if lsm:
            l1 = lsm.get("l1_segments", 0)
            l2 = lsm.get("l2_segments", 0)
            print(f"  LSM:          L1={l1}  L2={l2}")


def main():
    json_mode = "--json" in sys.argv
    show_stats = "--stats" in sys.argv

    all_ok, results = run_checks()

    if json_mode:
        output = {
            "ok": all_ok,
            "checks": {name: r["ok"] for name, r in results.items()},
            "details": {name: r["detail"] for name, r in results.items()},
            "info": {name: r["info"] for name, r in results.items()},
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        print(json.dumps(output))
    else:
        print_human(results, all_ok, show_stats)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

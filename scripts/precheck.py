#!/usr/bin/env python3
"""
🍄 precheck.py — Mycelium pre-flight health gate.

Runs BEFORE first inference every session. Verifies:
  1. log.jsonl exists and is writable (brain healthy)
  2. append.py is callable
  3. index.db exists
  4. daemon state file is readable
  5. evolution/ directory exists

Auto-fix attempts:
  - If log.jsonl missing → create empty
  - If evolution/ missing → create dir

Usage:
  python3 precheck.py              # human-readable output
  python3 precheck.py --json       # machine-readable JSON

Exit codes:
  0 = all checks passed
  1 = one or more checks failed (see output)
"""
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import MYCELIUM, LOG, INDEX, EVOLUTION_DIR

SCRIPTS = MYCELIUM / "scripts"
STATE = Path.home() / ".hermes" / "myceliumd" / "state.json"

# ── Checks ──

def check_brain():
    """log.jsonl exists and is writable."""
    if not LOG.exists():
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            LOG.touch()
            return True, "created log.jsonl (was missing)"
        except OSError as e:
            return False, f"cannot create log.jsonl: {e}"
    if not os.access(LOG, os.W_OK):
        return False, "log.jsonl not writable"
    return True, "ok"


def check_append():
    """append.py exists and is a file."""
    append = SCRIPTS / "append.py"
    if not append.exists():
        return False, "append.py missing"
    if not append.is_file():
        return False, "append.py is not a file"
    return True, "ok"


def check_index():
    """index.db exists (will be rebuilt by append if missing)."""
    if not INDEX.exists():
        return True, "missing (will rebuild on next append)"
    return True, "ok"


def check_daemon():
    """Check daemon state file for recent heartbeat."""
    if not STATE.exists():
        return True, "no state file (daemon may not be installed)"
    try:
        data = json.loads(STATE.read_text())
        if not isinstance(data, dict):
            return True, "state file is not a dict (unreadable)"
        last_run = data.get("last_run") or data.get("ts")
        if last_run:
            return True, f"last run: {last_run}"
        return True, "state exists (no timestamp)"
    except (json.JSONDecodeError, ValueError, AttributeError, OSError):
        return True, "state file unreadable"


def check_evolution_dir():
    """evolution/ directory exists for self-evolution engine."""
    if not EVOLUTION_DIR.exists():
        try:
            EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
            return True, "created evolution/ (was missing)"
        except OSError as e:
            return False, f"cannot create evolution/: {e}"
    return True, "ok"


# ── Main ──

def run_checks():
    checks = [
        ("brain", check_brain),
        ("append", check_append),
        ("index", check_index),
        ("daemon", check_daemon),
        ("evolution", check_evolution_dir),
    ]
    results = {}
    all_ok = True
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"exception: {e}"
        results[name] = {"ok": ok, "detail": detail}
        if not ok:
            all_ok = False
    return all_ok, results


def main():
    json_mode = "--json" in sys.argv
    all_ok, results = run_checks()

    if json_mode:
        output = {
            "ok": all_ok,
            "checks": {name: r["ok"] for name, r in results.items()},
            "details": {name: r["detail"] for name, r in results.items()},
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        print(json.dumps(output))
    else:
        status = "✅ All checks passed" if all_ok else "⚠️  Some checks failed"
        print("🍄 Mycelium Pre-Flight Check")
        print(f"{'─' * 40}")
        for name, r in results.items():
            icon = "✓" if r["ok"] else "✗"
            print(f"  {icon} {name}: {r['detail']}")
        print(f"{'─' * 40}")
        print(f"  {status}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

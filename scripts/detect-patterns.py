#!/usr/bin/env python3
"""
Skill Garden — pattern detector.

Scans ~/Documents/mycelium/log.jsonl and updates garden/patterns.json.
Detects recurring action patterns. At 3x count, sets 'ready: true' for offer.
"""
import json, os, sys
from collections import defaultdict
from pathlib import Path

MYCELIUM = Path.home() / "Documents/mycelium"
LOG = MYCELIUM / "log.jsonl"
BRANCHES = MYCELIUM / "branches"
PATTERNS = MYCELIUM / "garden" / "patterns.json"
STATE = MYCELIUM / "garden" / "state.json"

PATTERN_RULES = {
    "grav-health": {
        "label": "Check Grav shim health",
        "match": lambda e: e.get("type") == "finding" and e.get("finding", {}).get("target") == "grav-shim",
        "threshold": 3,
        "skill_hint": "Automate Grav health check + pool rotation"
    },
    "grav-pool": {
        "label": "Check Grav pool/quota status",
        "match": lambda e: "pool" in (e.get("user", "") + e.get("assistant", "")).lower() or "exhausted" in (e.get("user", "") + e.get("assistant", "")).lower(),
        "threshold": 3,
        "skill_hint": "Pool management — auto-rotate on exhaustion"
    },
    "page-radar": {
        "label": "Page Radar operations",
        "match": lambda e: "page-radar" in (e.get("user", "") + e.get("assistant", "")).lower() or "page radar" in (e.get("user", "") + e.get("assistant", "")).lower(),
        "threshold": 3,
        "skill_hint": "Page Radar start/stop/check/analyze workflow"
    },
    "companion-widget": {
        "label": "Companion widget work",
        "match": lambda e: "companion" in (e.get("user", "") + e.get("assistant", "")).lower() and ("widget" in (e.get("user", "") + e.get("assistant", "")).lower() or "p5" in (e.get("user", "") + e.get("assistant", "")).lower()),
        "threshold": 3,
        "skill_hint": "Companion widget build/run/modify workflow"
    },
    "db-restore": {
        "label": "Restore PostgreSQL database",
        "match": lambda e: "restore" in (e.get("user", "") + e.get("assistant", "")).lower() and ("postgres" in (e.get("user", "") + e.get("assistant", "")).lower() or "sql.gz" in (e.get("user", "") + e.get("assistant", "")).lower()),
        "threshold": 3,
        "skill_hint": "Postgres restore from SQL dump"
    },
    "finding-sqli": {
        "label": "SQL injection finding",
        "match": lambda e: e.get("finding", {}).get("type") == "SQLi",
        "threshold": 3,
        "skill_hint": "SQLi quick-check methodology"
    },
    "finding-subdomain": {
        "label": "Subdomain discovery",
        "match": lambda e: "subdomain" in (e.get("user", "") + e.get("assistant", "") + json.dumps(e.get("finding", {}))).lower() or e.get("finding", {}).get("type") == "subdomain",
        "threshold": 3,
        "skill_hint": "Subdomain recon workflow"
    },
    "branch-action": {
        "label": "Branch a conversation",
        "match": lambda e: e.get("type") == "branch",
        "threshold": 3,
        "skill_hint": "Conversation branching workflow"
    }
}


def load_log():
    if not LOG.exists():
        return []
    with open(LOG) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_branches():
    """Load all branch files for scanning too."""
    entries = []
    if BRANCHES.exists():
        for f in sorted(BRANCHES.glob("*.jsonl")):
            try:
                entries.extend(load_log(f))
            except Exception:
                pass
    return entries


def load_patterns():
    if PATTERNS.exists():
        with open(PATTERNS) as f:
            return json.load(f)
    return {"patterns": [], "description": "Tracked recurring action patterns. At 3x, I offer a skill."}


def load_state():
    if STATE.exists():
        with open(STATE) as f:
            return json.load(f)
    return {"offered": [], "accepted": [], "dormant": [], "archived": []}


def save_patterns(data):
    with open(PATTERNS, "w") as f:
        json.dump(data, f, indent=2)


def save_state(data):
    with open(STATE, "w") as f:
        json.dump(data, f, indent=2)


def detect(entries):
    log = load_log()
    branch_entries = load_branches()
    all_entries = log + branch_entries
    patterns = load_patterns()
    state = load_state()
    existing = {p["id"]: p for p in patterns["patterns"]}
    new_offers = []

    for rule_id, rule in PATTERN_RULES.items():
        count = 0
        for entry in all_entries:
            if count >= rule["threshold"]:
                break  # short-circuit — no need to count beyond threshold
            if rule["match"](entry):
                count += 1

        # Update or create pattern entry
        if rule_id in existing:
            existing[rule_id]["count"] = count
            existing[rule_id]["last_seen"] = log[-1]["ts"] if log else ""
            # Check if threshold just crossed and not already offered
            if count >= rule["threshold"] and not existing[rule_id].get("ready") and not any(o["id"] == rule_id for o in state["offered"]):
                existing[rule_id]["ready"] = True
                new_offers.append(rule_id)
            elif count < rule["threshold"]:
                existing[rule_id]["ready"] = False
        else:
            entry = {
                "id": rule_id,
                "label": rule["label"],
                "count": count,
                "threshold": rule["threshold"],
                "ready": False,
                "last_seen": log[-1]["ts"] if log else ""
            }
            if count >= rule["threshold"]:
                entry["ready"] = True
                new_offers.append(rule_id)
            existing[rule_id] = entry

    patterns["patterns"] = list(existing.values())
    save_patterns(patterns)
    save_state(state)

    return new_offers, patterns, state


def main():
    log = load_log()
    branches = load_branches()
    all_count = len(log) + len(branches)
    print(f"Scanning {len(log)} main turns + {len(branches)} branch turns = {all_count} total...")

    new_offers, patterns, state = detect(log)

    # Print summary
    for p in patterns["patterns"]:
        status = "🟢 READY" if p.get("ready") else "⏳"
        bar = "#" * min(p["count"], 10) + "." * max(0, 10 - min(p["count"], 10))
        print(f"  {p['id']:20s} [{bar}] {p['count']}/{p['threshold']}  {status}")

    if new_offers:
        print(f"\n🎯 New pattern{'s' if len(new_offers) > 1 else ''} ready for offer: {', '.join(new_offers)}")
    else:
        print("\nNo new patterns at threshold yet.")

    # Output machine-readable for Hermes
    print("\n---MACHINE---")
    print(json.dumps({"new_offers": new_offers}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Conversation Tree — branch manager CLI.

Usage:
  branch.py list                          # List all branches
  branch.py create <name>                 # Create new branch file
  branch.py merge <name>                  # Merge branch into main log
  branch.py diff <name>                   # Show diff between main and branch
  branch.py prune <name>                  # Delete branch
  branch.py status <name>                 # Show branch stats
"""
import json, sys, shutil
from pathlib import Path

MYCELIUM = Path.home() / "Documents/mycelium"
LOG = MYCELIUM / "log.jsonl"
BRANCHES = MYCELIUM / "branches"
BRANCHES.mkdir(parents=True, exist_ok=True)


def load_log(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save_log(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")


def list_branches():
    files = sorted(BRANCHES.glob("*.jsonl"))
    if not files:
        print("No branches.")
        return
    for f in files:
        entries = load_log(f)
        counts = {}
        for e in entries:
            t = e.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        last = entries[-1]["ts"] if entries else "?"
        first = entries[0]["ts"] if entries else "?"
        print(f"  {f.stem:30s} | {len(entries):3d} turns | {first[:16]} → {last[:16]} | types: {counts}")


def create_branch(name):
    path = BRANCHES / f"{name}.jsonl"
    if path.exists():
        print(f"Branch '{name}' already exists: {path}")
        return False
    # Seed with a header entry
    from datetime import datetime
    entry = {
        "turn": 0,
        "type": "branch",
        "session": name,
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": "",
        "assistant": f"Branch '{name}' created",
        "branch_action": "create"
    }
    save_log(path, [entry])
    print(f"Created: {path}")
    return True


def merge_branch(name):
    path = BRANCHES / f"{name}.jsonl"
    if not path.exists():
        print(f"Branch '{name}' not found.")
        return False

    branch_entries = load_log(path)
    if len(branch_entries) <= 1:
        print(f"Branch '{name}' is empty (only header). Nothing to merge.")
        return False

    main = load_log(LOG)
    next_turn = max(e.get("turn", 0) for e in main) + 1 if main else 1

    merged_count = 0
    for entry in branch_entries[1:]:  # skip header
        entry["turn"] = next_turn
        entry["branch_action"] = "merged"
        entry["_merged_from"] = name
        main.append(entry)
        next_turn += 1
        merged_count += 1

    save_log(LOG, main)

    # Mark branch as merged
    branch_entries[0]["branch_action"] = "merged-into-main"
    branch_entries[0]["assistant"] = f"Branch '{name}' merged into main ({merged_count} turns)"
    save_log(path, branch_entries)

    print(f"Merged {merged_count} turns from '{name}' into main log.")
    print(f"Branch file preserved with merge marker.")
    return True


def diff_branch(name):
    path = BRANCHES / f"{name}.jsonl"
    if not path.exists():
        print(f"Branch '{name}' not found.")
        return False

    branch = load_log(path)
    if len(branch) <= 1:
        print("Branch has no content turns to diff.")
        return False

    print(f"=== Diff: main ↔ {name} ===")
    print(f"Branch turns: {len(branch) - 1} (excluding header)")
    print()

    # Count types in branch vs main
    main = load_log(LOG)
    main_types = {}
    for e in main:
        t = e.get("type", "unknown")
        main_types[t] = main_types.get(t, 0) + 1

    branch_types = {}
    for e in branch[1:]:
        t = e.get("type", "unknown")
        branch_types[t] = branch_types.get(t, 0) + 1

    all_types = set(list(main_types.keys()) + list(branch_types.keys()))
    print(f"{'Type':20s} {'Main':>8s} {'Branch':>8s}")
    print("-" * 40)
    for t in sorted(all_types):
        print(f"{t:20s} {main_types.get(t, 0):>8d} {branch_types.get(t, 0):>8d}")

    print()
    print("--- Branch content ---")
    for entry in branch[1:]:
        ts = entry.get("ts", "")[11:19]
        role = entry.get("type", "?")
        user = entry.get("user", "")[:60]
        print(f"  [{ts}] ({role}) {user}")

    return True


def prune_branch(name):
    path = BRANCHES / f"{name}.jsonl"
    if not path.exists():
        print(f"Branch '{name}' not found.")
        return False
    path.unlink()
    print(f"Pruned: '{name}'")


def status_branch(name):
    path = BRANCHES / f"{name}.jsonl"
    if not path.exists():
        print(f"Branch '{name}' not found.")
        return False
    entries = load_log(path)
    print(f"Branch:    {name}")
    print(f"Turns:     {len(entries)}")
    if entries:
        print(f"Created:   {entries[0].get('ts', '?')}")
        print(f"Last:      {entries[-1].get('ts', '?')}")
        print(f"Status:    {entries[0].get('branch_action', 'active')}")
        types = {}
        for e in entries:
            t = e.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        print(f"Types:     {types}")
        if len(entries) > 1:
            print(f"\nLatest turn:")
            print(f"  {entries[-1].get('user', '')[:80]}")
            print(f"  → {entries[-1].get('assistant', '')[:80]}")


def main():
    if len(sys.argv) < 2:
        print("Usage: branch.py <list|create|merge|diff|prune|status> [name]")
        sys.exit(1)

    cmd = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "list":
        list_branches()
    elif cmd == "create":
        if not name:
            print("Usage: branch.py create <name>")
            sys.exit(1)
        create_branch(name)
    elif cmd == "merge":
        if not name:
            print("Usage: branch.py merge <name>")
            sys.exit(1)
        merge_branch(name)
    elif cmd == "diff":
        if not name:
            print("Usage: branch.py diff <name>")
            sys.exit(1)
        diff_branch(name)
    elif cmd == "prune":
        if not name:
            print("Usage: branch.py prune <name>")
            sys.exit(1)
        prune_branch(name)
    elif cmd == "status":
        if not name:
            print("Usage: branch.py status <name>")
            sys.exit(1)
        status_branch(name)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Vuln Hunter Notebook — findings query CLI.

Usage:
  findings.py list                          # List all findings
  findings.py by-target <name>              # Filter findings by target
  findings.py by-type <type>                # Filter by finding type (SQLi, XSS, etc.)
  findings.py by-severity <level>           # Filter by severity (critical/high/med/low)
  findings.py report [target]               # Generate markdown report (optionally per target)
  findings.py dead-ends                     # List all dead-end attempts
  findings.py stats                         # Summary statistics
  findings.py timeline [target]             # Chronological timeline of findings
"""
import json, sys
from pathlib import Path
from datetime import datetime
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import LOG, BRANCHES


def load_all():
    entries = []
    if LOG.exists():
        with open(LOG) as f:
            entries.extend([json.loads(l) for l in f if l.strip()])
    if BRANCHES.exists():
        for f in sorted(BRANCHES.glob("*.jsonl")):
            with open(f) as bf:
                entries.extend([json.loads(l) for l in bf if l.strip()])
    return entries


def get_findings(entries):
    """Extract all finding entries with structured data."""
    return [e for e in entries if e.get("type") == "finding" and e.get("finding")]


def normalize_finding(finding):
    finding = dict(finding or {})
    if not finding.get("severity"):
        finding["severity"] = "info"
    if not finding.get("detail"):
        finding["detail"] = finding.get("result", "")
    return finding


def get_dead_ends(entries):
    return [e for e in entries if e.get("type") == "dead-end"]


def cmd_list(entries):
    findings = get_findings(entries)
    if not findings:
        print("No findings yet.")
        return
    print(f"{'Turn':>5} {'Severity':10s} {'Type':18s} {'Target':30s} {'Detail'}")
    print("-" * 90)
    for e in sorted(findings, key=lambda x: x.get("ts", "")):
        f = normalize_finding(e.get("finding", {}))
        turn = e.get("turn", "?")
        sev = f.get("severity", "?")[:10]
        typ = f.get("type", "?")[:18]
        tgt = f.get("target", "?")[:30]
        det = f.get("detail", "")[:50]
        print(f"{turn:>5} {sev:10s} {typ:18s} {tgt:30s} {det}")


def cmd_by_target(entries, target):
    findings = get_findings(entries)
    target_lower = target.lower()
    matches = [e for e in findings if target_lower in e.get("finding", {}).get("target", "").lower()]
    if not matches:
        print(f"No findings for target '{target}'.")
        return
    print(f"Findings for: {target}")
    print()
    cmd_list(matches)


def cmd_by_type(entries, ftype):
    findings = get_findings(entries)
    matches = [e for e in findings if e.get("finding", {}).get("type", "").lower() == ftype.lower()]
    if not matches:
        print(f"No findings of type '{ftype}'.")
        return
    print(f"Findings of type: {ftype}")
    print()
    cmd_list(matches)


def cmd_by_severity(entries, level):
    findings = get_findings(entries)
    matches = [e for e in findings if e.get("finding", {}).get("severity", "").lower() == level.lower()]
    if not matches:
        print(f"No findings with severity '{level}'.")
        return
    print(f"Findings with severity: {level}")
    print()
    cmd_list(matches)


def cmd_dead_ends(entries):
    deads = get_dead_ends(entries)
    if not deads:
        print("No dead-ends logged.")
        return
    print(f"{'Turn':>5} {'Attempt':50s} {'Result'}")
    print("-" * 90)
    for e in sorted(deads, key=lambda x: x.get("ts", "")):
        turn = e.get("turn", "?")
        att = e.get("attempt", "")[:50]
        res = e.get("result", "")[:50]
        print(f"{turn:>5} {att:50s} {res}")


def cmd_report(entries, target=None):
    findings = get_findings(entries)
    if target:
        findings = [e for e in findings if target.lower() in e.get("finding", {}).get("target", "").lower()]

    if not findings:
        print("No findings to report.")
        return

    targets = set(e["finding"]["target"] for e in findings if e["finding"].get("target"))
    print("# Vulnerability Assessment Report")
    if target:
        print(f"\n**Target:** {target}")
    print(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"**Findings:** {len(findings)}")
    print()

    for tgt in sorted(targets):
        tgt_findings = [e for e in findings if e.get("finding", {}).get("target") == tgt]
        print(f"---")
        print(f"## Target: {tgt}")
        print()

        by_sev = {"critical": [], "high": [], "medium": [], "low": [], "info": [], "?": []}
        for e in tgt_findings:
            f = normalize_finding(e["finding"])
            sev = f.get("severity", "?").lower()
            by_sev.setdefault(sev, []).append((e, f))

        for sev in ["critical", "high", "medium", "low", "info", "?"]:
            items = by_sev.get(sev, [])
            if not items:
                continue
            print(f"### {sev.title()}")
            print()
            for e, f in items:
                print(f"- **Type:** {f.get('type', '?')}")
                if f.get("endpoint"):
                    print(f"  **Endpoint:** `{f.get('endpoint')}`")
                if f.get("parameter"):
                    print(f"  **Parameter:** `{f.get('parameter')}`")
                print(f"  **Detail:** {f.get('detail', '?')}")
                if f.get("evidence"):
                    print(f"  **Evidence:** `{f.get('evidence')}`")
                if f.get("remediation"):
                    print(f"  **Remediation:** {f.get('remediation')}")
                print()
            print()

    # Dead-ends
    deads = get_dead_ends(entries)
    if deads and not target:
        print("## Dead Ends (approaches tried)")
        print()
        for e in deads:
            print(f"- ✗ {e.get('attempt', '?')} → {e.get('result', '?')}")
        print()

    print("---")
    print(f"*Report auto-generated by Mycelium*")


def cmd_stats(entries):
    findings = get_findings(entries)
    deads = get_dead_ends(entries)

    print("=== Mycelium Findings Stats ===")
    print()
    print(f"Total findings:       {len(findings)}")
    print(f"Total dead-ends:      {len(deads)}")
    print(f"Total log entries:    {len(entries)}")
    print()

    if findings:
        normalized_findings = [normalize_finding(e["finding"]) for e in findings]
        type_counts = Counter(f["type"] for f in normalized_findings if f.get("type"))
        sev_counts = Counter(f.get("severity", "?") for f in normalized_findings)
        targets = set(f.get("target", "?") for f in normalized_findings)

        print(f"Targets: {len(targets)}")
        for t in sorted(targets):
            print(f"  - {t}")

        print()
        print("By type:")
        for t, c in type_counts.most_common():
            print(f"  - {t}: {c}")

        print()
        print("By severity:")
        for s in ["critical", "high", "medium", "low", "info"]:
            if sev_counts.get(s):
                print(f"  - {s}: {sev_counts[s]}")
    print()

    sessions = set(e["session"] for e in entries if e.get("session"))
    print(f"Sessions: {len(sessions)}")
    print(f"Date range: {entries[0]['ts'][:10] if entries else '?'} → {entries[-1]['ts'][:10] if entries else '?'}")

    # Per-engagement summary
    if findings:
        print()
        print("Per engagement:")
        for session in sorted(sessions):
            session_findings = [e for e in findings if e.get("session") == session]
            if session_findings:
                print(f"  {session}: {len(session_findings)} findings")


def cmd_timeline(entries, target=None):
    findings = get_findings(entries)
    if target:
        findings = [e for e in findings if target.lower() in e.get("finding", {}).get("target", "").lower()]

    if not findings:
        print("No findings to show.")
        return

    print(f"{'Time':20s} {'Type':18s} {'Target':30s} {'Detail'}")
    print("-" * 90)
    for e in sorted(findings, key=lambda x: x.get("ts", "")):
        ts = e.get("ts", "")[11:19]
        f = e.get("finding", {})
        typ = f.get("type", "?")[:18]
        tgt = f.get("target", "?")[:30]
        det = f.get("detail", "")[:50]
        print(f"{ts:20s} {typ:18s} {tgt:30s} {det}")


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    entries = load_all()

    if cmd == "list":
        cmd_list(entries)
    elif cmd == "by-target":
        if not arg:
            print("Usage: findings.py by-target <name>")
            sys.exit(1)
        cmd_by_target(entries, arg)
    elif cmd == "by-type":
        if not arg:
            print("Usage: findings.py by-type <type>")
            sys.exit(1)
        cmd_by_type(entries, arg)
    elif cmd == "by-severity":
        if not arg:
            print("Usage: findings.py by-severity <level>")
            sys.exit(1)
        cmd_by_severity(entries, arg)
    elif cmd == "report":
        cmd_report(entries, arg)
    elif cmd == "dead-ends":
        cmd_dead_ends(entries)
    elif cmd == "stats":
        cmd_stats(entries)
    elif cmd == "timeline":
        cmd_timeline(entries, arg)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Negation Index — tracks what DOESN'T work in Mycelium.

Failed approaches, dead ends, wrong paths, forbidden techniques.
Prevents repeating known-failed strategies.

Usage (as library):
    from mycelium_negation import NegationExtractor
    ne = NegationExtractor()
    signals = ne.detect("that's not the right approach, try X instead")
    ne.store(signals[0], session="my-session")
    ne.query(approach="try Y")

Usage (CLI):
    python3 mycelium_negation.py detect "don't use curl for that"
    python3 mycelium_negation.py store '{"approach":"curl","result":"failed","category":"forbidden-approach"}' --session test
    python3 mycelium_negation.py query [--approach X] [--entity Y]
    python3 mycelium_negation.py count
    python3 mycelium_negation.py recent [limit]
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import MYCELIUM, INDEX, extract_entities, init_index

# ── Negation signal patterns ──────────────────────────────────
# Each pattern: (compiled_re, category, approach_extractor_fn)
# The extractor captures the relevant "approach" from the match.

NEGATION_SIGNALS = [
    (
        re.compile(
            r"(?:that'?s|it'?s)\s+not\s+the\s+(?:right|correct|proper)\s+(?:way|approach|fix|solution|method)",
            re.IGNORECASE,
        ),
        "wrong-approach",
        lambda m: m.group(0).strip(),
    ),
    (
        re.compile(
            r"don'?t\s+(?:use|try|run|execute|call|call)\s+(.+?)(?:\s+again)?(?:\.|!|,|$)",
            re.IGNORECASE,
        ),
        "forbidden-approach",
        lambda m: m.group(1).strip(),
    ),
    (
        re.compile(
            r"(?:tried|tested|attempted|used)\s+(.+?)\s+(?:and\s+)?(?:it\s+)?(?:failed|broke|didn'?t\s+work|crashed|errored)",
            re.IGNORECASE,
        ),
        "failed-attempt",
        lambda m: m.group(1).strip(),
    ),
    (
        re.compile(
            r"(?:that|it|this)\s+(?:caused|introduced|broke|introduced|created)\s+(?:a\s+)?(?:new\s+)?(?:bug|issue|error|problem|regression|crash)",
            re.IGNORECASE,
        ),
        "caused-regression",
        lambda m: m.group(0).strip(),
    ),
    (
        re.compile(
            r"(?:wrong|incorrect|broken)\s+(?:port|url|path|endpoint|config|host|address|credential|key|token)",
            re.IGNORECASE,
        ),
        "wrong-context",
        lambda m: m.group(0).strip(),
    ),
    (
        re.compile(
            r"(?:already|repeatedly|keep|keep)\s+(?:told|said|explained|stated|mentioned)\s+(.+?)(?:\.|!|,|$)",
            re.IGNORECASE,
        ),
        "repeated-mistake",
        lambda m: m.group(1).strip(),
    ),
    (
        re.compile(
            r"how\s+many\s+times",
            re.IGNORECASE,
        ),
        "repeated-mistake",
        lambda m: "repeated failure (user frustration)",
    ),
    (
        re.compile(
            r"stop\s+(?:doing|using|trying|running|executing|calling|implementing)\s+(.+?)(?:\.|!|,|$)",
            re.IGNORECASE,
        ),
        "behavioral-drift",
        lambda m: m.group(1).strip(),
    ),
]


class NegationExtractor:
    """Detect, store, and query negation signals — failed approaches to avoid."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or INDEX

    # ── Detection ──────────────────────────────────────────────

    def detect(self, user_text: str) -> list[dict]:
        """Extract negation signals from user text.

        Returns list of dicts:
            {
                "approach": <extracted approach/description>,
                "category": <wrong-approach|forbidden-approach|...>,
                "context": <matched text>,
                "entities": [<auto-extracted entities>],
            }
        """
        signals = []
        for pat, category, extractor in NEGATION_SIGNALS:
            match = pat.search(user_text)
            if match:
                approach = extractor(match)
                entities = extract_entities(user_text)
                signals.append({
                    "approach": approach,
                    "category": category,
                    "context": match.group(0).strip(),
                    "entities": entities,
                })
        return signals

    # ── Storage ────────────────────────────────────────────────

    def store(self, negation: dict, session: str = "") -> None:
        """Store a negation record in SQLite.

        negation dict keys: approach (required), category, context, result,
        fix, entities (list or str)
        """
        conn = init_index(self.db_path)
        approach = negation.get("approach", "")
        context = negation.get("context", "")
        result = negation.get("result") or negation.get("category", "unknown")
        fix = negation.get("fix", None)
        entities = negation.get("entities", [])
        if isinstance(entities, list):
            entities = ",".join(entities)
        ts = datetime.now(timezone.utc).isoformat()
        user_msg = negation.get("user_msg", "")

        conn.execute(
            """INSERT INTO negations
               (approach, context, result, fix, entities, session, ts, user_msg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approach, context, result, fix, entities, session, ts, user_msg),
        )
        conn.commit()
        conn.close()

    # ── Querying ───────────────────────────────────────────────

    def query(self, approach: str | None = None, entity: str | None = None) -> list[dict]:
        """Find negations. Optional filters: approach substring, entity name."""
        conn = init_index(self.db_path)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM negations WHERE 1=1"
        params: list = []

        if approach:
            sql += " AND approach LIKE ?"
            params.append(f"%{approach}%")
        if entity:
            sql += " AND entities LIKE ?"
            params.append(f"%{entity}%")

        sql += " ORDER BY ts DESC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Total negations stored."""
        conn = init_index(self.db_path)
        total = conn.execute("SELECT COUNT(*) FROM negations").fetchone()[0]
        conn.close()
        return total

    def recent(self, limit: int = 10) -> list[dict]:
        """Most recent negations, newest first."""
        conn = init_index(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM negations ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ── CLI ────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Negation Index — what doesn't work")
    sub = parser.add_subparsers(dest="cmd")

    p_detect = sub.add_parser("detect", help="Detect negation signals in text")
    p_detect.add_argument("text", help="User text to analyze")

    p_store = sub.add_parser("store", help="Store a negation record")
    p_store.add_argument("json_data", help="Negation JSON (approach, result required)")
    p_store.add_argument("--session", default="", help="Session name")

    p_query = sub.add_parser("query", help="Query negations")
    p_query.add_argument("--approach", default=None, help="Filter by approach")
    p_query.add_argument("--entity", default=None, help="Filter by entity")

    sub.add_parser("count", help="Count total negations")

    p_recent = sub.add_parser("recent", help="Show recent negations")
    p_recent.add_argument("limit", nargs="?", type=int, default=10, help="Max results")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    ne = NegationExtractor()

    if args.cmd == "detect":
        signals = ne.detect(args.text)
        if not signals:
            print("No negation signals detected.")
        else:
            for s in signals:
                print(f"  [{s['category']}] {s['approach']}")
                print(f"    context: {s['context']}")
                if s["entities"]:
                    print(f"    entities: {', '.join(s['entities'])}")
                print()

    elif args.cmd == "store":
        data = json.loads(args.json_data)
        ne.store(data, session=args.session)
        print(f"Stored negation: {data.get('approach', '?')}")

    elif args.cmd == "query":
        results = ne.query(approach=args.approach, entity=args.entity)
        if not results:
            print("No negations found.")
        else:
            for r in results:
                print(f"  [{r['result']}] {r['approach']}")
                if r.get("context"):
                    print(f"    context: {r['context']}")
                if r.get("entities"):
                    print(f"    entities: {r['entities']}")
                if r.get("session"):
                    print(f"    session: {r['session']}")
                print(f"    ts: {r.get('ts', '?')}")
                print()

    elif args.cmd == "count":
        print(f"Total negations: {ne.count()}")

    elif args.cmd == "recent":
        for r in ne.recent(args.limit):
            print(f"  [{r.get('result', '?')}] {r.get('approach', '?')} — {r.get('ts', '?')}")


if __name__ == "__main__":
    main()

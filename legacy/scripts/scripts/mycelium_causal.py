#!/usr/bin/env python3
"""
Causal Chain DAG for Mycelium.

Tracks cause→effect relationships between turns:
  CAUSED:     finding N  → decision M   (bug led to fix)
  RESOLVED:   decision N → finding M    (fix worked)
  REGRESSED:  decision N → finding M    (fix caused new bug)
  SUPERSEDED: idea N     → idea M       (new idea replaced old)
  DEPLOYS:    decision N → decision M   (config deployed)

Usage:
    from mycelium_causal import CausalExtractor
    ce = CausalExtractor()
    edges = ce.extract_edges(current, prev_entries)
    ce.build_from_log()
    chain = ce.trace_cause(turn=5)
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Optional

from mycelium_lib import MYCELIUM, INDEX, init_index, load_log, extract_entities


# ── Keyword sets for edge detection ──────────────────────────
_REGRESS_KEYWORDS = re.compile(
    r'\b(?:broke|regression|new\s+bug|broke\s+thing|introduced\s+bug)\b',
    re.IGNORECASE,
)
_DEPLOY_KEYWORDS = re.compile(
    r'\b(?:deploy(?:ed|s|ing)?|config(?:ured|s)?|install(?:ed|s|ing)?|set\s+up)\b',
    re.IGNORECASE,
)
_RESOLVED_SEVERITIES = {'resolved', 'closed', 'fixed', 'mitigated'}


class CausalExtractor:
    """Extract and query causal edges between turns."""

    def __init__(self, db_path=None):
        self.db_path = db_path or INDEX
        self.conn = init_index(self.db_path)

    def close(self):
        self.conn.close()

    # ── Edge extraction ───────────────────────────────────────

    def extract_edges(self, current: dict, prev_entries: list[dict]) -> list[dict]:
        """Extract causal edges between current turn and prev turns."""
        edges = []
        cur_turn = current.get("turn", 0)
        cur_session = current.get("session", "")
        cur_type = current.get("type", "")
        cur_entities = set(current.get("entities", []))
        cur_text = self._full_text(current).lower()
        cur_finding = current.get("finding") or {}
        cur_severity = (cur_finding.get("severity") or "").lower()

        for prev in prev_entries:
            prev_turn = prev.get("turn", 0)
            prev_session = prev.get("session", "")
            prev_type = prev.get("type", "")
            prev_entities = set(prev.get("entities", []))
            prev_text = self._full_text(prev).lower()
            prev_finding = prev.get("finding") or {}
            prev_severity = (prev_finding.get("severity") or "").lower()

            # Only forward edges: cur_turn > prev_turn, within 10 turns
            gap = cur_turn - prev_turn
            if gap <= 0 or gap > 10:
                continue

            same_session = cur_session and prev_session and cur_session == prev_session
            shared_entities = cur_entities & prev_entities

            # ── CAUSED: finding → decision ──
            if prev_type == "finding" and cur_type == "decision" and same_session:
                edges.append({
                    "source_turn": prev_turn,
                    "target_turn": cur_turn,
                    "edge_type": "CAUSED",
                    "confidence": self._confidence(same_session, gap, shared_entities, cur_text, prev_text),
                    "session": cur_session,
                    "ts": current.get("ts", ""),
                })

            # ── RESOLVED: decision → finding(resolved/closed) ──
            if prev_type == "decision" and cur_type == "finding" and same_session:
                if cur_severity in _RESOLVED_SEVERITIES:
                    edges.append({
                        "source_turn": prev_turn,
                        "target_turn": cur_turn,
                        "edge_type": "RESOLVED",
                        "confidence": self._confidence(same_session, gap, shared_entities, cur_text, prev_text),
                        "session": cur_session,
                        "ts": current.get("ts", ""),
                    })

            # ── REGRESSED: decision → finding(broke/regression) ──
            if prev_type == "decision" and cur_type == "finding" and same_session:
                combined = cur_text + " " + (cur_finding.get("detail") or "")
                if _REGRESS_KEYWORDS.search(combined):
                    edges.append({
                        "source_turn": prev_turn,
                        "target_turn": cur_turn,
                        "edge_type": "REGRESSED",
                        "confidence": self._confidence(same_session, gap, shared_entities, cur_text, prev_text),
                        "session": cur_session,
                        "ts": current.get("ts", ""),
                    })

            # ── SUPERSEDED: idea → idea (similar entities) ──
            if prev_type == "idea" and cur_type == "idea" and same_session and shared_entities:
                edges.append({
                    "source_turn": prev_turn,
                    "target_turn": cur_turn,
                    "edge_type": "SUPERSEDED",
                    "confidence": self._confidence(same_session, gap, shared_entities, cur_text, prev_text),
                    "session": cur_session,
                    "ts": current.get("ts", ""),
                })

            # ── DEPLOYS: decision → decision(deploy/config) ──
            if prev_type == "decision" and cur_type == "decision" and same_session:
                if _DEPLOY_KEYWORDS.search(cur_text):
                    edges.append({
                        "source_turn": prev_turn,
                        "target_turn": cur_turn,
                        "edge_type": "DEPLOYS",
                        "confidence": self._confidence(same_session, gap, shared_entities, cur_text, prev_text),
                        "session": cur_session,
                        "ts": current.get("ts", ""),
                    })

        return edges

    # ── Storage ───────────────────────────────────────────────

    def store_edge(self, edge: dict) -> None:
        """Store one causal edge. INSERT OR IGNORE on UNIQUE constraint."""
        self.conn.execute(
            "INSERT OR IGNORE INTO causal_edges "
            "(source_turn, target_turn, edge_type, confidence, session, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                edge["source_turn"],
                edge["target_turn"],
                edge["edge_type"],
                edge.get("confidence", 0.5),
                edge.get("session", ""),
                edge.get("ts", ""),
            ),
        )
        self.conn.commit()

    # ── Tracing ───────────────────────────────────────────────

    def trace_cause(self, turn: int, max_depth: int = 10) -> list[int]:
        """Trace backwards from turn to root cause. Returns turn list [turn, ..., root]."""
        chain = [turn]
        visited = {turn}
        current = turn
        for _ in range(max_depth):
            rows = self.conn.execute(
                "SELECT source_turn FROM causal_edges "
                "WHERE target_turn = ? ORDER BY source_turn ASC",
                (current,),
            ).fetchall()
            if not rows:
                break
            # Take first source (closest cause)
            next_turn = rows[0][0]
            if next_turn in visited:
                break
            visited.add(next_turn)
            chain.append(next_turn)
            current = next_turn
        return chain

    def trace_effect(self, turn: int, max_depth: int = 10) -> list[int]:
        """Trace forwards from turn to final effect. Returns turn list [turn, ..., final]."""
        chain = [turn]
        visited = {turn}
        current = turn
        for _ in range(max_depth):
            rows = self.conn.execute(
                "SELECT target_turn FROM causal_edges "
                "WHERE source_turn = ? ORDER BY target_turn ASC",
                (current,),
            ).fetchall()
            if not rows:
                break
            next_turn = rows[0][0]
            if next_turn in visited:
                break
            visited.add(next_turn)
            chain.append(next_turn)
            current = next_turn
        return chain

    # ── Queries ───────────────────────────────────────────────

    def get_chain(self, turns: list[int]) -> list[dict]:
        """Get all causal edges between the given turns."""
        if not turns:
            return []
        placeholders = ",".join("?" for _ in turns)
        rows = self.conn.execute(
            f"SELECT source_turn, target_turn, edge_type, confidence, session, ts "
            f"FROM causal_edges "
            f"WHERE source_turn IN ({placeholders}) AND target_turn IN ({placeholders})",
            turns + turns,
        ).fetchall()
        return [
            {
                "source_turn": r[0], "target_turn": r[1], "edge_type": r[2],
                "confidence": r[3], "session": r[4], "ts": r[5],
            }
            for r in rows
        ]

    def regressions(self) -> list[dict]:
        """Find all REGRESSED edges."""
        rows = self.conn.execute(
            "SELECT source_turn, target_turn, edge_type, confidence, session, ts "
            "FROM causal_edges WHERE edge_type = 'REGRESSED'"
        ).fetchall()
        return [
            {
                "source_turn": r[0], "target_turn": r[1], "edge_type": r[2],
                "confidence": r[3], "session": r[4], "ts": r[5],
            }
            for r in rows
        ]

    def count(self) -> int:
        """Total edge count."""
        return self.conn.execute("SELECT COUNT(*) FROM causal_edges").fetchone()[0]

    # ── Build from log ────────────────────────────────────────

    def build_from_log(self, log_path=None) -> int:
        """Rebuild causal DAG from all log entries. Returns edge count."""
        entries = load_log(log_path)
        self.conn.execute("DELETE FROM causal_edges")
        # Sort by turn to ensure order
        entries.sort(key=lambda e: e.get("turn", 0))
        for i, current in enumerate(entries):
            prev_entries = entries[:i]
            edges = self.extract_edges(current, prev_entries)
            for edge in edges:
                self.store_edge(edge)
        self.conn.commit()
        return self.count()

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _full_text(entry: dict) -> str:
        """Combine user + assistant + finding detail for text matching."""
        parts = [
            entry.get("user", ""),
            entry.get("assistant", ""),
        ]
        finding = entry.get("finding") or {}
        detail = finding.get("detail") or finding.get("type") or ""
        if detail:
            parts.append(str(detail))
        return " ".join(parts)

    def _confidence(self, same_session: bool, gap: int, shared_entities: set,
                    cur_text: str, prev_text: str) -> float:
        """Score confidence for an edge."""
        if same_session and gap <= 5:
            conf = 0.9
        elif same_session and gap <= 10:
            conf = 0.7
        elif not same_session and shared_entities:
            conf = 0.5
        else:
            conf = 0.5
        # Text similarity boost
        if self._shared_keywords(cur_text, prev_text):
            conf = min(conf + 0.1, 1.0)
        return round(conf, 2)

    @staticmethod
    def _shared_keywords(text_a: str, text_b: str) -> bool:
        """Check if two texts share meaningful keywords (>3 chars)."""
        words_a = set(w for w in re.findall(r'\w{4,}', text_a.lower()))
        words_b = set(w for w in re.findall(r'\w{4,}', text_b.lower()))
        # Filter out common stop words
        stop = {'this', 'that', 'with', 'from', 'have', 'been', 'were', 'will',
                'does', 'into', 'also', 'than', 'them', 'then', 'your', 'what',
                'when', 'some', 'each', 'more', 'very', 'much', 'just', 'like',
                'about', 'could', 'would', 'should', 'there', 'their', 'which'}
        words_a -= stop
        words_b -= stop
        return bool(words_a & words_b)


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    ce = CausalExtractor()

    if len(sys.argv) < 2:
        print(f"Causal edges: {ce.count()}")
        ce.close()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "build":
        n = ce.build_from_log()
        print(f"Built {n} causal edges")

    elif cmd == "count":
        print(ce.count())

    elif cmd == "trace-cause":
        turn = int(sys.argv[2])
        chain = ce.trace_cause(turn)
        print(f"Cause chain from turn {turn}: {chain}")

    elif cmd == "trace-effect":
        turn = int(sys.argv[2])
        chain = ce.trace_effect(turn)
        print(f"Effect chain from turn {turn}: {chain}")

    elif cmd == "regressions":
        regs = ce.regressions()
        for r in regs:
            print(f"  REGRESSED: turn {r['source_turn']} → {r['target_turn']} "
                  f"(conf={r['confidence']}) [{r['session']}]")

    elif cmd == "chain":
        turns = [int(x) for x in sys.argv[2:]]
        edges = ce.get_chain(turns)
        for e in edges:
            print(f"  {e['edge_type']}: turn {e['source_turn']} → {e['target_turn']} "
                  f"(conf={e['confidence']})")

    else:
        print(f"Usage: {sys.argv[0]} [build|count|trace-cause|trace-effect|regressions|chain]")

    ce.close()

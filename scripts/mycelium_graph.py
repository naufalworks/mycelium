#!/usr/bin/env python3
"""
Entity Relationship Graph for Mycelium.

Tracks relationships between entities across turns:
  - co-occur: entities in same turn
  - resolves: "fixed X", "solved Y", "patched Z"
  - requires: "needs X", "uses Y", "depends on Z"
  - deploys: "deployed X", "installed Y", "set up Z"
  - affects: "changed X", "updated Y", "broke Z"

Usage:
    from mycelium_graph import EntityGraph
    g = EntityGraph()
    g.build_from_log()
    g.query_entity("grav")
    g.neighbors("grav", depth=2)
"""
from __future__ import annotations

import re
import sqlite3
from itertools import combinations

from mycelium_lib import MYCELIUM, INDEX, init_index, extract_entities, load_log


# ── Verb patterns for semantic edge detection ──────────────────

_EDGE_VERBS = {
    "resolves": re.compile(
        r'\b(?:fix(?:ed|es|ing)?|resolv(?:ed|es|ing)?|solv(?:ed|es|ing)?|patch(?:ed|es|ing)?)\b',
        re.IGNORECASE,
    ),
    "requires": re.compile(
        r'\b(?:need(?:s|ed|ing)?|requir(?:ed|es|ing)?|use(?:s|d|ing)?|depend(?:s|ed|ing)?\s+on)\b',
        re.IGNORECASE,
    ),
    "deploys": re.compile(
        r'\b(?:deploy(?:ed|s|ing)?|configur(?:ed|es|ing)?|install(?:ed|s|ing)?|set\s+up)\b',
        re.IGNORECASE,
    ),
    "affects": re.compile(
        r'\b(?:chang(?:ed|es|ing)|updat(?:ed|es|ing)|modif(?:ied|ies|ying)|broke(?:n)?)\b',
        re.IGNORECASE,
    ),
}


class EntityGraph:
    """Entity relationship graph backed by SQLite entity_edges table."""

    def __init__(self, db_path=None):
        from pathlib import Path
        self.db_path = Path(db_path) if db_path else INDEX
        self.conn = init_index(self.db_path)

    def close(self):
        self.conn.close()

    # ── Edge extraction ───────────────────────────────────────

    def extract_edges(self, entry: dict) -> list[dict]:
        """Extract all edges from a single log entry."""
        edges = []
        turn = entry.get("turn", 0)
        session = entry.get("session", "")
        ts = entry.get("ts", "")
        entities = entry.get("entities", [])
        user = entry.get("user", "")
        assistant = entry.get("assistant", "")
        text = (user + " " + assistant).lower()

        # co-occur: all entity pairs from this turn
        if len(entities) >= 2:
            for a, b in combinations(sorted(set(entities)), 2):
                edges.append({
                    "source": a, "target": b,
                    "edge_type": "co-occur",
                    "turn": turn, "session": session, "ts": ts,
                })

        # semantic edges: scan text for verb + entity
        for edge_type, pattern in _EDGE_VERBS.items():
            if not pattern.search(text):
                continue
            for ent in entities:
                ent_lower = ent.lower()
                # check verb appears before entity in text (loose proximity)
                for m in pattern.finditer(text):
                    if ent_lower in text[m.start():]:
                        words_before = text[:m.start()].split()
                        source = words_before[-1] if words_before else "context"
                        edges.append({
                            "source": source,
                            "target": ent,
                            "edge_type": edge_type,
                            "turn": turn, "session": session, "ts": ts,
                        })
                        break  # one edge per entity per edge_type per turn

        return edges

    # ── Storage ───────────────────────────────────────────────

    def store_edge(self, source: str, target: str, edge_type: str,
                   turn: int, session: str = "", ts: str = "") -> None:
        """Insert or bump weight of one edge."""
        self.conn.execute("""
            INSERT INTO entity_edges (source, target, edge_type, turn, session, ts, weight)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source, target, edge_type, turn)
            DO UPDATE SET weight = weight + 1
        """, (source, target, edge_type, turn, session, ts))
        self.conn.commit()

    def _store_edges(self, edges: list[dict]) -> None:
        """Batch-insert edges."""
        for e in edges:
            self.store_edge(**e)

    # ── Queries ───────────────────────────────────────────────

    def query_entity(self, entity: str) -> dict:
        """All relationships for an entity, grouped by edge_type.
        Returns {edge_type: [(other_entity, turn), ...]}
        """
        entity_l = entity.lower()
        rows = self.conn.execute(
            "SELECT source, target, edge_type, turn FROM entity_edges "
            "WHERE LOWER(source) = ? OR LOWER(target) = ?",
            (entity_l, entity_l),
        ).fetchall()

        result: dict[str, list[tuple[str, int]]] = {}
        for src, tgt, etype, turn in rows:
            other = tgt if src.lower() == entity_l else src
            result.setdefault(etype, []).append((other, turn))
        return result

    def neighbors(self, entity: str, depth: int = 1) -> set:
        """BFS to depth, returning set of neighboring entity names."""
        entity_l = entity.lower()
        visited = set()
        frontier = {entity_l}

        for _ in range(depth):
            next_frontier = set()
            for ent in frontier:
                rows = self.conn.execute(
                    "SELECT source, target FROM entity_edges "
                    "WHERE LOWER(source) = ? OR LOWER(target) = ?",
                    (ent, ent),
                ).fetchall()
                for src, tgt in rows:
                    other = tgt.lower() if src.lower() == ent else src.lower()
                    if other != entity_l and other not in visited:
                        next_frontier.add(other)
                        visited.add(other)
            frontier = next_frontier
        return visited

    def top_entities(self, limit: int = 10) -> list[tuple[str, int]]:
        """Most connected entities by distinct edge count."""
        rows = self.conn.execute(
            "SELECT entity, COUNT(*) AS cnt FROM ("
            "  SELECT LOWER(source) AS entity FROM entity_edges "
            "  UNION ALL "
            "  SELECT LOWER(target) AS entity FROM entity_edges"
            ") GROUP BY entity ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def count(self) -> int:
        """Total edge count."""
        return self.conn.execute("SELECT COUNT(*) FROM entity_edges").fetchone()[0]

    # ── Build from log ────────────────────────────────────────

    def build_from_log(self, log_path=None) -> int:
        """Rebuild graph from all log entries. Returns edge count."""
        entries = load_log(log_path)
        self.conn.execute("DELETE FROM entity_edges")
        total = 0
        for entry in entries:
            edges = self.extract_edges(entry)
            self._store_edges(edges)
            total += len(edges)
        self.conn.commit()
        return self.count()


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    g = EntityGraph()

    if len(sys.argv) < 2:
        print(f"Edges: {g.count()}")
        print("Entities:", g.top_entities(10))
        g.close()
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "build":
        n = g.build_from_log()
        print(f"Built {n} edges")

    elif cmd == "top":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for name, cnt in g.top_entities(limit):
            print(f"  {cnt:4d}  {name}")

    elif cmd == "query":
        entity = sys.argv[2] if len(sys.argv) > 2 else ""
        rels = g.query_entity(entity)
        for etype, pairs in rels.items():
            for other, turn in pairs:
                print(f"  {entity} --[{etype}]--> {other}  (turn {turn})")

    elif cmd == "neighbors":
        entity = sys.argv[2] if len(sys.argv) > 2 else ""
        depth = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        for n in sorted(g.neighbors(entity, depth)):
            print(f"  {n}")

    elif cmd == "count":
        print(g.count())

    else:
        print(f"Usage: {sys.argv[0]} [build|top|query|neighbors|count]")

    g.close()

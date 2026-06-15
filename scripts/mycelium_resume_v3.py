#!/usr/bin/env python3
"""
mycelium_resume_v3.py — V3 session resume using LSM layers.

Resume flow:
  1. Brain stats (1 line)
  2. Bloom pre-check on user_hint entities
  3. Load L0 entries (last 50 turns, full text)
  4. Filter by tier priority: S → A → B
  5. Token budget packing (1 token ≈ 4 chars)
  6. Entity graph enrichment for hint entities
  7. Negation check for hint entities
  8. Assemble formatted output

Usage:
    from mycelium_resume_v3 import ResumeV3
    rv = ResumeV3()
    print(rv.resume())
    print(rv.resume(user_hint="grav deployment"))
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import (
    MYCELIUM, INDEX, extract_entities, classify_tier, init_index,
)
from mycelium_lsm import MyceliumLSM
from mycelium_bloom import MyceliumBloom
from mycelium_graph import EntityGraph
from mycelium_negation import NegationExtractor


# ── Constants ────────────────────────────────────────────────
CHARS_PER_TOKEN = 4          # 1 token ≈ 4 chars (conservative)
L0_FETCH_LIMIT = 50          # turns to pull from L0
BLOOM_NAME = "entities"


class ResumeV3:
    """V3 session resume using LSM + Bloom + Graph + Negation."""

    def __init__(self, base_path: Path | str | None = None):
        self.base = Path(base_path) if base_path else MYCELIUM
        # LSM — primary entry store
        self.lsm = MyceliumLSM(self.base)
        self.lsm._ensure_loaded()
        # Populate L0 from jsonl if L0 is empty and log exists
        log_path = self.base / "log.jsonl"
        if self.lsm.l0.count() == 0 and log_path.exists():
            self.lsm.load_from_jsonl(log_path)
        # Bloom — probabilistic entity membership
        self.bloom = self._load_bloom()
        # Graph — entity relationships
        self.graph = EntityGraph(db_path=self.base / "index.db")
        # Negation — what doesn't work
        self.negation = NegationExtractor(db_path=self.base / "index.db")

    def _load_bloom(self) -> MyceliumBloom:
        """Load bloom from file or DB, or create empty."""
        bloom_path = self.base / f".bloom_{BLOOM_NAME}"
        if bloom_path.exists():
            return MyceliumBloom.load(bloom_path, name=BLOOM_NAME)
        try:
            return MyceliumBloom.load_from_db(name=BLOOM_NAME, db_path=self.base / "index.db")
        except (ValueError, FileNotFoundError):
            return MyceliumBloom(capacity=10000, name=BLOOM_NAME)

    # ── Brain Stats ──────────────────────────────────────────

    def brain_stats(self) -> str:
        """One-line brain stats: total entries, sessions, entities."""
        s = self.lsm.stats()
        total = s["total_entries"]
        # Count unique sessions from L0
        sessions = set()
        entities_set = set()
        for e in self.lsm.l0.to_list():
            sessions.add(e.get("session", "?"))
            entities_set.update(e.get("entities", []))
        # Also check DB for full counts
        try:
            conn = init_index(self.base / "index.db")
            db_sessions = conn.execute(
                "SELECT COUNT(DISTINCT session) FROM turns"
            ).fetchone()[0]
            db_entities = conn.execute(
                "SELECT COUNT(DISTINCT entity) FROM entities"
            ).fetchone()[0]
            conn.close()
            sessions_count = max(len(sessions), db_sessions)
            entities_count = max(len(entities_set), db_entities)
        except Exception:
            sessions_count = len(sessions) or 1
            entities_count = len(entities_set)

        return (
            f"🧠 {total} entries | {sessions_count} sessions | "
            f"{entities_count} entities | "
            f"L0={s['l0_entries']} L1={s['l1_entries']} L2={s['l2_entries']}"
        )

    # ── Token Budget Packing ─────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count: len(text) / CHARS_PER_TOKEN."""
        return max(1, len(text) // CHARS_PER_TOKEN)

    @staticmethod
    def _format_full(entry: dict) -> str:
        """Format entry with full text."""
        tier = entry.get("tier", "B")
        session = entry.get("session", "?")
        user = entry.get("user", "")[:120]
        assistant = entry.get("assistant", "")[:120]
        return f"[{tier}] turn {entry.get('turn', '?')} ({session})\n  U: {user}\n  A: {assistant}"

    @staticmethod
    def _format_compact(entry: dict) -> str:
        """Format entry in compact summary: [B] session | entities | user[:50]"""
        tier = entry.get("tier", "B")
        session = entry.get("session", "?")
        entities = ", ".join(entry.get("entities", [])[:3])
        user = entry.get("user", "")[:50]
        return f"[{tier}] {session} | {entities} | {user}"

    def pack_by_budget(self, entries: list, max_tokens: int) -> list:
        """Pack entries into token budget by tier priority.

        Priority: S (always, full) → A (full if budget) → B (compact).
        Returns list of formatted strings that fit within budget.
        """
        budget = max_tokens
        result = []

        # Sort by tier priority, then by turn descending (newest first)
        tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        sorted_entries = sorted(
            entries,
            key=lambda e: (tier_order.get(e.get("tier", "B"), 2), -e.get("turn", 0)),
        )

        for entry in sorted_entries:
            tier = entry.get("tier", "B")

            if tier == "S":
                # S-tier: always include full text
                text = self._format_full(entry)
                tokens = self._estimate_tokens(text)
                if tokens <= budget:
                    result.append(text)
                    budget -= tokens
                # else: S-tier overflows — truncate but still include
                else:
                    result.append(text[:budget * CHARS_PER_TOKEN])
                    budget = 0
                    break

            elif tier == "A":
                # A-tier: full text if budget allows
                text = self._format_full(entry)
                tokens = self._estimate_tokens(text)
                if tokens <= budget:
                    result.append(text)
                    budget -= tokens
                else:
                    # Try compact format
                    compact = self._format_compact(entry)
                    compact_tokens = self._estimate_tokens(compact)
                    if compact_tokens <= budget:
                        result.append(compact)
                        budget -= compact_tokens

            elif tier in ("B", "C"):
                # B/C-tier: compact format only
                compact = self._format_compact(entry)
                tokens = self._estimate_tokens(compact)
                if tokens <= budget:
                    result.append(compact)
                    budget -= tokens

        return result

    # ── Main Resume ──────────────────────────────────────────

    def resume(self, user_hint: str = None, max_tokens: int = 2000) -> str:
        """Full V3 resume. Returns formatted brain context string."""
        t0 = time.monotonic()
        sections = []

        # 1. Brain stats
        sections.append(self.brain_stats())

        # 2. Bloom pre-check
        hint_entities = []
        if user_hint:
            hint_entities = extract_entities(user_hint)
            if hint_entities:
                bloom_hits = []
                for ent in hint_entities:
                    if self.bloom.check(ent):
                        bloom_hits.append(ent)
                if bloom_hits:
                    sections.append(f"🔍 Bloom hits: {', '.join(bloom_hits)}")
                else:
                    sections.append("🔍 Bloom: no known entities in hint")

        # 3. Load L0 entries (last 50 turns)
        l0_entries = self.lsm.l0.to_list()

        # 4. Tier priority filter: S first, then A, then B
        tier_entries = {"S": [], "A": [], "B": []}
        for e in l0_entries:
            tier = e.get("tier", "B")
            if tier in tier_entries:
                tier_entries[tier].append(e)
            else:
                tier_entries["B"].append(e)

        # Flatten in priority order
        prioritized = tier_entries["S"] + tier_entries["A"] + tier_entries["B"]

        # 5. Token budget packing (reserve tokens for graph + negation)
        overhead_reserve = 200  # tokens for graph context + negation warnings
        entry_budget = max(100, max_tokens - overhead_reserve)
        packed = self.pack_by_budget(prioritized, entry_budget)

        if packed:
            sections.append("📋 Recent context:")
            sections.extend(packed)

        # 6. Entity graph enrichment
        if hint_entities:
            graph_sections = []
            for ent in hint_entities:
                try:
                    rels = self.graph.query_entity(ent)
                    if rels:
                        parts = []
                        for etype, pairs in rels.items():
                            others = set(p for p, _ in pairs[:5])
                            parts.append(f"{etype}: {', '.join(sorted(others)[:3])}")
                        graph_sections.append(f"  {ent}: {'; '.join(parts)}")
                except Exception:
                    pass  # graph may not have data
            if graph_sections:
                sections.append("🔗 Entity relations:")
                sections.extend(graph_sections)

        # 7. Negation check
        if hint_entities:
            negation_warnings = []
            for ent in hint_entities:
                try:
                    negs = self.negation.query(entity=ent)
                    for n in negs[:3]:
                        negation_warnings.append(
                            f"  ⚠️  [{n.get('result', '?')}] {n.get('approach', '?')}"
                        )
                except Exception:
                    pass
            if negation_warnings:
                sections.append("🚫 Known failures:")
                sections.extend(negation_warnings)

        # 8. Assemble
        elapsed_ms = (time.monotonic() - t0) * 1000
        sections.append(f"\n⏱️ Resume: {elapsed_ms:.1f}ms")

        return "\n".join(sections)

    def close(self):
        """Clean up connections."""
        try:
            self.graph.close()
        except Exception:
            pass


# ── CLI ──────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="V3 Mycelium Resume")
    parser.add_argument("--hint", default=None, help="User hint for entity enrichment")
    parser.add_argument("--tokens", "-t", type=int, default=2000, help="Token budget")
    parser.add_argument("--stats", action="store_true", help="Brain stats only")
    args = parser.parse_args()

    rv = ResumeV3()
    try:
        if args.stats:
            print(rv.brain_stats())
        else:
            print(rv.resume(user_hint=args.hint, max_tokens=args.tokens))
    finally:
        rv.close()


if __name__ == "__main__":
    main()

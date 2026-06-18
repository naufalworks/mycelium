#!/usr/bin/env python3
"""
Attention tracking + decay for mycelium entries.

Scores entries by hit frequency with exponential time decay.
Promotes/demotes tiers based on attention scores.

Usage:
    from mycelium_attention import AttentionTracker
    t = AttentionTracker()
    t.record_hit(turn=42, context="referenced in recall")
    print(t.score(turn=42))
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta

from mycelium_lib import MYCELIUM, INDEX, init_index

DECAY_HALF_LIFE_DAYS = 14


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _days_since(ts: str | None) -> float:
    """Days since a timestamp. Returns 0 if missing/parseable."""
    dt = _parse_ts(ts)
    if dt is None:
        return 0.0
    now = datetime.now(timezone.utc)
    # Ensure both are timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    return max(0.0, delta.total_seconds() / 86400.0)


def _entry_age_days(entry_ts: str | None) -> float:
    """Age of an entry in days from its timestamp."""
    dt = _parse_ts(entry_ts)
    if dt is None:
        return 0.0
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


class AttentionTracker:
    """Track attention scores for mycelium turns with exponential decay."""

    def __init__(self, db_path=None):
        self.conn = init_index(db_path)

    def close(self):
        self.conn.close()

    def record_hit(self, turn: int, context: str = "") -> None:
        """Increment hit_count, bump score, update last_referenced."""
        now = _now_iso()
        existing = self.conn.execute(
            "SELECT hit_count FROM attention WHERE turn=?", (turn,)
        ).fetchone()

        if existing:
            new_count = existing[0] + 1
            self.conn.execute(
                "UPDATE attention SET hit_count=?, score=?, last_referenced=? WHERE turn=?",
                (new_count, float(new_count), now, turn),
            )
        else:
            self.conn.execute(
                "INSERT INTO attention (turn, score, hit_count, last_referenced) VALUES (?,?,?,?)",
                (turn, 1.0, 1, now),
            )
        self.conn.commit()

    def score(self, turn: int) -> float:
        """Current attention score: base_score * time_decay.
        Returns 0 if no entry."""
        row = self.conn.execute(
            "SELECT hit_count, last_referenced FROM attention WHERE turn=?", (turn,)
        ).fetchone()
        if not row:
            return 0.0
        hit_count = row[0]
        if hit_count == 0:
            return 0.0
        base_score = float(hit_count)
        days = _days_since(row[1])
        time_decay = 0.5 ** (days / DECAY_HALF_LIFE_DAYS)
        return base_score * time_decay

    def _current_tier(self, turn: int) -> str:
        """Get tier from turns table."""
        row = self.conn.execute(
            "SELECT tier FROM turns WHERE turn=?", (turn,)
        ).fetchone()
        return row[0] if row else "B"

    def _set_tier(self, turn: int, tier: str) -> None:
        """Update tier in turns table."""
        self.conn.execute(
            "UPDATE turns SET tier=? WHERE turn=?", (tier, turn)
        )

    def promote(self, turn: int) -> str | None:
        """Promote tier if score threshold met. Returns new tier or None."""
        current = self._current_tier(turn)
        s = self.score(turn)
        now = _now_iso()
        new_tier = None

        if current == "B" and s > 5:
            new_tier = "A"
        elif current == "A" and s > 10:
            new_tier = "S"

        if new_tier:
            self._set_tier(turn, new_tier)
            self.conn.execute(
                "UPDATE attention SET last_promoted=? WHERE turn=?",
                (now, turn),
            )
            self.conn.commit()
            return new_tier
        return None

    def demote(self, turn: int) -> str | None:
        """Demote tier if inactive (hit_count==0) and age exceeds threshold.
        Returns new tier or None."""
        row = self.conn.execute(
            "SELECT hit_count FROM attention WHERE turn=?", (turn,)
        ).fetchone()
        if not row or row[0] > 0:
            return None

        # Get entry timestamp from turns table
        trow = self.conn.execute(
            "SELECT ts FROM turns WHERE turn=?", (turn,)
        ).fetchone()
        if not trow:
            return None

        age = _entry_age_days(trow[0])
        current = self._current_tier(turn)
        now = _now_iso()
        new_tier = None

        if current == "A" and age > 14:
            new_tier = "B"
        elif current == "B" and age > 30:
            new_tier = "C"

        if new_tier:
            self._set_tier(turn, new_tier)
            self.conn.execute(
                "UPDATE attention SET last_demoted=? WHERE turn=?",
                (now, turn),
            )
            self.conn.commit()
            return new_tier
        return None

    def decay_batch(self) -> dict:
        """Apply decay logic to all entries. Returns {promoted: [...], demoted: [...]}."""
        rows = self.conn.execute("SELECT turn FROM attention").fetchall()
        promoted = []
        demoted = []
        for (turn,) in rows:
            p = self.promote(turn)
            if p:
                promoted.append({"turn": turn, "tier": p})
            d = self.demote(turn)
            if d:
                demoted.append({"turn": turn, "tier": d})
        return {"promoted": promoted, "demoted": demoted}

    def top_entries(self, limit: int = 10) -> list:
        """Highest-scored entries with current attention score."""
        rows = self.conn.execute(
            "SELECT turn, hit_count, last_referenced FROM attention "
            "ORDER BY score DESC, hit_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for turn, hit_count, last_ref in rows:
            results.append({
                "turn": turn,
                "hit_count": hit_count,
                "last_referenced": last_ref,
                "current_score": self.score(turn),
            })
        return results

    def stale_entries(self, limit: int = 10) -> list:
        """Entries never referenced (no attention row) or zero hits."""
        rows = self.conn.execute(
            "SELECT t.turn, COALESCE(a.hit_count, 0), a.last_referenced "
            "FROM turns t LEFT JOIN attention a ON t.turn = a.turn "
            "WHERE COALESCE(a.hit_count, 0) = 0 "
            "ORDER BY t.turn ASC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for turn, hit_count, last_ref in rows:
            results.append({
                "turn": turn,
                "hit_count": hit_count,
                "last_referenced": last_ref,
                "current_score": self.score(turn),
            })
        return results

    def stats(self) -> dict:
        """Aggregate stats."""
        total = self.conn.execute("SELECT COUNT(*) FROM attention").fetchone()[0]
        avg_row = self.conn.execute(
            "SELECT AVG(score) FROM attention"
        ).fetchone()
        avg_score = avg_row[0] if avg_row[0] is not None else 0.0

        promoted_count = self.conn.execute(
            "SELECT COUNT(*) FROM attention WHERE last_promoted IS NOT NULL"
        ).fetchone()[0]
        demoted_count = self.conn.execute(
            "SELECT COUNT(*) FROM attention WHERE last_demoted IS NOT NULL"
        ).fetchone()[0]

        return {
            "total_tracked": total,
            "avg_score": round(avg_score, 4),
            "promoted_count": promoted_count,
            "demoted_count": demoted_count,
        }


if __name__ == "__main__":
    import sys
    import json as _json

    t = AttentionTracker()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"

    if cmd == "top":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        entries = t.top_entries(limit)
        if not entries:
            print("No attention entries with scores > 0")
        else:
            print(f"Top {len(entries)} most-attended entries:")
            for e in entries:
                print(f"  turn={e['turn']:5d}  hits={e['hit_count']}  score={e['current_score']:.4f}  last={e['last_referenced'][:19] if e['last_referenced'] else 'never'}")

    elif cmd == "stale":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        entries = t.stale_entries(limit)
        if not entries:
            print("No stale entries found")
        else:
            print(f"Stale entries (never referenced):")
            for e in entries:
                print(f"  turn={e['turn']:5d}  hits={e['hit_count']}  score={e['current_score']:.4f}")

    elif cmd == "decay":
        result = t.decay_batch()
        print(f"Decay complete: {len(result['promoted'])} promoted, {len(result['demoted'])} demoted")
        for p in result["promoted"]:
            print(f"  PROMOTE turn={p['turn']} → tier={p['tier']}")
        for d in result["demoted"]:
            print(f"  DEMOTE turn={d['turn']} → tier={d['tier']}")

    elif cmd == "stats":
        s = t.stats()
        print(f"Attention Tracker:")
        print(f"  Total tracked: {s['total_tracked']}")
        print(f"  Avg score:     {s['avg_score']}")
        print(f"  Promoted:      {s['promoted_count']}")
        print(f"  Demoted:       {s['demoted_count']}")

    elif cmd == "score":
        turn = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        score = t.score(turn)
        print(f"Turn {turn} score: {score:.4f}")

    else:
        print(f"Usage: {sys.argv[0]} [top|stale|decay|stats|score] [limit|turn]")
        sys.exit(1)

    t.close()

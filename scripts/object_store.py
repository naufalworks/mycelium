#!/usr/bin/env python3
"""
Content-addressed object store for Mycelium.

Stores conversation entries as immutable JSON files keyed by SHA256 hash.
Deduplicates identical entries across sessions via ref counting in SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Import from shared library
from mycelium_lib import MYCELIUM, INDEX, init_index, load_log


def _content_hash(entry: dict) -> str:
    """SHA256 of canonical JSON → first 16 hex chars."""
    raw = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ObjectStore:
    """Content-addressed storage for conversation entries."""

    def __init__(self, base_path: str | Path | None = None):
        self.base_path = Path(base_path) if base_path else MYCELIUM / "objects"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def put(self, entry: dict, session: str = "?") -> str:
        """Store entry. Returns content_hash. Deduplicates on hash collision."""
        h = _content_hash(entry)
        conn = init_index()
        row = conn.execute(
            "SELECT ref_count, sessions FROM objects WHERE content_hash=?", (h,)
        ).fetchone()

        if row:
            # Exists → increment ref, track session
            sessions = row[1] or ""
            if session not in sessions.split(","):
                sessions = ",".join(s.strip() for s in sessions.split(",") if s.strip()) + ("," if sessions else "") + session
            conn.execute(
                "UPDATE objects SET ref_count=ref_count+1, sessions=? WHERE content_hash=?",
                (sessions, h),
            )
            conn.commit()
            conn.close()
            return h

        # New object → write file + insert row
        obj_path = self.base_path / f"{h}.json"
        obj_path.write_text(
            json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
        )
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO objects (content_hash, ref_count, first_seen, sessions) VALUES (?,?,?,?)",
            (h, 1, now, session),
        )
        conn.commit()
        conn.close()
        return h

    def get(self, obj_hash: str) -> dict | None:
        """Retrieve object by hash."""
        obj_path = self.base_path / f"{obj_hash}.json"
        if not obj_path.exists():
            return None
        return json.loads(obj_path.read_text())

    def exists(self, obj_hash: str) -> bool:
        """Check if object file exists on disk."""
        return (self.base_path / f"{obj_hash}.json").exists()

    def ref_count(self, obj_hash: str) -> int:
        """How many turns reference this object."""
        conn = init_index()
        row = conn.execute(
            "SELECT ref_count FROM objects WHERE content_hash=?", (obj_hash,)
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def add_ref(self, obj_hash: str, session: str = "?") -> int:
        """Increment ref count, track session. Returns new count."""
        conn = init_index()
        row = conn.execute(
            "SELECT ref_count, sessions FROM objects WHERE content_hash=?", (obj_hash,)
        ).fetchone()
        if not row:
            conn.close()
            return 0
        sessions = row[1] or ""
        if session not in sessions.split(","):
            sessions = ",".join(s.strip() for s in sessions.split(",") if s.strip()) + ("," if sessions else "") + session
        conn.execute(
            "UPDATE objects SET ref_count=ref_count+1, sessions=? WHERE content_hash=?",
            (sessions, obj_hash),
        )
        conn.commit()
        row2 = conn.execute(
            "SELECT ref_count FROM objects WHERE content_hash=?", (obj_hash,)
        ).fetchone()
        conn.close()
        return row2[0] if row2 else 0

    def dedup_candidates(self, min_refs: int = 2) -> list:
        """Objects with ref_count >= min_refs."""
        conn = init_index()
        rows = conn.execute(
            "SELECT content_hash, ref_count, sessions FROM objects WHERE ref_count >= ? ORDER BY ref_count DESC",
            (min_refs,),
        ).fetchall()
        conn.close()
        return [
            {"content_hash": r[0], "ref_count": r[1], "sessions": r[2]}
            for r in rows
        ]

    def stats(self) -> dict:
        """Store statistics."""
        conn = init_index()
        total = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        refs = conn.execute("SELECT COALESCE(SUM(ref_count), 0) FROM objects").fetchone()[0]
        rows = conn.execute("SELECT content_hash, ref_count FROM objects").fetchall()
        conn.close()

        # Dedup savings = bytes stored × (ref_count - 1) for each object
        savings = 0
        for h, rc in rows:
            obj_path = self.base_path / f"{h}.json"
            if obj_path.exists() and rc > 1:
                size = obj_path.stat().st_size
                savings += size * (rc - 1)

        return {
            "total_objects": total,
            "total_refs": refs,
            "dedup_savings_bytes": savings,
        }

    def verify(self) -> dict:
        """Check all DB-referenced objects exist on disk."""
        conn = init_index()
        rows = conn.execute("SELECT content_hash FROM objects").fetchall()
        conn.close()
        missing = []
        for (h,) in rows:
            if not self.exists(h):
                missing.append(h)
        return {
            "total_in_db": len(rows),
            "missing": missing,
            "ok": len(missing) == 0,
        }

    def build_from_log(self, log_path: str | Path | None = None) -> int:
        """Build object store from existing log.jsonl. Returns count stored."""
        entries = load_log(log_path)
        count = 0
        for entry in entries:
            session = entry.get("session", "?")
            self.put(entry, session=session)
            count += 1
        return count

    def _all_entries(self) -> list:
        """Load all entries from log for stats computation."""
        return load_log()

#!/usr/bin/env python3
"""
Mycelium Semantic Memory Layer.

Stores structured memory facts extracted from brain sessions.
All facts are integrity-hashed and linked to the brain hash chain.

Tables:
  memory_facts        — entity-attribute-value with confidence, entropy, tier
  context_snapshots   — per-session structured summaries
"""

import json, sqlite3, hashlib, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

INDEX_PATH = None  # set by init_index()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity          TEXT NOT NULL,
    attribute       TEXT NOT NULL,
    value           TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    fact_type       TEXT DEFAULT 'fact',
    tier            INTEGER DEFAULT 0,
    entropy         REAL DEFAULT 0.5,
    source_session  TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    hash            TEXT,
    UNIQUE(entity, attribute, value)
);

CREATE TABLE IF NOT EXISTS context_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,
    summary         TEXT,
    topics          TEXT,
    decisions       TEXT,
    entities        TEXT,
    credentials     TEXT,
    turn_count      INTEGER DEFAULT 0,
    last_turn_hash  TEXT,
    created_at      TEXT NOT NULL,
    hash            TEXT
);

CREATE INDEX IF NOT EXISTS idx_mf_entity ON memory_facts(entity);
CREATE INDEX IF NOT EXISTS idx_mf_type ON memory_facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_mf_tier ON memory_facts(tier);
CREATE INDEX IF NOT EXISTS idx_cs_session ON context_snapshots(session_id);
"""


def conn() -> sqlite3.Connection:
    """Get SQLite connection to the index DB."""
    global INDEX_PATH
    if INDEX_PATH is None:
        from mycelium_lib import INDEX
        INDEX_PATH = INDEX
    c = sqlite3.connect(str(INDEX_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_tables():
    """Create memory tables if they don't exist."""
    db = conn()
    db.executescript(SCHEMA_SQL)
    db.commit()
    db.close()


def _fact_hash(entity: str, attr: str, value: str, session: str) -> str:
    raw = f"memory_fact|{entity}|{attr}|{value}|{session}|{datetime.now(timezone.utc).isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def insert_fact(
    entity: str,
    attribute: str,
    value: str,
    fact_type: str = "fact",
    confidence: float = 1.0,
    source_session: Optional[str] = None,
    entropy: float = 0.5,
) -> bool:
    """Insert or update a memory fact. Returns True if inserted, False if updated."""
    now = datetime.now(timezone.utc).isoformat()
    h = _fact_hash(entity, attribute, value, source_session or "auto")

    db = conn()
    try:
        db.execute("""
            INSERT INTO memory_facts (entity, attribute, value, confidence, fact_type,
                                      tier, entropy, source_session, created_at, updated_at, hash)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
        """, (entity, attribute, value, confidence, fact_type,
              entropy, source_session, now, now, h))
        db.commit()
        return True
    except sqlite3.IntegrityError:
        # Update existing — bump confidence and update timestamp
        db.execute("""
            UPDATE memory_facts
            SET confidence = MAX(confidence, ?),
                updated_at = ?,
                entropy = ?
            WHERE entity = ? AND attribute = ? AND value = ?
        """, (confidence, now, entropy, entity, attribute, value))
        db.commit()
        return False
    finally:
        db.close()


def recall_facts(
    entity: Optional[str] = None,
    attribute: Optional[str] = None,
    fact_type: Optional[str] = None,
    limit: int = 50,
    tier: Optional[int] = None,
) -> list[dict]:
    """Query memory facts with filters."""
    db = conn()
    where = []
    params = []

    if entity:
        where.append("entity LIKE ?")
        params.append(f"%{entity}%")
    if attribute:
        where.append("attribute LIKE ?")
        params.append(f"%{attribute}%")
    if fact_type:
        where.append("fact_type = ?")
        params.append(fact_type)
    if tier is not None:
        where.append("tier = ?")
        params.append(tier)

    sql = "SELECT * FROM memory_facts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tier ASC, confidence DESC, updated_at DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


def search_facts(query: str, limit: int = 20) -> list[dict]:
    """Full-text style search across facts using LIKE."""
    db = conn()
    like = f"%{query}%"
    rows = db.execute("""
        SELECT * FROM memory_facts
        WHERE entity LIKE ? OR attribute LIKE ? OR value LIKE ?
        ORDER BY confidence DESC, tier ASC
        LIMIT ?
    """, (like, like, like, limit)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def create_snapshot(
    session_id: str,
    summary: str = "",
    topics: Optional[list] = None,
    decisions: Optional[list] = None,
    entities: Optional[list] = None,
    credentials: Optional[list] = None,
    turn_count: int = 0,
    last_turn_hash: str = "",
) -> bool:
    """Create or replace a context snapshot for a session."""
    now = datetime.now(timezone.utc).isoformat()
    raw = f"snapshot|{session_id}|{summary}|{now}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]

    db = conn()
    try:
        db.execute("""
            INSERT OR REPLACE INTO context_snapshots
            (session_id, summary, topics, decisions, entities, credentials,
             turn_count, last_turn_hash, created_at, hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, summary,
            json.dumps(topics or []), json.dumps(decisions or []),
            json.dumps(entities or []), json.dumps(credentials or []),
            turn_count, last_turn_hash, now, h,
        ))
        db.commit()
        return True
    finally:
        db.close()


def get_snapshot(session_id: str) -> Optional[dict]:
    """Get snapshot for a session."""
    db = conn()
    row = db.execute("SELECT * FROM context_snapshots WHERE session_id = ?",
                     (session_id,)).fetchone()
    db.close()
    if row:
        d = dict(row)
        for field in ("topics", "decisions", "entities", "credentials"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
        return d
    return None


def last_snapshot() -> Optional[dict]:
    """Get the most recent snapshot."""
    db = conn()
    row = db.execute("""
        SELECT * FROM context_snapshots
        ORDER BY created_at DESC LIMIT 1
    """).fetchone()
    db.close()
    if row:
        d = dict(row)
        for field in ("topics", "decisions", "entities", "credentials"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
        return d
    return None


def fact_stats() -> dict:
    """Return summary statistics about stored facts."""
    db = conn()
    stats = {
        "total_facts": db.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0],
        "total_snapshots": db.execute("SELECT COUNT(*) FROM context_snapshots").fetchone()[0],
        "by_type": {},
        "by_tier": {},
    }
    for r in db.execute("SELECT fact_type, COUNT(*) as c FROM memory_facts GROUP BY fact_type"):
        stats["by_type"][r["fact_type"]] = r["c"]
    for r in db.execute("SELECT tier, COUNT(*) as c FROM memory_facts GROUP BY tier"):
        stats["by_tier"][f"tier_{r['tier']}"] = r["c"]
    db.close()
    return stats


def precheck() -> list[dict]:
    """Run memory layer precheck. Returns list of issues."""
    issues = []
    db = conn()
    try:
        tables = [r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for t in ("memory_facts", "context_snapshots"):
            if t not in tables:
                issues.append({"message": f"Memory table missing: {t}"})
    except Exception as e:
        issues.append({"message": f"Memory precheck error: {e}"})
    finally:
        db.close()
    return issues


# ── Entropy-Weighted Compaction ───────────────────────────

HOT_DAYS = 14
WARM_DAYS = 60
COOL_DAYS = 180


def _days_old(iso_ts: str) -> float:
    if not iso_ts:
        return 9999.0
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
    except Exception:
        return 9999.0


def _target_tier(confidence: float, entropy: float, days: float) -> int:
    """Entropy-weighted tier assignment.

    A fact's survival depends on how *surprising* it is, not just recency.
    High-entropy (novel/surprising) facts stay hot longer.
    Low-entropy (mundane/common) facts decay faster.
    """
    # Novel, surprising facts → stay hot
    if days <= HOT_DAYS and confidence >= 0.6 and entropy >= 0.4:
        return 0
    # High-entropy gets extra warmth even if older
    if days <= WARM_DAYS * (1 + entropy):
        return 1
    if days <= COOL_DAYS:
        return 2
    return 3


def compact(verbose: bool = True) -> dict:
    """Run one compaction pass: re-evaluate every fact's tier.

    Returns promotion/demotion counts.
    """
    db = conn()
    before = {}
    for r in db.execute("SELECT tier, COUNT(*) as c FROM memory_facts GROUP BY tier"):
        before[f"tier_{r['tier']}"] = r["c"]

    facts = db.execute("""
        SELECT id, confidence, entropy, created_at, updated_at, tier
        FROM memory_facts
    """).fetchall()

    up = down = stay = 0
    for r in facts:
        days = _days_old(r["updated_at"] or r["created_at"])
        target = _target_tier(r["confidence"], r["entropy"], days)
        if target < r["tier"]:
            up += 1
        elif target > r["tier"]:
            down += 1
        else:
            stay += 1
        if target != r["tier"]:
            db.execute("UPDATE memory_facts SET tier = ? WHERE id = ?", (target, r["id"]))

    db.commit()

    after = {}
    for r in db.execute("SELECT tier, COUNT(*) as c FROM memory_facts GROUP BY tier"):
        after[f"tier_{r['tier']}"] = r["c"]

    db.close()

    stats = {
        "promoted": up, "demoted": down, "unchanged": stay,
        "before": before, "after": after,
    }

    if verbose:
        print("🧹 Compact memory_facts")
        print(f"  Promoted (→hot): {up}  → Demoted (→cold): {down}  → Unchanged: {stay}")
        print(f"  Before: {before}")
        print(f"  After:  {after}")

    return stats


def dedup(verbose: bool = True) -> dict:
    """Merge duplicate (entity, attribute, value) triples.

    Keeps highest confidence, averages entropy, deletes the extras.
    """
    db = conn()
    dups = db.execute("""
        SELECT entity, attribute, value, COUNT(*) as cnt,
               MAX(confidence) as mc, AVG(entropy) as ae,
               GROUP_CONCAT(id) as ids
        FROM memory_facts
        GROUP BY entity, attribute, value
        HAVING cnt > 1
    """).fetchall()

    removed = 0
    for r in dups:
        ids = sorted(int(x) for x in r["ids"].split(","))
        keep, *deletions = ids
        db.execute("""
            UPDATE memory_facts SET confidence=?, entropy=? WHERE id=?
        """, (r["mc"], r["ae"], keep))
        if deletions:
            placeholders = ",".join("?" * len(deletions))
            db.execute(f"DELETE FROM memory_facts WHERE id IN ({placeholders})", deletions)
            removed += len(deletions)

    db.commit()
    db.close()

    if verbose:
        print(f"  Dedup: removed {removed} duplicate fact{'s' if removed != 1 else ''}")
    return {"removed": removed}


def full_compact(verbose: bool = True) -> dict:
    """Full compaction cycle: dedup → tier compaction."""
    if verbose:
        print("🍄 Mycelium Memory Compaction")
    return {"dedup": dedup(verbose), "tiers": compact(verbose)}

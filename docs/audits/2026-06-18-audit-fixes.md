# Mycelium Audit Fixes — 2026-06-18

## Fixes Applied

### 1. PRAGMA foreign_keys = ON (HIGH)
**Files:** `scripts/mycelium_lib.py`
**Issue:** SQLite foreign key constraints were declared in the schema but never enforced.
`PRAGMA foreign_keys` defaults to OFF in SQLite, so `FOREIGN KEY` references on
`entities.turn → turns.turn` and `findings.turn → turns.turn` were silently ignored.
**Fix:** Added `conn.execute("PRAGMA foreign_keys=ON")` to `init_index()` so every
connection enforces referential integrity.
**Impact:** Prevents orphaned entity/finding rows from accumulating.

### 2. Fixed deletion order in rebuild_index (HIGH)
**Files:** `scripts/mycelium_lib.py`
**Issue:** `rebuild_index()` deleted from `turns` before `entities`/`findings`, which
crashes when foreign keys are enforced (child rows still reference the parent).
**Fix:** Reordered deletions: entities → findings → turns (children first).

### 3. Fixed operation order in update_index (HIGH)
**Files:** `scripts/mycelium_lib.py`
**Issue:** `update_index()` ran `INSERT OR REPLACE INTO turns` before `DELETE FROM
entities WHERE turn=?`, which would fail with FK enforcement (REPLACE internally
DELETEs the parent row while child rows still exist).
**Fix:** Move child-row cleanup before the parent INSERT OR REPLACE.

### 4. Backfilled missing attention entries (HIGH)
**Files:** `scripts/append.py`, attention table in `index.db`
**Issue:** Turns 692-694 were appended to the log without corresponding attention rows,
likely from appends that skipped the index update path. These turns would never decay,
never be promoted, and would be invisible to attention-based queries.
**Fix:** Inserted attention rows for turns 692-694 with their correct tier-based scores.
**Verification:** Attention table now has 1439 entries matching 1439 turns — zero gaps.

### 5. Added file locking to log appends (MEDIUM)
**Files:** `scripts/append.py`
**Issue:** Concurrent appends from CLI + daemon could interleave JSON lines, corrupting
the log. No file-level locking existed.
**Fix:** Wrapped the append write in `fcntl.flock(f, LOCK_EX)` / `LOCK_UN`.
**Impact:** Only one process writes at a time; others block and retry automatically.

### 6. Restarted safety-net daemon (HIGH)
**Files:** launchd service, runtime scripts
**Issue:** The myceliumd daemon was loaded in launchd but no longer running (last run
~7 hours prior). The runtime copy of `append.py` was also stale (missing fcntl import).
**Fix:** Unloaded launchd plist, cleaned stale lock file, synced updated runtime scripts,
reloaded launchd service.
**Status:** Daemon runs every 60s via launchd, watching Hermes state.db for missed imports.

## Verification

```
Preflight check:  14/14 checks passed ✅
Tests:            228/228 passed ✅
Integrity chain:  1441 turns, all hashes match ✅
Daemon:           loaded in launchd ✅
```

## Relevant Files

| File | Changes |
|------|---------|
| `scripts/mycelium_lib.py` | PRAGMA foreign_keys, fix rebuild_index + update_index deletion order |
| `scripts/append.py` | fcntl import, LOCK_EX/LOCK_UN around log append |
| `scripts/precheck.py` | v3 preflight rewrite (pre-existing, committed alongside) |
| `scripts/mycelium_attention.py` | CLI entry point for attention tracking (pre-existing) |

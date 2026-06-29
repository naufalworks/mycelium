//! Safety harness — snapshot, rollback, and entry-count invariant.
//!
//! Before any mutation the harness snapshots the database via `VACUUM INTO`.
//! On failure the snapshot is copied back over the original DB file (after
//! checkpointing the WAL).  The harness also verifies that the entry count
//! never decreases — a crucial invariant for tamper-evident logging.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use rusqlite::Connection;
use uuid::Uuid;

/// Pre-repair snapshot + safety invariants.
///
/// # Usage
///
/// 1. `SafetyHarness::new(db_path, mycelium_dir)`
/// 2. `harness.snapshot(&conn)` — creates a clean `.db` copy via `VACUUM INTO`
/// 3. Perform repair operations
/// 4. `harness.verify_entry_count(&conn)` — ensures entries were not deleted
/// 5. `harness.rollback(&conn)` — if verification fails, restore the snapshot
pub struct SafetyHarness {
    /// Path to the live database file.
    db_path: PathBuf,
    /// Directory where snapshot files are stored.
    snapshots_dir: PathBuf,
    /// Whether a snapshot exists for the current repair cycle.
    has_snapshot: AtomicBool,
    /// The ID (UUID) of the active snapshot, if any.
    active_snapshot_id: Mutex<Option<String>>,
    /// Entry count recorded at snapshot time (must never decrease).
    entry_count_before: Mutex<i64>,
}

impl SafetyHarness {
    /// Create a new safety harness for the given database path.
    ///
    /// `mycelium_dir` is the `.mycelium/` data directory under which a
    /// `snapshots/` sub-directory will be created.
    pub fn new(db_path: PathBuf, mycelium_dir: PathBuf) -> Self {
        let snapshots_dir = mycelium_dir.join("snapshots");
        Self {
            db_path,
            snapshots_dir,
            has_snapshot: AtomicBool::new(false),
            active_snapshot_id: Mutex::new(None),
            entry_count_before: Mutex::new(0),
        }
    }

    /// Whether a snapshot has been taken.
    pub fn has_snapshot(&self) -> bool {
        self.has_snapshot.load(Ordering::SeqCst)
    }

    /// The ID of the active snapshot, if any.
    pub fn active_snapshot_id(&self) -> Option<String> {
        self.active_snapshot_id.lock().unwrap().clone()
    }

    /// Take a full snapshot of the database via `VACUUM INTO`.
    ///
    /// The snapshot is written to `{snapshots_dir}/{uuid}.db`.  The WAL is
    /// checkpointed first so the snapshot reflects all committed writes.
    ///
    /// Returns the snapshot UUID string.
    pub fn snapshot(&self, conn: &Connection) -> anyhow::Result<String> {
        let id = Uuid::new_v4().to_string();
        let snapshot_path = self.snapshots_dir.join(format!("{id}.db"));

        std::fs::create_dir_all(&self.snapshots_dir)?;

        // Checkpoint WAL so `VACUUM INTO` captures all committed data.
        conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;

        // VACUUM INTO creates a fresh, compact copy of the database.
        let escaped_path = snapshot_path.to_string_lossy().replace('\'', "''");
        let vacuum_cmd = format!("VACUUM INTO '{escaped_path}'");
        conn.execute_batch(&vacuum_cmd)?;

        self.has_snapshot.store(true, Ordering::SeqCst);
        *self.active_snapshot_id.lock().unwrap() = Some(id.clone());

        // Record the entry count at snapshot time.
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0))
            .unwrap_or(0);
        *self.entry_count_before.lock().unwrap() = count;

        tracing::info!("snapshot {id} created at {:?}", snapshot_path);
        Ok(id)
    }

    /// Roll back to the active snapshot by copying the snapshot file over
    /// the live database.
    ///
    /// The WAL is checkpointed (truncated) first so that the subsequent
    /// file copy is clean.  After rollback the snapshot is consumed —
    /// `has_snapshot()` returns `false` and `active_snapshot_id()` returns
    /// `None`.
    pub fn rollback(&self, conn: &Connection) -> anyhow::Result<()> {
        let id = self.active_snapshot_id.lock().unwrap().take();
        match id {
            Some(id) => {
                let snapshot_path = self.snapshots_dir.join(format!("{id}.db"));
                if !snapshot_path.exists() {
                    return Err(anyhow::anyhow!(
                        "snapshot file not found: {:?}",
                        snapshot_path,
                    ));
                }

                // Checkpoint + truncate WAL so no pending transactions linger.
                conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;

                // Copy the snapshot back over the live database file.
                std::fs::copy(&snapshot_path, &self.db_path)?;

                self.has_snapshot.store(false, Ordering::SeqCst);

                tracing::warn!("rollback to snapshot {id} completed");
                Ok(())
            }
            None => Err(anyhow::anyhow!("no active snapshot to rollback to")),
        }
    }

    /// Verify the entry-count invariant: the number of rows in `entries`
    /// must not have decreased since the snapshot was taken.
    ///
    /// Returns an error if the count is lower, which indicates that entries
    /// were deleted — a violation of the safety policy.
    pub fn verify_entry_count(&self, conn: &Connection) -> anyhow::Result<()> {
        let before = *self.entry_count_before.lock().unwrap();
        let after: i64 = conn
            .query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0))
            .map_err(|e| anyhow::anyhow!("failed to query entry count: {e}"))?;

        anyhow::ensure!(
            after >= before,
            "entry count decreased: before={before}, after={after}",
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    /// Helper: create a file-based SQLite database with `entries` table.
    fn setup_file_db() -> (TempDir, PathBuf, Connection) {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             CREATE TABLE entries (
                id INTEGER PRIMARY KEY,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                user_content TEXT
             );
             INSERT INTO entries (prev_hash, hash) VALUES ('a', 'b');
             INSERT INTO entries (prev_hash, hash) VALUES ('b', 'c');
             INSERT INTO entries (prev_hash, hash) VALUES ('c', 'd');",
        )
        .unwrap();
        (dir, db_path, conn)
    }

    #[test]
    fn test_new_has_no_snapshot() {
        let dir = TempDir::new().unwrap();
        let harness =
            SafetyHarness::new(dir.path().join("db.db"), dir.path().join(".mycelium"));
        assert!(!harness.has_snapshot());
        assert!(harness.active_snapshot_id().is_none());
    }

    #[test]
    fn test_snapshot_creates_file() {
        let (_dir, db_path, conn) = setup_file_db();
        let mycelium_dir = db_path.parent().unwrap().join(".mycelium");
        let harness = SafetyHarness::new(db_path, mycelium_dir.clone());

        let id = harness.snapshot(&conn).unwrap();
        assert!(harness.has_snapshot());
        assert_eq!(harness.active_snapshot_id().unwrap(), id);

        let snapshot_path = mycelium_dir.join("snapshots").join(format!("{id}.db"));
        assert!(snapshot_path.exists(), "snapshot file should exist");
    }

    #[test]
    fn test_rollback_restores_snapshot() {
        let (_dir, db_path, conn) = setup_file_db();
        let mycelium_dir = db_path.parent().unwrap().join(".mycelium");
        let harness = SafetyHarness::new(db_path.clone(), mycelium_dir);

        // Snapshot (3 entries)
        harness.snapshot(&conn).unwrap();

        // Mutate: delete an entry
        conn.execute("DELETE FROM entries WHERE id = 1", []).unwrap();
        let after_delete: i64 =
            conn.query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0))
                .unwrap();
        assert_eq!(after_delete, 2);

        // Rollback
        harness.rollback(&conn).unwrap();
        assert!(!harness.has_snapshot());
        assert!(harness.active_snapshot_id().is_none());

        // Re-open the DB to verify the rollback was persistent
        let conn2 = Connection::open(&db_path).unwrap();
        let count: i64 = conn2
            .query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 3, "rollback should restore 3 entries");
    }

    #[test]
    fn test_verify_entry_count_passes() {
        let (_dir, _db_path, _conn) = setup_file_db();
        // Snapshot records count = 3; we don't change anything, so it passes.
        // But we can't snapshot from memory and verify — need file DB.
        // Actually, snapshot works on any Connection. Let's test on file db.
        let (_dir, db_path, conn) = setup_file_db();
        let mycelium_dir = db_path.parent().unwrap().join(".mycelium");
        let harness = SafetyHarness::new(db_path, mycelium_dir);
        harness.snapshot(&conn).unwrap();
        assert!(harness.verify_entry_count(&conn).is_ok());
    }

    #[test]
    fn test_verify_entry_count_fails_on_delete() {
        let (_dir, db_path, conn) = setup_file_db();
        let mycelium_dir = db_path.parent().unwrap().join(".mycelium");
        let harness = SafetyHarness::new(db_path, mycelium_dir);
        harness.snapshot(&conn).unwrap();

        conn.execute("DELETE FROM entries WHERE id = 1", []).unwrap();
        let result = harness.verify_entry_count(&conn);
        assert!(result.is_err(), "should fail when entries are deleted");
        let err = result.unwrap_err().to_string();
        assert!(err.contains("decreased"), "error should mention decrease: {err}");
    }

    #[test]
    fn test_rollback_without_snapshot_errors() {
        let dir = TempDir::new().unwrap();
        let (db_path, conn) = {
            let (_dir, p, c) = setup_file_db();
            (p, c)
        };
        let harness = SafetyHarness::new(db_path, dir.path().join(".mycelium"));
        let result = harness.rollback(&conn);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("no active snapshot"));
    }
}

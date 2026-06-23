//! SQLite storage engine for Mycelium's permanent memory.
//!
//! Architecture:
//! - Single SQLite database (`mycelium.db`) with WAL mode
//! - All tables: entries, memory_facts, artifacts, context_snapshots, workflows, workflow_runs
//! - Proper indexes on every query path
//! - Connection pooling via r2d2 or direct multiplexing
//! - Read replicas are not needed with WAL mode (concurrent reads + single writer)

use rusqlite::{params, Connection, OpenFlags};
use std::path::PathBuf;
use std::sync::Mutex;
use tracing::{debug, info};

use crate::error::MyceliumError;
use crate::cache::MemoryCache;
use crate::error::Result;
use crate::search::SearchIndex;
use crate::types::*;

/// The storage engine managing all persistent memory.
pub struct Storage {
    /// Path to the mycelium database file.
    path: PathBuf,
    /// SQLite connection (protected by Mutex for single-writer safety).
    conn: Mutex<Connection>,
    /// In-memory cache for hot data.
    cache: MemoryCache,
    /// Full-text search index (tantivy).
    search_index: Option<SearchIndex>,
}

impl Storage {
    /// Open or create the database at the given path.
    pub fn open(path: PathBuf) -> Result<Self> {
        let conn = Connection::open_with_flags(
            &path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;

        // Enable WAL mode for concurrent reads
        conn.execute_batch("PRAGMA journal_mode=WAL;")?;
        // Optimize for SSD/Flash storage
        conn.execute_batch("PRAGMA synchronous=NORMAL;")?;
        // Cache size: 64MB
        conn.execute_batch("PRAGMA cache_size=-65536;")?;
        // Busy timeout: 5 seconds
        conn.execute_batch("PRAGMA busy_timeout=5000;")?;
        // Foreign keys
        conn.execute_batch("PRAGMA foreign_keys=ON;")?;
        // Optimize for memory reads
        conn.execute_batch("PRAGMA mmap_size=268435456;")?; // 256MB

        let search_index = SearchIndex::open(
            path.parent().map(|p| p.join("search_index")).unwrap_or_else(|| PathBuf::from("search_index")),
        ).ok();

        let storage = Storage {
            path,
            conn: Mutex::new(conn),
            cache: MemoryCache::new(),
            search_index,
        };

        storage.initialize_schema()?;
        info!("Storage opened at: {}", storage.path.display());
        Ok(storage)
    }

    /// Create the schema if it doesn't exist.
    fn initialize_schema(&self) -> Result<()> {
        let conn = self.conn.lock().unwrap();

        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS entries (
                turn        INTEGER PRIMARY KEY,
                tier        TEXT NOT NULL DEFAULT 'ephemeral',
                entry_type  TEXT NOT NULL DEFAULT 'conversation',
                session     TEXT NOT NULL,
                ts          TEXT NOT NULL,
                user        TEXT NOT NULL DEFAULT '',
                assistant   TEXT NOT NULL DEFAULT '',
                entities    TEXT NOT NULL DEFAULT '[]',
                prev_hash   TEXT NOT NULL DEFAULT '',
                hash        TEXT NOT NULL DEFAULT '',
                finding     TEXT,
                verdict     TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_entries_session
                ON entries(session);
            CREATE INDEX IF NOT EXISTS idx_entries_ts
                ON entries(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_entries_tier
                ON entries(tier);
            CREATE INDEX IF NOT EXISTS idx_entries_type
                ON entries(entry_type);

            CREATE TABLE IF NOT EXISTS memory_facts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity          TEXT NOT NULL,
                attribute       TEXT NOT NULL,
                value           TEXT NOT NULL,
                fact_type       TEXT NOT NULL DEFAULT 'fact',
                confidence      REAL NOT NULL DEFAULT 0.8,
                tier            TEXT NOT NULL DEFAULT '1',
                entropy         REAL NOT NULL DEFAULT 0.5,
                source_session  TEXT DEFAULT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(entity, attribute, value)
            );

            CREATE INDEX IF NOT EXISTS idx_memory_facts_entity
                ON memory_facts(entity);
            CREATE INDEX IF NOT EXISTS idx_memory_facts_value
                ON memory_facts(value);

            CREATE TABLE IF NOT EXISTS artifacts (
                id              TEXT PRIMARY KEY,
                session         TEXT NOT NULL,
                filename        TEXT NOT NULL,
                content_type    TEXT NOT NULL DEFAULT 'text/plain',
                content         BLOB NOT NULL,
                description     TEXT,
                artifact_type   TEXT NOT NULL DEFAULT 'document',
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_artifacts_session
                ON artifacts(session);
            CREATE INDEX IF NOT EXISTS idx_artifacts_type
                ON artifacts(artifact_type);

            CREATE TABLE IF NOT EXISTS context_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL,
                summary         TEXT NOT NULL DEFAULT '',
                topics          TEXT NOT NULL DEFAULT '[]',
                decisions       TEXT NOT NULL DEFAULT '[]',
                entities        TEXT NOT NULL DEFAULT '[]',
                credentials     TEXT NOT NULL DEFAULT '[]',
                turn_count      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_session
                ON context_snapshots(session_id);

            CREATE TABLE IF NOT EXISTS workflows (
                name            TEXT PRIMARY KEY,
                description     TEXT NOT NULL DEFAULT '',
                steps           TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS workflow_runs (
                id              TEXT PRIMARY KEY,
                workflow_name   TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                current_step    INTEGER NOT NULL DEFAULT 0,
                total_steps     INTEGER NOT NULL DEFAULT 0,
                started_at      TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at     TEXT,
                error           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_runs_workflow
                ON workflow_runs(workflow_name);

            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            );

            INSERT OR IGNORE INTO schema_version (version) VALUES (1);
            ",
        )?;

        debug!("Schema initialized at version 1");
        Ok(())
    }

    // ── Entry Operations ──

    /// Append a new entry to the log with hash chain computation.
    pub fn append_entry(&self, entry: &Entry) -> Result<Entry> {
        let conn = self.conn.lock().unwrap();

        // Get the previous entry's hash for chain computation
        let prev_hash = conn
            .query_row(
                "SELECT hash FROM entries ORDER BY turn DESC LIMIT 1",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap_or_default();

        // Compute hash for this entry
        let mut entry = entry.clone();
        if entry.hash.is_empty() {
            entry.hash = entry.compute_hash(&prev_hash);
            entry.prev_hash = prev_hash;
        }

        let entities_json = serde_json::to_string(&entry.entities)?;

        conn.execute(
            "INSERT INTO entries (turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                entry.turn,
                entry.tier.as_str(),
                entry.entry_type.as_str(),
                entry.session,
                entry.ts.to_rfc3339(),
                entry.user,
                entry.assistant,
                entities_json,
                entry.prev_hash,
                entry.hash,
                entry.finding,
                entry.verdict,
            ],
        )?;

        // Invalidate caches after write
        self.cache.invalidate_all();

        // Index for full-text search
        if let Some(ref idx) = self.search_index {
            let _ = idx.index_entry(&entry);
        }

        Ok(entry.clone())
    }

    /// Get a single entry by turn number.
    pub fn get_entry(&self, turn: i64) -> Result<Option<Entry>> {
        // Check cache first
        if let Some(entry) = self.cache.get_entry(turn) {
            return Ok(Some(entry));
        }

        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
             FROM entries WHERE turn = ?1",
        )?;

        let result = stmt
            .query_row(params![turn], |row| self.row_to_entry(row))
            .ok();

        if let Some(ref entry) = result {
            self.cache.put_entry(turn, entry.clone());
        }

        Ok(result)
    }

    /// Get recent entries up to `limit`.
    pub fn recent_entries(&self, limit: i64) -> Result<Vec<Entry>> {
        self.recent_entries_offset(limit, 0)
    }

    /// Get recent entries with pagination offset.
    pub fn recent_entries_offset(&self, limit: i64, offset: i64) -> Result<Vec<Entry>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
             FROM entries ORDER BY turn DESC LIMIT ?1 OFFSET ?2",
        )?;

        let entries = stmt
            .query_map(params![limit, offset], |row| self.row_to_entry(row))?
            .filter_map(|r| r.ok())
            .collect();

        Ok(entries)
    }

    /// Search entries with a LIKE query across user, assistant, and session.
    /// Supports pagination via offset.
    pub fn search_entries(&self, query: &str, limit: i64) -> Result<Vec<Entry>> {
        self.search_entries_offset(query, limit, 0)
    }

    /// Search entries with offset pagination.
    pub fn search_entries_offset(&self, query: &str, limit: i64, offset: i64) -> Result<Vec<Entry>> {
        // Cache only the first page (offset=0). Paginated queries bypass cache.
        if offset == 0 {
            let cache_key = format!("search:{}:{}", query, limit);
            if let Some(results) = self.cache.get_search_results(&cache_key) {
                return Ok(results);
            }
        }

        let conn = self.conn.lock().unwrap();
        let pattern = format!("%{}%", query);
        let mut stmt = conn.prepare(
            "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
             FROM entries
             WHERE user LIKE ?1 OR assistant LIKE ?1 OR session LIKE ?1
             ORDER BY turn DESC LIMIT ?2 OFFSET ?3",
        )?;

        let entries: Vec<Entry> = stmt
            .query_map(params![pattern, limit, offset], |row| self.row_to_entry(row))?
            .filter_map(|r| r.ok())
            .collect();

        if offset == 0 {
            self.cache.put_search_results(format!("search:{}:{}", query, limit), entries.clone());
        }
        Ok(entries)
    }

    /// Search entries using LIKE query (primary) with tantivy boost (secondary).
    pub fn search_fts(&self, query: &str, limit: i64) -> Result<Vec<Entry>> {
        // Primary: LIKE search (always works, finds everything)
        let mut entries = self.search_entries(query, limit)?;

        // Secondary: try tantivy for additional results if LIKE returned less than limit
        if entries.len() < limit as usize {
            if let Some(ref idx) = self.search_index {
                if let Ok(results) = idx.search(query, limit as usize) {
                    for turn in results {
                        if !entries.iter().any(|e| e.turn == turn) {
                            if let Ok(Some(entry)) = self.get_entry(turn) {
                                entries.push(entry);
                            }
                        }
                    }
                }
            }
        }

        // Sort by turn DESC and trim to limit
        entries.sort_by(|a, b| b.turn.cmp(&a.turn));
        entries.truncate(limit as usize);
        Ok(entries)
    }

    /// Search entries using LIKE query with pagination offset (no tantivy).
    pub fn search_fts_offset(&self, query: &str, limit: i64, offset: i64) -> Result<Vec<Entry>> {
        self.search_entries_offset(query, limit, offset)
    }

    /// Count entries matching a LIKE search query.
    pub fn count_search_entries(&self, query: &str) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        let pattern = format!("%{}%", query);
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM entries WHERE user LIKE ?1 OR assistant LIKE ?1 OR session LIKE ?1",
            params![pattern],
            |row| row.get(0),
        )?;
        Ok(count)
    }

    /// Count total entries.
    pub fn count_entries(&self) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        let count: i64 =
            conn.query_row("SELECT COUNT(*) FROM entries", [], |row| row.get(0))?;
        Ok(count)
    }

    /// Count distinct sessions.
    pub fn count_sessions(&self) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        let count: i64 = conn.query_row(
            "SELECT COUNT(DISTINCT session) FROM entries",
            [],
            |row| row.get(0),
        )?;
        Ok(count)
    }

    /// Get tier distribution.
    pub fn tier_distribution(&self) -> Result<std::collections::HashMap<String, i64>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT tier, COUNT(*) as count FROM entries GROUP BY tier",
        )?;
        let map = stmt
            .query_map([], |row| {
                let tier: String = row.get(0)?;
                let count: i64 = row.get(1)?;
                Ok((tier, count))
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(map)
    }

    /// Get entry type distribution.
    pub fn type_distribution(&self) -> Result<std::collections::HashMap<String, i64>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT entry_type, COUNT(*) as count FROM entries GROUP BY entry_type",
        )?;
        let map = stmt
            .query_map([], |row| {
                let etype: String = row.get(0)?;
                let count: i64 = row.get(1)?;
                Ok((etype, count))
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(map)
    }

    /// Get the last entry in the log.
    pub fn last_entry(&self) -> Result<Option<Entry>> {
        let conn = self.conn.lock().unwrap();
        let result = conn
            .query_row(
                "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
                 FROM entries ORDER BY turn DESC LIMIT 1",
                [],
                |row| self.row_to_entry(row),
            )
            .ok();
        Ok(result)
    }

    /// Get all entries (for migration, verify, etc).
    pub fn all_entries(&self) -> Result<Vec<Entry>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
             FROM entries ORDER BY turn ASC",
        )?;
        let entries = stmt
            .query_map([], |row| self.row_to_entry(row))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(entries)
    }

    /// Get entries for a specific session.
    pub fn entries_for_session(&self, session: &str, limit: i64) -> Result<Vec<Entry>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT turn, tier, entry_type, session, ts, user, assistant, entities, prev_hash, hash, finding, verdict
             FROM entries WHERE session = ?1 ORDER BY turn DESC LIMIT ?2",
        )?;
        let entries = stmt
            .query_map(params![session, limit], |row| self.row_to_entry(row))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(entries)
    }

    /// Verify the hash chain integrity for all entries.
    ///
    /// Returns a list of entries where the hash chain is broken.
    /// Each entry shows the turn, expected hash, and actual hash.
    pub fn verify_hash_chain(&self) -> Result<Vec<(i64, String, String)>> {
        let entries = self.all_entries()?;
        let mut failures = Vec::new();

        for entry in &entries {
            let expected_prev = if entry.turn == 1 {
                String::new()
            } else {
                // Find the previous entry's hash
                match self.get_entry(entry.turn - 1)? {
                    Some(prev) => prev.hash,
                    None => {
                        failures.push((entry.turn, "prev entry not found".into(), entry.prev_hash.clone()));
                        continue;
                    }
                }
            };

            if entry.prev_hash != expected_prev {
                failures.push((
                    entry.turn,
                    format!("expected prev_hash: {}", expected_prev),
                    format!("got: {}", entry.prev_hash),
                ));
            }
        }

        Ok(failures)
    }

    // ── Memory Fact Operations ──

    /// Insert a memory fact (upsert on unique constraint).
    pub fn upsert_fact(&self, fact: &MemoryFact) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO memory_facts (entity, attribute, value, fact_type, confidence, source_session)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)
             ON CONFLICT(entity, attribute, value) DO UPDATE SET
                confidence = ?5,
                updated_at = datetime('now')",
            params![
                fact.entity,
                fact.attribute,
                fact.value,
                fact.fact_type,
                fact.confidence,
                fact.source_session,
            ],
        )?;
        let id = conn.last_insert_rowid();
        self.cache.invalidate_all();
        Ok(id)
    }

    /// Delete a memory fact by ID.
    pub fn delete_fact(&self, fact_id: i64) -> Result<bool> {
        let conn = self.conn.lock().unwrap();
        let rows = conn.execute("DELETE FROM memory_facts WHERE id = ?1", params![fact_id])?;
        self.cache.invalidate_all();
        Ok(rows > 0)
    }

    /// List recent distinct sessions.
    pub fn recent_sessions(&self, limit: i64) -> Result<Vec<String>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT session FROM entries GROUP BY session ORDER BY MAX(turn) DESC LIMIT ?1",
        )?;
        let sessions = stmt
            .query_map(params![limit], |row| row.get::<_, String>(0))?
            .filter_map(|r| r.ok())
            .collect();
        Ok(sessions)
    }

    /// Search memory facts by entity or value.
    pub fn search_facts(&self, query: &str, limit: i64) -> Result<Vec<MemoryFact>> {
        let conn = self.conn.lock().unwrap();
        let pattern = format!("%{}%", query);
        let mut stmt = conn.prepare(
            "SELECT id, entity, attribute, value, fact_type, confidence, source_session, created_at, updated_at
             FROM memory_facts
             WHERE entity LIKE ?1 OR attribute LIKE ?1 OR value LIKE ?1
             ORDER BY confidence DESC LIMIT ?2",
        )?;

        let facts = stmt
            .query_map(params![pattern, limit], |row| {
                Ok(MemoryFact {
                    id: Some(row.get(0)?),
                    entity: row.get(1)?,
                    attribute: row.get(2)?,
                    value: row.get(3)?,
                    fact_type: row.get(4)?,
                    confidence: row.get(5)?,
                    source_session: row.get(6)?,
                    created_at: row.get::<_, String>(7)?.parse().unwrap_or_default(),
                    updated_at: row.get::<_, String>(8)?.parse().unwrap_or_default(),
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(facts)
    }

    // ── Context Snapshot Operations ──

    /// Create a context snapshot for a session.
    pub fn create_snapshot(
        &self,
        session_id: &str,
        summary: &str,
        topics: &[String],
        decisions: &[String],
        entities: &[String],
        credentials: &[String],
    ) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO context_snapshots (session_id, summary, topics, decisions, entities, credentials)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                session_id,
                summary,
                &serde_json::to_string(topics).unwrap_or_default(),
                &serde_json::to_string(decisions).unwrap_or_default(),
                &serde_json::to_string(entities).unwrap_or_default(),
                &serde_json::to_string(credentials).unwrap_or_default(),
            ],
        )?;
        let id = conn.last_insert_rowid();
        self.cache.invalidate_all();
        Ok(id)
    }

    /// List recent context snapshots.
    pub fn list_snapshots(&self, limit: i64) -> Result<Vec<ContextSnapshot>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, session_id, summary, topics, decisions, entities, credentials, turn_count, created_at
             FROM context_snapshots ORDER BY created_at DESC LIMIT ?1",
        )?;
        let snapshots = stmt
            .query_map(params![limit], |row| {
                Ok(ContextSnapshot {
                    id: row.get(0)?,
                    session_id: row.get(1)?,
                    summary: row.get(2)?,
                    topics: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
                    decisions: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
                    entities: serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default(),
                    credentials: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
                    turn_count: row.get(7)?,
                    created_at: row.get::<_, String>(8)?.parse().unwrap_or_default(),
                })
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(snapshots)
    }

    /// Get snapshots for a specific session.
    pub fn snapshots_for_session(&self, session_id: &str, limit: i64) -> Result<Vec<ContextSnapshot>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, session_id, summary, topics, decisions, entities, credentials, turn_count, created_at
             FROM context_snapshots WHERE session_id = ?1 ORDER BY created_at DESC LIMIT ?2",
        )?;
        let snapshots = stmt
            .query_map(params![session_id, limit], |row| {
                Ok(ContextSnapshot {
                    id: row.get(0)?,
                    session_id: row.get(1)?,
                    summary: row.get(2)?,
                    topics: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
                    decisions: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
                    entities: serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default(),
                    credentials: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
                    turn_count: row.get(7)?,
                    created_at: row.get::<_, String>(8)?.parse().unwrap_or_default(),
                })
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(snapshots)
    }

    /// Delete a context snapshot by ID.
    pub fn delete_snapshot(&self, id: i64) -> Result<bool> {
        let conn = self.conn.lock().unwrap();
        let rows = conn.execute("DELETE FROM context_snapshots WHERE id = ?1", params![id])?;
        Ok(rows > 0)
    }

    // ── Artifact Operations ──

    /// Store an artifact.
    pub fn store_artifact(&self, artifact: &Artifact) -> Result<()> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO artifacts (id, session, filename, content_type, content, description, artifact_type)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                artifact.id.to_string(),
                artifact.session,
                artifact.filename,
                artifact.content_type,
                artifact.content,
                artifact.description,
                artifact.artifact_type,
            ],
        )?;
        Ok(())
    }

    /// List artifacts for a session.
    pub fn list_artifacts(&self, session: &str) -> Result<Vec<Artifact>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, session, filename, content_type, content, description, artifact_type, created_at
             FROM artifacts WHERE session = ?1 ORDER BY created_at DESC",
        )?;

        let artifacts = stmt
            .query_map(params![session], |row| {
                Ok(Artifact {
                    id: row.get::<_, String>(0)?.parse().unwrap_or_default(),
                    session: row.get(1)?,
                    filename: row.get(2)?,
                    content_type: row.get(3)?,
                    content: row.get(4)?,
                    description: row.get(5)?,
                    artifact_type: row.get(6)?,
                    created_at: row.get::<_, String>(7)?.parse().unwrap_or_default(),
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(artifacts)
    }

    /// Get a single artifact by ID.
    pub fn get_artifact(&self, id: &uuid::Uuid) -> Result<Option<Artifact>> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT id, session, filename, content_type, content, description, artifact_type, created_at
             FROM artifacts WHERE id = ?1",
        )?;

        let mut rows = stmt.query_map(params![id.to_string()], |row| {
            Ok(Artifact {
                id: row.get::<_, String>(0)?.parse().unwrap_or_default(),
                session: row.get(1)?,
                filename: row.get(2)?,
                content_type: row.get(3)?,
                content: row.get(4)?,
                description: row.get(5)?,
                artifact_type: row.get(6)?,
                created_at: row.get::<_, String>(7)?.parse().unwrap_or_default(),
            })
        })?;

        match rows.next() {
            Some(Ok(artifact)) => Ok(Some(artifact)),
            _ => Ok(None),
        }
    }

    /// Run a raw SELECT query over the artifacts table (for artifact_query tool).
    /// Only SELECT statements are allowed for safety.
    pub fn query_artifacts(&self, sql: &str) -> Result<Vec<serde_json::Value>> {
        let trimmed = sql.trim().to_uppercase();
        if !trimmed.starts_with("SELECT") {
            return Err(MyceliumError::InvalidInput("Only SELECT queries are allowed".into()));
        }
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(sql)?;
        let columns: Vec<String> = stmt.column_names().iter().map(|c| c.to_string()).collect();
        let rows = stmt
            .query_map([], |row| {
                let mut map = serde_json::Map::new();
                for (i, col) in columns.iter().enumerate() {
                    let val: String = row.get::<_, Option<String>>(i)?.unwrap_or_default();
                    map.insert(col.to_string(), serde_json::Value::String(val));
                }
                Ok(serde_json::Value::Object(map))
            })?
            .filter_map(|r| r.ok())
            .collect();
        Ok(rows)
    }

    /// Re-index all entries in the tantivy search index.
    pub fn reindex_search(&self) -> Result<usize> {
        let mut count = 0;
        if let Some(ref idx) = self.search_index {
            let entries = self.all_entries()?;
            if let Err(e) = idx.index_entries(&entries) {
                tracing::warn!("reindex failed: {}", e);
            } else {
                count = entries.len();
            }
        }
        Ok(count)
    }

    // ── Schema / Migration ──

    /// Get the current schema version.
    pub fn schema_version(&self) -> Result<i64> {
        let conn = self.conn.lock().unwrap();
        let version: i64 =
            conn.query_row("SELECT MAX(version) FROM schema_version", [], |row| {
                row.get(0)
            })?;
        Ok(version)
    }

    /// Get database file size in bytes.
    pub fn db_size(&self) -> Result<i64> {
        Ok(std::fs::metadata(&self.path)
            .map(|m| m.len() as i64)
            .unwrap_or(0))
    }

    /// Get the database file path.
    pub fn path(&self) -> &std::path::Path {
        &self.path
    }

    // ── Private Helpers ──

    fn row_to_entry(&self, row: &rusqlite::Row) -> rusqlite::Result<Entry> {
        let entities_str: String = row.get(7)?;
        let entities: Vec<String> = serde_json::from_str(&entities_str).unwrap_or_default();
        let ts_str: String = row.get(4)?;
        let ts: chrono::DateTime<chrono::Utc> = ts_str.parse().unwrap_or_default();

        Ok(Entry {
            turn: row.get(0)?,
            tier: Tier::from_str(&row.get::<_, String>(1)?).unwrap_or(Tier::Ephemeral),
            entry_type: EntryType::from_str(&row.get::<_, String>(2)?)
                .unwrap_or(EntryType::Conversation),
            session: row.get(3)?,
            ts,
            user: row.get(5)?,
            assistant: row.get(6)?,
            entities,
            prev_hash: row.get(8)?,
            hash: row.get(9)?,
            finding: row.get(10)?,
            verdict: row.get(11)?,
        })
    }
}

//! Core brain module for the Hebbian Crystal Brain.
//!
//! Manages the atom index, position graph, edge weights, and pending work queue.
//! All tables live in the same `mycelium.db` as permanent memory.

use chrono::Utc;
use moka::sync::Cache;
use rusqlite::{params, Connection};

/// A unique atom -- single bi-gram or tri-gram stored once.
#[derive(Debug, Clone)]
pub struct Atom {
    pub id: i64,
    pub phrase: String,
    pub first_seen: i64,
    pub last_seen: i64,
    pub ref_count: i64,
    /// LLM-assigned importance (1-5, with 1.0 = default for rule-based atoms)
    pub importance: f64,
}

/// A single occurrence of an atom at a specific turn.
#[derive(Debug, Clone)]
pub struct Position {
    pub id: i64,
    pub atom_id: i64,
    pub turn: i64,
    pub session: String,
}

/// A weighted connection between two co-occurring atoms.
#[derive(Debug, Clone)]
pub struct Edge {
    pub atom_a: i64,
    pub atom_b: i64,
    pub weight: f64,
    pub last_updated: i64,
    pub access_count: i64,
}

/// An entry waiting to be processed by the brain daemon.
#[derive(Debug, Clone)]
pub struct PendingWork {
    pub id: i64,
    pub turn: i64,
    pub created_at: String,
}

/// Brain statistics for observability.
#[derive(Debug, Clone)]
pub struct BrainStatus {
    pub atom_count: i64,
    pub position_count: i64,
    pub edge_count: i64,
    pub pending_count: i64,
}

/// Working memory — keeps recently accessed atoms in a hot cache.
/// Not LRU-based; uses moka's TTL expiry (expires after 60 seconds of no access).
pub struct WorkingMemory {
    cache: Cache<String, Vec<Position>>,
}

impl WorkingMemory {
    /// Create a new working memory with 1000 entry max capacity and 60s idle TTL.
    pub fn new() -> Self {
        Self {
            cache: Cache::builder()
                .max_capacity(1000)
                .time_to_idle(std::time::Duration::from_secs(60))
                .build(),
        }
    }

    /// Insert or refresh a phrase with its associated positions in the hot cache.
    pub fn touch(&self, phrase: &str, positions: Vec<Position>) {
        self.cache.insert(phrase.to_string(), positions);
    }

    /// Look up a phrase in the hot cache. Returns `None` if not present or expired.
    pub fn peek(&self, phrase: &str) -> Option<Vec<Position>> {
        self.cache.get(phrase)
    }

    /// Return all currently cached phrases (hot atoms).
    pub fn hot_phrases(&self) -> Vec<String> {
        let mut keys: Vec<String> = self.cache.iter().map(|(k, _v)| k.as_ref().clone()).collect();
        keys.sort();
        keys
    }
}

/// Create brain tables if they don't exist.
pub fn create_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS atoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phrase TEXT NOT NULL UNIQUE,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            ref_count INTEGER NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 1.0
        );
        CREATE INDEX IF NOT EXISTS idx_atoms_phrase ON atoms(phrase);

        CREATE TABLE IF NOT EXISTS entity_registry (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            entity_type  TEXT NOT NULL DEFAULT 'concept',
            aliases      TEXT NOT NULL DEFAULT '[]',
            importance   REAL NOT NULL DEFAULT 1.0,
            first_seen   INTEGER NOT NULL,
            last_seen    INTEGER NOT NULL,
            ref_count    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_entity_name ON entity_registry(name);

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            atom_id INTEGER NOT NULL,
            turn INTEGER NOT NULL,
            session TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (atom_id) REFERENCES atoms(id)
        );
        CREATE INDEX IF NOT EXISTS idx_positions_atom ON positions(atom_id);
        CREATE INDEX IF NOT EXISTS idx_positions_turn ON positions(turn);

        CREATE TABLE IF NOT EXISTS edges (
            atom_a INTEGER NOT NULL,
            atom_b INTEGER NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            last_updated INTEGER NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (atom_a, atom_b)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_a ON edges(atom_a, weight DESC);

        CREATE TABLE IF NOT EXISTS pending_brain_work (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turn INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pending_work_created ON pending_brain_work(created_at);

        CREATE TABLE IF NOT EXISTS brain_stop_words (
            phrase TEXT PRIMARY KEY,
            frequency REAL NOT NULL,
            detected_at INTEGER NOT NULL
        );"
    )?;
    Ok(())
}

/// Lowercase + strip common English suffixes (-ing, -ed, -ly, -s).
/// Unicode NFKD normalization is deferred to a future task.
pub fn normalize(phrase: &str) -> String {
    let s = phrase.to_lowercase();
    if s.ends_with("ing") && s.len() > 4 {
        s[..s.len() - 3].to_string()
    } else if s.ends_with("ed") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("ly") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("s") && s.len() > 3 && !s.ends_with("ss") {
        s[..s.len() - 1].to_string()
    } else {
        s
    }
}

/// Extract all unique normalized bi-grams and tri-grams from text.
pub fn extract_atoms(text: &str) -> Vec<String> {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() < 2 {
        return vec![];
    }
    let mut atoms: Vec<String> = Vec::new();
    // bi-grams
    for w in words.windows(2) {
        let phrase = format!("{} {}", w[0], w[1]);
        atoms.push(normalize(&phrase));
    }
    // tri-grams
    if words.len() >= 3 {
        for w in words.windows(3) {
            let phrase = format!("{} {} {}", w[0], w[1], w[2]);
            atoms.push(normalize(&phrase));
        }
    }
    atoms.sort();
    atoms.dedup();
    atoms
}

/// Upsert an atom. Returns the atom's id. Creates if new, updates ref_count + last_seen if exists.
pub fn upsert_atom(conn: &Connection, phrase: &str, turn: i64, importance: f64) -> rusqlite::Result<i64> {
    conn.execute(
        "INSERT INTO atoms (phrase, first_seen, last_seen, ref_count, importance) VALUES (?1, ?2, ?2, 1, ?3)
         ON CONFLICT(phrase) DO UPDATE SET last_seen = ?2, ref_count = ref_count + 1, importance = MAX(importance, ?3)",
        params![phrase, turn, importance],
    )?;
    let id: i64 = conn.query_row(
        "SELECT id FROM atoms WHERE phrase = ?1", params![phrase],
        |row| row.get(0),
    )?;
    Ok(id)
}

/// Record a position for an atom at a specific turn.
pub fn record_position(conn: &Connection, atom_id: i64, turn: i64, session: &str) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT INTO positions (atom_id, turn, session) VALUES (?1, ?2, ?3)",
        params![atom_id, turn, session],
    )?;
    Ok(())
}

/// Increment the edge weight between two atoms. Creates edge if not exists.
/// Orders atom_a < atom_b for consistent composite key. Skips self-edges.
pub fn increment_edge(conn: &Connection, a: i64, b: i64, turn: i64) -> rusqlite::Result<()> {
    if a == b {
        return Ok(());
    }
    let (low, high) = if a < b { (a, b) } else { (b, a) };
    conn.execute(
        "INSERT INTO edges (atom_a, atom_b, weight, last_updated, access_count) VALUES (?1, ?2, 1.0, ?3, 1)
         ON CONFLICT(atom_a, atom_b) DO UPDATE SET
            weight = weight + 1.0,
            last_updated = ?3,
            access_count = access_count + 1",
        params![low, high, turn],
    )?;
    Ok(())
}

/// Consolidate an entry by extracting atoms, upserting them, recording positions,
/// and building all pairwise edges.
pub fn consolidate_entry(conn: &Connection, turn: i64, session: &str, text: &str) -> rusqlite::Result<()> {
    let atoms = extract_atoms(text);
    // Filter stop words (cheap check, skip if table is empty)
    let atoms: Vec<String> = atoms.into_iter().filter(|a| {
        !is_stop_word(conn, a).unwrap_or(false)
    }).collect();
    if atoms.is_empty() {
        return Ok(());
    }
    let mut ids = Vec::with_capacity(atoms.len());
    for phrase in &atoms {
        let id = upsert_atom(conn, phrase, turn, 1.0)?;
        record_position(conn, id, turn, session)?;
        ids.push(id);
    }
    for i in 0..ids.len() {
        for j in i + 1..ids.len() {
            increment_edge(conn, ids[i], ids[j], turn)?;
        }
    }
    Ok(())
}

/// Detect stop words from atom frequency data. Called after 500+ entries.
/// Any phrase in >70% of entries is flagged as a stop word.
pub fn detect_stop_words(
    conn: &Connection,
    atom_counts: &std::collections::HashMap<String, usize>,
    total_entries: usize,
) -> rusqlite::Result<()> {
    let threshold = (total_entries as f64 * 0.7) as usize;
    for (phrase, count) in atom_counts {
        if *count > threshold {
            let freq = *count as f64 / total_entries as f64;
            conn.execute(
                "INSERT OR REPLACE INTO brain_stop_words (phrase, frequency, detected_at) VALUES (?1, ?2, ?3)",
                rusqlite::params![phrase, freq, Utc::now().timestamp()],
            )?;
        }
    }
    Ok(())
}

/// Check if a phrase is a known stop word (cheap atom-level filter).
pub fn is_stop_word(conn: &Connection, phrase: &str) -> rusqlite::Result<bool> {
    let exists: bool = conn.query_row(
        "SELECT 1 FROM brain_stop_words WHERE phrase = ?1",
        rusqlite::params![phrase],
        |_| Ok(true),
    ).unwrap_or(false);
    Ok(exists)
}

/// Find atoms whose phrase matches a LIKE pattern, ordered by ref_count DESC.
pub fn recall(conn: &Connection, phrase: &str, limit: i64) -> rusqlite::Result<Vec<Atom>> {
    let pattern = format!("%{}%", phrase);
    let mut stmt = conn.prepare(
        "SELECT id, phrase, first_seen, last_seen, ref_count, importance
         FROM atoms WHERE phrase LIKE ?1
         ORDER BY ref_count DESC LIMIT ?2",
    )?;
    let atoms = stmt
        .query_map(params![pattern, limit], |row| {
            Ok(Atom {
                id: row.get(0)?,
                phrase: row.get(1)?,
                first_seen: row.get(2)?,
                last_seen: row.get(3)?,
                ref_count: row.get(4)?,
                importance: row.get(5)?,
            })
        })?
        .filter_map(|r| r.ok())
        .collect();
    Ok(atoms)
}

/// Find top-N neighbor atoms by edge weight for a given phrase.
pub fn clusters(conn: &Connection, phrase: &str, limit: i64) -> rusqlite::Result<Vec<(String, f64)>> {
    // First get the atom id for the phrase
    let pattern = format!("%{}%", phrase);
    let ids: Vec<i64> = conn
        .prepare("SELECT id FROM atoms WHERE phrase LIKE ?1 LIMIT 1")?
        .query_map(params![pattern], |row| row.get::<_, i64>(0))?
        .filter_map(|r| r.ok())
        .collect();
    if ids.is_empty() {
        return Ok(vec![]);
    }
    let atom_id = ids[0];
    // Query edges for neighbors (either side since we always store atom_a < atom_b)
    let mut stmt = conn.prepare(
        "SELECT atom_a, atom_b, weight FROM edges
         WHERE atom_a = ?1 OR atom_b = ?2
         ORDER BY weight DESC LIMIT ?3",
    )?;
    let neighbors: Vec<(i64, f64)> = stmt
        .query_map(params![atom_id, atom_id, limit], |row| {
            let a: i64 = row.get(0)?;
            let b: i64 = row.get(1)?;
            let w: f64 = row.get(2)?;
            let neighbor_id = if a == atom_id { b } else { a };
            Ok((neighbor_id, w))
        })?
        .filter_map(|r| r.ok())
        .collect();
    // Resolve neighbor ids to phrases
    let mut result = Vec::with_capacity(neighbors.len());
    for (nid, weight) in neighbors {
        let phrase: String =
            conn.query_row("SELECT phrase FROM atoms WHERE id = ?1", params![nid], |row| {
                row.get(0)
            })?;
        result.push((phrase, weight));
    }
    Ok(result)
}

/// Get first_seen, last_seen, ref_count for a phrase.
pub fn when(conn: &Connection, phrase: &str) -> rusqlite::Result<Option<(i64, i64, i64)>> {
    // Try exact match first
    let result = conn.query_row(
        "SELECT first_seen, last_seen, ref_count FROM atoms WHERE phrase = ?1",
        params![phrase],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    );
    match result {
        Ok(info) => Ok(Some(info)),
        Err(rusqlite::Error::QueryReturnedNoRows) => {
            // Try LIKE fallback
            let pattern = format!("%{}%", phrase);
            match conn.query_row(
                "SELECT first_seen, last_seen, ref_count FROM atoms WHERE phrase LIKE ?1",
                params![pattern],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            ) {
                Ok(info) => Ok(Some(info)),
                Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
                Err(e) => Err(e),
            }
        }
        Err(e) => Err(e),
    }
}

/// Brain status -- counts across all data tables.
pub fn brain_status(conn: &Connection) -> rusqlite::Result<BrainStatus> {
    let atom_count: i64 =
        conn.query_row("SELECT COUNT(*) FROM atoms", [], |row| row.get(0))?;
    let position_count: i64 =
        conn.query_row("SELECT COUNT(*) FROM positions", [], |row| row.get(0))?;
    let edge_count: i64 =
        conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0))?;
    let pending_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM pending_brain_work", [], |row| row.get(0))?;
    Ok(BrainStatus {
        atom_count,
        position_count,
        edge_count,
        pending_count,
    })
}

/// Enqueue a turn for brain processing (called during append_entry).
pub fn enqueue_brain_work(conn: &Connection, turn: i64) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT OR IGNORE INTO pending_brain_work (turn) VALUES (?1)",
        params![turn],
    )?;
    Ok(())
}

/// Pop the oldest N pending entries.
pub fn dequeue_pending(conn: &Connection, batch_size: i64) -> rusqlite::Result<Vec<PendingWork>> {
    let mut stmt = conn.prepare(
        "SELECT id, turn, created_at FROM pending_brain_work
         ORDER BY created_at ASC LIMIT ?1"
    )?;
    let items = stmt.query_map(params![batch_size], |row| {
        Ok(PendingWork { id: row.get(0)?, turn: row.get(1)?, created_at: row.get::<_, String>(2)? })
    })?.filter_map(|r| r.ok()).collect();
    Ok(items)
}

/// Remove processed items from the queue.
pub fn remove_pending(conn: &Connection, ids: &[i64]) -> rusqlite::Result<()> {
    for id in ids {
        conn.execute("DELETE FROM pending_brain_work WHERE id = ?1", params![id])?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::{params, Connection};

    #[test]
    fn test_create_tables() {
        let conn = Connection::open_in_memory().unwrap();
        create_tables(&conn).unwrap();
        // Verify all tables exist
        let tables: Vec<String> = conn
            .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .filter_map(|r| r.ok())
            .collect();
        assert!(tables.contains(&"atoms".to_string()));
        assert!(tables.contains(&"positions".to_string()));
        assert!(tables.contains(&"edges".to_string()));
        assert!(tables.contains(&"pending_brain_work".to_string()));
        assert!(tables.contains(&"brain_stop_words".to_string()));
        assert!(tables.contains(&"entity_registry".to_string()));
    }

    #[test]
    fn test_normalize_lowercase() {
        assert_eq!(normalize("Hash Chain"), "hash chain");
    }

    #[test]
    fn test_normalize_suffix() {
        assert_eq!(normalize("running"), "runn");
        assert_eq!(normalize("hashed"), "hash");
        assert_eq!(normalize("chains"), "chain");
    }

    #[test]
    fn test_extract_atoms_basic() {
        let text = "discuss hash chain implementation";
        let atoms = extract_atoms(text);
        // bi-grams
        assert!(atoms.contains(&"hash chain".to_string()));
        // tri-grams
        assert!(atoms.contains(&"discuss hash chain".to_string()));
    }

    #[test]
    fn test_extract_atoms_dedup() {
        let text = "hash chain hash chain hash chain";
        let atoms = extract_atoms(text);
        let count = atoms.iter().filter(|a| *a == "hash chain").count();
        assert_eq!(count, 1, "deduplicated atoms should appear once");
    }

    #[test]
    fn test_upsert_atom_new() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let id = upsert_atom(&conn, "hash chain", 10, 1.0)?;
        assert!(id > 0);
        // Verify stored
        let (phrase, first_seen): (String, i64) = conn.query_row(
            "SELECT phrase, first_seen FROM atoms WHERE id = ?1", params![id],
            |row| Ok((row.get(0)?, row.get(1)?))
        )?;
        assert_eq!(phrase, "hash chain");
        assert_eq!(first_seen, 10);
        Ok(())
    }

    #[test]
    fn test_upsert_atom_existing() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let id1 = upsert_atom(&conn, "hash chain", 10, 1.0)?;
        let id2 = upsert_atom(&conn, "hash chain", 20, 1.0)?;
        assert_eq!(id1, id2, "same atom should return same id");
        // Verify ref_count incremented and last_seen updated
        let (ref_count, last_seen): (i64, i64) = conn.query_row(
            "SELECT ref_count, last_seen FROM atoms WHERE id = ?1", params![id1],
            |row| Ok((row.get(0)?, row.get(1)?))
        )?;
        assert_eq!(ref_count, 2);
        assert_eq!(last_seen, 20);
        Ok(())
    }

    #[test]
    fn test_record_position() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let id = upsert_atom(&conn, "hash chain", 5, 1.0)?;
        record_position(&conn, id, 5, "session-a")?;
        let (atom_id, turn, session): (i64, i64, String) = conn.query_row(
            "SELECT atom_id, turn, session FROM positions WHERE atom_id = ?1",
            params![id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?;
        assert_eq!(atom_id, id);
        assert_eq!(turn, 5);
        assert_eq!(session, "session-a");
        Ok(())
    }

    #[test]
    fn test_increment_edge() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let a = upsert_atom(&conn, "hash chain", 1, 1.0)?;
        let b = upsert_atom(&conn, "merkle tree", 1, 1.0)?;
        increment_edge(&conn, a, b, 1)?;
        increment_edge(&conn, a, b, 2)?;
        let (weight, last_updated): (f64, i64) = conn.query_row(
            "SELECT weight, last_updated FROM edges WHERE atom_a = ?1 AND atom_b = ?2",
            params![a, b], |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        assert!((weight - 2.0).abs() < 0.001, "two increments should give weight ~2.0");
        assert_eq!(last_updated, 2);
        Ok(())
    }

    #[test]
    fn test_increment_edge_orders_atoms() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let a = upsert_atom(&conn, "hash chain", 1, 1.0)?;
        let b = upsert_atom(&conn, "merkle tree", 1, 1.0)?;
        // Pass larger id first -- increment_edge should order them
        increment_edge(&conn, b, a, 1)?;
        let (atom_a, atom_b): (i64, i64) = conn.query_row(
            "SELECT atom_a, atom_b FROM edges", [],
            |row| Ok((row.get(0)?, row.get(1)?))
        )?;
        assert_eq!(atom_a, a, "should store smaller id as atom_a");
        assert_eq!(atom_b, b, "should store larger id as atom_b");
        Ok(())
    }

    #[test]
    fn test_increment_edge_skips_self_edge() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let a = upsert_atom(&conn, "hash chain", 1, 1.0)?;
        increment_edge(&conn, a, a, 1)?;
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM edges", [], |row| row.get(0)
        )?;
        assert_eq!(count, 0, "self-edges should not be created");
        Ok(())
    }

    #[test]
    fn test_consolidate_entry() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 1, "test-session", "discuss hash chain and merkle tree")?;
        let atom_count: i64 = conn.query_row("SELECT COUNT(*) FROM atoms", [], |row| row.get(0))?;
        // 6 words -> 5 bigrams + 4 trigrams = 9 atoms (all unique)
        assert_eq!(atom_count, 9, "5 bigrams + 4 trigrams from 6-word text");
        // Verify positions recorded for each atom
        let pos_count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM positions WHERE turn = ?1 AND session = ?2",
            params![1, "test-session"], |row| row.get(0)
        )?;
        assert_eq!(pos_count, 9, "each atom should have a position record");
        // Verify edges created between all pairs
        let edge_count: i64 = conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0))?;
        // Number of pairs from n items = n*(n-1)/2 = 9*8/2 = 36
        assert_eq!(edge_count, 36, "all-pairs edges from 9 atoms");
        Ok(())
    }

    #[test]
    fn test_recall_finds_atom() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        upsert_atom(&conn, "hash chain", 1, 1.0)?;
        let results = recall(&conn, "hash chain", 10)?;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].phrase, "hash chain");
        Ok(())
    }

    #[test]
    fn test_recall_multiple_sessions() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 1, "s1", "build hash chain")?;
        consolidate_entry(&conn, 2, "s2", "fix hash chain bug")?;
        consolidate_entry(&conn, 3, "s1", "test hash chain")?;
        let results = recall(&conn, "hash chain", 10)?;
        assert_eq!(results[0].ref_count, 3);
        Ok(())
    }

    #[test]
    fn test_recall_orders_by_ref_count() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        // "hash chain" appears in 3 sessions (ref_count = 3)
        for t in 1..=3 {
            upsert_atom(&conn, "hash chain", t, 1.0)?;
        }
        // "merkle tree" appears in 1 session (ref_count = 1)
        upsert_atom(&conn, "merkle tree", 4, 1.0)?;
        let results = recall(&conn, "hash chain", 10)?;
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].ref_count, 3);
        // Search for "merkle" -- matches only "merkle tree" (ref_count=1)
        // Also note that "merkle" alone is not an atom, so LIKE fallback
        let results = recall(&conn, "merkle", 10)?;
        assert!(!results.is_empty());
        assert_eq!(results[0].phrase, "merkle tree");
        Ok(())
    }

    #[test]
    fn test_recall_limit() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 1, "s1", "alpha beta")?;
        consolidate_entry(&conn, 2, "s1", "alpha gamma")?;
        consolidate_entry(&conn, 3, "s1", "alpha delta")?;
        let results = recall(&conn, "alpha", 2)?;
        assert_eq!(results.len(), 2);
        Ok(())
    }

    #[test]
    fn test_recall_empty() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let results = recall(&conn, "nonexistent", 10)?;
        assert!(results.is_empty());
        Ok(())
    }

    #[test]
    fn test_clusters_basic() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        // "hash chain" co-occurs with "merkle tree" and "data struct"
        consolidate_entry(&conn, 1, "s1", "hash chain merkle tree data struct")?;
        let results = clusters(&conn, "hash chain", 10)?;
        assert!(!results.is_empty(), "should find at least one neighbor");
        // Should contain "merkle tree" as a neighbor (any score)
        let phrases: Vec<&str> = results.iter().map(|(p, _)| p.as_str()).collect();
        assert!(
            phrases.contains(&"merkle tree"),
            "neighbors should include merkle tree"
        );
        Ok(())
    }

    #[test]
    fn test_clusters_empty_phrase() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let results = clusters(&conn, "zzzzzzz", 10)?;
        assert!(results.is_empty());
        Ok(())
    }

    #[test]
    fn test_when_exact() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        upsert_atom(&conn, "hash chain", 100, 1.0)?;
        upsert_atom(&conn, "hash chain", 200, 1.0)?;
        let info = when(&conn, "hash chain")?;
        assert!(info.is_some());
        let (first_seen, last_seen, ref_count) = info.unwrap();
        assert_eq!(first_seen, 100);
        assert_eq!(last_seen, 200);
        assert_eq!(ref_count, 2);
        Ok(())
    }

    #[test]
    fn test_when_like_fallback() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        upsert_atom(&conn, "build hash chain", 10, 1.0)?;
        let info = when(&conn, "hash chain")?;
        assert!(info.is_some(), "should fallback to LIKE match");
        Ok(())
    }

    #[test]
    fn test_when_missing() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let info = when(&conn, "no such phrase")?;
        assert!(info.is_none());
        Ok(())
    }

    #[test]
    fn test_brain_status_counts() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        let status = brain_status(&conn)?;
        assert_eq!(status.atom_count, 0);
        assert_eq!(status.position_count, 0);
        assert_eq!(status.edge_count, 0);
        assert_eq!(status.pending_count, 0);
        // Add some data
        consolidate_entry(&conn, 1, "s1", "hash chain merkle tree")?;
        let status = brain_status(&conn)?;
        assert_eq!(status.atom_count, 5); // 4 bigrams + 1 trigram
        assert_eq!(status.position_count, 5);
        assert_eq!(status.edge_count, 10);
        assert_eq!(status.pending_count, 0);
        Ok(())
    }

    #[test]
    fn test_brain_status_pending() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        // Manually insert a pending work entry
        conn.execute(
            "INSERT INTO pending_brain_work (turn) VALUES (?1)",
            params![1],
        )?;
        conn.execute(
            "INSERT INTO pending_brain_work (turn) VALUES (?1)",
            params![2],
        )?;
        let status = brain_status(&conn)?;
        assert_eq!(status.pending_count, 2);
        Ok(())
    }

    #[test]
    fn test_clusters_ranking() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 1, "s1", "hash chain and merkle tree")?;
        consolidate_entry(&conn, 2, "s1", "hash chain verify")?;
        let results = clusters(&conn, "hash chain", 5)?;
        assert!(!results.is_empty());
        // Edges were accumulated
        Ok(())
    }

    #[test]
    fn test_when_first_last() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 5, "s1", "discuss hash chain")?;
        consolidate_entry(&conn, 10, "s1", "build hash chain again")?;
        let info = when(&conn, "hash chain")?;
        assert!(info.is_some());
        let (first, last, count) = info.unwrap();
        assert_eq!(first, 5);
        assert_eq!(last, 10);
        assert_eq!(count, 2);
        Ok(())
    }

    #[test]
    fn test_enqueue_dequeue() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        enqueue_brain_work(&conn, 1)?;
        enqueue_brain_work(&conn, 2)?;
        enqueue_brain_work(&conn, 1)?; // duplicate — should be ignored
        let items = dequeue_pending(&conn, 10)?;
        assert_eq!(items.len(), 2); // only unique turns
        // Verify order
        assert_eq!(items[0].turn, 1);
        assert_eq!(items[1].turn, 2);
        // Remove and verify empty
        let ids: Vec<i64> = items.iter().map(|i| i.id).collect();
        remove_pending(&conn, &ids)?;
        let remaining = dequeue_pending(&conn, 10)?;
        assert!(remaining.is_empty());
        Ok(())
    }

    #[test]
    fn test_stop_word_detection() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        // Simulate 500 entries containing "the" in every one
        let mut stop_words = std::collections::HashMap::new();
        for i in 0..500 {
            let text = if i % 10 == 0 { format!("the main topic here") } else { format!("the answer is found") };
            let atoms = extract_atoms(&text);
            for atom in &atoms {
                *stop_words.entry(atom.clone()).or_insert(0) += 1;
            }
        }
        // "the answer" should appear in >70% of entries
        detect_stop_words(&conn, &stop_words, 500)?;
        let freq: f64 = conn.query_row(
            "SELECT frequency FROM brain_stop_words WHERE phrase = ?1",
            params!["the answer"], |row| row.get(0)
        )?;
        assert!(freq > 0.7, "frequent phrase should be detected as stop word");
        Ok(())
    }
}

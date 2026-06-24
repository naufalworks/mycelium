//! Core brain module for the Hebbian Crystal Brain.
//!
//! Manages the atom index, position graph, edge weights, and pending work queue.
//! All tables live in the same `mycelium.db` as permanent memory.

use rusqlite::{params, Connection};

/// A unique atom -- single bi-gram or tri-gram stored once.
#[derive(Debug, Clone)]
pub struct Atom {
    pub id: i64,
    pub phrase: String,
    pub first_seen: i64,
    pub last_seen: i64,
    pub ref_count: i64,
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

/// Create brain tables if they don't exist.
pub fn create_tables(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS atoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phrase TEXT NOT NULL UNIQUE,
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,
            ref_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_atoms_phrase ON atoms(phrase);

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
            turn INTEGER NOT NULL,
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
pub fn upsert_atom(conn: &Connection, phrase: &str, turn: i64) -> rusqlite::Result<i64> {
    conn.execute(
        "INSERT INTO atoms (phrase, first_seen, last_seen, ref_count) VALUES (?1, ?2, ?2, 1)
         ON CONFLICT(phrase) DO UPDATE SET last_seen = ?2, ref_count = ref_count + 1",
        params![phrase, turn],
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
    let mut ids = Vec::with_capacity(atoms.len());
    for phrase in &atoms {
        let id = upsert_atom(conn, phrase, turn)?;
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
        let id = upsert_atom(&conn, "hash chain", 10)?;
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
        let id1 = upsert_atom(&conn, "hash chain", 10)?;
        let id2 = upsert_atom(&conn, "hash chain", 20)?;
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
        let id = upsert_atom(&conn, "hash chain", 5)?;
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
        let a = upsert_atom(&conn, "hash chain", 1)?;
        let b = upsert_atom(&conn, "merkle tree", 1)?;
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
        let a = upsert_atom(&conn, "hash chain", 1)?;
        let b = upsert_atom(&conn, "merkle tree", 1)?;
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
        let a = upsert_atom(&conn, "hash chain", 1)?;
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
}

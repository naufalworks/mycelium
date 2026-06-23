//! Core brain module for the Hebbian Crystal Brain.
//!
//! Manages the atom index, position graph, edge weights, and pending work queue.
//! All tables live in the same `mycelium.db` as permanent memory.

use rusqlite::Connection;

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

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::Connection;

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
}

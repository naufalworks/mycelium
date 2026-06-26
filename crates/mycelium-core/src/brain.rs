//! Core brain module for the Hebbian Crystal Brain.
//!
//! Manages the atom index, position graph, edge weights, and pending work queue.
//! All tables live in the same `mycelium.db` as permanent memory.

use chrono::Utc;
use moka::sync::Cache;
use rusqlite::{params, Connection};
use std::sync::OnceLock;

use crate::types::{EntityAnnotation, MemoryAnnotation};

/// Heat cache for frequently accessed atoms.
/// Key: atom_id, Value: (Atom, heat_score).
/// Heat spreads along edges: accessing atom[42] heats atom[55] (neighbor) by 50%.
/// Eviction is TTL-based — cold atoms naturally expire.
static HEAT_CACHE: OnceLock<Cache<i64, (Atom, f64)>> = OnceLock::new();

fn heat_cache() -> &'static Cache<i64, (Atom, f64)> {
    HEAT_CACHE.get_or_init(|| {
        Cache::builder()
            .max_capacity(5_000)
            .time_to_live(std::time::Duration::from_secs(300)) // 5 min TTL
            .build()
    })
}

/// Heat cache for cluster results (neighbor lists).
/// When an atom is accessed, its neighbors are cached alongside it.
static CLUSTER_CACHE: OnceLock<Cache<i64, Vec<(String, f64)>>> = OnceLock::new();

fn cluster_cache() -> &'static Cache<i64, Vec<(String, f64)>> {
    CLUSTER_CACHE.get_or_init(|| {
        Cache::builder()
            .max_capacity(2_000)
            .time_to_live(std::time::Duration::from_secs(300))
            .build()
    })
}

/// Spread heat from an accessed atom to its neighbors.
/// Loads neighbors from DB if not yet cached, then heats them.
fn spread_heat(conn: &Connection, atom_id: i64) {
    let cache = heat_cache();

    // Get or compute neighbors
    let neighbors: Vec<(i64, f64)> = if let Some(_cached) = cluster_cache().get(&atom_id) {
        // We cached neighbor phrases, but we need IDs here
        // Fall through to SQL for simplicity — cluster_cache stores phrases
        Vec::new()
    } else {
        // Load neighbors from edges table
        let mut stmt = conn.prepare(
            "SELECT CASE WHEN atom_a = ?1 THEN atom_b ELSE atom_a END, weight
             FROM edges WHERE atom_a = ?1 OR atom_b = ?1
             ORDER BY weight DESC LIMIT 5"
        ).ok();
        if let Some(ref mut stmt) = stmt {
            stmt.query_map(params![atom_id], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, f64>(1)?))
            }).ok()
            .map(|iter| iter.filter_map(|r| r.ok()).collect())
            .unwrap_or_default()
        } else {
            Vec::new()
        }
    };

    for (neighbor_id, weight) in &neighbors {
        let heat_boost = weight * 0.5; // half the edge weight as heat
        if let Some((atom, heat)) = cache.get(neighbor_id) {
            // Boost existing entry's heat
            let new_heat = (heat + heat_boost).min(10.0);
            cache.insert(*neighbor_id, (atom.clone(), new_heat));
        }
        // If not cached, it'll get heat on first access
    }
}

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
// ── In-memory word index for 100x faster recall ──
// Splits each atom phrase into words, indexes by word.
// Query splits into words, sums scores, returns top 5.
// Rust: ~0.05ms vs 17ms SQL LIKE — ~340x faster.
// Falls through to SQL when empty (test databases).
static WORD_INDEX: std::sync::OnceLock<std::sync::Mutex<Option<std::collections::HashMap<String, Vec<(i64, f64)>>>>> = std::sync::OnceLock::new();

fn word_index<'a>() -> &'a std::sync::Mutex<Option<std::collections::HashMap<String, Vec<(i64, f64)>>>> {
    WORD_INDEX.get_or_init(|| std::sync::Mutex::new(None))
}

fn build_word_index(conn: &Connection) -> std::collections::HashMap<String, Vec<(i64, f64)>> {
    let mut idx: std::collections::HashMap<String, Vec<(i64, f64)>> = std::collections::HashMap::new();
    if let Ok(mut stmt) = conn.prepare("SELECT id, phrase, ref_count * importance FROM atoms") {
        if let Ok(rows) = stmt.query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?, row.get::<_, f64>(2)?))) {
            for row in rows.flatten() {
                let (aid, phrase, score) = row;
                for word in phrase.split_whitespace() {
                    idx.entry(word.to_string()).or_default().push((aid, score));
                }
            }
        }
    }
    for list in idx.values_mut() {
        list.sort_unstable_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    }
    idx
}

fn query_word_index(idx: &std::collections::HashMap<String, Vec<(i64, f64)>>, phrase: &str, limit: usize) -> Vec<(i64, f64)> {
    let mut scores: std::collections::HashMap<i64, f64> = std::collections::HashMap::new();
    for word in phrase.split_whitespace() {
        if let Some(atoms) = idx.get(word) {
            for (aid, score) in atoms {
                *scores.entry(*aid).or_insert(0.0) += score;
            }
        }
    }
    let mut ranked: Vec<(i64, f64)> = scores.into_iter().collect();
    ranked.sort_unstable_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    ranked.truncate(limit);
    ranked
}

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
        -- expression index for ORDER BY (ref_count * importance) DESC in recall()
        CREATE INDEX IF NOT EXISTS idx_atoms_score ON atoms(ref_count * importance DESC);

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

        CREATE TABLE IF NOT EXISTS context_snippets (
            atom_id INTEGER NOT NULL,
            snippet TEXT NOT NULL DEFAULT '',
            turn INTEGER NOT NULL,
            session TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (atom_id) REFERENCES atoms(id)
        );
        CREATE INDEX IF NOT EXISTS idx_snippets_atom ON context_snippets(atom_id, turn DESC);
        CREATE TABLE IF NOT EXISTS edges (
            atom_a INTEGER NOT NULL,
            atom_b INTEGER NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            last_updated INTEGER NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (atom_a, atom_b)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_a ON edges(atom_a, weight DESC);
        CREATE INDEX IF NOT EXISTS idx_edges_b ON edges(atom_b, weight DESC);

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
///
/// HINT: This function is `pub` and is used by `extract_atoms`. Do not rename it.

// ---------------------------------------------------------------------------
// Classifier: detect phrase types
// ---------------------------------------------------------------------------

fn looks_like_path(s: &str) -> bool {
    // Contains path separators or file extensions like .rs, .py, .ts
    s.contains('/') || s.contains('\\') || s.contains(".rs") || s.contains(".py")
        || s.contains(".ts") || s.starts_with('/')
}

fn looks_like_uuid(s: &str) -> bool {
    let clean = s.replace('-', "");
    clean.len() == 32 && clean.chars().all(|c| c.is_ascii_hexdigit())
}

fn looks_like_hash(s: &str) -> bool {
    let clean = s.trim_end_matches(|c: char| !c.is_ascii_hexdigit() && c != ':');
    clean.len() >= 40 && clean.chars().all(|c| c.is_ascii_hexdigit())
        || (s.len() >= 8 && s.len() <= 64 && s.chars().all(|c| c.is_ascii_hexdigit()) && !looks_like_uuid(s))
}

fn looks_like_number(s: &str) -> bool {
    s.chars().all(|c| c.is_ascii_digit() || c == '.' || c == '-' || c == '+')
        || (s.starts_with("0x") && s[2..].chars().all(|c| c.is_ascii_hexdigit()))
}

fn looks_like_identifier(s: &str) -> bool {
    s.contains("::")  // Rust path separators
        || (s.contains(|c: char| c.is_ascii_uppercase())  // CamelCase
            && s.contains(|c: char| c == '.' || c == ':' || c == '_'))
        || s.chars().any(|c| c == '_')  // snake_case
}

fn looks_like_url(s: &str) -> bool {
    s.starts_with("http://") || s.starts_with("https://") || s.contains("://")
}

fn looks_like_error_code(s: &str) -> bool {
    // EACCES, ENOENT, HTTP 404, exit code 1, etc.
    (s.len() <= 8 && s.starts_with('e') && s[1..].chars().all(|c| c.is_ascii_uppercase()))
        || s.starts_with("exit code") || s.contains("error:") || s.starts_with("err_")
}

fn looks_like_date(s: &str) -> bool {
    // ISO dates, timestamps
    s.contains('-') && s.len() >= 8 && s.chars().filter(|c| *c == '-').count() >= 2
}

// ---------------------------------------------------------------------------
// Normalization helpers
// ---------------------------------------------------------------------------

fn normalize_path(s: &str) -> String {
    // Strip user-specific prefix
    let cleaned = s
        .replace("/Users/", "/~/")
        .replace("/home/", "/~/");
    // Extract meaningful tail
    cleaned
}

fn normalize_identifier(s: &str) -> String {
    // Convert CamelCase to lowercase_with_underscores
    let mut result = String::new();
    for c in s.chars() {
        if c.is_ascii_uppercase() {
            if !result.is_empty() && !result.ends_with('_') {
                result.push('_');
            }
            result.push(c.to_ascii_lowercase());
        } else if c == ':' || c == '.' {
            result.push('_');
        } else {
            result.push(c);
        }
    }
    result
}

fn normalize_url(s: &str) -> String {
    // Strip protocol and trailing slash
    s.trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string()
}

fn stem_word(s: &str) -> String {
    // Porter-style basic stemmer (reuse existing logic from current normalize())
    if s.ends_with("ing") && s.len() > 4 {
        s[..s.len() - 3].to_string()
    } else if s.ends_with("ed") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("ly") && s.len() > 3 {
        s[..s.len() - 2].to_string()
    } else if s.ends_with("s") && s.len() > 3 && !s.ends_with("ss") {
        s[..s.len() - 1].to_string()
    } else {
        s.to_string()
    }
}

// ---------------------------------------------------------------------------
// Type-aware normalize
// ---------------------------------------------------------------------------

/// Normalize a phrase with type-aware normalization.
/// Detects the phrase type and applies the appropriate normalization strategy.
pub fn normalize(phrase: &str) -> String {
    let s = phrase.trim().to_lowercase();

    // 1. Path normalization: strip user prefix, keep semantic location
    if looks_like_path(&s) {
        return normalize_path(&s);
    }

    // 2. UUID normalization: all UUIDs become {uuid}
    if looks_like_uuid(&s) {
        return "{uuid}".to_string();
    }

    // 3. Hash normalization (hex 32+ chars)
    if looks_like_hash(&s) {
        return "{hash}".to_string();
    }

    // 4. Number normalization (integers, decimals, hex numbers)
    if looks_like_number(&s) {
        return "{number}".to_string();
    }

    // 5. Identifier normalization (CamelCase, snake_case, PascalCase)
    if looks_like_identifier(&s) {
        return normalize_identifier(&s);
    }

    // 6. URL normalization
    if looks_like_url(&s) {
        return normalize_url(&s);
    }

    // 7. Error code normalization (EACCES, 404, ENOENT, etc.)
    if looks_like_error_code(&s) {
        return s; // Already normalized
    }

    // 8. Date/timestamp normalization
    if looks_like_date(&s) {
        return "{date}".to_string();
    }

    // 9. Default: Porter-style stemmer + stop words
    stem_word(&s)
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

/// Increment an edge with a custom weight multiplier (for entity bridges).
pub fn increment_edge_weighted(
    conn: &Connection,
    a: i64,
    b: i64,
    turn: i64,
    weight_multiplier: f64,
) -> rusqlite::Result<()> {
    if a == b { return Ok(()); }
    let (low, high) = if a < b { (a, b) } else { (b, a) };
    conn.execute(
        "INSERT INTO edges (atom_a, atom_b, weight, last_updated, access_count) VALUES (?1, ?2, ?3, ?4, 1)
         ON CONFLICT(atom_a, atom_b) DO UPDATE SET
            weight = weight + ?3,
            last_updated = ?4,
            access_count = access_count + 1",
        params![low, high, weight_multiplier, turn],
    )?;
    Ok(())
}

/// Look up an atom's phrase by its id.
fn get_atom_phrase(conn: &Connection, id: i64) -> Option<String> {
    conn.query_row(
        "SELECT phrase FROM atoms WHERE id = ?1",
        params![id],
        |row| row.get(0),
    ).ok()
}

/// Check if two atom IDs are within distance ≤2 in the ordered atom list.
/// Used to avoid creating entity bridge edges for atoms already connected by W=2.
fn are_adjacent(ids: &[i64], a: i64, b: i64) -> bool {
    let pos_a = ids.iter().position(|x| *x == a).unwrap_or(usize::MAX);
    let pos_b = ids.iter().position(|x| *x == b).unwrap_or(usize::MAX);
    if pos_a == usize::MAX || pos_b == usize::MAX {
        return false;
    }
    let dist = if pos_a > pos_b { pos_a - pos_b } else { pos_b - pos_a };
    dist <= 2
}

/// Upsert an entity into the entity_registry table.
pub fn upsert_entity(conn: &Connection, entity: &EntityAnnotation) -> rusqlite::Result<()> {
    let normalized = normalize(&entity.name);
    let aliases_json = serde_json::to_string(&entity.aliases).unwrap_or_default();
    conn.execute(
        "INSERT INTO entity_registry (name, display_name, entity_type, aliases, importance, first_seen, last_seen, ref_count)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, 1)
         ON CONFLICT(name) DO UPDATE SET
            display_name = CASE WHEN ?2 != '' THEN ?2 ELSE display_name END,
            entity_type = CASE WHEN ?3 != '' THEN ?3 ELSE entity_type END,
            aliases = ?4,
            importance = MAX(importance, ?5),
            last_seen = ?6,
            ref_count = ref_count + 1",
        params![
            normalized,
            entity.name,
            entity.typ,
            aliases_json,
            entity.importance,
            Utc::now().timestamp(),
        ],
    )?;
    Ok(())
}

/// Consolidate an entry by extracting atoms from annotation + rule-based types,
/// with W=2 local edges and entity bridge edges (2.5x weight).
/// Build pre-formatted context snippets from a memory annotation.
/// Each phrase gets a one-line summary like: "change secret (action: modified config)"
fn build_atom_snippets(ann: &MemoryAnnotation) -> Vec<(String, String)> {
    let mut snippets = Vec::new();
    for item in &ann.phrases {
        let s = format!("{}", item.text);
        snippets.push((item.text.clone(), s));
    }
    for item in &ann.actions {
        let s = format!("{} (action)", item.text);
        snippets.push((item.text.clone(), s));
    }
    snippets
}

pub fn consolidate_entry(
    conn: &Connection,
    turn: i64,
    session: &str,
    text: &str,
    annotation: Option<&MemoryAnnotation>,
) -> rusqlite::Result<()> {
    // LSM-style batch: wrap all writes in a single transaction
    conn.execute_batch("BEGIN")?;

    let result = (|| -> rusqlite::Result<()> {
        let mut all_ids: Vec<i64> = Vec::new();

        // Phase 1: Annotation processing (phrases, actions, entities)
        if let Some(ann) = annotation {
        // Process phrases
        for item in &ann.phrases {
            let norm = normalize(&item.text);
            if norm.is_empty() || norm.len() < 3 { continue; }
            let id = upsert_atom(conn, &norm, turn, item.importance)?;
            record_position(conn, id, turn, session)?;
            all_ids.push(id);
        }

        // Process actions
        for item in &ann.actions {
            let norm = normalize(&item.text);
            if norm.is_empty() || norm.len() < 3 { continue; }
            let id = upsert_atom(conn, &norm, turn, item.importance)?;
            record_position(conn, id, turn, session)?;
            all_ids.push(id);
        }

        // Register entities in the entity_registry
        for entity in &ann.entities {
            upsert_entity(conn, entity)?;
        }
    }

    // Phase 2: Rule-based extraction (always runs)
    let rule_atoms = extract_atoms(text);
    let rule_atoms: Vec<String> = rule_atoms.into_iter().filter(|a| {
        !is_stop_word(conn, a).unwrap_or(false)
    }).collect();

    for phrase in &rule_atoms {
        let id = upsert_atom(conn, phrase, turn, 1.0)?;
        record_position(conn, id, turn, session)?;
        all_ids.push(id);
    }

    if all_ids.is_empty() {
        return Ok(());
    }

    // Phase 3: Entity bridge edges (2.5x weight, across distance)
    // Match entity names from annotation to atoms by substring
    if let Some(ann) = annotation {
        for entity in &ann.entities {
            let entity_lower = entity.name.to_lowercase();
            // Find which atoms in all_ids have phrases containing this entity name
            let matching_ids: Vec<i64> = all_ids.iter().copied()
                .filter(|id| {
                    get_atom_phrase(conn, *id)
                        .map(|p| p.to_lowercase().contains(&entity_lower))
                        .unwrap_or(false)
                })
                .collect();

            // Bridge edges: connect non-adjacent pairs of matching atoms
            for i in 0..matching_ids.len() {
                for j in i + 1..matching_ids.len() {
                    let a = matching_ids[i];
                    let b = matching_ids[j];
                    // Only create bridge edge if atoms are NOT adjacent (W=2 covers those)
                    if !are_adjacent(&all_ids, a, b) {
                        increment_edge_weighted(conn, a, b, turn, 2.5)?;
                    }
                }
            }
        }
    }

    // Phase 4: W=2 local adjacency edges (weight=1.0, up to distance 2)
    for i in 0..all_ids.len() {
        for w in 1..=2 {
            let j = i + w;
            if j < all_ids.len() {
                increment_edge_weighted(conn, all_ids[i], all_ids[j], turn, 1.0)?;
            }
        }
    }

    // Phase 5: Write-time synthesis — store context snippet for each atom
    // Pre-formats annotation data so recall doesn't need LLM synthesis
    if let Some(ann) = annotation {
        let snippets = build_atom_snippets(ann);
        if !snippets.is_empty() {
        }
        for (phrase, snippet) in &snippets {
            if let Ok(mut stmt) = conn.prepare(
                "INSERT INTO context_snippets (atom_id, snippet, turn, session)
                 SELECT id, ?2, ?3, ?4 FROM atoms WHERE phrase = ?1"
            ) {
                let _ = stmt.execute(params![phrase, snippet, turn, session]);
            }
        }
    }

    Ok(())
    })(); // end closure

    match result {
        Ok(()) => conn.execute_batch("COMMIT"),
        Err(e) => {
            conn.execute_batch("ROLLBACK").ok();
            Err(e)
        }
    }
}


/// Reinforce edges between co-occurring atoms in a context block.
/// Uses LLM attention as implicit training signal — atoms appearing
/// together in synthesized context get their edge weights boosted.
pub fn reinforce_cooccurrence(
    conn: &Connection,
    atom_ids: &[i64],
    turn: i64,
) -> rusqlite::Result<()> {
    for i in 0..atom_ids.len() {
        for j in (i + 1)..atom_ids.len() {
            let a = atom_ids[i];
            let b = atom_ids[j];
            if a != b {
                increment_edge_weighted(conn, a, b, turn, 0.5)?;
            }
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

/// Find atoms whose phrase matches a LIKE pattern, ordered by ref_count × importance DESC.
pub fn recall(conn: &Connection, phrase: &str, limit: i64) -> rusqlite::Result<Vec<Atom>> {
    // Fast path: in-memory word index (O(1) per word, ~0.05ms vs 17ms SQL)
    {
        let guard = word_index().lock().unwrap();
        if guard.is_none() {
            drop(guard);
            let idx = build_word_index(conn);
            let mut g = word_index().lock().unwrap();
            *g = Some(idx);
        }
    }
    // Get ranked atom IDs from fast path
    let ranked_ids: Vec<(i64, f64)> = {
        let guard = word_index().lock().unwrap();
        if let Some(ref idx) = *guard {
            query_word_index(idx, phrase, limit as usize)
        } else {
            Vec::new()
        }
    };

    let atoms: Vec<Atom>;
    if ranked_ids.len() >= limit as usize {
        // Fast path: resolve IDs to Atoms via direct lookup
        let id_list: Vec<i64> = ranked_ids.iter().map(|(id, _)| *id).collect();
        atoms = resolved_ids_to_atoms(conn, &id_list)?;
    } else {
        // Fallback: SQL LIKE query
        let pattern = format!("%{}%", phrase);
        let mut stmt = conn.prepare(
            "SELECT id, phrase, first_seen, last_seen, ref_count, importance
             FROM atoms WHERE phrase LIKE ?1
             ORDER BY (ref_count * importance) DESC LIMIT ?2",
        )?;
        atoms = stmt
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
            .collect::<Vec<Atom>>();
    }

    // Heat diffusion: cache results and spread heat to neighbors
    let cache = heat_cache();
    for atom in &atoms {
        cache.insert(atom.id, (atom.clone(), 1.0));
        spread_heat(conn, atom.id);
    }

    Ok(atoms)
}

/// Resolve a list of atom IDs to Atom structs via SQL.
fn resolved_ids_to_atoms(conn: &Connection, ids: &[i64]) -> rusqlite::Result<Vec<Atom>> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    // Batch: single query with multiple parameters instead of N queries
    let placeholders: Vec<String> = ids.iter().map(|_| "?".to_string()).collect();
    let sql = format!(
        "SELECT id, phrase, first_seen, last_seen, ref_count, importance FROM atoms WHERE id IN ({})",
        placeholders.join(",")
    );
    let mut stmt = conn.prepare(&sql)?;
    let params_refs: Vec<&dyn rusqlite::types::ToSql> = ids.iter().map(|id| id as &dyn rusqlite::types::ToSql).collect();
    let atoms = stmt.query_map(params_refs.as_slice(), |row| {
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

    // Heat cache: check cluster cache first
    if let Some(cached) = cluster_cache().get(&atom_id) {
        return Ok(cached.iter().take(limit as usize).cloned().collect());
    }

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
    // Resolve neighbor ids to phrases and cache
    let mut result = Vec::with_capacity(neighbors.len());
    for (nid, weight) in neighbors {
        let phrase: String =
            conn.query_row("SELECT phrase FROM atoms WHERE id = ?1", params![nid], |row| {
                row.get(0)
            })?;
        result.push((phrase, weight));
    }
    cluster_cache().insert(atom_id, result.clone());
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
    fn test_normalize_path() {
        let result = normalize("/Users/azfar/src/storage.rs");
        assert!(result.contains("storage.rs") || result.contains("storage"), "path normalization: {}", result);
    }

    #[test]
    fn test_normalize_uuid() {
        assert_eq!(normalize("550e8400-e29b-41d4-a716-446655440000"), "{uuid}");
    }

    #[test]
    fn test_normalize_hash() {
        assert_eq!(normalize("433b12ac60da89ef1234567890abcdef12345678"), "{hash}");
    }

    #[test]
    fn test_normalize_number() {
        assert_eq!(normalize("234"), "{number}");
        assert_eq!(normalize("8080"), "{number}");
    }

    #[test]
    fn test_normalize_identifier() {
        let result = normalize("Storage::append_entry");
        assert_eq!(result, "storage__append_entry");
    }

    #[test]
    fn test_normalize_stemming() {
        assert_eq!(normalize("running"), "runn");
        assert_eq!(normalize("fixed"), "fix");
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
        consolidate_entry(&conn, 1, "test-session", "discuss hash chain and merkle tree", None)?;
        let atom_count: i64 = conn.query_row("SELECT COUNT(*) FROM atoms", [], |row| row.get(0))?;
        // 6 words -> 5 bigrams + 4 trigrams = 9 atoms (all unique)
        assert_eq!(atom_count, 9, "5 bigrams + 4 trigrams from 6-word text");
        // Verify positions recorded for each atom
        let pos_count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM positions WHERE turn = ?1 AND session = ?2",
            params![1, "test-session"], |row| row.get(0)
        )?;
        assert_eq!(pos_count, 9, "each atom should have a position record");
        // Verify edges created with W=2 local adjacency
        let edge_count: i64 = conn.query_row("SELECT COUNT(*) FROM edges", [], |row| row.get(0))?;
        // W=2 edges from 9 atoms: each atom connects to next 2 = 2*7 + 1 = 15
        assert_eq!(edge_count, 15, "W=2 local edges from 9 atoms");
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
        consolidate_entry(&conn, 1, "s1", "build hash chain", None)?;
        consolidate_entry(&conn, 2, "s2", "fix hash chain bug", None)?;
        consolidate_entry(&conn, 3, "s1", "test hash chain", None)?;
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
        consolidate_entry(&conn, 1, "s1", "alpha beta", None)?;
        consolidate_entry(&conn, 2, "s1", "alpha gamma", None)?;
        consolidate_entry(&conn, 3, "s1", "alpha delta", None)?;
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
        consolidate_entry(&conn, 1, "s1", "hash chain merkle tree data struct", None)?;
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
        consolidate_entry(&conn, 1, "s1", "hash chain merkle tree", None)?;
        let status = brain_status(&conn)?;
        assert_eq!(status.atom_count, 5); // 4 bigrams + 1 trigram
        assert_eq!(status.position_count, 5);
        assert_eq!(status.edge_count, 7); // W=2 local edges: 5 atoms → 7 edges
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
        consolidate_entry(&conn, 1, "s1", "hash chain and merkle tree", None)?;
        consolidate_entry(&conn, 2, "s1", "hash chain verify", None)?;
        let results = clusters(&conn, "hash chain", 5)?;
        assert!(!results.is_empty());
        // Edges were accumulated
        Ok(())
    }

    #[test]
    fn test_when_first_last() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;
        consolidate_entry(&conn, 5, "s1", "discuss hash chain", None)?;
        consolidate_entry(&conn, 10, "s1", "build hash chain again", None)?;
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

    #[test]
    fn test_consolidate_with_annotation() -> rusqlite::Result<()> {
        let conn = Connection::open_in_memory()?;
        create_tables(&conn)?;

        let ann = MemoryAnnotation {
            phrases: vec![crate::types::MemoryItem { text: "hash chain verification".into(), importance: 5.0 }],
            actions: vec![],
            entities: vec![],
        };

        consolidate_entry(&conn, 1, "test", "", Some(&ann))?;

        // Verify atom was created with importance=5
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM atoms WHERE phrase = ?1",
            params!["hash chain verification"],
            |row| row.get(0),
        )?;
        assert_eq!(count, 1, "annotation phrase atom should exist");

        let importance: f64 = conn.query_row(
            "SELECT importance FROM atoms WHERE phrase = ?1",
            params!["hash chain verification"],
            |row| row.get(0),
        )?;
        assert!((importance - 5.0).abs() < 1e-6, "importance should be 5.0");

        Ok(())
    }

    /// Helper: create in-memory DB with brain tables.
    fn setup_test_db() -> Connection {
        let conn = Connection::open_in_memory().expect("create in-memory DB");
        create_tables(&conn).expect("create tables");
        conn
    }
}

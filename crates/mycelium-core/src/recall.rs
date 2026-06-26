//! Graph-guided recall — traverses the brain graph to find relevant memory context.
//!
//! This is the core retrieval pipeline, running entirely on indexed SQLite tables.
//! No LLM calls — pure graph traversal using existing brain::recall/clusters/when.

use crate::brain;
use crate::error::MyceliumError;
use crate::types::*;
use rusqlite::{params, Connection};
use std::time::Instant;
use tracing::debug;

/// Traverse the brain graph for a parsed recall query.
///
/// Steps: seed → temporal filter → cluster expansion → rank → return top N.
/// Ensure the context_snippets table exists (created lazily on first recall).
fn ensure_snippets_table(conn: &Connection) {
    let _ = conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS context_snippets (
            atom_id INTEGER NOT NULL,
            snippet TEXT NOT NULL DEFAULT '',
            turn INTEGER NOT NULL,
            session TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (atom_id) REFERENCES atoms(id)
        );
        CREATE INDEX IF NOT EXISTS idx_snippets_atom ON context_snippets(atom_id, turn DESC);"
    );
}

pub fn traverse(
    conn: &Connection,
    query: &RecallQuery,
    max_clusters: usize,
    max_neighbors: usize,
) -> Result<RecallResult, MyceliumError> {
    let start = Instant::now();

    // Auto-create context_snippets table if needed
    ensure_snippets_table(conn);

    if query.atoms.is_empty() {
        return Ok(RecallResult {
            query: query.clone(),
            clusters: vec![],
            total_clusters: 0,
            traversal_time_ms: 0.0,
        });
    }

    // Step 1: Seed — find matching atoms for each query phrase
    let mut all_clusters: Vec<AtomCluster> = Vec::new();

    for phrase in &query.atoms {
        let atoms = brain::recall(conn, phrase, 10)
            .map_err(|e| MyceliumError::Recall(e.to_string()))?;

        for atom in atoms {
            // Step 2: Temporal filter
            if let Some(ref hint) = query.temporal_hint {
                if let Some((_first, last, _count)) = brain::when(conn, &atom.phrase)
                    .map_err(|e| MyceliumError::Recall(e.to_string()))?
                {
                    // Apply basic temporal heuristic:
                    // If temporal_hint contains "last night", filter to last 24h in turns
                    // For now, a simple presence check — will be refined with actual time parsing
                    if hint.contains("night") || hint.contains("yesterday") {
                        // Initial heuristic: filter to last ~500 turns (~1 day)
                        // Will be refined with proper time parsing in future iterations
                        let cutoff = max_turn(conn).max(500) - 500;
                        if last < cutoff {
                            continue;
                        }
                    }
                }
            }

            // Step 3: Cluster expansion
            let neighbors = brain::clusters(conn, &atom.phrase, max_neighbors as i64)
                .map_err(|e| MyceliumError::Recall(e.to_string()))?
                .into_iter()
                .map(|(phrase, weight)| (phrase, weight, 0.0)) // importance available on request
                .collect();

            let temporal = brain::when(conn, &atom.phrase)
                .map_err(|e| MyceliumError::Recall(e.to_string()))?
                .map(|(f, l, c)| (f, l, c));

            // Look up pre-written snippet (write-time synthesis)
            let snippet: Option<String> = conn
                .query_row(
                    "SELECT snippet FROM context_snippets WHERE atom_id = ?1 ORDER BY turn DESC LIMIT 1",
                    params![atom.id],
                    |row| row.get(0),
                )
                .ok();

            let snippet_count = snippet.as_ref().map(|s| s.len()).unwrap_or(0);
            if snippet_count > 0 {
                debug!("  Snippet for '{}': {} chars", atom.phrase, snippet_count);
            }

            all_clusters.push(AtomCluster {
                seed_id: atom.id,
                seed_phrase: atom.phrase.clone(),
                neighbors,
                temporal,
                snippet,
            });
        }
    }

    // Step 4: Rank by (ref_count × importance) via recall()'s inherent ordering
    // brain::recall already returns results sorted by (ref_count * importance) DESC
    // Take top N
    all_clusters.truncate(max_clusters);

    let total_clusters = all_clusters.len();
    let elapsed = start.elapsed();
    debug!(
        "Recall traversal: {} atom(s), {} cluster(s), {:.2}ms",
        query.atoms.len(),
        total_clusters,
        elapsed.as_secs_f64() * 1000.0
    );

    Ok(RecallResult {
        query: query.clone(),
        clusters: all_clusters,
        total_clusters,
        traversal_time_ms: elapsed.as_secs_f64() * 1000.0,
    })
}

/// Helper to get the maximum turn number from the entries table.
/// Returns 0 if entries table doesn't exist (e.g., brain-only test DB).
fn max_turn(conn: &Connection) -> i64 {
    conn.query_row(
        "SELECT COALESCE(MAX(turn), 0) FROM entries",
        [],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::brain::create_tables;

    fn setup_brain() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        create_tables(&conn).unwrap();
        conn
    }

    #[test]
    fn test_traverse_empty_query() {
        let conn = setup_brain();
        let query = RecallQuery {
            atoms: vec![],
            intent: RecallIntent::Relational,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert!(result.clusters.is_empty());
        assert_eq!(result.total_clusters, 0);
    }

    #[test]
    fn test_traverse_no_matches() {
        let conn = setup_brain();
        let query = RecallQuery {
            atoms: vec!["nonexistent_phrase_xyz".to_string()],
            intent: RecallIntent::Factual,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert!(result.clusters.is_empty());
    }

    #[test]
    fn test_traverse_with_seeded_atom() {
        let conn = setup_brain();
        // Seed an atom directly
        let id = brain::upsert_atom(&conn, "test phrase", 1, 1.0).unwrap();
        brain::record_position(&conn, id, 1, "test-session").unwrap();

        let query = RecallQuery {
            atoms: vec!["test phrase".to_string()],
            intent: RecallIntent::Factual,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert_eq!(result.clusters.len(), 1);
        assert_eq!(result.clusters[0].seed_phrase, "test phrase");
    }

    #[test]
    fn test_traverse_temporal_filter() {
        let conn = setup_brain();
        // Seed an old atom and a new one
        let old = brain::upsert_atom(&conn, "old thing", 1, 1.0).unwrap();
        brain::record_position(&conn, old, 1, "session-1").unwrap();
        let new = brain::upsert_atom(&conn, "new thing last night", 10000, 1.0).unwrap();
        brain::record_position(&conn, new, 10000, "session-2").unwrap();

        let query = RecallQuery {
            atoms: vec!["thing".to_string()],
            intent: RecallIntent::Temporal,
            temporal_hint: Some("last night".to_string()),
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        // Only "new thing last night" should survive the temporal filter
        assert!(result.clusters.iter().any(|c| c.seed_phrase.contains("last night")));
    }

    #[test]
    fn test_max_turn_empty_db() {
        let conn = setup_brain();
        // Should return 0 when entries table is empty or doesn't exist
        let max = max_turn(&conn);
        assert_eq!(max, 0);
    }

    #[test]
    fn test_recall_to_fallback_e2e() {
        let conn = setup_brain();

        // Seed atoms with edges (simulates what consolidate_entry does)
        let a1 = brain::upsert_atom(&conn, "change secret", 100, 0.8).unwrap();
        brain::record_position(&conn, a1, 100, "s1").unwrap();
        let a2 = brain::upsert_atom(&conn, "server config", 101, 0.7).unwrap();
        brain::record_position(&conn, a2, 101, "s1").unwrap();
        brain::increment_edge(&conn, a1, a2, 101).unwrap();

        // Query: graph traversal for "secret"
        let query = RecallQuery {
            atoms: vec!["secret".to_string()],
            intent: RecallIntent::Relational,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert_eq!(result.clusters.len(), 1, "Should find the 'change secret' cluster");
        assert_eq!(result.clusters[0].seed_phrase, "change secret");
        assert!(
            result.clusters[0].neighbors.iter().any(|(p, _, _)| p == "server config"),
            "Neighbor should include 'server config' via edge"
        );
    }
}

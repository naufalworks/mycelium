//! In-memory hot graph — heat-governed atom cache with speculative spread.
//! SQLite is the source of truth. HotGraph is always disposable.

use parking_lot::RwLock;
use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tracing::{debug, trace};

// ── Graph Types ──

/// A weighted connection from one atom to a neighbor, expressed by phrase.
/// Different from `brain::Edge` (which uses atom IDs in SQLite): the hot graph
/// is keyed by phrase strings, so neighbor references are phrase strings.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Edge {
    pub neighbor: String,
    pub weight: f64,
}

/// A position occurrence of an atom in a session/turn. Lighter than
/// `brain::Position` (which carries DB ids); the hot graph only needs the
/// minimal context for visualization / rehydration.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct Position {
    pub turn: i64,
    pub session: String,
}

// ── Heat Constants ──

/// Decay multiplier per tick (60s tick → half-life ≈ 14 min).
pub const DECAY_RATE: f64 = 0.95;
/// Neighbor gets half of source's heat delta.
pub const SPREAD_FACTOR: f64 = 0.5;
/// Don't propagate noise below this floor.
pub const SPREAD_MINIMUM: f64 = 0.01;
/// Atoms below this threshold leave L1.
pub const EVICT_THRESHOLD: f64 = 0.1;
/// Cold atoms with weighted importance above this enter L1.
pub const PROMOTE_THRESHOLD: f64 = 0.3;
/// Heat multiplier for newly consolidated atoms.
pub const CONSOLIDATION_HEAT_MULTIPLIER: f64 = 1.5;
/// Heat added on query match.
pub const QUERY_HIT_HEAT: f64 = 1.0;
/// Heat added on recall hit.
pub const RECALL_HIT_HEAT: f64 = 0.5;
/// Interval between decay ticks.
pub const DECAY_INTERVAL: Duration = Duration::from_secs(60);

// ── HotGraph ──

/// The in-memory atom graph. Atoms above EVICT_THRESHOLD live here.
pub struct HotGraph {
    atoms: RwLock<HashMap<String, HotAtom>>,
    metrics: Arc<HeatMetrics>,
}

/// A single atom in the hot graph with its live heat value.
pub struct HotAtom {
    pub phrase: String,
    pub heat: f64,
    pub importance: f64,
    pub ref_count: i64,
    pub edges: Vec<Edge>,
    pub positions: Vec<Position>,
    pub last_accessed: Instant,
}

// ── HeatMetrics ──

/// Observability counters for heat operations.
#[derive(Debug)]
pub struct HeatMetrics {
    pub hot_count: AtomicI64,
    pub total_heat: Mutex<f64>,
    pub bumps: AtomicU64,
    pub evictions: AtomicU64,
    pub promotions: AtomicU64,
    pub hits: AtomicU64,
    pub misses: AtomicU64,
}

impl HeatMetrics {
    pub fn new() -> Self {
        Self {
            hot_count: AtomicI64::new(0),
            total_heat: Mutex::new(0.0),
            bumps: AtomicU64::new(0),
            evictions: AtomicU64::new(0),
            promotions: AtomicU64::new(0),
            hits: AtomicU64::new(0),
            misses: AtomicU64::new(0),
        }
    }

    pub fn snapshot(&self) -> HeatMetricsSnapshot {
        HeatMetricsSnapshot {
            hot_count: self.hot_count.load(Ordering::Relaxed),
            total_heat: *self.total_heat.lock().unwrap(),
            bumps: self.bumps.load(Ordering::Relaxed),
            evictions: self.evictions.load(Ordering::Relaxed),
            promotions: self.promotions.load(Ordering::Relaxed),
            hits: self.hits.load(Ordering::Relaxed),
            misses: self.misses.load(Ordering::Relaxed),
        }
    }
}

impl Default for HeatMetrics {
    fn default() -> Self {
        Self::new()
    }
}

/// Snapshot of heat metrics at a point in time.
#[derive(Debug, Clone, serde::Serialize)]
pub struct HeatMetricsSnapshot {
    pub hot_count: i64,
    pub total_heat: f64,
    pub bumps: u64,
    pub evictions: u64,
    pub promotions: u64,
    pub hits: u64,
    pub misses: u64,
}

/// Immutable snapshot of a hot atom for external consumption.
#[derive(Debug, Clone, serde::Serialize)]
pub struct HotAtomSnapshot {
    pub phrase: String,
    pub heat: f64,
    pub importance: f64,
    pub ref_count: i64,
    pub edges: Vec<Edge>,
    pub positions: Vec<Position>,
}

impl HotGraph {
    /// Create an empty HotGraph.
    pub fn new() -> Self {
        Self {
            atoms: RwLock::new(HashMap::new()),
            metrics: Arc::new(HeatMetrics::new()),
        }
    }

    /// Seed a newly consolidated atom into the hot graph with initial heat.
    pub fn seed(
        &self,
        phrase: &str,
        importance: f64,
        ref_count: i64,
        positions: Vec<Position>,
        edges: Vec<Edge>,
    ) {
        let heat = importance * CONSOLIDATION_HEAT_MULTIPLIER;
        let mut atoms = self.atoms.write();
        let entry = atoms.entry(phrase.to_string()).or_insert_with(|| {
            self.metrics.hot_count.fetch_add(1, Ordering::Relaxed);
            HotAtom {
                phrase: phrase.to_string(),
                heat: 0.0,
                importance: 0.0,
                ref_count: 0,
                edges: Vec::new(),
                positions: Vec::new(),
                last_accessed: Instant::now(),
            }
        });
        entry.heat += heat;
        entry.importance = importance;
        entry.ref_count = ref_count;
        entry.positions = positions;
        entry.edges = edges;
        entry.last_accessed = Instant::now();

        *self.metrics.total_heat.lock().unwrap() += heat;
        trace!("heat:seed phrase={} heat={:.2}", phrase, entry.heat);
    }

    /// Bump an atom's heat (called on query match or recall hit).
    pub fn bump(&self, phrase: &str, delta: f64) {
        let mut atoms = self.atoms.write();
        if let Some(atom) = atoms.get_mut(phrase) {
            atom.heat += delta;
            atom.last_accessed = Instant::now();
            self.metrics.bumps.fetch_add(1, Ordering::Relaxed);
            *self.metrics.total_heat.lock().unwrap() += delta;
            trace!(
                "heat:bump phrase={} delta={:.2} new_heat={:.2}",
                phrase, delta, atom.heat
            );
        }
    }

    /// Get a snapshot of a hot atom (fast L1 lookup). Returns None if not in hot graph.
    pub fn get(&self, phrase: &str) -> Option<HotAtomSnapshot> {
        let atoms = self.atoms.read();
        let result = atoms.get(phrase).map(|a| HotAtomSnapshot {
            phrase: a.phrase.clone(),
            heat: a.heat,
            importance: a.importance,
            ref_count: a.ref_count,
            edges: a.edges.clone(),
            positions: a.positions.clone(),
        });
        if result.is_some() {
            self.metrics.hits.fetch_add(1, Ordering::Relaxed);
        } else {
            self.metrics.misses.fetch_add(1, Ordering::Relaxed);
        }
        result
    }

    /// Run one decay cycle: spread, decay, evict, promote.
    pub fn tick_decay(&self) {
        let _span = tracing::debug_span!("heat:decay-cycle").entered();

        // Phase 1: Decay all atoms and collect spread deltas
        let mut spread_deltas: HashMap<String, f64> = HashMap::new();
        let mut to_evict: Vec<String> = Vec::new();

        {
            let mut atoms = self.atoms.write();
            for (phrase, atom) in atoms.iter_mut() {
                // Decay
                atom.heat *= DECAY_RATE;

                // Spread to neighbors
                if atom.heat > EVICT_THRESHOLD {
                    for edge in &atom.edges {
                        let spread = atom.heat * SPREAD_FACTOR * edge.weight;
                        if spread > SPREAD_MINIMUM {
                            *spread_deltas.entry(edge.neighbor.clone()).or_insert(0.0) += spread;
                            trace!(
                                "heat:spread {} -> {} via edge={:.2} delta={:.4}",
                                phrase, edge.neighbor, edge.weight, spread
                            );
                        }
                    }
                } else {
                    to_evict.push(phrase.clone());
                }
            }
        }

        // Phase 2: Apply spread deltas (need to release write lock first)
        // Then evict cold atoms
        {
            let mut atoms = self.atoms.write();
            for (phrase, delta) in &spread_deltas {
                if let Some(atom) = atoms.get_mut(phrase) {
                    atom.heat += delta;
                    *self.metrics.total_heat.lock().unwrap() += delta;
                }
                // If atom isn't hot yet, it might be a cold atom that should be promoted.
                // Promotion happens separately via SQLite scan.
            }

            for phrase in &to_evict {
                if let Some(atom) = atoms.remove(phrase) {
                    self.metrics.hot_count.fetch_sub(1, Ordering::Relaxed);
                    self.metrics.evictions.fetch_add(1, Ordering::Relaxed);
                    *self.metrics.total_heat.lock().unwrap() -= atom.heat;
                    debug!("heat:evict phrase={} heat={:.2}", phrase, atom.heat);
                }
            }
        }
    }

    /// Rebuild HotGraph from SQLite data (called on restart).
    /// Reads all atoms from SQLite and promotes the hottest ones.
    pub fn rebuild_from_sqlite(conn: &rusqlite::Connection) -> anyhow::Result<Self> {
        let graph = Self::new();
        // Load atoms in descending order of importance * ref_count
        let mut stmt = conn.prepare(
            "SELECT phrase, importance, ref_count FROM atoms ORDER BY ref_count * importance DESC LIMIT 10000",
        )?;
        let rows = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, f64>(1)?,
                row.get::<_, i64>(2)?,
            ))
        })?;

        for row in rows.flatten() {
            let (phrase, importance, ref_count) = row;
            if importance * ref_count as f64 > PROMOTE_THRESHOLD {
                // Load edges for this atom using the brain's edges table.
                // The brain stores edges as (atom_a INTEGER, atom_b INTEGER, weight),
                // so we join through the atoms table to resolve phrase strings.
                let edges: Vec<Edge> = match conn.prepare(
                    "SELECT a2.phrase, e.weight
                     FROM edges e
                     JOIN atoms a1 ON e.atom_a = a1.id
                     JOIN atoms a2 ON e.atom_b = a2.id
                     WHERE a1.phrase = ?1
                     UNION ALL
                     SELECT a1.phrase, e.weight
                     FROM edges e
                     JOIN atoms a1 ON e.atom_a = a1.id
                     JOIN atoms a2 ON e.atom_b = a2.id
                     WHERE a2.phrase = ?1"
                ) {
                    Ok(mut stmt) => stmt
                        .query_map([&phrase], |row| {
                            Ok(Edge {
                                neighbor: row.get(0)?,
                                weight: row.get(1)?,
                            })
                        })?
                        .flatten()
                        .collect(),
                    Err(_) => {
                        // edges table may not exist; degrade gracefully
                        Vec::new()
                    }
                };

                graph.metrics.promotions.fetch_add(1, Ordering::Relaxed);
                graph.seed(&phrase, importance, ref_count, Vec::new(), edges);
            }
        }

        debug!(
            "heat:rebuild loaded {} atoms",
            graph.metrics.hot_count.load(Ordering::Relaxed)
        );
        Ok(graph)
    }

    pub fn metrics(&self) -> &HeatMetrics {
        &self.metrics
    }

    /// Return current hot atoms sorted by heat (descending), up to `limit`.
    pub fn top_atoms(&self, limit: usize) -> Vec<HotAtomSnapshot> {
        let atoms = self.atoms.read();
        let mut sorted: Vec<HotAtomSnapshot> = atoms
            .values()
            .map(|a| HotAtomSnapshot {
                phrase: a.phrase.clone(),
                heat: a.heat,
                importance: a.importance,
                ref_count: a.ref_count,
                edges: a.edges.clone(),
                positions: a.positions.clone(),
            })
            .collect();
        sorted.sort_by(|a, b| b.heat.partial_cmp(&a.heat).unwrap_or(std::cmp::Ordering::Equal));
        sorted.truncate(limit);
        sorted
    }
}

impl Default for HotGraph {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_heat_bump() {
        let graph = HotGraph::new();
        graph.seed("test", 2.0, 1, vec![], vec![]);
        let before = graph.get("test").unwrap().heat;
        graph.bump("test", 1.0);
        let after = graph.get("test").unwrap().heat;
        assert!((after - before - 1.0).abs() < 1e-6);
    }

    #[test]
    fn test_heat_seed_with_importance() {
        let graph = HotGraph::new();
        graph.seed("alpha", 3.0, 5, vec![], vec![]);
        let atom = graph.get("alpha").unwrap();
        assert!((atom.heat - 3.0 * CONSOLIDATION_HEAT_MULTIPLIER).abs() < 1e-6);
        assert_eq!(atom.importance, 3.0);
        assert_eq!(atom.ref_count, 5);
    }

    #[test]
    fn test_heat_not_found() {
        let graph = HotGraph::new();
        assert!(graph.get("nonexistent").is_none());
    }

    #[test]
    fn test_heat_bump_non_existent() {
        let graph = HotGraph::new();
        graph.bump("nope", 1.0); // should not panic
        assert!(graph.get("nope").is_none());
    }

    #[test]
    fn test_heat_decay_single_atom() {
        let graph = HotGraph::new();
        graph.seed("x", 10.0, 1, vec![], vec![]);
        let heat_before = graph.get("x").unwrap().heat;
        graph.tick_decay();
        let heat_after = graph.get("x").unwrap().heat;
        assert!((heat_after - heat_before * DECAY_RATE).abs() < 1e-6);
    }

    #[test]
    fn test_heat_spread_to_neighbor() {
        let graph = HotGraph::new();
        graph.seed(
            "a",
            10.0,
            1,
            vec![],
            vec![Edge {
                neighbor: "b".to_string(),
                weight: 0.5,
            }],
        );
        // Seed "b" with enough importance to stay above EVICT_THRESHOLD so
        // it survives the decay cycle long enough to receive spread heat.
        graph.seed("b", 1.0, 1, vec![], vec![]);

        // After seed: a has heat 15.0, b has heat 1.5
        // Decay a to 14.25, spread 14.25 * 0.5 * 0.5 = 3.5625 to b
        graph.tick_decay();

        let b_heat_after = graph.get("b").unwrap().heat;
        let expected_b_heat = 1.5 * DECAY_RATE + 15.0 * DECAY_RATE * SPREAD_FACTOR * 0.5;
        assert!(
            (b_heat_after - expected_b_heat).abs() < 0.01,
            "b heat after spread: {} (expected ~{})",
            b_heat_after,
            expected_b_heat
        );
    }

    #[test]
    fn test_heat_spread_below_minimum() {
        let graph = HotGraph::new();
        graph.seed(
            "a",
            0.01,
            1,
            vec![],
            vec![Edge {
                neighbor: "b".to_string(),
                weight: 0.01,
            }],
        );
        // Seed "b" with enough heat to survive decay so we can observe spread.
        graph.seed("b", 1.0, 1, vec![], vec![]);
        let before = graph.get("b").unwrap().heat;
        graph.tick_decay();
        let after = graph.get("b").unwrap().heat;
        // "a" is below EVICT_THRESHOLD and gets evicted before spreading,
        // so the only change to "b" is its own decay (not spread).
        let expected = before * DECAY_RATE;
        assert!(
            (after - expected).abs() < 1e-9,
            "no significant spread expected: {} -> {} (expected {})",
            before, after, expected
        );
    }

    #[test]
    fn test_heat_eviction() {
        let graph = HotGraph::new();
        graph.seed("cold", 0.05, 1, vec![], vec![]);
        assert!(graph.get("cold").is_some());
        // Initial heat = 0.05 * 1.5 = 0.075 — below EVICT_THRESHOLD (0.1)
        graph.tick_decay();
        assert!(graph.get("cold").is_none(), "atom should be evicted");
    }

    #[test]
    fn test_top_atoms_ordering() {
        let graph = HotGraph::new();
        graph.seed("low", 1.0, 1, vec![], vec![]);
        graph.seed("high", 10.0, 1, vec![], vec![]);
        let top = graph.top_atoms(10);
        assert_eq!(top[0].phrase, "high", "highest heat should be first");
        assert_eq!(top[1].phrase, "low", "lower heat should be second");
    }

    #[test]
    fn test_metrics_tracking() {
        let graph = HotGraph::new();
        let m = graph.metrics();
        assert_eq!(m.hot_count.load(Ordering::Relaxed), 0);
        graph.seed("x", 1.0, 1, vec![], vec![]);
        assert_eq!(m.hot_count.load(Ordering::Relaxed), 1);
        graph.bump("x", 1.0);
        assert_eq!(m.bumps.load(Ordering::Relaxed), 1);
        let snapshot = m.snapshot();
        assert_eq!(snapshot.bumps, 1);
    }

    #[test]
    fn test_concurrent_bump_and_read() {
        use std::thread;
        let graph = Arc::new(HotGraph::new());
        graph.seed("concurrent", 5.0, 1, vec![], vec![]);

        let mut handles = vec![];
        for _ in 0..10 {
            let g = Arc::clone(&graph);
            handles.push(thread::spawn(move || {
                g.bump("concurrent", 0.1);
                g.get("concurrent");
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        // No panic = test passes
    }
}

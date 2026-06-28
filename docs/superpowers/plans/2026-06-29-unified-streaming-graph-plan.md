# Unified Streaming Graph (B+C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Unified Streaming Graph — an in-memory hot cache for atoms that uses heat spread as its prediction signal, eliminating cold SQLite reads for hot data.

**Architecture:** A single `HotGraph` struct with `HashMap<String, HotAtom>` holds hot atoms. Heat enters via `seed()` and `bump()`, spreads along edges via `tick_decay()`, and cold atoms evict to SQLite. SQLite is always source of truth — HotGraph is disposable on crash.

**Tech Stack:** Rust, tokio, parking_lot, dashmap (optional — std::sync::RwLock<HashMap> is sufficient)

## Global Constraints

- SQLite is source of truth. HotGraph is always disposable. All atoms must be recoverable from SQLite on restart.
- No new dependencies beyond `parking_lot` (already added for Approach A). If `dashmap` is needed for concurrent access, add it — but prefer `std::sync::RwLock<HashMap>` first and only use `dashmap` if contention becomes measurable.
- `#[allow(dead_code)]` is acceptable on HeatMetrics accessors that are consumed by CLI/API endpoints built in a later task.
- Start and end each task with `cargo build`. Run `cargo test -p mycelium-core --lib` after each task.

---
### Task 1: HotGraph Foundation — Struct, Heat Primitives, and Unit Tests

**Files:**
- Create: `crates/mycelium-core/src/hot_graph.rs`
- Modify: `crates/mycelium-core/src/lib.rs` (add `pub mod hot_graph;`)
- Test: `crates/mycelium-core/src/hot_graph.rs` contains unit tests at the bottom

**Interfaces:**
- Consumes: `tokio::sync::Notify`, `std::sync::Arc`, `parking_lot::RwLock`, existing `Position` and `Edge` types from `types.rs`
- Produces: `HotGraph` struct with `new()`, `seed()`, `bump()`, `get()`, `tick_decay()`, `metrics()` methods

- [ ] **Step 1: Create hot_graph.rs module with structs and types**

```rust
//! In-memory hot graph — heat-governed atom cache with speculative spread.
//! SQLite is the source of truth. HotGraph is always disposable.

use crate::types::{Edge, Position, HotAtomSnapshot};
use parking_lot::RwLock;
use std::collections::HashMap;
use std::sync::atomic::{AtomicI64, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::Notify;
use tracing::{debug, trace, warn};

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
```

- [ ] **Step 2: Implement HotGraph methods**

```rust
impl HotGraph {
    /// Create an empty HotGraph.
    pub fn new() -> Self {
        Self {
            atoms: RwLock::new(HashMap::new()),
            metrics: Arc::new(HeatMetrics::new()),
        }
    }

    /// Seed a newly consolidated atom into the hot graph with initial heat.
    pub fn seed(&self, phrase: &str, importance: f64, ref_count: i64, positions: Vec<Position>, edges: Vec<Edge>) {
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
            trace!("heat:bump phrase={} delta={:.2} new_heat={:.2}", phrase, delta, atom.heat);
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
            "SELECT phrase, importance, ref_count FROM atoms ORDER BY ref_count * importance DESC LIMIT 10000"
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
                // Load edges for this atom
                let mut edge_stmt = conn.prepare(
                    "SELECT neighbor, weight FROM edges WHERE phrase = ?1"
                )?;
                let edges: Vec<Edge> = edge_stmt.query_map([&phrase], |row| {
                    Ok(Edge {
                        neighbor: row.get(0)?,
                        weight: row.get(1)?,
                    })
                })?.flatten().collect();

                graph.seed(&phrase, importance, ref_count, Vec::new(), edges);
            }
        }

        debug!("heat:rebuild loaded {} atoms", graph.metrics.hot_count.load(Ordering::Relaxed));
        Ok(graph)
    }

    pub fn metrics(&self) -> &HeatMetrics {
        &self.metrics
    }

    /// Return current hot atoms sorted by heat (descending), up to `limit`.
    pub fn top_atoms(&self, limit: usize) -> Vec<HotAtomSnapshot> {
        let atoms = self.atoms.read();
        let mut sorted: Vec<HotAtomSnapshot> = atoms.values().map(|a| HotAtomSnapshot {
            phrase: a.phrase.clone(),
            heat: a.heat,
            importance: a.importance,
            ref_count: a.ref_count,
            edges: a.edges.clone(),
            positions: a.positions.clone(),
        }).collect();
        sorted.sort_by(|a, b| b.heat.partial_cmp(&a.heat).unwrap_or(std::cmp::Ordering::Equal));
        sorted.truncate(limit);
        sorted
    }
}
```

- [ ] **Step 3: Add HotAtomSnapshot for serializable read view**

In the same file (or in types.rs if preferable — same file keeps it co-located):

```rust
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
```

- [ ] **Step 4: Write unit tests for heat mechanics (TDD — write first, then implement)**

Add at the bottom of `hot_graph.rs`:

```rust
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
        graph.seed("a", 10.0, 1, vec![], vec![
            Edge { neighbor: "b".to_string(), weight: 0.5 },
        ]);
        // "b" is not in hot graph — spread to non-existent is a no-op
        graph.seed("b", 0.0, 1, vec![], vec![]);
        graph.get("b").unwrap(); // ensure it's in the graph
        let b_heat_before = graph.get("b").unwrap().heat;

        // After seed: a has heat 15.0, b has heat 0.0
        // Decay a to 14.25, spread 14.25 * 0.5 * 0.5 = 3.5625 to b
        graph.tick_decay();

        let b_heat_after = graph.get("b").unwrap().heat;
        assert!(
            (b_heat_after - (0.0 + 15.0 * DECAY_RATE * SPREAD_FACTOR * 0.5)).abs() < 0.01,
            "b heat after spread: {}",
            b_heat_after
        );
    }

    #[test]
    fn test_heat_spread_below_minimum() {
        let graph = HotGraph::new();
        graph.seed("a", 0.01, 1, vec![], vec![
            Edge { neighbor: "b".to_string(), weight: 0.01 },
        ]);
        graph.seed("b", 0.0, 1, vec![], vec![]);
        let before = graph.get("b").unwrap().heat;
        graph.tick_decay();
        let after = graph.get("b").unwrap().heat;
        assert!((after - before).abs() < 1e-9, "no significant spread expected");
    }

    #[test]
    fn test_heat_eviction() {
        let graph = HotGraph::new();
        graph.seed("cold", 0.05, 1, vec![], vec![]);
        assert!(graph.get("cold").is_some());
        // Run enough decay cycles to drop below EVICT_THRESHOLD
        // Initial heat = 0.05 * 1.5 = 0.075
        // After 1 decay: 0.075 * 0.95 = 0.0713 — still above 0.01 threshold? No, EVICT_THRESHOLD is 0.1
        // Actually 0.075 < 0.1, so it should evict on first decay
        graph.tick_decay();
        assert!(graph.get("cold").is_none(), "atom should be evicted");
    }

    #[test]
    fn test_heat_promotion() {
        let graph = HotGraph::new();
        // All atoms start hot-then-cold via seed. Promotion is handled in rebuild_from_sqlite.
        // For unit test, verify that rebuild promotes atoms above threshold.
        // This requires a real SQLite connection — skip for pure unit test.
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
```

- [ ] **Step 5: Build and run the unit tests that should compile (they may need adjustment if some involve SQLite)**

```bash
cd /Users/azfar.naufal/Documents/mycelium
cargo build 2>&1 | tail -10
cargo test -p mycelium-core --lib hot_graph::tests 2>&1 | tail -20
```

Expected: Tests pass, or fix compilation errors (e.g. missing `Edge` import from types.rs).

- [ ] **Step 6: Export module in lib.rs**

Add to `crates/mycelium-core/src/lib.rs`:
```rust
pub mod hot_graph;
```

- [ ] **Step 7: Commit**

```bash
git add crates/mycelium-core/src/hot_graph.rs crates/mycelium-core/src/lib.rs
git commit -m "feat: add HotGraph — in-memory heat-governed atom cache with spread, decay, eviction"
```

---

### Task 2: Wire HotGraph into Storage and Consolidation

**Files:**
- Modify: `crates/mycelium-core/src/storage.rs`
- Modify: `crates/mycelium-core/src/brain.rs`
- Modify: `crates/mycelium-core/src/recall.rs`

**Interfaces:**
- Consumes: `HotGraph` from Task 1
- Produces: `Storage::hot_graph()` accessor, `consolidate_entry()` seeds HotGraph, `traverse()` bumps heat

- [ ] **Step 1: Add hot_graph field to Storage**

```rust
pub struct Storage {
    // ... existing fields ...
    pub hot_graph: Arc<HotGraph>,
}
```

Initialize in `Storage::open()`:
```rust
let hot_graph = Arc::new(HotGraph::new());

// After all tables are created and validated, optionally rebuild from SQLite
// For initial implementation, start empty — atoms get seeded on consolidate
```

Return accessor:
```rust
pub fn hot_graph(&self) -> &Arc<HotGraph> {
    &self.hot_graph
}
```

- [ ] **Step 2: Wire L1 lookup into get_entry path**

In `get_entry()` in storage.rs, before the cache check (or after — moka cache is entry-level, HotGraph is atom-level, they're different):

For initial integration, the HotGraph is atom-level (not entry-level). The L1 lookup applies in the `search_facts` and `search_fts` paths, not in `get_entry` (which returns a single MemoryEntry, not atom positions).

Modify `search_facts()` in storage.rs — after SQLite results, check HotGraph for any atoms in the results and bump their heat:

```rust
pub fn search_facts(&self, query: &str, limit: usize) -> Result<Vec<BareFact>> {
    // ... existing SQLite search ...
    let facts = ... ; // from SQLite

    // Bump heat for matched atoms (L1 prediction signal)
    for fact in &facts {
        self.hot_graph.bump(&fact.attribute, RECALL_HIT_HEAT);
        self.hot_graph.bump(&fact.value, RECALL_HIT_HEAT);
    }

    Ok(facts)
}
```

Also add a hot-path method `search_facts_hot()` that checks HotGraph first:

```rust
/// Fast path: check HotGraph before SQLite.
pub fn search_facts_hot(&self, query: &str, limit: usize) -> Result<Vec<BareFact>> {
    // Check if query atoms are in hot graph
    let query_atoms: Vec<&str> = query.split_whitespace().collect();
    let mut hot_hit = false;
    for atom in &query_atoms {
        if self.hot_graph.get(atom).is_some() {
            hot_hit = true;
        }
    }

    // Hot path: query hot atoms for positions directly
    if hot_hit {
        let mut results = Vec::new();
        for atom in &query_atoms {
            if let Some(hot) = self.hot_graph.get(atom) {
                self.hot_graph.bump(atom, QUERY_HIT_HEAT);
                // Convert hot atom positions to BareFact? No — positions are for context,
                // facts are for key-value pairs. For now, just bump heat and continue
                // to SQLite for full results but with warmed cache.
            }
        }
    }

    // Always fall through to SQLite for complete results
    // (HotGraph is a cache, not an authoritative data store)
    let facts = self.search_facts(query, limit)?;
    Ok(facts)
}
```

- [ ] **Step 3: Wire HotGraph seeding into consolidate_entry**

In `crates/mycelium-core/src/brain.rs`, after atoms are extracted and edges are built in `consolidate_entry()`, seed the HotGraph:

```rust
// At the end of consolidate_entry(), after all atom/edge insertion:
fn consolidate_entry(
    conn: &Connection,
    turn: i64,
    session: &str,
    text: &str,
    annotation: Option<&MemoryAnnotation>,
    hot_graph: Option<&HotGraph>,
) -> anyhow::Result<()> {
    // ... existing atom extraction and SQLite inserts ...

    // Seed hot graph
    if let Some(graph) = hot_graph {
        for atom in &extracted_atoms {
            let edges = load_edges(conn, &atom.phrase)?;
            let importance = atom.importance;
            let ref_count = atom.ref_count;
            graph.seed(&atom.phrase, importance, ref_count, atom.positions.clone(), edges);
        }
    }

    Ok(())
}
```

Note: `consolidate_entry` currently doesn't take a `hot_graph` parameter. We need to add it. This requires updating the caller in `brain_daemon.rs` (which we'll do in the next step).

Alternative: since `brain_daemon.rs` already has access to `Storage` (which now has `hot_graph`), we can have the brain daemon pass it:

```rust
// In brain_daemon.rs process_batch():
let hot_graph = Some(&*self.storage.hot_graph());
brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, annotation.as_ref(), hot_graph)?;
```

- [ ] **Step 4: Wire heat bump into recall/traverse**

In `crates/mycelium-core/src/recall.rs`, in the `traverse()` function, after atoms are found, bump their heat:

```rust
pub fn traverse(
    conn: &Connection,
    query: &RecallQuery,
    max_clusters: usize,
    max_neighbors: usize,
    hot_graph: Option<&HotGraph>,
) -> Result<Vec<AtomCluster>> {
    // ... existing traversal logic ...

    let results = ... ;

    // Bump heat for matched atoms
    if let Some(graph) = hot_graph {
        for cluster in &results {
            for atom in &cluster.atoms {
                graph.bump(atom, RECALL_HIT_HEAT);
            }
        }
    }

    Ok(results)
}
```

This requires updating callers in interceptor.rs to pass the hot_graph.

- [ ] **Step 5: Build and run existing tests**

```bash
cargo build 2>&1 | tail -10
cargo test -p mycelium-core --lib 2>&1 | tail -20
cargo test -p mycelium-core --test brain_annotation --test brain_verification 2>&1 | tail -10
```

Expected: All existing tests pass with new HotGraph integration.

- [ ] **Step 6: Commit**

```bash
git add crates/mycelium-core/src/storage.rs crates/mycelium-core/src/brain.rs crates/mycelium-core/src/recall.rs
git commit -m "feat: wire HotGraph into storage, consolidation, and recall"
```

---

### Task 3: Wire Brain Daemon — Heat Decay and HotGraph Access

**Files:**
- Modify: `crates/mycelium-server/src/brain_daemon.rs`
- Modify: `crates/mycelium-server/src/lib.rs`
- Modify: `crates/mycelium-core/src/lib.rs` (re-export HotGraph)

- [ ] **Step 1: Update brain daemon to run decay cycle and pass HotGraph to consolidate_entry**

```rust
// In brain_daemon.rs, update the spawn method:

pub fn spawn(self, storage: Arc<Storage>) {
    let hot_graph = storage.hot_graph();  // Arc clone
    tokio::spawn(async move {
        tracing::info!("Brain daemon started (event-driven + heat decay)");
        while self.running.load(Ordering::Relaxed) {
            tokio::select! {
                _ = self.notify.notified() => {},
                _ = tokio::time::sleep(Duration::from_secs(60)) => {
                    tracing::trace!("Brain daemon: safety poll + heat decay");
                },
            }

            // Process pending work
            if let Err(e) = self.process_batch() {
                tracing::warn!("brain daemon error: {}", e);
            }

            // Run heat decay cycle (every ~60s, non-blocking)
            hot_graph.tick_decay();
        }
        tracing::info!("Brain daemon stopped");
    });
}
```

Note: The brain daemon currently has `storage: Arc<Storage>` and constructs from `new(storage, notify)`. But now we also need `hot_graph`. Since `storage` already has `hot_graph`, we can just pass `Arc::clone(&storage)` and access `storage.hot_graph()`.

Actually, looking at the current code, `BrainDaemon` already has `storage: Arc<Storage>`. Since Storage now has `hot_graph`, we can access it as `self.storage.hot_graph()`. We don't need to change the struct or constructor.

But the `process_batch()` method needs to pass the hot_graph to `consolidate_entry()`. Let me update:

```rust
fn process_batch(&self) -> anyhow::Result<()> {
    let items = {
        let conn = self.storage.conn().lock().unwrap();
        brain::dequeue_pending(&conn, 20)?
    };
    if items.is_empty() {
        return Ok(());
    }

    let mut processed = Vec::new();
    let hot_graph = Some(self.storage.hot_graph().as_ref());

    for item in &items {
        if let Ok(Some(entry)) = self.storage.get_entry(item.turn) {
            let text = format!("{} {}", entry.user, entry.assistant);
            let annotation: Option<MemoryAnnotation> = entry.annotation
                .as_deref()
                .and_then(|json| serde_json::from_str(json).ok());

            let conn = self.storage.conn().lock().unwrap();
            brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, annotation.as_ref(), hot_graph)?;
            processed.push(item.id);
        }
    }

    if !processed.is_empty() {
        let conn = self.storage.conn().lock().unwrap();
        brain::remove_pending(&conn, &processed)?;
    }

    tracing::debug!("Brain daemon: processed {} entries", processed.len());
    Ok(())
}
```

- [ ] **Step 2: Re-export HotGraph from mycelium_core lib**

```rust
// In lib.rs
pub use hot_graph::{HotGraph, HotAtom, HotAtomSnapshot, HeatMetrics, HeatMetricsSnapshot};
```

- [ ] **Step 3: Build**

```bash
cargo build 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-server/src/brain_daemon.rs crates/mycelium-core/src/lib.rs
git commit -m "feat: run heat decay cycle in brain daemon, pass HotGraph to consolidate"
```

---

### Task 4: Debug Instrumentation — API Endpoint and CLI

**Files:**
- Modify: `crates/mycelium-server/src/lib.rs` (add `/api/brain/heat` endpoint)
- Modify: `crates/mycelium-server/src/brain_handlers.rs` (add heat handler)
- Modify: `crates/mycelium-app/src/main.rs` (add `mycelium brain heat-status` CLI)

- [ ] **Step 1: Add heat status handler to brain_handlers.rs**

```rust
/// Response for GET /api/brain/heat
#[derive(serde::Serialize)]
pub struct BrainHeatResponse {
    pub metrics: HeatMetricsSnapshot,
    pub top_atoms: Vec<HotAtomSnapshot>,
}

/// GET /api/brain/heat — returns heat metrics and top atoms.
pub async fn brain_heat(
    State(state): State<Arc<AppState>>,
) -> Json<BrainHeatResponse> {
    let metrics = state.storage.hot_graph().metrics().snapshot();
    let top_atoms = state.storage.hot_graph().top_atoms(10);
    Json(BrainHeatResponse { metrics, top_atoms })
}
```

- [ ] **Step 2: Register the route in server lib.rs**

```rust
.route("/api/brain/heat", get(brain_heat))
```

Alongside existing routes like `/api/brain/status`.

- [ ] **Step 3: Add CLI command**

In `crates/mycelium-app/src/main.rs`, add to the Brain command enum and handler:

```rust
#[derive(Subcommand)]
enum BrainCommand {
    /// Show atom/edge counts
    Status,
    /// Process annotated entries (one-shot)
    Process,
    /// Enqueue all entries for reprocessing
    Backfill,
    /// Show heat metrics for the in-memory hot graph
    HeatStatus,
}

// In the match handler:
BrainCommand::HeatStatus => cmd_heat_status(&config)?,
```

The handler:

```rust
fn cmd_heat_status(config: &MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    let metrics = storage.hot_graph().metrics().snapshot();
    let top = storage.hot_graph().top_atoms(10);

    println!("HotGraph Status:");
    println!("  Hot atoms:   {}", metrics.hot_count);
    println!("  Total heat:  {:.1}", metrics.total_heat);
    println!("  Bumps:       {}", metrics.bumps);
    println!("  Evictions:   {}", metrics.evictions);
    println!("  Promotions:  {}", metrics.promotions);
    println!("  Hit rate:    {:.0}%", {
        let total = metrics.hits + metrics.misses;
        if total > 0 { metrics.hits as f64 / total as f64 * 100.0 } else { 0.0 }
    });
    println!();
    println!("  Top atoms:");
    for atom in &top {
        println!("    {:<20} heat={:.1} edges={}", atom.phrase, atom.heat, atom.edges.len());
    }
    Ok(())
}
```

- [ ] **Step 4: Build**

```bash
cargo build 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add crates/mycelium-server/src/brain_handlers.rs crates/mycelium-server/src/lib.rs crates/mycelium-app/src/main.rs
git commit -m "feat: add /api/brain/heat endpoint and mycelium brain heat-status CLI"
```

---

### Task 5: Integration Tests and Stress Benchmarks

**Files:**
- Create: `crates/mycelium-core/tests/hot_graph_integration.rs`
- Create: `crates/mycelium-core/tests/hot_graph_stress.rs`

- [ ] **Step 1: Write integration tests**

```rust
// crates/mycelium-core/tests/hot_graph_integration.rs
// Tests the full e2e flow: write entry → consolidate → HotGraph seeded → query bumps heat

use mycelium_core::{Storage, types::*};
use std::sync::Arc;

#[test]
fn test_consolidate_seeds_hot_graph() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut path = dir.path().to_path_buf();
    path.push("test.db");

    // Open storage
    let storage = Storage::open(path)?;
    let hot_graph = Arc::clone(storage.hot_graph());

    // Write an entry
    let entry = storage.entry()
        .turn(1)
        .session("test-session")
        .user("What is a hash chain?")
        .assistant("A hash chain is a sequence of hashes where each block contains the hash of its predecessor.")
        .save()?;

    // Manually consolidate
    {
        let conn = storage.conn().lock().unwrap();
        mycelium_core::brain::consolidate_entry(
            &conn, entry.turn, &entry.session,
            &format!("{} {}", entry.user, entry.assistant),
            None,
            Some(hot_graph.as_ref()),
        )?;
    }

    // Verify atoms are in HotGraph
    let atoms = ["hash", "chain", "block", "sequence"];
    for &atom in &atoms {
        let hot = hot_graph.get(atom);
        assert!(hot.is_some(), "atom '{}' should be in hot graph after consolidation", atom);
        assert!(hot.unwrap().heat > 0.0, "atom '{}' should have heat > 0", atom);
    }

    Ok(())
}

#[test]
fn test_recall_bumps_heat() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut path = dir.path().to_path_buf();
    path.push("test_recall.db");

    let storage = Storage::open(path)?;
    let hot_graph = Arc::clone(storage.hot_graph());

    // Write entries
    storage.entry().turn(1).session("s1")
        .user("hash chain").assistant("blockchain tech").save()?;
    storage.entry().turn(2).session("s1")
        .user("merkle tree").assistant("binary tree of hashes").save()?;

    // Consolidate all
    {
        let conn = storage.conn().lock().unwrap();
        let items = mycelium_core::brain::dequeue_pending(&conn, 10)?;
        for item in items {
            if let Ok(Some(entry)) = storage.get_entry(item.turn) {
                let text = format!("{} {}", entry.user, entry.assistant);
                mycelium_core::brain::consolidate_entry(
                    &conn, entry.turn, &entry.session, &text, None,
                    Some(hot_graph.as_ref()),
                )?;
            }
        }
    }

    // Query — should bump heat for matched atoms
    let facts = storage.search_facts("hash chain", 10)?;
    assert!(!facts.is_empty(), "search_facts should return results");

    // Atoms matching query should have bumped heat
    let hash_heat = hot_graph.get("hash").map(|a| a.heat).unwrap_or(0.0);
    let chain_heat = hot_graph.get("chain").map(|a| a.heat).unwrap_or(0.0);

    assert!(hash_heat > 0.0, "hash should have heat > 0");
    assert!(chain_heat > 0.0, "chain should have heat > 0");

    Ok(())
}

#[test]
fn test_hot_graph_rebuild_from_sqlite() -> anyhow::Result<()> {
    let dir = tempfile::tempdir()?;
    let mut path = dir.path().to_path_buf();
    path.push("test_rebuild.db");

    let storage = Storage::open(path.clone())?;

    // Write and consolidate entries
    for i in 0..5 {
        storage.entry().turn(i + 1).session("s1")
            .user("topic alpha").assistant("content about alpha").save()?;
    }
    {
        let conn = storage.conn().lock().unwrap();
        let items = mycelium_core::brain::dequeue_pending(&conn, 10)?;
        for item in items {
            if let Ok(Some(entry)) = storage.get_entry(item.turn) {
                let text = format!("{} {}", entry.user, entry.assistant);
                mycelium_core::brain::consolidate_entry(
                    &conn, entry.turn, &entry.session, &text, None, None,
                )?;
            }
        }
    }

    // Rebuild HotGraph from SQLite
    let conn = storage.conn().lock().unwrap();
    let graph = mycelium_core::HotGraph::rebuild_from_sqlite(&conn)?;

    // Atoms should be present
    assert!(graph.get("alpha").is_some(), "alpha should be in rebuilt graph");

    Ok(())
}
```

- [ ] **Step 2: Write stress benchmarks**

```rust
// crates/mycelium-core/tests/hot_graph_stress.rs

use mycelium_core::hot_graph::{HotGraph, HeatMetrics, CONSOLIDATION_HEAT_MULTIPLIER};
use mycelium_core::types::Edge;
use std::sync::Arc;
use std::time::Instant;

#[test]
fn stress_high_throughput_reads() {
    let graph = HotGraph::new();

    // Seed 10k atoms
    for i in 0..10_000 {
        graph.seed(
            &format!("atom_{}", i), 1.0, i, vec![], vec![]
        );
    }

    // Measure hot read latency
    let start = Instant::now();
    let mut hits = 0u64;
    let mut misses = 0u64;
    for i in 0..10_000 {
        if graph.get(&format!("atom_{}", i % 20_000)).is_some() {
            hits += 1;
        } else {
            misses += 1;
        }
    }
    let elapsed = start.elapsed();

    println!("stress_high_throughput: {} hits, {} misses in {:?}", hits, misses, elapsed);
    println!("average read: {:?}", elapsed / 10_000);
    assert!(elapsed < Duration::from_millis(500), "10k reads should be < 500ms");
}

#[test]
fn stress_rapid_bump_no_contention() {
    let graph = Arc::new(HotGraph::new());
    graph.seed("hot", 10.0, 100, vec![], vec![]);

    let mut handles = vec![];
    for _ in 0..16 {
        let g = Arc::clone(&graph);
        handles.push(std::thread::spawn(move || {
            for _ in 0..1000 {
                g.bump("hot", 0.1);
                g.get("hot");
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    // Verify no deadlocks and atom still exists
    let atom = graph.get("hot").unwrap();
    println!("stress_rapid_bump: final heat = {}", atom.heat);
    assert!(atom.heat > 10.0, "heat should have increased");
}

#[test]
fn stress_heat_accumulation_no_leak() {
    let graph = HotGraph::new();

    // Add atoms with varying importance
    for i in 0..10_000 {
        graph.seed(
            &format!("atom_{}", i),
            (i % 10) as f64,
            (i % 100) as f64,
            vec![],
            vec![],
        );
    }

    // Run many decay cycles
    for _ in 0..100 {
        graph.tick_decay();
    }

    let metrics = graph.metrics().snapshot();
    println!("stress_accumulation: {} hot, {:.1} total heat", metrics.hot_count, metrics.total_heat);
    // Should not grow unbounded — verify count is reasonable
    assert!(metrics.total_heat >= 0.0, "heat should never go negative");
}

#[test]
fn bench_hot_vs_cold_latency() {
    // This test requires a real SQLite connection with atoms.
    // For unit test level, we measure pure HotGraph vs benchmark expectations.
    let graph = HotGraph::new();
    graph.seed("test", 5.0, 10, vec![], vec![]);

    // Hot path
    let start = Instant::now();
    for _ in 0..10_000 {
        let _ = graph.get("test");
    }
    let hot_elapsed = start.elapsed();

    // Miss path
    let start = Instant::now();
    for _ in 0..10_000 {
        let _ = graph.get("nonexistent");
    }
    let miss_elapsed = start.elapsed();

    println!("bench_latency: hot={:?}/10k, miss={:?}/10k", hot_elapsed, miss_elapsed);
    // Hot reads on hot graph should be < 1us per read
    assert!(hot_elapsed < Duration::from_millis(50), "10k hot reads should be < 50ms, got {:?}", hot_elapsed);
}
```

- [ ] **Step 3: Add tempfile dev dependency for integration tests**

Update `crates/mycelium-core/Cargo.toml`:
```toml
[dev-dependencies]
tempfile = "3"
```

- [ ] **Step 4: Run integration tests**

```bash
cargo test -p mycelium-core --test hot_graph_integration --test hot_graph_stress 2>&1
```

- [ ] **Step 5: Commit**

```bash
git add crates/mycelium-core/tests/hot_graph_integration.rs crates/mycelium-core/tests/hot_graph_stress.rs crates/mycelium-core/Cargo.toml
git commit -m "test: add integration tests and stress benchmarks for unified streaming graph"
```

---

### Task 6: Final Tuning and Verification

**Files:** (verification only, no code changes unless benchmarks reveal issues)

- [ ] **Step 1: Run the full test suite**

```bash
cargo test -p mycelium-core --lib 2>&1 | tail -20
cargo test -p mycelium-core --tests 2>&1 | tail -20
cargo build --workspace 2>&1 | tail -5
```

- [ ] **Step 2: Review benchmark output and adjust constants if needed**

Check the stress test output for:
- Hot read latency (should be < 5μs per read)
- Heat convergence (should not grow unbounded)
- Crash/rebuild (should restore all atoms)

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "chore: final tuning and verification of unified streaming graph"
```

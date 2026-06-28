# B+C: Unified Streaming Graph — Mycelium Hot Brain

**Date:** 2026-06-29
**Status:** Draft — Pre-Implementation Spec
**Priority:** Invention

## Problem

Mycelium's brain (atom/edge/position graph) is stored entirely in SQLite. Every recall query hits the database — even for atoms that were accessed moments ago. The existing moka cache helps at the entry level, but the *atom graph itself* has no hot cache, no prediction layer, and no self-adapting relevance signal.

The system has no way to answer: *"which memories will this user need next?"* — it only answers *"what matches this query?"* after you type it.

## Solution: Unified Streaming Graph

A single `heat: f64` value per atom serves as **both cache policy and prediction signal**:

- **Hot atoms** (heat > threshold) live in an in-memory `HashMap` — zero-ms reads
- **Heat spreads** along edges when an atom is accessed — this IS the speculation engine
- **Heat decays** over time — cold atoms naturally evict back to SQLite
- **No separate speculation engine** — the graph adapts to access patterns by itself

### The Invariant (must never break)

```
SQLite is source of truth. HotGraph is always disposable.
Crash → HotGraph is rebuilt from SQLite. Zero data loss.
```

---

## Architecture

```
     ┌──────────────────────────────────────────┐
     │             Consolidation                  │
     │  (already exists: extract atoms, edges)    │
     │  + now seeds HotGraph with heat            │
     └────┬──────────────┬───────────────────────┘
          │              │
          ▼              ▼
   ┌──────────┐   ┌──────────┐
   │ SQLite   │   │ HotGraph │  <── heat governs everything
   │ durable  │   │ L1 cache │
   │ cold     │   │ hot      │
   │ tier     │   │ tier     │
   └──────────┘   └──────────┘
                       │
               background decay
               cycle every 60s
                       │
                       ▼
              ┌────────────────┐
              │  Heat Engine    │
              │  spread → decay │
              │  → evict → pro. │
              └────────────────┘
```

---

## Data Structures

### HotGraph

```rust
/// The in-memory atom graph. Everything below EVICT_THRESHOLD is SQLite-only.
pub struct HotGraph {
    /// Hot atoms: phrase → hot atom with heat, edges, recent positions.
    atoms: RwLock<HashMap<String, HotAtom>>,
    /// Background task handle for the decay cycle.
    decay_handle: Mutex<Option<JoinHandle<()>>>,
    /// Wake signal from storage (reuse existing Notify).
    notify: Arc<Notify>,
    /// Metrics counter for debug/observability.
    metrics: Arc<HeatMetrics>,
}

/// A single atom in the hot graph.
pub struct HotAtom {
    pub phrase: String,
    pub heat: f64,
    pub importance: f64,
    pub ref_count: i64,
    pub edges: Vec<Edge>,
    pub positions: Vec<Position>,
    pub last_accessed: Instant,
}

/// A weighted connection between two atoms.
pub struct Edge {
    pub neighbor: String,
    pub weight: f64,
}
```

### Heat Constants

```rust
pub const DECAY_RATE: f64 = 0.95;          // × per minute → half-life ≈ 14 min
pub const SPREAD_FACTOR: f64 = 0.5;        // neighbor gets half of source's delta
pub const SPREAD_MINIMUM: f64 = 0.01;      // don't propagate noise
pub const EVICT_THRESHOLD: f64 = 0.1;      // atoms below this leave L1
pub const PROMOTE_THRESHOLD: f64 = 0.3;    // atoms above this from cold enter L1
pub const CONSOLIDATION_HEAT_MULTIPLIER: f64 = 1.5;
pub const QUERY_HIT_HEAT: f64 = 1.0;
pub const RECALL_HIT_HEAT: f64 = 0.5;
```

---

## Heat Mechanics

### Heat Sources

| Trigger | Heat added | When |
|---|---|---|
| New entry consolidated → atom extracted | `importance × 1.5` | After `consolidate_entry()` |
| Query matches atom (search/traverse) | `+1.0` | During recall |
| Atom returned in recall results | `+0.5` | During traverse |
| Edge spread from neighbor | `delta × 0.5 × weight` | During decay cycle |

### Heat Spread Formula (one level per cycle)

```
spread_to_neighbor = source_heat_increase × SPREAD_FACTOR × edge_weight
if spread_to_neighbor > SPREAD_MINIMUM:
    neighbor.heat += spread_to_neighbor
```

### Decay Cycle (every 60s)

```
for each atom in HotGraph:
    atom.heat ×= DECAY_RATE           // cool down
    if atom.heat > EVICT_THRESHOLD:
        spread heat to neighbors       // one level only
    else:
        evict atom from HotGraph       // stays in SQLite

for each cold atom in SQLite (sampled):
    if importance × ref_count > PROMOTE_THRESHOLD:
        promote to HotGraph with PROMOTE_THRESHOLD heat
```

---

## Integration Points

### `consolidate_entry()` — in `brain.rs`

After existing atom extraction and edge creation, seed HotGraph:

```rust
let hot_graph = ... // from Storage
let base_heat = importance * CONSOLIDATION_HEAT_MULTIPLIER;
hot_graph.seed(&phrase, &positions, &edges, base_heat, importance, ref_count);
```

### `traverse()` / recall — in `recall.rs`

When an atom is visited during recall traversal, bump its heat:

```rust
hot_graph.bump(&atom.phrase, RECALL_HIT_HEAT);
```

### `search()` / `search_facts()` — in `storage.rs`

Before SQLite lookup, check HotGraph:

```rust
// Fast path: check hot graph
if let Some(hot) = self.hot_graph.get(&phrase) {
    self.hot_graph.bump(&phrase, QUERY_HIT_HEAT);
    return Ok(hot.positions.clone());
}
// Cold path: SQLite
```

### Brain Daemon — in `brain_daemon.rs`

After each `process_batch()` (which runs consolidate), trigger heat decay:

```rust
// Inside the brain daemon loop, after consolidate
self.hot_graph.tick_decay();  // runs one decay cycle, non-blocking
```

---

## Debug Instrumentation

Every heat operation logs a structured trace event for observability:

```rust
pub struct HeatMetrics {
    /// Total atoms currently in HotGraph.
    pub hot_count: AtomicI64,
    /// Total atoms in SQLite (permanent).
    pub cold_count: AtomicI64,
    /// Sum of all heat values (for detecting decay drift).
    pub total_heat: AtomicF64,  // using atomic_float or relaxed ordering
    /// Counter of bump operations.
    pub bumps: AtomicU64,
    /// Counter of evictions per cycle.
    pub evictions: AtomicU64,
    /// Counter of promotions per cycle.
    pub promotions: AtomicU64,
    /// Histogram of heat values (logged every cycle).
    pub heat_samples: Mutex<Vec<f64>>,
    /// Latency of last N HotGraph lookups.
    pub lookup_times: Mutex<Vec<Duration>>,
    /// Hit rate: hot graph found / total lookups.
    pub hits: AtomicU64,
    pub misses: AtomicU64,
}
```

### Tracing Events

Every significant heat operation emits a `tracing::debug!` event:

```rust
// On bump
tracing::debug!(
    "heat:bump phrase={} delta={:.2} new_heat={:.2}",
    phrase, delta, new_heat
);

// On spread
tracing::debug!(
    "heat:spread {} -> {} via edge={:.2} delta={:.4}",
    source, target, weight, spread
);

// On evict
tracing::debug!(
    "heat:evict phrase={} heat={:.2}",
    phrase, heat
);

// On promote
tracing::debug!(
    "heat:promote phrase={} heat={:.2}",
    phrase, initial_heat
);
```

### /api/brain/heat endpoint

New REST endpoint for live inspection:

```json
GET /api/brain/heat

{
  "hot_count": 142,
  "cold_count": 3801,
  "total_heat": 847.3,
  "bumps_per_min": 23,
  "evictions_per_hour": 8,
  "promotions_per_hour": 12,
  "hit_rate": 0.78,
  "top_atoms": [
    {"phrase": "hash chain", "heat": 12.4, "importance": 3, "edges": 5},
    {"phrase": "merkle tree", "heat": 8.2, "importance": 3, "edges": 3},
  ],
  "histogram": {
    "0.1-0.5": 45,
    "0.5-1.0": 38,
    "1.0-5.0": 42,
    "5.0-10.0": 12,
    "10.0+": 5
  }
}
```

### Debug CLI Command

```
mycelium brain heat-status

HotGraph Status:
  Hot atoms:   142
  Cold atoms:  3801
  Hit rate:    78%
  Total heat:  847.3
  Top atoms:
    hash chain     heat=12.4  edges=5
    merkle tree    heat=8.2   edges=3
    consensus      heat=5.1   edges=7
```

---

## Testing Strategy

### Unit Tests

| Test | What it verifies |
|---|---|
| `test_heat_bump` | bump() adds heat, returns new value |
| `test_heat_spread` | bump atom A → neighbor B gets spread heat |
| `test_heat_spread_minimum` | tiny bumps don't propagate noise |
| `test_heat_decay` | decay cycle correctly multiplies all heats by DECAY_RATE |
| `test_heat_eviction` | atom below threshold is evicted from HotGraph |
| `test_heat_promotion` | cold atom above threshold is promoted to HotGraph |
| `test_heat_spread_chain` | 3-atom chain: heat spreads one level, not infinite cascade |
| `test_hot_graph_rebuild` | Drop HotGraph → rebuild from SQLite → all atoms present |
| `test_hot_graph_miss_fallback` | Query non-hot atom → falls through to SQLite |
| `test_hot_graph_hit_fallback` | Query hot atom → returns from HotGraph, bump called |
| `test_concurrent_bump_and_decay` | RwLock handles concurrent reads/writes |
| `test_heat_persistence` | Atoms in HotGraph match SQLite after crash |

### Integration Tests

| Test | What it verifies |
|---|---|
| `test_consolidate_seeds_heat` | Write entry → consolidate → atom appears in HotGraph with correct heat |
| `test_recall_bumps_heat` | Recall query → matched atoms get heat bump |
| `test_search_hot_path` | Search for hot atom → bypasses SQLite, returns positions |
| `test_heat_decay_background` | Brain daemon runs decay cycle → hot atoms cool over time |
| `test_heat_spread_e2e` | Write entry about X → query Y (neighbor of X) → Y has elevated heat |

### Penetration / Stress Tests

| Test | What it verifies |
|---|---|
| `stress_high_throughput_reads` | 10k random queries — measure hot path vs cold path latency |
| `stress_heat_accumulation` | 1000 rapid writes to the same session — ensure heat doesn't overflow or cause memory leak |
| `stress_crash_recovery` | Kill process mid-decay — rebuild HotGraph from SQLite — verify data integrity |
| `stress_many_atoms` | 100k atoms in SQLite, 10k hot — verify eviction/promotion ratios |
| `stress_rapid_bump` | Same atom bumped 10k times in 1 second — no contention, no deadlock |
| `stress_miss_heavy_workload` | 90% cold miss rate — verify SQLite fallback path doesn't degrade |
| `stress_concurrent_access` | 16 threads: reads + bumps + decay simultaneously — no panics |

### "Pentest" — Behavioral Validation

These tests verify the feature DOES something measurable:

| Test | What it verifies |
|---|---|
| `bench_hot_vs_cold_latency` | Hot read should be **< 5μs**. Cold read (SQLite) is **~500μs**. Hot path must be 100x+ faster. |
| `bench_hit_rate_evolution` | Write 100 entries, query 10 of them repeatedly. After 50 queries, hit rate should be >60%. |
| `bench_heat_spread_effect` | Query atom A 10 times. Atom B (A's neighbor) should have higher heat than unrelated atom C. |
| `bench_detect_memory_leak` | Run 1hr with steady write rate. HotGraph count should plateau, not grow unbounded. |
| `bench_decay_convergence` | After 2hr of no queries, all atoms should be below EVICT_THRESHOLD. |
| `bench_crash_restart_speed` | Measure time to rebuild HotGraph from SQLite with 1k/10k/100k atoms. |

### Success Criteria (Hard Gates)

| Metric | Target | How Measured |
|---|---|---|
| Hot read latency | < 5μs (mean) | `bench_hot_vs_cold_latency` |
| Cold read latency | < 1ms (mean) | Same benchmark — SQLite must not regress |
| Hit rate after warmup | > 60% | Simulate 10 sessions × 20 queries each |
| Heat leak | < 1% per hour | `total_heat` drift over 1hr steady state |
| HotGraph memory | < 100MB for 10k atoms | `stress_many_atoms` |
| Crash recovery | All atoms present | Random SIGKILL → rebuild → compare |
| No unbounded growth | HotGraph count plateau | `bench_detect_memory_leak` (1hr) |
| No deadlocks | 0 panics | `stress_concurrent_access` (16 threads, 60s) |

---

## Implementation Plan (Draft Phases)

1. **HotGraph struct + heat primitives** (brain.rs: new module): `HotGraph` struct, `seed()`, `bump()`, `get()`, edges from SQLite
2. **Heat engine** (brain.rs): `tick_decay()` — spread, decay, evict, promote
3. **Integration into Storage** (storage.rs): `hot_graph` field, L1 lookup in `get_entry`
4. **Integration into consolidation** (brain.rs): Wire `seed()` into `consolidate_entry()`
5. **Integration into recall** (recall.rs): Wire `bump()` into `traverse()`
6. **Debug instrumentation** (brain.rs + new endpoint): `HeatMetrics`, `/api/brain/heat`, CLI command
7. **Unit tests** — all heat mechanics
8. **Integration tests** — e2e flows
9. **Stress/pentest suite** — benchmarks + behavioral validation
10. **Final tuning** — adjust constants based on benchmark results, remove any dead code, add `#[allow(dead_code)]` only where truly needed

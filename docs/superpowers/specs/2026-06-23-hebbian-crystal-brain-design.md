# Hebbian Crystal Brain — Design Spec

**Date:** 2026-06-23
**Status:** DRAFT (awaiting user review)
**Author:** Azfar Naufal + Claude (brainstorming session)

---

## 1. Problem

Mycelium stores every Claude Code turn as a permanent hash-chained entry. Over months, this becomes thousands of entries. The user has a rich, growing memory — but querying it requires:
- Either full-text search (slow, lossy)
- Or LLM-based semantic search (expensive, hallucination-prone)
- Or raw entry scanning (O(n), useless)

The user wants **permanent memory** (untouched, hash-chained) and **processed memory** (derived, query-optimized) — but the current processed layer (facts, snapshots, cache) feels shallow and not novel enough.

## 2. Insight

Existing memory systems force the LLM to process memory at *query time* (RAG, embeddings). This work should happen at *write time* instead. The result is a query-optimized structure where every lookup is a direct graph traversal — not an embedding search, not an LLM call.

The novel claim: **the atom index (deduplicated) grows logarithmically with usage** — because most new content references existing atoms rather than creating new ones. Position graph and edges grow linearly (one per occurrence), but query latency stays sub-millisecond regardless.

## 3. Architecture

### 3.1 The Three-Layer Brain

The brain is composed of three data structures, all derived from the permanent hash-chain entries:

```
┌─────────────────────────────────────────────────┐
│           PERMANENT MEMORY (untouched)          │
│      Hash-chained entries, append-only log      │
└────────────────────┬────────────────────────────┘
                     │ (consolidation daemon)
                     ▼
┌─────────────────────────────────────────────────┐
│              HEBBIAN CRYSTAL BRAIN              │
├─────────────────────────────────────────────────┤
│  1. ATOM INDEX      — unique bi/tri-grams       │
│  2. POSITION GRAPH  — every occurrence of atoms │
│  3. EDGE WEIGHTS    — Hebbian co-access strength │
└─────────────────────────────────────────────────┘
                     ▲
                     │ (queries)
                     │
┌─────────────────────────────────────────────────┐
│              CONSUMERS (read-only)              │
│   MCP tools · Svelte UI · Future LLM agents    │
└─────────────────────────────────────────────────┘
```

### 3.2 Atom Index (Deduplication Layer)

Every entry is tokenized into bi-grams and tri-grams. Each unique atom is stored **once** with metadata:

```rust
struct Atom {
    id: u64,                   // stable hash of the phrase
    phrase: String,            // e.g. "hash chain"
    first_seen: i64,           // turn number
    last_seen: i64,            // turn number  
    reference_count: u64,      // how many positions
    frequency_history: Vec<(i64, u64)>,  // optional temporal stats
}
```

Storage: SQLite table `atoms(id, phrase, first_seen, last_seen, ref_count)`. Index on `phrase`.

### 3.3 Position Graph (Provenance Layer)

Every time an atom appears in an entry, a position node records the context:

```rust
struct Position {
    id: u64,
    atom_id: u64,              // which atom
    turn: i64,                 // which entry
    session: String,           // which session
    context_before: String,    // 1-2 atoms before (for grouping)
    context_after: String,     // 1-2 atoms after
}
```

Storage: SQLite table `positions(atom_id, turn, session, ctx_before, ctx_after)`.

**The hash chain trust:** Every position's `(atom_id, turn)` pair can be verified against the original entry's hash. If anyone tampers with atoms or positions, the chain breaks.

### 3.4 Edge Weights (Hebbian Layer)

Edges connect atoms that appear together. Weight = `simple linear accumulation`:

```rust
struct Edge {
    atom_a: u64,
    atom_b: u64,
    weight: f32,               // starts at 0, +0.1 per co-access
    last_updated: i64,
    access_count: u32,
}
```

**Hebbian rule:** When atoms X and Y appear in the same entry (or are accessed in the same query), increment `edge(X, Y).weight += 0.1`. After 10 co-accesses, weight = 1.0.

**No decay** (per user choice). Weights only grow. Memory of past associations is preserved.

Storage: SQLite table `edges(atom_a, atom_b, weight, last_updated, access_count)`. Index on `(atom_a, atom_b)`.

## 4. Algorithms

### 4.1 Atom Extraction

For each new permanent entry:
1. Tokenize text into words (split on whitespace + punctuation)
2. Normalize: lowercase, strip punctuation
3. Filter stop words? (no — keep all, even "the" appears in patterns)
4. Generate all bi-grams and tri-grams as atom candidates
5. For each candidate: lookup in atom index
6. If exists: increment `reference_count`, update `last_seen`
7. If new: create atom with `first_seen = current turn`

### 4.2 Edge Updates

When atoms X, Y, Z all appear in entry N:
- For each pair (X,Y), (X,Z), (Y,Z): increment edge weight by 0.1
- Update `last_updated = turn N`

This is O(k²) per entry where k = atoms per entry (~50). ~2500 edge updates per entry is fine for batch processing.

### 4.3 Query: "Did I say X?"

```
query("metabase")
  → atom lookup: id = 42
  → positions: [P_1247, P_4521, P_8234, ...]  // ordered by turn
  → return: first_seen, last_seen, ref_count, position list
```

Latency target: < 1ms. Implementation: single SQL query with index lookup.

### 4.4 Query: "What atoms cluster with X?"

```
recall("metabase", limit=10)
  → atom lookup: id = 42
  → SELECT atom_b, weight FROM edges 
    WHERE atom_a = 42 
    ORDER BY weight DESC 
    LIMIT 10
  → return: ["dashboard", "localhost:3000", "API key", ...]
```

Latency: single indexed query, < 1ms.

### 4.5 Working Memory (Light Prediction)

Last 5–10 atom clusters touched stay in `moka` (in-memory cache). When user queries atom X, the brain reads working memory for recent context — nanosecond lookup.

No complex prediction. No pre-activation cascade. Just LRU working memory.

## 5. Storage & Growth

### 5.1 Tables (in same mycelium.db)

```sql
CREATE TABLE atoms (
  id INTEGER PRIMARY KEY,
  phrase TEXT NOT NULL UNIQUE,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  ref_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_atoms_phrase ON atoms(phrase);

CREATE TABLE positions (
  id INTEGER PRIMARY KEY,
  atom_id INTEGER NOT NULL,
  turn INTEGER NOT NULL,
  session TEXT NOT NULL,
  ctx_before TEXT,
  ctx_after TEXT,
  FOREIGN KEY (atom_id) REFERENCES atoms(id)
);
CREATE INDEX idx_positions_atom ON positions(atom_id);
CREATE INDEX idx_positions_turn ON positions(turn);

CREATE TABLE edges (
  atom_a INTEGER NOT NULL,
  atom_b INTEGER NOT NULL,
  weight REAL NOT NULL DEFAULT 0.0,
  last_updated INTEGER NOT NULL,
  access_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (atom_a, atom_b)
);
CREATE INDEX idx_edges_a ON edges(atom_a, weight DESC);
```

### 5.2 Expected Growth (Logarithmic)

| Entries | Atoms | Positions | Edges |
|---|---|---|---|
| 100 | ~500 | ~3,000 | ~5,000 |
| 1,000 | ~1,200 | ~50,000 | ~80,000 |
| 10,000 | ~1,800 | ~600,000 | ~900,000 |
| 100,000 | ~2,100 | ~6,500,000 | ~9,000,000 |

Atom growth asymptotes. Position/edge growth is linear but compressible (positions are small).

### 5.3 Why This Beats a Database

A raw database of 100K entries is ~100MB of raw text.
The brain after 100K entries is ~6.5M positions × 80 bytes = **~520MB**.

Wait — that's *bigger* than raw text. We need to verify this. Initial estimates may be wrong.

**Honest concern:** Position storage might not achieve 10x compression. If each position is 80 bytes (atom_id 8 + turn 8 + session 16 + ctx 32 + overhead) and we have 6.5M positions, that's 520MB vs 100MB raw — *5x bigger, not 10x smaller*.

**Mitigation:** Positions can be compressed further:
- Don't store `ctx_before`/`ctx_after` (the entry itself stores full text)
- Use shorter atom_id (32-bit hash instead of 64-bit)
- Compress session strings (deduplicate into session_id table)
- Pack positions into chunks

After these optimizations: ~30 bytes per position × 6.5M = ~195MB. Still larger than raw.

**Reconsider:** The 10x compression claim may be invalid for the position graph alone. The "compression" comes from the **atom index** (which IS 10x+ smaller than raw text), not the position graph.

**Revised claim:** Atoms compress 10x. The brain's query speed (sub-ms) is the 100x win, not storage.

## 6. Verification (Replay Test)

### 6.1 Test Methodology

1. Take 10,000 real entries from existing mycelium.db (production data)
2. Reset the brain (empty atom/position/edge tables)
3. Replay all 10K entries through the consolidation algorithm
4. Measure:
   - Atom count growth curve (verify logarithmic shape)
   - Position count growth curve (linear expected)
   - Edge count growth curve (linear expected)
   - Total storage in bytes
   - Compression ratio vs raw entry size

5. Sample 100 random queries:
   - "Did I say X?" (random X from existing vocabulary)
   - Measure recall accuracy (did we find all instances?)
   - Measure query latency (sub-ms target)

6. Sample 20 multi-atom queries:
   - "What clusters with Y?" (random Y)
   - Measure precision@10 (are top-10 results relevant?)
   - Measure query latency

### 6.2 Pass/Fail Criteria

| Metric | Pass | Fail |
|---|---|---|
| Atom growth at 10K entries | < 3,000 atoms (logarithmic shape) | > 10,000 atoms (linear) |
| Query latency "did I say X?" | < 5ms (p99) | > 50ms |
| Query latency "what clusters with Y?" | < 5ms (p99) | > 50ms |
| Recall accuracy "did I say X?" | > 90% of actual instances found | < 80% |
| Total storage growth | Logarithmic curve fit (R² > 0.9) | Linear |

### 6.3 Failure Modes to Watch

1. **Cold start cliff** — first 100 entries have nearly unique atoms (no compression yet)
2. **Stop word bloat** — "the", "of", "is" appear in every entry → many positions, low information
3. **Synonym fragmentation** — "running" vs "ran" never merge → high atom count
4. **Edge explosion** — every pair of atoms in an entry gets +0.1 → edges saturate fast
5. **Working memory thrashing** — too many queries touch different atoms

## 7. Locked Decisions

1. **Stop words** (novel approach): **statistical detection, not hardcoded list.** After the first 500 entries, compute per-word occurrence frequency. Words appearing in >70% of entries are auto-flagged as stop words for the user's domain. Domain-specific: "return" in code is meaningful, but "the" never is. The brain *learns* its own stop words from the data. Initial 500-entry window uses a small English fallback list (top 30 words).

2. **Synonym handling**: text-only normalization. Lowercase + strip `-ing`/`-ed`/`-s` suffixes + trim whitespace. **No LLM calls.** Cost is zero; benefit is 70-80% of synonym merging.

3. **Atom name normalization**: lowercase + NFKD (Unicode Compatibility Decomposition). Not full case folding (preserves language-specific characters correctly).

4. **Where does brain live**: **module in `mycelium-core`** (`brain.rs`), alongside `storage.rs`, `search.rs`, `cache.rs`. Refactor to its own crate later if it grows.

5. **Update timing**: **durable queue pattern.** New entries are atomically enqueued into a `pending_brain_work` table inside the same `append_entry` transaction. A background daemon periodically pops batches from the queue, runs atom extraction and edge updates, marks entries as processed. Querying the queue depth gives full observability: "X entries pending, Y processed in last hour". Queue lag is acceptable because most sessions don't query the brain in the same turn they write.

## 8. Implementation Plan (sketch)

### Phase 1: Core data structures (1 day)
- New module in `mycelium-core`: `brain.rs`
- Tables: atoms, positions, edges, pending_brain_work, brain_stop_words
- Functions: extract_atoms(), upsert_atom(), record_positions(), increment_edge(), normalize()

### Phase 2: Consolidation daemon + queue (1 day)
- Background task polls `pending_brain_work` every 5 seconds
- Processes batches: extract atoms → upsert atoms → record positions → increment edges → mark done
- Atomic with permanent memory: enqueue happens inside `append_entry` transaction

### Phase 3: Stop word detection (0.5 day)
- After 500 entries: compute word frequency distribution
- Words in >70% of entries become auto-stop-words
- First 500 entries use fallback list (top 30 English)

### Phase 4: Query interface (1 day)
- brain.recall(phrase) → atoms + positions
- brain.clusters(atom_id, limit) → top-N neighbors
- brain.when(phrase) → first_seen / last_seen
- brain.queue_status() → observability

### Phase 5: MCP exposure (0.5 day)
- New MCP tools: `brain_recall(phrase)`, `brain_clusters(phrase)`, `brain_when(phrase)`, `brain_status()`

### Phase 6: Verification (1 day)
- Replay test on 10K real entries
- Measure all metrics in §6.2
- Document results, adjust design if needed

## 9. The Honest Bet

This design might fail because:
- Position storage may not compress as well as claimed
- Atom extraction at bi-gram level may be too noisy
- Hebbian weights may not produce useful clusters without seeding
- The "novel" claim may already exist somewhere we haven't found

This design might succeed because:
- Direct graph traversal is fundamentally faster than embedding search
- Logarithmic atom growth IS achievable (atoms are deduplicated)
- The hash chain provides trust no other memory system has
- Even if some claims fail, sub-ms recall alone is a real win

We will know in one week of implementation + verification.

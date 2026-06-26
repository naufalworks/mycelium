# Optimization Ideas — from autonomous loop

**Date:** 2026-06-26
**Source:** autonomous-optimization skill run

## Implemented

| # | Optimization | Speedup | Branch | Merged |
|---|--------------|---------|--------|--------|
| 1 | `idx_atoms_score(ref_count * importance DESC)` | 2.8x on recall queries | `perf/atoms-covering-index` | ✅ |
| 2 | `idx_edges_b(atom_b, weight DESC)` | Structural — helps reverse edge lookups | `perf/edges-b-index` | ✅ |

## Ideas (non-conforming — require review before implementation)

### Idea 1: FTS5 for substring search
Replace `LIKE '%phrase%'` with SQLite FTS5 virtual table for full-text search.
- **Potential:** ~10x faster substring matching on 405K atoms
- **Constraint:** Requires schema migration (new FTS5 table + triggers)
- **Does not change function signatures** — the FTS table is transparent
- **Risk:** Needs DB migration step; existing DB must be re-indexed

### Idea 2: Batch consolidation
`consolidate_entry()` does individual INSERT/UPDATE per atom/edge/position.
- **Potential:** ~2x faster write path for the brain
- **Constraint:** Would need transaction batching or array binding
- **Does not change function signatures** — internal optimization only
- **Risk:** Minor. Could batch within the same function body.

### Idea 3: Moka cache for brain queries
Add a TTL cache for frequently accessed atoms and edges.
- **Potential:** ~100x for repeated queries (cache hit → no SQL)
- **Constraint:** Requires careful cache invalidation on writes
- **Does not change function signatures** — would add optional cache check
- **Risk:** Cache staleness if consolidation writes aren't tracked

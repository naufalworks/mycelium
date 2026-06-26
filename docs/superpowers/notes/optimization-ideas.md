# Optimization Ideas — from autonomous loop

**Date:** 2026-06-26
**Source:** autonomous-optimization skill run

## Implemented (all merged to main)

| # | Optimization | Speedup | Technique | Branch |
|---|--------------|---------|-----------|--------|
| 1 | `idx_atoms_score` expression index | **2.8x** on recall queries | Database | `perf/atoms-covering-index` |
| 2 | `idx_edges_b` reverse edge index | Structural — reverse lookups | Database | `perf/edges-b-index` |
| 3 | Semantic heat diffusion cache | **0ms** for cached atom neighbors | Graph-predictive caching | `perf/heat-diffusion` |
| 4 | LSM-style transaction merge | **~18%** faster consolidation | Batch writes | `perf/lsm-merge-buffer` |

## Skipped (during loop — issues found)

### Bloom Filter Cascade / In-memory phrase index
- Problem: Global static state (`OnceLock<Mutex<Vec<String>>>`) causes test pollution
- In-memory DB tests share global state, producing incorrect results
- Would need a per-connection cache instead of global static
- Worth revisiting with a different approach (ask user before re-attempting)

## Ideas (require review before implementation)

### Idea 1: FTS5 for substring search (10x+)
Replace `LIKE '%phrase%'` with SQLite FTS5 virtual table.
- Constraint: Requires schema migration (new FTS5 table + triggers)
- Does not change function signatures
- Risk: Needs DB migration step

### Idea 2: Per-connection bloom cache
Instead of global static, attach bloom filter to Connection via rusqlite hook.
- More complex but avoids test pollution
- Each connection gets its own independent cache

#!/usr/bin/env python3
"""
benchmark_report.py — Generate markdown benchmark report for Mycelium v3.

Reads benchmark_results.json (from benchmark_v3.py) and produces
BENCHMARK_V3.md with metrics tables, pass/fail, and analysis.

Usage:
    cd ~/Documents/mycelium && python3 scripts/benchmark_report.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mycelium_lib import MYCELIUM

RESULTS_FILE = MYCELIUM / "benchmark_results.json"
REPORT_FILE = MYCELIUM / "BENCHMARK_V3.md"


def load_results() -> dict:
    if not RESULTS_FILE.exists():
        print(f"ERROR: {RESULTS_FILE} not found. Run benchmark_v3.py first.")
        sys.exit(1)
    with open(RESULTS_FILE) as f:
        return json.load(f)


def _pass_fail(actual, target, direction="below"):
    """Return ✅ or ❌ based on whether actual meets target."""
    if direction == "below":
        return "✅" if actual <= target else "❌"
    elif direction == "above":
        return "✅" if actual >= target else "❌"
    return "❓"


def generate_report(results: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    r = results

    # Extract key metrics
    resume = r.get("resume", {})
    storage = r.get("storage", {})
    bloom = r.get("bloom", {})
    graph = r.get("graph", {})
    negation = r.get("negation", {})

    # Pass/fail checks
    checks = []

    # Resume: new avg < 1ms
    resume_pass = _pass_fail(resume.get("new_avg_ms", 999), 1.0, "below")
    checks.append(("Resume speed", f"{resume.get('new_avg_ms', '?')}ms", "<1ms", resume_pass))
    checks.append(("Resume speedup", f"{resume.get('speedup', '?')}x", "≥50x",
                     _pass_fail(resume.get("speedup", 0), 50, "above")))

    # Storage: new size < 70KB (for 677 entries ~ 346KB raw)
    storage_pass = _pass_fail(storage.get("new_kb", 9999), 70, "below")
    checks.append(("Storage size", f"{storage.get('new_kb', '?')}KB", "<70KB", storage_pass))
    checks.append(("Compression ratio", f"{storage.get('compression_ratio', '?')}x", "≥5x",
                     _pass_fail(storage.get("compression_ratio", 0), 5, "above")))

    # Bloom: < 0.01ms = 10μs
    bloom_us = bloom.get("bloom_avg_us", 999)
    bloom_pass = _pass_fail(bloom_us, 10, "below")
    checks.append(("Bloom check speed", f"{bloom_us:.1f}μs", "<10μs", bloom_pass))
    checks.append(("Bloom speedup", f"{bloom.get('speedup', '?')}x", "≥500x",
                     _pass_fail(bloom.get("speedup", 0), 500, "above")))

    # Graph: < 1ms
    graph_pass = _pass_fail(graph.get("avg_ms", 999), 1.0, "below")
    checks.append(("Graph query speed", f"{graph.get('avg_ms', '?')}ms", "<1ms", graph_pass))

    # Negation: < 1ms
    neg_combined = negation.get("combined_avg_ms", 999)
    neg_pass = _pass_fail(neg_combined, 1.0, "below")
    checks.append(("Negation check", f"{neg_combined:.3f}ms", "<1ms", neg_pass))

    total_pass = sum(1 for c in checks if c[3] == "✅")
    total_checks = len(checks)

    # Build report
    md = f"""# Mycelium v3 Benchmark Report

Generated: {now}

## Summary

**{total_pass}/{total_checks}** targets met.

| Metric | Actual | Target | Status |
|--------|--------|--------|--------|
"""
    for name, actual, target, status in checks:
        md += f"| {name} | {actual} | {target} | {status} |\n"

    md += f"""
---

## 1. Resume Speed

The v3 resume replaces full JSONL scan with LSM-tree L0 in-memory lookup
+ tier-priority filtering.

| | Old (JSONL scan) | New (LSM L0) |
|--|-------------------|--------------|
| Avg time | {resume.get('old_avg_ms', '?')}ms | {resume.get('new_avg_ms', '?')}ms |
| Speedup | — | **{resume.get('speedup', '?')}x** |
| Target | 50ms | <1ms |
| Iterations | {resume.get('iterations', '?')} | {resume.get('iterations', '?')} |

**How it works:** L0 is an in-memory dict (O(1) turn lookup). The last {resume.get('log_entries', '?')} entries
are loaded from JSONL into L0 on init. Resume pulls L0 entries, sorts by tier
(S→A→B), and packs into token budget. No disk I/O for resume.

---

## 2. Storage Size

LSM tiers compress older data: L0 keeps hot data in memory, L1 uses gzip-compressed
JSONL segments, L2 stores one-line summaries.

| | Raw JSONL | LSM Total |
|--|-----------|-----------|
| Size | {storage.get('old_kb', '?')}KB | {storage.get('new_kb', '?')}KB |
| L0 (hot) | — | {round(storage.get('l0_bytes', 0) / 1024, 1)}KB |
| L1 (warm, gzip) | — | {round(storage.get('l1_bytes', 0) / 1024, 1)}KB |
| L2 (cold, summary) | — | {round(storage.get('l2_bytes', 0) / 1024, 1)}KB |
| Compression | — | **{storage.get('compression_ratio', '?')}x** |

**How it works:** On flush (L0→L1), entries are gzip-compressed (typically 3-5x).
On compaction (L1→L2), full text is replaced with 120-char summaries.
Hash chain integrity preserved across all levels.

---

## 3. Bloom Filter

O(1) probabilistic entity membership checks via double-hashed bit array.

| | Full JSONL scan | Bloom check |
|--|-----------------|-------------|
| Avg time | {bloom.get('scan_avg_ms', '?')}ms | {bloom_us:.1f}μs |
| Speedup | — | **{bloom.get('speedup', '?')}x** |
| Filter bits | — | {bloom.get('bloom_bits', '?')} |
| Hash functions (k) | — | {bloom.get('bloom_k', '?')} |
| Entities indexed | — | {bloom.get('entity_count', '?')} |

**How it works:** Bloom filter with k=7 hash functions on {bloom.get('bloom_bits', '?')} bits.
Double hashing (SHA256 + SHA512) for k independent positions.
No false negatives — if bloom says "not present", entity is DEFINITELY absent.
False positive rate ≈ 1%.

---

## 4. Entity Graph

SQLite-backed relationship graph: co-occurrence + semantic edges (resolves,
requires, deploys, affects) extracted from verb patterns.

| | Query |
|--|-------|
| Avg time | {graph.get('avg_ms', '?')}ms |
| Target | <1ms |
| Entities | {graph.get('entity_count', '?')} |
| Total queries | {graph.get('total_queries', '?')} |

**How it works:** Entity edges stored in SQLite with indexed source/target columns.
`query_entity()` does a simple WHERE match — O(1) with index. BFS `neighbors()`
traverses edges to configurable depth.

---

## 5. Negation Index

Detects and stores failed approaches (wrong-approach, forbidden-approach,
failed-attempt, caused-regression, etc.) via regex pattern matching.

| | Detection | Query | Combined |
|--|-----------|-------|----------|
| Avg time | {negation.get('detect_avg_ms', '?')}ms | {negation.get('query_avg_ms', '?')}ms | {negation.get('combined_avg_ms', '?')}ms |
| Target | — | — | <1ms |

**How it works:** 8 regex patterns detect negation signals (e.g., "don't use X",
"tried X and it failed"). Results stored in SQLite with indexed approach column.
Query does LIKE match for flexible substring search.

---

## Architecture Overview

```
Session Resume (v3):
  1. Brain stats (L0 + DB counts)           — O(1)
  2. Bloom pre-check on hint entities        — O(k) per entity
  3. L0 entries (in-memory dict)             — O(1) lookup
  4. Tier priority filter (S→A→B)           — O(n) on L0 only
  5. Token budget packing                    — O(n) greedy
  6. Entity graph enrichment                 — O(1) per entity (SQLite)
  7. Negation warnings                       — O(1) per entity (SQLite)
```

All V3 components are **zero external dependencies** — pure Python + SQLite + gzip.

---

*Benchmarked {resume.get('log_entries', '?')} log entries, {ITERATIONS} iterations per metric.*
*Generated by mycelium/scripts/benchmark_report.py*
"""
    return md


# Avoid name clash with the constant in benchmark_v3.py
ITERATIONS = 1000


def main():
    results = load_results()
    report = generate_report(results)
    REPORT_FILE.write_text(report)
    print(f"📄 Report written to: {REPORT_FILE}")
    print(f"   Size: {len(report)} bytes")

    # Quick summary
    r = results
    print(f"\n{'='*60}")
    print(f"  Resume: {r['resume']['old_avg_ms']:.1f}ms → {r['resume']['new_avg_ms']:.2f}ms ({r['resume']['speedup']}x)")
    print(f"  Storage: {r['storage']['old_kb']}KB → {r['storage']['new_kb']}KB ({r['storage']['compression_ratio']}x)")
    print(f"  Bloom: {r['bloom']['scan_avg_ms']:.2f}ms → {r['bloom']['bloom_avg_us']:.0f}μs ({r['bloom']['speedup']}x)")
    print(f"  Graph: {r['graph']['avg_ms']:.2f}ms/query")
    print(f"  Negation: {r['negation']['combined_avg_ms']:.3f}ms combined")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

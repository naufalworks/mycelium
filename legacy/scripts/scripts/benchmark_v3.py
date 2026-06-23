#!/usr/bin/env python3
"""
benchmark_v3.py — Benchmark suite for Mycelium v3 improvements.

Measures:
  1. Resume speed: old JSONL scan vs v3 LSM L0 lookup
  2. Storage size: raw JSONL vs LSM tiered storage
  3. Bloom filter: probabilistic check vs full JSONL scan
  4. Entity graph query speed
  5. Negation check speed

Usage:
    cd ~/Documents/mycelium && python3 scripts/benchmark_v3.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mycelium_lib import MYCELIUM, load_log, extract_entities, init_index

# ── Paths ────────────────────────────────────────────────────
RUNTIME_LOG = Path.home() / ".hermes" / "myceliumd" / "runtime" / "log.jsonl"
LOCAL_LOG = MYCELIUM / "log.jsonl"

ITERATIONS = 1000
SAMPLE_SIZE = 50  # entries to process in resume benchmarks


def _get_log_path() -> Path:
    """Find the best available log file."""
    if RUNTIME_LOG.exists():
        return RUNTIME_LOG
    if LOCAL_LOG.exists():
        return LOCAL_LOG
    print("ERROR: No log.jsonl found")
    sys.exit(1)


# ── 1. Resume Benchmark ──────────────────────────────────────

def benchmark_resume(log_path: Path) -> dict:
    """Compare old JSONL scan vs v3 LSM-based resume."""
    from mycelium_lsm import MyceliumLSM

    entries = load_log(log_path)
    if not entries:
        return {"error": "no entries in log"}

    # --- OLD: scan entire JSONL, parse last 50, format as resume ---
    def old_resume():
        """Simulate v2 resume: read entire JSONL, take last N, format."""
        all_entries = []
        with open(log_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        recent = all_entries[-SAMPLE_SIZE:]
        result = []
        for e in recent:
            tier = e.get("tier", "B")
            session = e.get("session", "?")
            user = e.get("user", "")[:120]
            assistant = e.get("assistant", "")[:120]
            result.append(f"[{tier}] turn {e.get('turn', '?')} ({session})\n  U: {user}\n  A: {assistant}")
        return result

    # Warm up
    old_resume()

    old_times = []
    for _ in range(ITERATIONS):
        t0 = time.monotonic()
        old_resume()
        old_times.append((time.monotonic() - t0) * 1000)

    old_avg_ms = sum(old_times) / len(old_times)

    # --- NEW: LSM L0 lookup + tier filtering ---
    with tempfile.TemporaryDirectory(prefix="myc_bench_") as tmpdir:
        lsm_base = Path(tmpdir)
        lsm = MyceliumLSM(lsm_base)
        lsm.load_from_jsonl(log_path)

        def new_resume():
            l0_entries = lsm.l0.to_list()
            tier_entries = {"S": [], "A": [], "B": []}
            for e in l0_entries:
                tier = e.get("tier", "B")
                if tier in tier_entries:
                    tier_entries[tier].append(e)
                else:
                    tier_entries["B"].append(e)
            prioritized = tier_entries["S"] + tier_entries["A"] + tier_entries["B"]
            result = []
            for e in prioritized[:SAMPLE_SIZE]:
                tier = e.get("tier", "B")
                session = e.get("session", "?")
                user = e.get("user", "")[:120]
                assistant = e.get("assistant", "")[:120]
                result.append(f"[{tier}] turn {e.get('turn', '?')} ({session})\n  U: {user}\n  A: {assistant}")
            return result

        # Warm up
        new_resume()

        new_times = []
        for _ in range(ITERATIONS):
            t0 = time.monotonic()
            new_resume()
            new_times.append((time.monotonic() - t0) * 1000)

        new_avg_ms = sum(new_times) / len(new_times)

    return {
        "metric": "resume_speed",
        "old_avg_ms": round(old_avg_ms, 3),
        "new_avg_ms": round(new_avg_ms, 3),
        "speedup": round(old_avg_ms / new_avg_ms, 1) if new_avg_ms > 0 else float("inf"),
        "iterations": ITERATIONS,
        "log_entries": len(entries),
        "target_old_ms": 50,
        "target_new_ms": 1.0,
        "target_speedup": 50,
    }


# ── 2. Storage Benchmark ─────────────────────────────────────

def benchmark_storage(log_path: Path) -> dict:
    """Compare raw JSONL size vs LSM tiered storage size."""
    from mycelium_lsm import MyceliumLSM

    old_size = log_path.stat().st_size

    with tempfile.TemporaryDirectory(prefix="myc_bench_") as tmpdir:
        lsm_base = Path(tmpdir)
        lsm = MyceliumLSM(lsm_base)
        lsm.load_from_jsonl(log_path)

        stats = lsm.stats()
        l0_size = stats["l0_size_bytes"]
        l1_size = stats["l1_size_bytes"]
        new_total = stats["total_size_bytes"]
        l2_size = new_total - l0_size - l1_size

    ratio = old_size / new_total if new_total > 0 else 0

    return {
        "metric": "storage_size",
        "old_bytes": old_size,
        "old_kb": round(old_size / 1024, 1),
        "new_bytes": new_total,
        "new_kb": round(new_total / 1024, 1),
        "l0_bytes": l0_size,
        "l1_bytes": l1_size,
        "l2_bytes": l2_size,
        "compression_ratio": round(ratio, 1),
        "target_old_kb": 346,
        "target_new_kb": 70,
        "target_ratio": 5,
    }


# ── 3. Bloom Filter Benchmark ────────────────────────────────

def benchmark_bloom(log_path: Path) -> dict:
    """Bloom filter check speed vs full JSONL scan for entity membership."""
    from mycelium_bloom import MyceliumBloom

    entries = load_log(log_path)
    if not entries:
        return {"error": "no entries"}

    # Collect sample entities from log
    all_entities = set()
    for e in entries:
        all_entities.update(e.get("entities", []))
    # Also extract from text
    for e in entries[:200]:
        user = e.get("user", "")
        assistant = e.get("assistant", "")
        all_entities.update(extract_entities(user + " " + assistant))

    entity_list = sorted(all_entities)
    sample_entities = entity_list[:min(50, len(entity_list))]
    if not sample_entities:
        return {"error": "no entities found"}

    # Build bloom filter
    bloom = MyceliumBloom(capacity=max(len(entity_list) * 2, 10000), name="bench_entities")
    for ent in entity_list:
        bloom.add_entity(ent)

    # --- Bloom check speed ---
    bloom_times = []
    for _ in range(ITERATIONS):
        for ent in sample_entities:
            t0 = time.monotonic()
            bloom.check(ent)
            bloom_times.append((time.monotonic() - t0) * 1000)

    bloom_avg_us = (sum(bloom_times) / len(bloom_times)) * 1000  # convert ms to us

    # --- Full JSONL scan speed (simulating v2 approach) ---
    scan_times = []
    for _ in range(min(ITERATIONS, 100)):  # 100 iters for scan (it's slower)
        for ent in sample_entities:
            t0 = time.monotonic()
            found = False
            for e in entries:
                if ent in e.get("entities", []):
                    found = True
                    break
            scan_times.append((time.monotonic() - t0) * 1000)

    scan_avg_ms = sum(scan_times) / len(scan_times)

    return {
        "metric": "bloom_filter",
        "bloom_avg_us": round(bloom_avg_us, 3),
        "scan_avg_ms": round(scan_avg_ms, 3),
        "speedup": round(scan_avg_ms / (bloom_avg_us / 1000), 1) if bloom_avg_us > 0 else float("inf"),
        "entity_count": len(entity_list),
        "sample_size": len(sample_entities),
        "iterations": ITERATIONS,
        "bloom_bits": bloom._m,
        "bloom_k": bloom._k,
        "target_bloom_us": 10,  # 0.01ms = 10us
        "target_speedup": 500,
    }


# ── 4. Entity Graph Benchmark ────────────────────────────────

def benchmark_graph(log_path: Path) -> dict:
    """Entity graph query speed."""
    from mycelium_graph import EntityGraph

    entries = load_log(log_path)
    if not entries:
        return {"error": "no entries"}

    # Collect entities
    all_entities = set()
    for e in entries:
        all_entities.update(e.get("entities", []))
    sample_entities = sorted(all_entities)[:min(20, len(all_entities))]
    if not sample_entities:
        return {"error": "no entities"}

    with tempfile.TemporaryDirectory(prefix="myc_bench_") as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        graph = EntityGraph(db_path=db_path)
        # Build graph from log
        graph.build_from_log(log_path)

        # --- Query speed ---
        query_times = []
        for _ in range(ITERATIONS):
            for ent in sample_entities:
                t0 = time.monotonic()
                graph.query_entity(ent)
                query_times.append((time.monotonic() - t0) * 1000)

        avg_ms = sum(query_times) / len(query_times)
        graph.close()

    return {
        "metric": "graph_query",
        "avg_ms": round(avg_ms, 3),
        "total_queries": len(query_times),
        "entity_count": len(all_entities),
        "sample_size": len(sample_entities),
        "iterations": ITERATIONS,
        "target_ms": 1.0,
    }


# ── 5. Negation Benchmark ────────────────────────────────────

def benchmark_negation(log_path: Path) -> dict:
    """Negation detection + query speed."""
    from mycelium_negation import NegationExtractor

    entries = load_log(log_path)
    if not entries:
        return {"error": "no entries"}

    sample_phrases = [
        "don't use curl for that",
        "that's not the right approach, try python instead",
        "tried SSH and it failed",
        "stop using grep for large files",
        "that caused a new bug in the deploy",
        "wrong port configuration for grav",
        "how many times do I have to explain",
        "don't try running the old script again",
        "that's not the correct way to fix this",
        "tested the API and it errored out",
    ] * 100  # repeat to fill iterations

    with tempfile.TemporaryDirectory(prefix="myc_bench_") as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        ne = NegationExtractor(db_path=db_path)

        # --- Detection speed (regex matching) ---
        detect_times = []
        for phrase in sample_phrases[:ITERATIONS]:
            t0 = time.monotonic()
            ne.detect(phrase)
            detect_times.append((time.monotonic() - t0) * 1000)

        detect_avg_ms = sum(detect_times) / len(detect_times)

        # --- Store some negations for query bench ---
        for phrase in sample_phrases[:50]:
            signals = ne.detect(phrase)
            for s in signals:
                ne.store(s, session="bench")

        # --- Query speed ---
        query_phrases = ["curl", "python", "SSH", "grep", "deploy", "port", "script"]
        query_times = []
        for _ in range(ITERATIONS):
            for phrase in query_phrases:
                t0 = time.monotonic()
                ne.query(approach=phrase)
                query_times.append((time.monotonic() - t0) * 1000)

        query_avg_ms = sum(query_times) / len(query_times)

    return {
        "metric": "negation",
        "detect_avg_ms": round(detect_avg_ms, 3),
        "query_avg_ms": round(query_avg_ms, 3),
        "combined_avg_ms": round(detect_avg_ms + query_avg_ms, 3),
        "negations_stored": 50,
        "iterations": ITERATIONS,
        "target_ms": 1.0,
    }


# ── Main ─────────────────────────────────────────────────────

def run_all() -> dict:
    """Run all benchmarks, return results dict."""
    log_path = _get_log_path()
    print(f"📂 Log: {log_path} ({log_path.stat().st_size / 1024:.1f} KB)")

    results = {}

    print("\n⏱️  Benchmark 1: Resume Speed...")
    results["resume"] = benchmark_resume(log_path)
    r = results["resume"]
    print(f"   Old: {r['old_avg_ms']:.2f}ms | New: {r['new_avg_ms']:.2f}ms | Speedup: {r['speedup']}x")

    print("\n💾 Benchmark 2: Storage Size...")
    results["storage"] = benchmark_storage(log_path)
    r = results["storage"]
    print(f"   Old: {r['old_kb']}KB | New: {r['new_kb']}KB | Ratio: {r['compression_ratio']}x")

    print("\n🌸 Benchmark 3: Bloom Filter...")
    results["bloom"] = benchmark_bloom(log_path)
    r = results["bloom"]
    print(f"   Bloom: {r['bloom_avg_us']:.1f}μs | Scan: {r['scan_avg_ms']:.2f}ms | Speedup: {r['speedup']}x")

    print("\n🔗 Benchmark 4: Entity Graph...")
    results["graph"] = benchmark_graph(log_path)
    r = results["graph"]
    print(f"   Query avg: {r['avg_ms']:.2f}ms")

    print("\n🚫 Benchmark 5: Negation Index...")
    results["negation"] = benchmark_negation(log_path)
    r = results["negation"]
    print(f"   Detect: {r['detect_avg_ms']:.3f}ms | Query: {r['query_avg_ms']:.3f}ms | Combined: {r['combined_avg_ms']:.3f}ms")

    return results


if __name__ == "__main__":
    results = run_all()
    # Save results for benchmark_report.py
    out_path = MYCELIUM / "benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n✅ Results saved to {out_path}")

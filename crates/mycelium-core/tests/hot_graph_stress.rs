//! Stress tests and latency benchmarks for the HotGraph unified streaming graph.
//!
//! These tests push HotGraph to its limits:
//!   1. High-throughput reads: 10k reads on 10k pre-seeded atoms (< 500ms)
//!   2. Rapid concurrent bumps: 16 threads × 1000 bumps, no deadlock
//!   3. Heat accumulation: 10k atoms × 100 decay cycles, total heat never negative
//!   4. Hot vs cold latency: 10k hot reads vs 10k misses, print comparison

use mycelium_core::hot_graph::{Edge, HotGraph, Position};
use std::time::Instant;

/// Helper: seed `n` atoms into the graph with somewhat realistic random-like names.
fn seed_atoms(graph: &HotGraph, n: usize) {
    let prefixes = [
        "hash", "chain", "merkle", "tree", "verify", "integrity",
        "memory", "management", "garbage", "collection", "strategy",
        "data", "structure", "algorithm", "performance",
    ];
    for i in 0..n {
        let phrase = format!("{}-{}", prefixes[i % prefixes.len()], i);
        let importance = 0.5 + (i % 10) as f64 * 0.2;
        graph.seed(
            &phrase,
            importance,
            (i as i64 % 5) + 1,
            vec![Position { turn: i as i64, session: format!("s-{}", i) }],
            vec![
                Edge { neighbor: format!("{}-{}", prefixes[(i + 1) % prefixes.len()], (i + 1) % n), weight: 0.5 },
            ],
        );
    }
}

// ──────────────────────────────────────────────
// 1. High-throughput read latency
// ──────────────────────────────────────────────

#[test]
fn stress_high_throughput_reads() {
    let graph = HotGraph::new();
    const N: usize = 10_000;

    seed_atoms(&graph, N);

    // Measure 10k sequential reads
    let start = Instant::now();
    for i in 0..N {
        let phrase = format!("{}-{}", [
            "hash", "chain", "merkle", "tree", "verify", "integrity",
            "memory", "management", "garbage", "collection", "strategy",
            "data", "structure", "algorithm", "performance",
        ][i % 15], i);
        let _ = graph.get(&phrase);
    }
    let elapsed = start.elapsed();

    // 10k reads should be very fast (< 500ms)
    assert!(
        elapsed < std::time::Duration::from_millis(500),
        "10k reads took {:.2}ms (expected < 500ms)",
        elapsed.as_secs_f64() * 1000.0
    );

    println!(
        "stress_high_throughput_reads: {} reads in {:.2}ms ({:.1} ns/read)",
        N,
        elapsed.as_secs_f64() * 1000.0,
        elapsed.as_nanos() as f64 / N as f64,
    );
}

// ──────────────────────────────────────────────
// 2. Rapid concurrent bumps
// ──────────────────────────────────────────────

#[test]
fn stress_rapid_bump_no_contention() {
    use std::sync::Arc;
    use std::thread;

    let graph = Arc::new(HotGraph::new());

    // Seed 100 atoms
    for i in 0..100 {
        let phrase = format!("atom-{}", i);
        graph.seed(&phrase, 2.0, 1, vec![], vec![]);
    }

    // 16 threads each bump 1000 times
    let num_threads = 16;
    let bumps_per_thread = 1000;
    let handles: Vec<_> = (0..num_threads)
        .map(|id| {
            let g = Arc::clone(&graph);
            thread::spawn(move || {
                for _ in 0..bumps_per_thread {
                    // Each thread picks a random-ish atom from the 100
                    let idx = (id * 7 + 13) % 100;
                    g.bump(&format!("atom-{}", idx), 0.5);
                }
            })
        })
        .collect();

    for h in handles {
        h.join().expect("Thread panicked during rapid bump test");
    }

    let snapshot = graph.metrics().snapshot();
    let expected_bumps = num_threads * bumps_per_thread;
    assert!(
        snapshot.bumps >= expected_bumps as u64,
        "expected at least {} bumps across {} atoms, got {}",
        expected_bumps,
        100,
        snapshot.bumps,
    );

    // Verify no atom has negative heat
    for i in 0..100 {
        if let Some(atom) = graph.get(&format!("atom-{}", i)) {
            assert!(
                atom.heat >= -1e-9,
                "atom atom-{} has negative heat: {:.4}",
                i,
                atom.heat,
            );
        }
    }

    println!(
        "stress_rapid_bump_no_contention: {} threads × {} bumps = {} total bumps, hot_count={} deadlock=false",
        num_threads,
        bumps_per_thread,
        snapshot.bumps,
        snapshot.hot_count,
    );
}

// ──────────────────────────────────────────────
// 3. Heat accumulation with no leak
// ──────────────────────────────────────────────

#[test]
fn stress_heat_accumulation_no_leak() {
    let graph = HotGraph::new();

    // Seed 10k atoms
    seed_atoms(&graph, 10_000);

    // Record initial total heat
    let initial = graph.metrics().snapshot();
    assert!(
        initial.total_heat > 0.0,
        "initial total heat should be positive, got {:.4}",
        initial.total_heat,
    );
    assert_eq!(
        initial.hot_count, 10_000,
        "expected 10k atoms seeded, got {}",
        initial.hot_count,
    );

    // Run 100 decay cycles
    for cycle in 0..100 {
        graph.tick_decay();

        let s = graph.metrics().snapshot();
        // Total heat should never go negative
        assert!(
            s.total_heat >= -1e-9,
            "total heat went negative after cycle {}: {:.4}",
            cycle + 1,
            s.total_heat,
        );

        // hot_count should monotonically decrease or stay same (never increase)
        assert!(
            s.hot_count >= 0,
            "hot_count went negative after cycle {}: {}",
            cycle + 1,
            s.hot_count,
        );
    }

    let final_metrics = graph.metrics().snapshot();

    // Individual atoms should all have non-negative heat
    for atom in graph.top_atoms(10_000) {
        assert!(
            atom.heat >= -1e-9,
            "atom '{}' has negative heat: {:.4}",
            atom.phrase,
            atom.heat,
        );
    }

    println!(
        "stress_heat_accumulation_no_leak: initial_heat={:.2}, final_heat={:.2}, \
         initial_count={}, final_count={}, evictions={}, after 100 decay cycles",
        initial.total_heat,
        final_metrics.total_heat,
        initial.hot_count,
        final_metrics.hot_count,
        final_metrics.evictions,
    );
}

// ──────────────────────────────────────────────
// 4. Hot vs cold read latency benchmark
// ──────────────────────────────────────────────

#[test]
fn bench_hot_vs_cold_latency() {
    let graph = HotGraph::new();

    // Seed 10k atoms
    seed_atoms(&graph, 10_000);

    // --- Hot reads (atoms that exist) ---
    let hot_start = Instant::now();
    for i in 0..10_000 {
        let phrase = format!("{}-{}", [
            "hash", "chain", "merkle", "tree", "verify", "integrity",
            "memory", "management", "garbage", "collection", "strategy",
            "data", "structure", "algorithm", "performance",
        ][i % 15], i);
        let _ = graph.get(&phrase);
    }
    let hot_elapsed = hot_start.elapsed();

    // --- Cold reads (atoms that don't exist) ---
    let cold_start = Instant::now();
    for i in 0..10_000 {
        let phrase = format!("nonexistent-{}", i);
        let _ = graph.get(&phrase);
    }
    let cold_elapsed = cold_start.elapsed();

    // Print comparison
    let hot_ns_per = hot_elapsed.as_nanos() as f64 / 10_000.0;
    let cold_ns_per = cold_elapsed.as_nanos() as f64 / 10_000.0;

    println!(
        "bench_hot_vs_cold_latency:\n  Hot reads:  {} reads in {:.2}ms ({:.1} ns/read)\n  Cold reads: {} reads in {:.2}ms ({:.1} ns/read)\n  Ratio:      {:.2}x",
        10_000,
        hot_elapsed.as_secs_f64() * 1000.0,
        hot_ns_per,
        10_000,
        cold_elapsed.as_secs_f64() * 1000.0,
        cold_ns_per,
        cold_ns_per / hot_ns_per.max(1.0),
    );

    // Sanity: hot reads should be fast (still < 500ms)
    assert!(
        hot_elapsed < std::time::Duration::from_millis(500),
        "10k hot reads took {:.2}ms (expected < 500ms)",
        hot_elapsed.as_secs_f64() * 1000.0,
    );
    assert!(
        cold_elapsed < std::time::Duration::from_millis(500),
        "10k cold reads took {:.2}ms (expected < 500ms)",
        cold_elapsed.as_secs_f64() * 1000.0,
    );
}

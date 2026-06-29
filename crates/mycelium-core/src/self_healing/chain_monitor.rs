use std::path::{Path, PathBuf};
use std::sync::Arc;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};

use crate::Storage;

/// Cached result of the last verify for change detection.
#[derive(Debug, Serialize, Deserialize, Clone)]
struct CachedChainState {
    /// Hash of the entire chain state (sha256 of concatenated prev_hash values).
    chain_hash: String,
    /// Unix timestamp of last check.
    checked_at: i64,
    /// Number of broken entries at last check.
    broken_count: usize,
}

/// Monitor that detects new chain breaks by comparing cached state vs live.
pub struct ChainMonitor {
    storage: Arc<Storage>,
    cache_path: PathBuf,
    cached_state: Mutex<Option<CachedChainState>>,
}

/// Result when the monitor detects a chain that needs repair.
#[derive(Debug, Clone)]
pub struct RepairTrigger {
    pub broken_count: usize,
    pub total_entries: i64,
    pub segment_start: i64,
    pub segment_end: i64,
}

impl ChainMonitor {
    pub fn new(storage: Arc<Storage>, mycelium_dir: &Path) -> Self {
        let cache_path = mycelium_dir.join("chain-state.json");
        let cached = std::fs::read_to_string(&cache_path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok());
        Self {
            storage,
            cache_path,
            cached_state: Mutex::new(cached),
        }
    }

    /// Run one check cycle. Returns RepairTrigger if repair is needed.
    pub fn run_tick(&self) -> anyhow::Result<Option<RepairTrigger>> {
        let failures = self.storage.verify_hash_chain()?;
        let total: i64 = self
            .storage
            .conn()
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0))?;

        // Compute chain hash for change detection
        let chain_hash = self.compute_chain_hash(&failures, total);

        let broken_count = failures.len();
        let cached = self.cached_state.lock();

        // Same state as last check? Skip.
        if let Some(ref c) = *cached {
            if c.chain_hash == chain_hash && c.broken_count == broken_count {
                drop(cached);
                self.save_cache(broken_count, &chain_hash)?;
                return Ok(None);
            }
        }
        drop(cached);

        // Determine segment boundaries from failures
        let (segment_start, segment_end) = if broken_count > 0 {
            let turns: Vec<i64> = failures.iter().map(|(t, _, _)| *t).collect();
            let min_t = turns.iter().min().copied().unwrap_or(0);
            let max_t = turns.iter().max().copied().unwrap_or(0);
            (min_t, max_t)
        } else {
            (0, 0)
        };

        let trigger = RepairTrigger {
            broken_count,
            total_entries: total,
            segment_start,
            segment_end,
        };

        self.save_cache(broken_count, &chain_hash)?;

        if broken_count > 0 {
            tracing::warn!(
                "chain monitor: {} broken entries (segment {}..{})",
                broken_count,
                segment_start,
                segment_end
            );
            Ok(Some(trigger))
        } else {
            Ok(None)
        }
    }

    fn compute_chain_hash(&self, failures: &[(i64, String, String)], total: i64) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        for (turn, _, _) in failures {
            hasher.update(turn.to_le_bytes());
        }
        hasher.update(total.to_le_bytes());
        hex::encode(hasher.finalize())
    }

    fn save_cache(&self, broken_count: usize, chain_hash: &str) -> anyhow::Result<()> {
        let state = CachedChainState {
            chain_hash: chain_hash.to_string(),
            checked_at: chrono::Utc::now().timestamp(),
            broken_count,
        };
        let json = serde_json::to_string_pretty(&state)?;
        std::fs::write(&self.cache_path, json)?;
        Ok(())
    }
}

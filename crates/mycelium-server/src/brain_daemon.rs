//! Background daemon that processes pending brain work.
//! Waits on `Storage`'s `Notify` signal instead of polling,
//! consolidates entries into atoms/positions/edges.
//!
//! Also runs a chain monitor that detects hash-chain breaks and spawns
//! background LLM-driven repair tasks.

use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::sync::Notify;
use mycelium_core::Storage;
use mycelium_core::brain;
use mycelium_core::self_healing::{ChainMonitor, Policy, SafetyHarness};
use mycelium_core::types::MemoryAnnotation;

pub struct BrainDaemon {
    storage: Arc<Storage>,
    notify: Arc<Notify>,
    running: Arc<AtomicBool>,
    chain_monitor: Arc<ChainMonitor>,
    policy: Arc<Policy>,
    safety: Arc<SafetyHarness>,
    root_dir: std::path::PathBuf,
}

impl BrainDaemon {
    pub fn new(storage: Arc<Storage>, notify: Arc<Notify>, root_dir: &Path) -> Self {
        let mycelium_dir = root_dir.join(".mycelium");
        let db_path = root_dir.join("mycelium.db");

        let chain_monitor = Arc::new(ChainMonitor::new(
            Arc::clone(&storage),
            &mycelium_dir,
        ));
        let policy = Arc::new(
            Policy::load_or_create(root_dir)
                .expect("failed to load self-healing policy"),
        );
        let safety = Arc::new(SafetyHarness::new(db_path, mycelium_dir));

        Self {
            storage,
            notify,
            running: Arc::new(AtomicBool::new(true)),
            chain_monitor,
            policy,
            safety,
            root_dir: root_dir.to_path_buf(),
        }
    }

    /// Spawn the daemon loop as a background tokio task.
    pub fn spawn(self) {
        use mycelium_core::self_healing::{AuditWriter, LLMAgent, LLMConfig, LLMProvider};

        tokio::spawn(async move {
            tracing::info!("Brain daemon started (event-driven mode)");
            while self.running.load(Ordering::Relaxed) {
                // Wait for a wake signal, with 60 s safety timeout
                tokio::select! {
                    _ = self.notify.notified() => {},
                    _ = tokio::time::sleep(Duration::from_secs(60)) => {
                        tracing::trace!("Brain daemon: safety poll after timeout");
                    },
                }

                if let Err(e) = self.process_batch() {
                    tracing::warn!("brain daemon error: {}", e);
                }
                // Run decay cycle on every wake — keeps the hot graph fresh
                self.storage.hot_graph().tick_decay();

                // Check for hash-chain breaks and spawn repair if needed.
                // The repair runs in a background task — never blocks the daemon loop.
                match self.chain_monitor.run_tick() {
                    Ok(Some(trigger)) => {
                        tracing::warn!(
                            "broken chain detected: {} entries ({}..{})",
                            trigger.broken_count, trigger.segment_start, trigger.segment_end,
                        );
                        let agent = LLMAgent {
                            provider: LLMProvider::new(LLMConfig::default()),
                            storage: Arc::clone(&self.storage),
                            policy: Arc::clone(&self.policy),
                            safety: Arc::clone(&self.safety),
                            max_tool_calls: self.policy.max_tool_calls(),
                            timeout: std::time::Duration::from_secs(
                                self.policy.max_wall_time_seconds(),
                            ),
                        };
                        let root = self.root_dir.clone();
                        tokio::spawn(async move {
                            match agent.run().await {
                                Ok(log) => {
                                    let audit = AuditWriter::new(&root);
                                    if let Ok(path) = audit.write_repair_log(&log) {
                                        tracing::info!(
                                            "chain repair complete, audit: {}",
                                            path.display(),
                                        );
                                    }
                                }
                                Err(e) => tracing::error!("chain repair failed: {}", e),
                            }
                        });
                    }
                    Ok(None) => {} // chain intact or no change
                    Err(e) => tracing::warn!("chain monitor tick error: {}", e),
                }
            }
            tracing::info!("Brain daemon stopped");
        });
    }

    /// Dequeue up to 20 pending items, consolidate them, and remove completed items.
    pub fn process_batch(&self) -> anyhow::Result<()> {
        // Lock only to dequeue — drop the guard so get_entry (which locks internally) won't deadlock.
        let items = {
            let conn = self.storage.conn().lock().unwrap();
            brain::dequeue_pending(&conn, 20)?
        };

        if items.is_empty() {
            return Ok(());
        }

        let mut processed = Vec::new();

        for item in &items {
            if let Ok(Some(entry)) = self.storage.get_entry(item.turn) {
                let text = format!("{} {}", entry.user, entry.assistant);
                // Parse annotation JSON if present
                let annotation: Option<MemoryAnnotation> = entry.annotation
                    .as_deref()
                    .and_then(|json| serde_json::from_str(json).ok());
                // Lock again for each consolidation (short-lived).
                let conn = self.storage.conn().lock().unwrap();
                brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, annotation.as_ref(), Some(self.storage.hot_graph().as_ref()))?;
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
}

//! Background daemon that processes pending brain work.
//! Polls the `pending_brain_work` queue every 5 seconds,
//! consolidates entries into atoms/positions/edges.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use mycelium_core::Storage;
use mycelium_core::brain;

pub struct BrainDaemon {
    storage: Arc<Storage>,
    running: Arc<AtomicBool>,
}

impl BrainDaemon {
    pub fn new(storage: Arc<Storage>) -> Self {
        Self { storage, running: Arc::new(AtomicBool::new(true)) }
    }

    /// Spawn the daemon loop as a background tokio task.
    pub fn spawn(self) {
        tokio::spawn(async move {
            tracing::info!("Brain daemon started");
            while self.running.load(Ordering::Relaxed) {
                if let Err(e) = self.process_batch() {
                    tracing::warn!("brain daemon error: {}", e);
                }
                tokio::time::sleep(Duration::from_secs(5)).await;
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
                // Lock again for each consolidation (short-lived).
                let conn = self.storage.conn().lock().unwrap();
                brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, None)?;
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

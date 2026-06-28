//! Background daemon that processes pending brain work.
//! Waits on `Storage`'s `Notify` signal instead of polling,
//! consolidates entries into atoms/positions/edges.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::sync::Notify;
use mycelium_core::Storage;
use mycelium_core::brain;
use mycelium_core::types::MemoryAnnotation;

pub struct BrainDaemon {
    storage: Arc<Storage>,
    notify: Arc<Notify>,
    running: Arc<AtomicBool>,
}

impl BrainDaemon {
    pub fn new(storage: Arc<Storage>, notify: Arc<Notify>) -> Self {
        Self { storage, notify, running: Arc::new(AtomicBool::new(true)) }
    }

    /// Spawn the daemon loop as a background tokio task.
    pub fn spawn(self) {
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
                brain::consolidate_entry(&conn, entry.turn, &entry.session, &text, annotation.as_ref())?;
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

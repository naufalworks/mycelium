//! In-memory cache for frequently accessed memory data.
//!
//! Uses `moka` — a high-performance concurrent cache.
//! This reduces SQLite reads for hot paths like session context
//! and recent entries.

use moka::sync::Cache as MokaCache;
use std::time::Duration;

use crate::types::{Entry, MemoryFact};

/// Wraps moka caches for the memory system.
pub struct MemoryCache {
    /// Cache recent entries by turn number.
    entries: MokaCache<i64, Entry>,
    /// Cache session context bundles.
    session_context: MokaCache<String, Vec<Entry>>,
    /// Cache search results (TTL-based invalidation).
    search_results: MokaCache<String, Vec<Entry>>,
    /// Cache memory facts by entity.
    facts_by_entity: MokaCache<String, Vec<MemoryFact>>,
}

impl MemoryCache {
    pub fn new() -> Self {
        Self {
            // Keep up to 500 entries, expire after 5 minutes
            entries: MokaCache::builder()
                .max_capacity(500)
                .time_to_live(Duration::from_secs(300))
                .build(),

            // Keep up to 50 session contexts, expire after 2 minutes
            session_context: MokaCache::builder()
                .max_capacity(50)
                .time_to_live(Duration::from_secs(120))
                .build(),

            // Keep up to 200 search result sets, expire after 30 seconds
            search_results: MokaCache::builder()
                .max_capacity(200)
                .time_to_live(Duration::from_secs(30))
                .build(),

            // Keep up to 1000 entity facts, expire after 60 seconds
            facts_by_entity: MokaCache::builder()
                .max_capacity(1000)
                .time_to_live(Duration::from_secs(60))
                .build(),
        }
    }

    // ── Entries ──

    pub fn get_entry(&self, turn: i64) -> Option<Entry> {
        self.entries.get(&turn)
    }

    pub fn put_entry(&self, turn: i64, entry: Entry) {
        self.entries.insert(turn, entry);
    }

    // ── Session Context ──

    pub fn get_session_context(&self, session: &str) -> Option<Vec<Entry>> {
        self.session_context.get(session)
    }

    pub fn put_session_context(&self, session: String, entries: Vec<Entry>) {
        self.session_context.insert(session, entries);
    }

    // ── Search Results ──

    pub fn get_search_results(&self, query: &str) -> Option<Vec<Entry>> {
        self.search_results.get(query)
    }

    pub fn put_search_results(&self, query: String, entries: Vec<Entry>) {
        self.search_results.insert(query, entries);
    }

    // ── Memory Facts ──

    pub fn get_facts(&self, entity: &str) -> Option<Vec<MemoryFact>> {
        self.facts_by_entity.get(entity)
    }

    pub fn put_facts(&self, entity: String, facts: Vec<MemoryFact>) {
        self.facts_by_entity.insert(entity, facts);
    }

    /// Invalidate all caches (e.g., after a write).
    pub fn invalidate_all(&self) {
        self.entries.invalidate_all();
        self.session_context.invalidate_all();
        self.search_results.invalidate_all();
        self.facts_by_entity.invalidate_all();
    }
}

impl Default for MemoryCache {
    fn default() -> Self {
        Self::new()
    }
}

//! Mycelium Core — permanent memory engine for AI agents.
//!
//! This is the foundational crate providing:
//! - Data types (entries, memory facts, artifacts, workflows)
//! - SQLite-based storage with WAL mode
//! - In-memory cache via moka
//! - Full-text search via tantivy
//! - Hash-chain verification for tamper-evident logging

pub mod types;
pub mod storage;
pub mod cache;
pub mod search;
pub mod error;
pub mod brain;
pub mod recall;

pub use types::*;
pub use storage::*;
pub use error::*;

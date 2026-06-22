//! Error types for the Mycelium core.

use thiserror::Error;

#[derive(Error, Debug)]
pub enum MyceliumError {
    #[error("Storage error: {0}")]
    Storage(#[from] rusqlite::Error),

    #[error("Search engine error: {0}")]
    Tantivy(#[from] tantivy::TantivyError),

    #[error("Query parse error: {0}")]
    QueryParse(#[from] tantivy::query::QueryParserError),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),

    #[error("Timestamp error: {0}")]
    Timestamp(#[from] chrono::ParseError),

    #[error("Search error: {0}")]
    Search(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Invalid input: {0}")]
    InvalidInput(String),

    #[error("Cache error: {0}")]
    Cache(String),

    #[error("Hash chain verification failed at turn {turn}: expected {expected}, got {actual}")]
    HashMismatch { turn: i64, expected: String, actual: String },

    #[error("Database migration required: {0}")]
    MigrationRequired(String),
}

pub type Result<T> = std::result::Result<T, MyceliumError>;

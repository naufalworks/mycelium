//! Core data types for the Mycelium memory system.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use uuid::Uuid;

/// A single memory entry — the fundamental unit of permanent memory.
///
/// Entries are hash-chained (each entry's `hash` includes the `prev_hash`),
/// forming a tamper-evident append-only log of all AI agent activity.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entry {
    /// Auto-incrementing turn number (monotonic, per-writer)
    pub turn: i64,
    /// Tier classification (e.g. "core", "ephemeral", "archived")
    pub tier: Tier,
    /// Entry type (e.g. "conversation", "fact", "finding", "system")
    pub entry_type: EntryType,
    /// Session identifier this entry belongs to
    pub session: String,
    /// ISO 8601 timestamp of creation
    pub ts: DateTime<Utc>,
    /// User message (truncated to 500 chars)
    pub user: String,
    /// Assistant response (truncated to 2000 chars)
    pub assistant: String,
    /// Extracted entity names
    pub entities: Vec<String>,
    /// SHA-256 hash of the previous entry (empty string for first entry)
    pub prev_hash: String,
    /// SHA-256 hash of this entry (computed from all fields except this one)
    pub hash: String,
    /// Optional finding/insight attached to this entry
    pub finding: Option<String>,
    /// Optional verdict associated with the finding
    pub verdict: Option<String>,
}

impl Entry {
    /// Compute the SHA-256 hash of this entry.
    ///
    /// Hash = hex(first_8_bytes( SHA-256(prev_hash + canonical_json_without_hash) ))
    /// This is compatible with the Go implementation.
    pub fn compute_hash(&self, prev_hash: &str) -> String {
        // Build a sorted map of all fields except "hash"
        let mut map = BTreeMap::from([
            ("turn", serde_json::Value::Number(self.turn.into())),
            ("tier", serde_json::Value::String(self.tier.as_str().to_string())),
            ("entry_type", serde_json::Value::String(self.entry_type.as_str().to_string())),
            ("session", serde_json::Value::String(self.session.clone())),
            ("ts", serde_json::Value::String(self.ts.to_rfc3339())),
            ("user", serde_json::Value::String(self.user.clone())),
            ("assistant", serde_json::Value::String(self.assistant.clone())),
            ("entities", serde_json::Value::Array(
                self.entities.iter().map(|e| serde_json::Value::String(e.clone())).collect()
            )),
            ("prev_hash", serde_json::Value::String(self.prev_hash.clone())),
        ]);

        // Include optional fields if present
        if let Some(finding) = &self.finding {
            map.insert("finding", serde_json::Value::String(finding.clone()));
        }
        if let Some(verdict) = &self.verdict {
            map.insert("verdict", serde_json::Value::String(verdict.clone()));
        }

        // Serialize to canonical JSON (sorted keys, no whitespace)
        let canonical = serde_json::to_string(&map).unwrap_or_default();

        // SHA-256(prev_hash + canonical), take first 8 bytes, hex-encode
        let mut hasher = Sha256::new();
        hasher.update(prev_hash.as_bytes());
        hasher.update(canonical.as_bytes());
        let result = hasher.finalize();
        hex::encode(&result[..8])
    }
}

/// Tier classification for memory entries.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum Tier {
    Core,
    Ephemeral,
    Archived,
}

impl Tier {
    pub fn as_str(&self) -> &'static str {
        match self {
            Tier::Core => "core",
            Tier::Ephemeral => "ephemeral",
            Tier::Archived => "archived",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "core" => Some(Tier::Core),
            "ephemeral" => Some(Tier::Ephemeral),
            "archived" => Some(Tier::Archived),
            _ => None,
        }
    }
}

/// Entry type classification.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum EntryType {
    Conversation,
    Fact,
    Finding,
    System,
}

impl EntryType {
    pub fn as_str(&self) -> &'static str {
        match self {
            EntryType::Conversation => "conversation",
            EntryType::Fact => "fact",
            EntryType::Finding => "finding",
            EntryType::System => "system",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "conversation" => Some(EntryType::Conversation),
            "fact" => Some(EntryType::Fact),
            "finding" => Some(EntryType::Finding),
            "system" => Some(EntryType::System),
            _ => None,
        }
    }
}

/// A structured memory fact (stored in the `memory_facts` table).
///
/// Facts are extracted from conversations by the hippocampus system.
/// They form a semantic knowledge graph of entity-attribute-value triples.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryFact {
    pub id: Option<i64>,
    pub entity: String,
    pub attribute: String,
    pub value: String,
    pub fact_type: String,
    pub confidence: f64,
    pub source_session: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// An artifact stored from a conversation.
///
/// Artifacts are code files, documents, or structured data that the AI
/// agent produced during a conversation session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Artifact {
    pub id: Uuid,
    pub session: String,
    pub filename: String,
    pub content_type: String,
    pub content: Vec<u8>,
    pub description: Option<String>,
    pub artifact_type: String,
    pub created_at: DateTime<Utc>,
}

/// A context snapshot — a summary of session state at a point in time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextSnapshot {
    pub id: i64,
    pub session_id: String,
    pub summary: String,
    pub topics: Vec<String>,
    pub decisions: Vec<String>,
    pub entities: Vec<String>,
    pub credentials: Vec<String>,
    pub turn_count: i64,
    pub created_at: DateTime<Utc>,
}

/// A workflow definition (stored workflow steps).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Workflow {
    pub name: String,
    pub description: String,
    pub steps: Vec<WorkflowStep>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

/// A single step within a workflow.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowStep {
    pub name: String,
    pub command: String,
    pub timeout_secs: Option<u64>,
}

/// A workflow run (a specific execution of a workflow).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowRun {
    pub id: Uuid,
    pub workflow_name: String,
    pub status: RunStatus,
    pub current_step: i64,
    pub total_steps: i64,
    pub started_at: DateTime<Utc>,
    pub finished_at: Option<DateTime<Utc>>,
    pub error: Option<String>,
}

/// Status of a workflow run.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum RunStatus {
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl RunStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            RunStatus::Pending => "pending",
            RunStatus::Running => "running",
            RunStatus::Completed => "completed",
            RunStatus::Failed => "failed",
            RunStatus::Cancelled => "cancelled",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "pending" => Some(RunStatus::Pending),
            "running" => Some(RunStatus::Running),
            "completed" => Some(RunStatus::Completed),
            "failed" => Some(RunStatus::Failed),
            "cancelled" => Some(RunStatus::Cancelled),
            _ => None,
        }
    }
}

/// Stats about the brain state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrainStats {
    pub total_turns: i64,
    pub total_sessions: i64,
    pub tiers: std::collections::HashMap<String, i64>,
    pub types: std::collections::HashMap<String, i64>,
    pub storage_bytes: i64,
    pub last_turn: Option<LastTurnInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LastTurnInfo {
    pub turn: i64,
    pub ts: String,
    pub tier: String,
}

/// Health status returned by the daemon.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonHealth {
    pub running: bool,
    pub pid: Option<u32>,
    pub uptime_secs: Option<u64>,
    pub memory_mb: Option<f64>,
    pub cpu_percent: Option<f64>,
    pub db_size_mb: Option<f64>,
    pub log_size_mb: Option<f64>,
}

/// User-facing configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MyceliumConfig {
    pub root_dir: std::path::PathBuf,
    pub proxy_port: u16,
    pub server_port: u16,
    pub upstream_url: String,
    pub max_concurrent: usize,
}

impl Default for MyceliumConfig {
    fn default() -> Self {
        Self {
            root_dir: std::path::PathBuf::from(
                std::env::var("MYCELIUM_ROOT")
                    .unwrap_or_else(|_| {
                        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
                        format!("{}/.hermes/myceliumd/runtime", home)
                    }),
            ),
            proxy_port: 8443,
            server_port: 8421,
            upstream_url: "http://localhost:8080".to_string(),
            max_concurrent: 20,
        }
    }
}

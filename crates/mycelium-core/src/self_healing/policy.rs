//! Policy loader — loads and parses the self-healing policy from disk.
//!
//! The policy consists of:
//! - `policy.md` — Natural-language instructions for the LLM agent
//! - `safety.json` — Structured safety constraints (whitelist, limits)

use std::path::Path;
use std::sync::Mutex;
use serde::Deserialize;

/// Structured safety constraints parsed from `safety.json`.
#[derive(Debug, Clone, Deserialize)]
struct SafetyConfig {
    #[serde(default = "default_allowed_tables")]
    allowed_tables: Vec<String>,
    #[serde(default = "default_allowed_columns")]
    allowed_columns: Vec<String>,
    #[serde(default = "default_max_tool_calls")]
    max_tool_calls: usize,
    #[serde(default = "default_max_wall_time_seconds")]
    max_wall_time_seconds: u64,
    #[serde(default)]
    forbidden_actions: Vec<String>,
}

fn default_allowed_tables() -> Vec<String> {
    vec!["entries".to_string()]
}

fn default_allowed_columns() -> Vec<String> {
    vec!["hash".to_string(), "prev_hash".to_string()]
}

fn default_max_tool_calls() -> usize {
    50
}

fn default_max_wall_time_seconds() -> u64 {
    300
}

/// The self-healing policy loaded from `.mycelium/policy.md` and
/// `.mycelium/safety.json`.
///
/// Provides the natural-language policy text and structured safety
/// constraints that govern LLM-driven hash-chain repair.
pub struct Policy {
    /// Raw text of `policy.md`.
    policy_md: String,
    /// Parsed safety constraints, protected by a mutex for interior
    /// mutability during lazy-reload scenarios.
    safety_config: Mutex<SafetyConfig>,
}

impl Policy {
    /// Load (or create with defaults) the policy files under the given
    /// project root directory.
    ///
    /// Creates `.mycelium/` and writes default `policy.md` + `safety.json`
    /// if they do not already exist, then parses and returns the policy.
    pub fn load_or_create(root_dir: &Path) -> anyhow::Result<Self> {
        let mycelium_dir = root_dir.join(".mycelium");
        std::fs::create_dir_all(&mycelium_dir)?;

        let policy_path = mycelium_dir.join("policy.md");
        let safety_path = mycelium_dir.join("safety.json");

        // Write default policy.md if it doesn't exist
        if !policy_path.exists() {
            std::fs::write(&policy_path, include_str!("../../defaults/policy.md"))?;
        }

        // Write default safety.json if it doesn't exist
        if !safety_path.exists() {
            std::fs::write(&safety_path, include_str!("../../defaults/safety.json"))?;
        }

        let policy_md = std::fs::read_to_string(&policy_path)?;
        let safety_raw = std::fs::read_to_string(&safety_path)?;
        let safety_config: SafetyConfig = serde_json::from_str(&safety_raw)?;

        Ok(Self {
            policy_md,
            safety_config: Mutex::new(safety_config),
        })
    }

    /// Return the raw natural-language policy text.
    pub fn policy_md(&self) -> &str {
        &self.policy_md
    }

    /// Return the list of table names that may be modified.
    pub fn allowed_tables(&self) -> Vec<String> {
        self.safety_config.lock().unwrap().allowed_tables.clone()
    }

    /// Return the list of column names that may be modified.
    pub fn allowed_columns(&self) -> Vec<String> {
        self.safety_config.lock().unwrap().allowed_columns.clone()
    }

    /// Maximum number of LLM tool calls permitted per repair session.
    pub fn max_tool_calls(&self) -> usize {
        self.safety_config.lock().unwrap().max_tool_calls
    }

    /// Maximum wall-clock time (seconds) permitted per repair session.
    pub fn max_wall_time_seconds(&self) -> u64 {
        self.safety_config.lock().unwrap().max_wall_time_seconds
    }

    /// Actions that the LLM is explicitly forbidden from performing.
    pub fn forbidden_actions(&self) -> Vec<String> {
        self.safety_config.lock().unwrap().forbidden_actions.clone()
    }

    /// Validate that a (table, column) pair is safe to modify
    /// according to the loaded policy whitelist.
    pub fn check_allowed(&self, table: &str, column: &str) -> anyhow::Result<()> {
        let config = self.safety_config.lock().unwrap();
        anyhow::ensure!(
            config.allowed_tables.iter().any(|t| t == table),
            "table '{}' is not in the whitelist; allowed: {:?}",
            table,
            config.allowed_tables,
        );
        anyhow::ensure!(
            config.allowed_columns.iter().any(|c| c == column),
            "column '{}' is not in the whitelist; allowed: {:?}",
            column,
            config.allowed_columns,
        );
        Ok(())
    }

    /// Validate that a hash string has the correct truncated SHA-256
    /// hex format (16 hex characters).
    pub fn validate_hash_format(hash: &str) -> anyhow::Result<()> {
        anyhow::ensure!(
            hash.len() == 16,
            "hash length {} != 16",
            hash.len(),
        );
        anyhow::ensure!(
            hash.chars().all(|c| c.is_ascii_hexdigit()),
            "hash contains non-hex characters",
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_load_or_create_creates_defaults() {
        let dir = TempDir::new().unwrap();
        let root = dir.path();
        let policy = Policy::load_or_create(root).unwrap();

        assert!(policy.policy_md().contains("Self-Healing"));
        assert_eq!(policy.allowed_tables(), vec!["entries"]);
        assert!(policy.allowed_columns().contains(&"hash".to_string()));
        assert_eq!(policy.max_tool_calls(), 50);
        assert_eq!(policy.max_wall_time_seconds(), 300);
        assert!(policy.forbidden_actions().contains(&"delete_entry".to_string()));
    }

    #[test]
    fn test_check_allowed_passes_for_whitelisted() {
        let dir = TempDir::new().unwrap();
        let policy = Policy::load_or_create(dir.path()).unwrap();

        assert!(policy.check_allowed("entries", "hash").is_ok());
        assert!(policy.check_allowed("entries", "prev_hash").is_ok());
    }

    #[test]
    fn test_check_allowed_rejects_unknown() {
        let dir = TempDir::new().unwrap();
        let policy = Policy::load_or_create(dir.path()).unwrap();

        assert!(policy.check_allowed("entries", "user_content").is_err());
        assert!(policy.check_allowed("memory_facts", "hash").is_err());
    }

    #[test]
    fn test_validate_hash_format() {
        assert!(Policy::validate_hash_format("a1b2c3d4e5f67890").is_ok());
        assert!(Policy::validate_hash_format("0000000000000000").is_ok());
        assert!(Policy::validate_hash_format("ffffffffffffffff").is_ok());
    }

    #[test]
    fn test_validate_hash_format_rejects_bad() {
        // Too short
        assert!(Policy::validate_hash_format("a1b2c3d4e5f6789").is_err());
        // Too long
        assert!(Policy::validate_hash_format("a1b2c3d4e5f678901").is_err());
        // Non-hex character
        assert!(Policy::validate_hash_format("a1b2c3d4e5f6780g").is_err());
        // Empty
        assert!(Policy::validate_hash_format("").is_err());
        // Uppercase is valid for most hex, but is_ascii_hexdigit allows it
        assert!(Policy::validate_hash_format("ABCDEF0123456789").is_ok());
    }

    #[test]
    fn test_load_existing_does_not_overwrite() {
        let dir = TempDir::new().unwrap();
        let mycelium_dir = dir.path().join(".mycelium");
        std::fs::create_dir_all(&mycelium_dir).unwrap();

        // Write a custom policy.md
        let custom_policy = "CUSTOM POLICY";
        std::fs::write(mycelium_dir.join("policy.md"), custom_policy).unwrap();

        // Write a custom safety.json
        let custom_safety = r#"{
            "allowed_tables": ["custom_table"],
            "allowed_columns": ["col_a"],
            "max_tool_calls": 10,
            "max_wall_time_seconds": 60,
            "forbidden_actions": []
        }"#;
        std::fs::write(mycelium_dir.join("safety.json"), custom_safety).unwrap();

        let policy = Policy::load_or_create(dir.path()).unwrap();
        assert_eq!(policy.policy_md(), custom_policy);
        assert_eq!(policy.allowed_tables(), vec!["custom_table"]);
        assert_eq!(policy.allowed_columns(), vec!["col_a"]);
        assert_eq!(policy.max_tool_calls(), 10);
        assert_eq!(policy.max_wall_time_seconds(), 60);
        assert!(policy.forbidden_actions().is_empty());

        // Check the default was NOT written over the custom content
        let content = std::fs::read_to_string(mycelium_dir.join("policy.md")).unwrap();
        assert_eq!(content, custom_policy);
    }
}

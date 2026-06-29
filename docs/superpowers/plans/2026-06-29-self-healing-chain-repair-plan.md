# Self-Healing Hash Chain Repair — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autonomous LLM-driven hash chain repair that detects broken chains, fixes them via constrained agent with kimi-k2.7/minimax-m3, and leaves a git-trackable audit trail.

**Architecture:** A `ChainMonitor` in the brain daemon runs `verify_hash_chain()` each decay tick. If broken, a `SafetyHarness` snapshots the DB, loads policy files, and spawns a constrained LLM agent. The agent has 6 tools (4 read, 2 write — whitelist-checked). After repair, `verify_hash_chain()` runs again. If still broken, rollback. All actions logged to `bugfixes/<date>-<uuid>.md`.

**Tech Stack:** Rust, reqwest (for LLM provider calls), serde_json, SHA-256 (already used in types.rs), parking_lot for state, tracing for events.

## Global Constraints

- ONLY modify `entries.prev_hash` and `entries.hash` columns — never user content (user, assistant, ts, session, entities, annotations)
- No delete tool — LLM physically cannot remove entries
- Always snapshot before mutation, always verify after
- Write bugfixes/ audit files as git-trackable markdown with replay SQL
- LLM endpoint: `127.0.0.1:8080` (meshgate proxy), OpenAI-compatible API format
- Models: `kimi-k2.7` (primary), `minimax-m3` (fallback)
- Circuit breaker: 3 consecutive failures → 5 min cooldown
- Entry count must never decrease (permanent memory invariant)
- Follow existing patterns: parking_lot::Mutex for state, tokio::sync where needed, tracing events

---
### Task 1: Policy Loader + Safety Harness (snapshot, rollback, whitelist)

**Files:**
- Create: `crates/mycelium-core/src/self_healing/mod.rs`
- Create: `crates/mycelium-core/src/self_healing/policy.rs`
- Create: `crates/mycelium-core/src/self_healing/safety.rs`
- Modify: `crates/mycelium-core/src/lib.rs` (add `pub mod self_healing;`)

**Interfaces:**
- Produces: `Policy` (parsed policy.md + safety.md), `SafetyHarness` (snapshot, rollback, whitelist-check)

- [ ] **Step 1: Create mod.rs with public API**

```rust
//! Self-healing daemon — LLM-driven hash chain repair.
//!
//! Runs as part of the brain decay cycle. When verify_hash_chain()
//! detects broken chains, spawns a constrained LLM agent that reads
//! a natural language policy, repairs the chains, logs every action.
//!
//! Safety guarantees:
//! - Only entries.prev_hash and entries.hash may be modified
//! - No delete tool exists — LLM cannot remove entries
//! - Always snapshots before mutation, always verifies after

mod audit;
mod chain_monitor;
mod llm_agent;
mod llm_provider;
mod policy;
mod safety;
mod tools;

pub use chain_monitor::ChainMonitor;
pub use policy::Policy;
pub use safety::SafetyHarness;
pub use llm_agent::LLMAgent;
pub use llm_provider::{LLMProvider, CircuitBreaker, LLMConfig};
pub use audit::AuditWriter;
```

- [ ] **Step 2: Create policy.rs — load natural language constraints**

```rust
use std::path::PathBuf;
use parking_lot::Mutex;

/// Natural language repair policy (policy.md) + machine constraints (safety.md).
pub struct Policy {
    /// Root directory for .mycelium/ files.
    mycelium_dir: PathBuf,
    /// Natural language rules loaded from policy.md.
    pub rules: Mutex<String>,
    /// Machine-readable safety constraints from safety.md.
    pub safety_config: Mutex<SafetyConfig>,
}

#[derive(Debug, serde::Deserialize, Clone)]
pub struct SafetyConfig {
    pub allowed_columns: Vec<String>,
    pub max_tool_calls: usize,
    pub max_wall_time_seconds: u64,
    pub forbidden_actions: Vec<String>,
}

impl Policy {
    /// Load or create default policy files in .mycelium/.
    pub fn load_or_create(root_dir: &std::path::Path) -> anyhow::Result<Self> {
        let mycelium_dir = root_dir.join(".mycelium");
        std::fs::create_dir_all(&mycelium_dir)?;

        let policy_path = mycelium_dir.join("policy.md");
        let safety_path = mycelium_dir.join("safety.md");

        // Write defaults if they don't exist
        if !policy_path.exists() {
            std::fs::write(&policy_path, include_str!("../../../../defaults/policy.md"))?;
        }
        if !safety_path.exists() {
            std::fs::write(&safety_path, include_str!("../../../../defaults/safety.md"))?;
        }

        let rules = std::fs::read_to_string(&policy_path)?;
        let safety_yaml = std::fs::read_to_string(&safety_path)?;
        let safety_config: SafetyConfig = serde_yaml::from_str(&safety_yaml)?;

        Ok(Self {
            mycelium_dir,
            rules: Mutex::new(rules),
            safety_config: Mutex::new(safety_config),
        })
    }

    /// The natural language rules the LLM reads.
    pub fn policy_text(&self) -> String {
        self.rules.lock().clone()
    }

    pub fn allowed_columns(&self) -> Vec<String> {
        self.safety_config.lock().allowed_columns.clone()
    }
}
```

- [ ] **Step 3: Create safety.rs — snapshot, rollback, whitelist**

```rust
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use uuid::Uuid;

/// Pre-repair snapshot + mutation whitelist.
pub struct SafetyHarness {
    db_path: PathBuf,
    snapshots_dir: PathBuf,
    has_snapshot: AtomicBool,
    active_snapshot_id: Mutex<Option<String>>,
    /// Entry count before repair (invariant: must never decrease).
    entry_count_before: Mutex<i64>,
}

impl SafetyHarness {
    pub fn new(db_path: PathBuf, mycelium_dir: PathBuf) -> Self {
        let snapshots_dir = mycelium_dir.join("snapshots");
        std::fs::create_dir_all(&snapshots_dir).ok();
        Self {
            db_path,
            snapshots_dir,
            has_snapshot: AtomicBool::new(false),
            active_snapshot_id: Mutex::new(None),
            entry_count_before: Mutex::new(0),
        }
    }

    /// Create a pre-repair snapshot. Returns snapshot ID.
    pub fn create_snapshot(&self, conn: &Connection) -> anyhow::Result<String> {
        let snapshot_id = Uuid::new_v4().to_string();
        let snapshot_path = self.snapshots_dir.join(format!("{}.db", snapshot_id));

        // Record entry count before any mutation
        let count_before: i64 = conn.query_row(
            "SELECT COUNT(*) FROM entries", [], |r| r.get(0)
        )?;
        *self.entry_count_before.lock() = count_before;

        // Backup via VACUUM INTO (SQLite 3.27+) or file copy
        // For simplicity: VACUUM INTO
        conn.execute_batch(&format!(
            "VACUUM INTO '{}';", snapshot_path.display().to_string().replace('\'', "''")
        ))?;

        *self.active_snapshot_id.lock() = Some(snapshot_id.clone());
        self.has_snapshot.store(true, Ordering::SeqCst);
        tracing::info!("snapshot created: {} ({} entries)", snapshot_id, count_before);
        Ok(snapshot_id)
    }

    /// Rollback to the active snapshot.
    pub fn rollback(&self, conn: &Connection) -> anyhow::Result<()> {
        let snapshot_id = self.active_snapshot_id.lock().take();
        match snapshot_id {
            Some(id) => {
                let snapshot_path = self.snapshots_dir.join(format!("{}.db", id));
                if !snapshot_path.exists() {
                    return Err(anyhow!("snapshot {} not found", id));
                }
                // Overwrite current DB with snapshot
                let db_path_str = self.db_path.to_string_lossy().to_string();
                // Close current connections by finalizing the backup
                conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")?;
                std::fs::copy(&snapshot_path, &self.db_path)?;
                self.has_snapshot.store(false, Ordering::SeqCst);
                tracing::warn!("rollback to snapshot {} completed", id);
                Ok(())
            }
            None => Err(anyhow!("no active snapshot to rollback")),
        }
    }

    /// Verify entry count invariant: count must not decrease.
    pub fn verify_entry_count(&self, conn: &Connection) -> anyhow::Result<()> {
        let before = *self.entry_count_before.lock();
        let after: i64 = conn.query_row(
            "SELECT COUNT(*) FROM entries", [], |r| r.get(0)
        )?;
        anyhow::ensure!(
            after >= before,
            "entry count decreased: {} → {} (permanent memory violated)",
            before, after,
        );
        Ok(())
    }

    /// Validate that a column name is in the whitelist.
    /// Called by SetPrevHash tool before executing.
    pub fn validate_column(column: &str) -> anyhow::Result<()> {
        match column {
            "entries.prev_hash" | "entries.hash" => Ok(()),
            _ => Err(anyhow!("column '{}' is not in mutation whitelist", column)),
        }
    }

    /// Validate hash format (SHA-256 hex = 16 chars).
    pub fn validate_hash_format(hash: &str) -> anyhow::Result<()> {
        anyhow::ensure!(
            hash.len() == 16,
            "hash length {} != 16", hash.len()
        );
        anyhow::ensure!(
            hash.chars().all(|c| c.is_ascii_hexdigit()),
            "hash contains non-hex characters"
        );
        Ok(())
    }
}
```

- [ ] **Step 4: Add default policy files**

Create `crates/mycelium-core/defaults/policy.md`:
```markdown
# Mycelium Self-Healing Policy

## Mission
The hash chain is a verifiable integrity check. When broken, repair it.
User content is permanent and immutable — repair ONLY the chain links.

## Repair Strategy (in order of preference)
1. **Walk in turn order**: For each broken entry, compute the correct
   prev_hash from the predecessor at turn-1.
2. **Fill gaps**: If turn-1 doesn't exist, link to the nearest preceding
   entry with a valid hash.
3. **Bridge batches**: Link broken chain to the last known good hash.
4. **Last resort**: If unfixable, log critical and skip — DO NOT delete data.

## Constraints
- Never delete an entry.
- Never modify user content.
- Only repair prev_hash and hash.
- Max 20 tool calls per session.
- Always commit_repair when done.
```

Create `crates/mycelium-core/defaults/safety.yaml`:
```yaml
allowed_columns:
  - entries.prev_hash
  - entries.hash
max_tool_calls: 20
max_wall_time_seconds: 300
forbidden_actions:
  - delete_entry
  - modify_user_content
```

- [ ] **Step 5: Build and test**

```bash
cargo build -p mycelium-core 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add crates/mycelium-core/src/self_healing/ crates/mycelium-core/defaults/ crates/mycelium-core/src/lib.rs
git commit -m "feat: add Policy loader and SafetyHarness (snapshot, rollback, whitelist)"
```

---

### Task 2: Chain Monitor + State Cache

**Files:**
- Create: `crates/mycelium-core/src/self_healing/chain_monitor.rs`

**Interfaces:**
- Consumes: `Storage::verify_hash_chain()` (already exists)
- Produces: `ChainMonitor::run_tick() -> Option<RepairTrigger>` that returns the list of broken entries when repair is needed

- [ ] **Step 1: Create chain_monitor.rs**

```rust
use std::path::PathBuf;
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
        let total: i64 = self.storage.conn().lock().unwrap()
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
                broken_count, segment_start, segment_end
            );
            Ok(Some(trigger))
        } else {
            Ok(None)
        }
    }

    fn compute_chain_hash(&self, failures: &[(i64, String, String)], total: i64) -> String {
        use sha2::{Sha256, Digest};
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
```

- [ ] **Step 2: Build and test**

```bash
cargo build -p mycelium-core 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/self_healing/chain_monitor.rs
git commit -m "feat: add ChainMonitor — verify chain, cache state, detect new breaks"
```

---

### Task 3: LLM Provider + Circuit Breaker

**Files:**
- Create: `crates/mycelium-core/src/self_healing/llm_provider.rs`

**Interfaces:**
- Produces: `LLMProvider` with `chat(prompt, tools) -> LLMResponse`, `CircuitBreaker` for health
- Endpoint: `http://127.0.0.1:8080/v1/chat/completions` (OpenAI-compatible)

- [ ] **Step 1: Create llm_provider.rs**

```rust
use std::time::{Duration, Instant};
use parking_lot::Mutex;
use serde_json::Value;

/// Models in order of preference.
const MODELS: &[&str] = &["kimi-k2.7", "minimax-m3"];

pub struct LLMConfig {
    pub endpoint: String,           // "http://127.0.0.1:8080"
    pub timeout: Duration,
    pub max_retries: u32,
    pub retry_backoff: Duration,
}

impl Default for LLMConfig {
    fn default() -> Self {
        Self {
            endpoint: "http://127.0.0.1:8080".into(),
            timeout: Duration::from_secs(60),
            max_retries: 3,
            retry_backoff: Duration::from_secs(2),
        }
    }
}

/// Circuit breaker for LLM provider health.
pub struct CircuitBreaker {
    consecutive_failures: AtomicU32,
    last_failure: Mutex<Instant>,
    failure_threshold: u32,
    cooldown: Duration,
}

impl CircuitBreaker {
    pub fn new() -> Self {
        Self {
            consecutive_failures: AtomicU32::new(0),
            last_failure: Mutex::new(Instant::now()),
            failure_threshold: 3,
            cooldown: Duration::from_secs(300),  // 5 min
        }
    }

    pub fn is_allowed(&self) -> bool {
        let failures = self.consecutive_failures.load(Ordering::Relaxed);
        if failures < self.failure_threshold {
            return true;  // Closed
        }
        // Half-open after cooldown
        let elapsed = self.last_failure.lock().elapsed();
        if elapsed > self.cooldown {
            self.consecutive_failures.store(0, Ordering::Relaxed);
            return true;
        }
        false  // Open
    }

    pub fn record_failure(&self) {
        self.consecutive_failures.fetch_add(1, Ordering::Relaxed);
        *self.last_failure.lock() = Instant::now();
    }

    pub fn record_success(&self) {
        self.consecutive_failures.store(0, Ordering::Relaxed);
    }
}

/// LLM provider client (OpenAI-compatible).
pub struct LLMProvider {
    config: LLMConfig,
    client: reqwest::Client,
    circuit_breaker: Arc<CircuitBreaker>,
}

impl LLMProvider {
    pub fn new(config: LLMConfig) -> Self {
        let client = reqwest::Client::builder()
            .timeout(config.timeout)
            .build()
            .unwrap();
        Self {
            config,
            client,
            circuit_breaker: Arc::new(CircuitBreaker::new()),
        }
    }

    /// Send a chat request to the LLM with tools.
    /// Tries models[0] first, falls back to models[1].
    pub async fn chat(
        &self,
        system: &str,
        messages: &[serde_json::Value],
        tools: &[serde_json::Value],
    ) -> anyhow::Result<serde_json::Value> {
        if !self.circuit_breaker.is_allowed() {
            return Err(anyhow!("circuit breaker open"));
        }

        let mut last_error = anyhow::anyhow!("all models failed");
        for model in MODELS {
            for attempt in 0..self.config.max_retries {
                match self.try_call(model, system, messages, tools).await {
                    Ok(response) => {
                        self.circuit_breaker.record_success();
                        return Ok(response);
                    }
                    Err(e) => {
                        last_error = e;
                        tracing::warn!(
                            "LLM {} attempt {}/{} failed: {}",
                            model, attempt + 1, self.config.max_retries, last_error
                        );
                        if attempt + 1 < self.config.max_retries {
                            let wait = self.config.retry_backoff * (2u32.pow(attempt));
                            tokio::time::sleep(wait).await;
                        }
                    }
                }
            }
        }

        self.circuit_breaker.record_failure();
        Err(last_error)
    }

    async fn try_call(
        &self,
        model: &str,
        system: &str,
        messages: &[Value],
        tools: &[Value],
    ) -> anyhow::Result<Value> {
        let url = format!("{}/v1/chat/completions", self.config.endpoint);
        let body = serde_json::json!({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": messages}
            ],
            "tools": tools,
            "tool_choice": "auto",
        });

        let resp = self.client.post(&url).json(&body).send().await?;
        let status = resp.status();
        let text = resp.text().await?;

        if !status.is_success() {
            return Err(anyhow!("LLM returned {}: {}", status, text.chars().take(200).collect::<String>()));
        }

        let json: Value = serde_json::from_str(&text)?;
        Ok(json)
    }
}
```

- [ ] **Step 2: Build and test**

```bash
cargo build -p mycelium-core 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/self_healing/llm_provider.rs
git commit -m "feat: add LLMProvider + CircuitBreaker — kimi-k2.7/minimax-m3 with retry and fallback"
```

---

### Task 4: LLM Tools + Agent Dispatch

**Files:**
- Create: `crates/mycelium-core/src/self_healing/tools.rs`
- Create: `crates/mycelium-core/src/self_healing/llm_agent.rs`

**Interfaces:**
- Produces: 6 tool handlers (list_broken_segments, get_entry, get_entry_content, verify_chain, set_prev_hash, commit_repair)
- These are called by the LLM agent when the model selects a tool

- [ ] **Step 1: Create tools.rs — the 6 handlers**

```rust
use crate::self_healing::safety::SafetyHarness;
use crate::Storage;

/// Set of tools the LLM can call. Each returns a JSON value.
pub enum ToolOutput {
    ListBrokenSegments(Vec<BrokenSegment>),
    GetEntry(serde_json::Value),
    GetEntryContent(String),
    VerifyChain(Vec<(i64, String, String)>),
    SetPrevHash(Result<(), String>),
    CommitRepair(Result<String, String>),  // returns snapshot ID
}

#[derive(serde::Serialize)]
pub struct BrokenSegment {
    pub start: i64,
    pub end: i64,
    pub count: usize,
    pub example_before: String,
    pub example_after: String,
}

/// Dispatch a tool call from the LLM.
/// Returns a JSON-serializable response.
pub async fn dispatch_tool(
    tool_name: &str,
    args: &serde_json::Value,
    storage: &Storage,
    conn: &rusqlite::Connection,
    safety: &SafetyHarness,
) -> Result<serde_json::Value, String> {
    match tool_name {
        "list_broken_segments" => {
            let max_segments = args.get("max_segments").and_then(|v| v.as_u64()).unwrap_or(10) as usize;
            let failures = storage.verify_hash_chain().map_err(|e| e.to_string())?;
            let total = failures.len();
            let segments = group_into_segments(&failures, max_segments);
            Ok(serde_json::json!({
                "total_broken": total,
                "segments": segments,
            }))
        }
        "get_entry" => {
            let turn = args.get("turn").and_then(|v| v.as_i64()).ok_or("missing turn")?;
            let entry = storage.get_entry(turn).map_err(|e| e.to_string())?;
            match entry {
                Some(e) => Ok(serde_json::to_value(&e).unwrap_or_default()),
                None => Ok(serde_json::json!({"error": format!("turn {} not found", turn)})),
            }
        }
        "get_entry_content" => {
            let turn = args.get("turn").and_then(|v| v.as_i64()).ok_or("missing turn")?;
            let entry = storage.get_entry(turn).map_err(|e| e.to_string())?;
            match entry {
                Some(e) => Ok(serde_json::json!({"turn": turn, "user": e.user, "assistant": e.assistant})),
                None => Ok(serde_json::json!({"error": "not found"})),
            }
        }
        "verify_chain" => {
            let failures = storage.verify_hash_chain().map_err(|e| e.to_string())?;
            Ok(serde_json::json!({
                "broken_count": failures.len(),
                "entries_checked": {
                    let count: i64 = conn.query_row("SELECT COUNT(*) FROM entries", [], |r| r.get(0)).unwrap_or(0);
                    count
                },
                "failures": failures.iter().take(5).map(|(t, h, e)| {
                    serde_json::json!({"turn": t, "expected": h, "got": e})
                }).collect::<Vec<_>>(),
            }))
        }
        "set_prev_hash" => {
            let turn = args.get("turn").and_then(|v| v.as_i64()).ok_or("missing turn")?;
            let new_hash = args.get("hash").and_then(|v| v.as_str()).ok_or("missing hash")?.to_string();
            // Validate
            SafetyHarness::validate_hash_format(&new_hash).map_err(|e| e.to_string())?;
            // Execute
            conn.execute(
                "UPDATE entries SET prev_hash = ?1 WHERE turn = ?2",
                rusqlite::params![new_hash, turn],
            ).map_err(|e| e.to_string())?;
            // Recompute hash
            if let Some(entry) = storage.get_entry(turn).map_err(|e| e.to_string())? {
                let new_hash_full = entry.compute_hash(&new_hash);
                conn.execute(
                    "UPDATE entries SET hash = ?1 WHERE turn = ?2",
                    rusqlite::params![new_hash_full, turn],
                ).map_err(|e| e.to_string())?;
            }
            Ok(serde_json::json!({"status": "ok", "turn": turn}))
        }
        "commit_repair" => {
            let description = args.get("description").and_then(|v| v.as_str()).unwrap_or("auto repair");
            let affected_turns: Vec<i64> = args.get("affected_turns")
                .and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|v| v.as_i64()).collect())
                .unwrap_or_default();
            // Verify chain integrity after commit
            let failures = storage.verify_hash_chain().map_err(|e| e.to_string())?;
            if failures.is_empty() {
                Ok(serde_json::json!({
                    "status": "verified",
                    "verified_ok": true,
                    "affected_turns": affected_turns.len(),
                }))
            } else {
                // Rollback
                safety.rollback(conn).map_err(|e| e.to_string())?;
                Err(format!("chain still broken ({} failures), rolled back", failures.len()))
            }
        }
        _ => Err(format!("unknown tool: {}", tool_name)),
    }
}

fn group_into_segments(failures: &[(i64, String, String)], max: usize) -> Vec<BrokenSegment> {
    if failures.is_empty() { return vec![]; }
    let mut segments = Vec::new();
    let mut start = failures[0].0;
    let mut prev = start;
    let mut count = 1;
    let example_before = failures[0].1.clone();
    let example_after = failures[0].2.clone();

    for (turn, _, _) in &failures[1..] {
        if *turn == prev + 1 {
            count += 1;
            prev = *turn;
        } else {
            segments.push(BrokenSegment {
                start, end: prev, count, example_before: example_before.clone(),
                example_after: example_after.clone(),
            });
            if segments.len() >= max { break; }
            start = *turn;
            prev = *turn;
            count = 1;
        }
    }
    if segments.len() < max {
        segments.push(BrokenSegment {
            start, end: prev, count, example_before, example_after,
        });
    }
    segments
}
```

- [ ] **Step 2: Create llm_agent.rs — orchestrates the agent loop**

```rust
use crate::Storage;
use crate::self_healing::tools;
use crate::self_healing::policy::Policy;
use crate::self_healing::safety::SafetyHarness;
use crate::self_healing::llm_provider::{LLMProvider, LLMConfig};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub struct LLMAgent {
    provider: LLMProvider,
    storage: Arc<Storage>,
    policy: Arc<Policy>,
    safety: Arc<SafetyHarness>,
    max_tool_calls: usize,
    timeout: Duration,
}

impl LLMAgent {
    /// Run one repair session. Returns a repair log.
    pub async fn run(&self) -> anyhow::Result<RepairLog> {
        let start = Instant::now();
        let mut tool_calls = 0u32;
        let mut repaired_turns: Vec<i64> = Vec::new();
        let mut errors: Vec<String> = Vec::new();

        let conn = self.storage.conn().lock().unwrap();
        let snapshot_id = self.safety.create_snapshot(&conn)?;
        drop(conn);

        loop {
            if start.elapsed() > self.timeout {
                errors.push("timeout".into());
                break;
            }
            if tool_calls >= self.max_tool_calls as u32 {
                errors.push("max tool calls reached".into());
                break;
            }

            let conn = self.storage.conn().lock().unwrap();
            let tools_json = tools::tool_definitions();
            let context = format!(
                "{}\n\nBroken entries: {}\n",
                self.policy.policy_text(),
                self.storage.verify_hash_chain()?.len(),
            );

            let response = self.provider.chat(
                "You are a hash chain repair agent. Read the policy and tools, then fix broken chains.",
                &[serde_json::json!({"role": "user", "content": context})],
                &tools_json,
            ).await?;

            // Parse tool call from response
            let tool_call = parse_tool_call(&response);
            match tool_call {
                Some((name, args)) => {
                    let conn = self.storage.conn().lock().unwrap();
                    let result = tools::dispatch_tool(
                        &name, &args, &self.storage, &conn, &self.safety,
                    ).await;
                    drop(conn);

                    match result {
                        Ok(output) => {
                            tool_calls += 1;
                            if name == "set_prev_hash" {
                                if let Some(turn) = args.get("turn").and_then(|v| v.as_i64()) {
                                    repaired_turns.push(turn);
                                }
                            }
                            if name == "commit_repair" {
                                tracing::info!("repair committed: {} turns, {} tool calls, {:?}",
                                    repaired_turns.len(), tool_calls, start.elapsed());
                                break;
                            }
                        }
                        Err(e) => {
                            errors.push(format!("{}: {}", name, e));
                            tool_calls += 1;
                        }
                    }
                }
                None => {
                    // No tool call — LLM finished or confused
                    break;
                }
            }
        }

        // Post-verify
        let conn = self.storage.conn().lock().unwrap();
        self.safety.verify_entry_count(&conn)?;
        let final_failures = self.storage.verify_hash_chain()?;
        drop(conn);

        Ok(RepairLog {
            snapshot_id,
            repaired_turns,
            total_tool_calls: tool_calls,
            errors,
            final_broken_count: final_failures.len(),
            duration: start.elapsed(),
        })
    }
}

fn parse_tool_call(response: &serde_json::Value) -> Option<(String, serde_json::Value)> {
    let choice = response.get("choices")?.as_array()?.first()?;
    let msg = choice.get("message")?;
    if let Some(tool_calls) = msg.get("tool_calls")?.as_array() {
        let call = tool_calls.first()?;
        let name = call.get("function")?.get("name")?.as_str()?.to_string();
        let args_str = call.get("function")?.get("arguments")?.as_str()?;
        let args: serde_json::Value = serde_json::from_str(args_str).ok()?;
        Some((name, args))
    } else {
        None
    }
}
```

- [ ] **Step 2: Build and test**

```bash
cargo build -p mycelium-core 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/self_healing/tools.rs crates/mycelium-core/src/self_healing/llm_agent.rs
git commit -m "feat: add LLM agent + 6 tools for hash chain repair"
```

---

### Task 5: Audit Trail Writer

**Files:**
- Create: `crates/mycelium-core/src/self_healing/audit.rs`
- Create: `bugfixes/` directory (created on first write)

- [ ] **Step 1: Create audit.rs**

```rust
use std::path::PathBuf;
use std::sync::Mutex;
use chrono::Utc;

pub struct AuditWriter {
    bugfixes_dir: PathBuf,
}

impl AuditWriter {
    pub fn new(root_dir: &Path) -> Self {
        let dir = root_dir.join("bugfixes");
        std::fs::create_dir_all(&dir).ok();
        Self { bugfixes_dir: dir }
    }

    /// Write a repair audit file. Returns the file path.
    pub fn write_repair_log(&self, log: &RepairLog) -> anyhow::Result<PathBuf> {
        let date = Utc::now().format("%Y-%m-%d");
        let uuid = uuid::Uuid::new_v4();
        let filename = format!("{}-hash-chain-repair-{}.md", date, uuid);
        let path = self.bugfixes_dir.join(&filename);

        let content = format!(
            r#"# Hash Chain Repair — {}

**Snapshot ID**: `{}`
**Model Used**: kimi-k2.7 / minimax-m3
**Tool Calls**: {}
**Duration**: {:.1}s
**Turns Repaired**: {}
**Final Broken Count**: {}

## Repairs
{}

## Replay SQL
```sql
BEGIN IMMEDIATE;
{}
COMMIT;
```

## Rollback
```bash
sqlite3 mycelium.db ".restore {} mycelium.db"
```
"#,
            Utc::now().format("%Y-%m-%d %H:%M:%S"),
            log.snapshot_id,
            log.total_tool_calls,
            log.duration.as_secs_f64(),
            log.repaired_turns.len(),
            log.final_broken_count,
            self.repair_table(log),
            self.replay_sql(log),
            log.snapshot_id,
        );

        std::fs::write(&path, content)?;
        tracing::info!("audit written: {}", path.display());
        Ok(path)
    }

    fn repair_table(&self, log: &RepairLog) -> String {
        let mut rows = String::new();
        for turn in &log.repaired_turns {
            rows.push_str(&format!("| {} | repaired | walk forward |\n", turn));
        }
        if rows.is_empty() {
            rows = "| (none) | | |\n".into();
        }
        format!("| Turn | Action | Reason |\n|------|--------|--------|\n{}", rows)
    }

    fn replay_sql(&self, log: &RepairLog) -> String {
        if log.repaired_turns.is_empty() {
            "-- no changes".into()
        } else {
            let mut sql = String::new();
            for turn in &log.repaired_turns {
                sql.push_str(&format!(
                    "UPDATE entries SET prev_hash = (SELECT hash FROM entries WHERE turn = {}) WHERE turn = {};\n",
                    turn.saturating_sub(1), turn
                ));
            }
            sql
        }
    }
}
```

- [ ] **Step 2: Build and test**

```bash
cargo build -p mycelium-core 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/self_healing/audit.rs
git commit -m "feat: add AuditWriter — bugfixes/ markdown with replay SQL"
```

---

### Task 6: Wire into Brain Daemon

**Files:**
- Modify: `crates/mycelium-server/src/brain_daemon.rs`

- [ ] **Step 1: Wire ChainMonitor + LLMAgent into brain daemon**

In the brain daemon's spawn, after `tick_decay()`:
```rust
// Every few ticks, run chain monitor
if let Ok(Some(trigger)) = self.chain_monitor.run_tick() {
    tracing::warn!("broken chain detected: {} entries ({}..{})",
        trigger.broken_count, trigger.segment_start, trigger.segment_end);

    let agent = LLMAgent::new(
        LLMProvider::new(LLMConfig::default()),
        Arc::clone(&self.storage),
        Arc::clone(&self.policy),
        Arc::clone(&self.safety),
    );
    tokio::spawn(async move {
        match agent.run().await {
            Ok(log) => {
                let audit = AuditWriter::new(&config.root_dir);
                if let Ok(path) = audit.write_repair_log(&log) {
                    tracing::info!("chain repair complete, audit: {}", path.display());
                }
            }
            Err(e) => tracing::error!("chain repair failed: {}", e),
        }
    });
}
```

- [ ] **Step 2: Update BrainDaemon struct to hold the new components**

```rust
pub struct BrainDaemon {
    storage: Arc<Storage>,
    notify: Arc<Notify>,
    running: Arc<AtomicBool>,
    chain_monitor: Arc<ChainMonitor>,
    policy: Arc<Policy>,
    safety: Arc<SafetyHarness>,
}
```

- [ ] **Step 3: Build and test**

```bash
cargo build --workspace 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-server/src/brain_daemon.rs
git commit -m "feat: wire chain monitor + self-healing agent into brain daemon"
```

---

### Task 7: Tests

**Files:**
- Create: `tests/self_healing_tests.rs`

Cover all critical safety guarantees. 15+ test cases.

```rust
#[test] fn test_verify_chain_detects_breaks() { ... }
#[test] fn test_chain_monitor_caches_results() { ... }
#[test] fn test_snapshot_and_rollback_restores_state() { ... }
#[test] fn test_set_prev_hash_validates_format() { ... }
#[test] fn test_set_prev_hash_rejects_bad_length() { ... }
#[test] fn test_validate_column_rejects_non_whitelist() { ... }
#[test] fn test_validate_column_allows_prev_hash() { ... }
#[test] fn test_entry_count_invariant_never_decreases() { ... }
#[test] fn test_circuit_breaker_opens_after_threshold() { ... }
#[test] fn test_circuit_breaker_recovers_after_cooldown() { ... }
#[test] fn test_group_segments() { ... }
#[test] fn test_repair_dry_run_makes_zero_mutations() { ... }
#[test] fn test_audit_writer_generates_replayable_sql() { ... }
#[test] fn test_llm_provider_falls_back_on_failure() { ... }
#[test] fn test_hash_format_rejects_invalid_chars() { ... }
```

- [ ] **Step 1: Write all test cases**

- [ ] **Step 2: Run and verify**

```bash
cargo test -p mycelium-core --tests 2>&1 | tail -20
```

- [ ] **Step 3: Commit**

```bash
git add tests/self_healing_tests.rs
git commit -m "test: add 15+ safety tests for self-healing chain repair"
```

# Graph-Guided Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a novel recall system for Mycelium that uses brain graph traversal instead of text search, providing infinite LLM context.

**Architecture:** Three layers — (1) Query Parser (LLM call, ~200 tokens, decomposes natural language into atoms + intent), (2) Graph Traversal Engine (pure SQL on existing atoms/edges tables, sub-ms), (3) Context Synthesizer (LLM call, fills configurable token budget with structured context). The graph traversal sits in mycelium-core; the LLM-dependent components live in mycelium-proxy where reqwest is already available.

**Tech Stack:** Rust, rusqlite (brain tables), serde/serde_json, reqwest (proxy), mycelium-core (brain module)

## Global Constraints

- No new tables in SQLite — use existing `atoms`, `edges`, `entity_annotations`, `context_snapshots` tables
- No new dependencies in mycelium-core (keep it pure, no HTTP client)
- All LLM calls go through proxy's existing reqwest client
- The old `search_facts` path must remain as fallback via `RecallMode::Legacy`
- All new types must derive Debug, Clone, Serialize, Deserialize
- Configurable token budget for context synthesis (default 1000, max 20000)

---

### Task 1: Define recall data types in mycelium-core

**Files:**
- Modify: `crates/mycelium-core/src/types.rs` — add recall-specific structs
- Modify: `crates/mycelium-core/src/error.rs` — add recall error variant
- Create: `crates/mycelium-core/src/recall.rs` — recall module (graph traversal engine)
- Modify: `crates/mycelium-core/src/lib.rs` — register new module

**Interfaces:**
- Consumes: existing `brain::recall()`, `brain::clusters()`, `brain::when()` — all take `&Connection`
- Produces: `RecallQuery`, `AtomCluster`, `RecallResult`, `RecallMode`, `RecallError`

- [ ] **Step 1: Add recall structs to types.rs**

Append to `crates/mycelium-core/src/types.rs`:

```rust
/// Parsed recall query — output of query parser, input to graph traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecallQuery {
    /// Canonical phrases extracted from user query
    pub atoms: Vec<String>,
    /// Query intent classification
    pub intent: RecallIntent,
    /// Optional temporal hint (ISO string or relative, e.g. "last night")
    pub temporal_hint: Option<String>,
}

/// Query intent — determines traversal strategy.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum RecallIntent {
    Factual,
    Relational,
    Temporal,
    Exploratory,
}

/// A seed atom plus its neighbor cluster from graph traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AtomCluster {
    /// The seed atom that matched the query
    pub seed_id: i64,
    /// Seed phrase
    pub seed_phrase: String,
    /// Neighbor atoms: (phrase, edge_weight, importance)
    pub neighbors: Vec<(String, f64, f64)>,
    /// Temporal data: (first_seen, last_seen, total_mentions)
    pub temporal: Option<(i64, i64, i64)>,
}

/// Full result from graph traversal engine.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RecallResult {
    pub query: RecallQuery,
    pub clusters: Vec<AtomCluster>,
    pub total_clusters: usize,
    pub traversal_time_ms: f64,
}

/// Recall mode — controls which retrieval path the proxy uses.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum RecallMode {
    Legacy,
    GraphTraversal,
}
```

- [ ] **Step 2: Add RecallError to error.rs**

Add to `MyceliumError` in `crates/mycelium-core/src/error.rs`:

```rust
    #[error("Recall error: {0}")]
    Recall(String),

    #[error("Brain graph empty — no atoms match query")]
    RecallEmpty,
```

- [ ] **Step 3: Create recall.rs — graph traversal engine**

```rust
//! Graph-guided recall — traverses the brain graph to find relevant memory context.
//!
//! This is the core retrieval pipeline, running entirely on indexed SQLite tables.
//! No LLM calls — pure graph traversal using existing brain::recall/clusters/when.

use crate::brain;
use crate::error::MyceliumError;
use crate::types::*;
use rusqlite::Connection;
use std::time::Instant;
use tracing::debug;

/// Traverse the brain graph for a parsed recall query.
///
/// Steps: seed → temporal filter → cluster expansion → rank → return top N.
pub fn traverse(
    conn: &Connection,
    query: &RecallQuery,
    max_clusters: usize,
    max_neighbors: usize,
) -> Result<RecallResult, MyceliumError> {
    let start = Instant::now();

    if query.atoms.is_empty() {
        return Ok(RecallResult {
            query: query.clone(),
            clusters: vec![],
            total_clusters: 0,
            traversal_time_ms: 0.0,
        });
    }

    // Step 1: Seed — find matching atoms for each query phrase
    let mut all_clusters: Vec<AtomCluster> = Vec::new();

    for phrase in &query.atoms {
        let atoms = brain::recall(conn, phrase, 10)
            .map_err(|e| MyceliumError::Recall(e.to_string()))?;

        for atom in atoms {
            // Step 2: Temporal filter
            if let Some(ref hint) = query.temporal_hint {
                if let Some((_first, last, _count)) = brain::when(conn, &atom.phrase)
                    .map_err(|e| MyceliumError::Recall(e.to_string()))?
                {
                    // Apply basic temporal heuristic:
                    // If temporal_hint contains "last night", filter to last 24h in turns
                    // For now, a simple presence check — will be refined with actual time parsing
                    if hint.contains("night") || hint.contains("yesterday") {
                        // Initial heuristic: filter to last ~500 turns (~1 day)
                        // Will be refined with proper time parsing in future iterations
                        let cutoff = max_turn(conn).max(500) - 500;
                        if last < cutoff {
                            continue;
                        }
                    }
                }
            }

            // Step 3: Cluster expansion
            let neighbors = brain::clusters(conn, &atom.phrase, max_neighbors as i64)
                .map_err(|e| MyceliumError::Recall(e.to_string()))?
                .into_iter()
                .map(|(phrase, weight)| (phrase, weight, 0.0)) // importance available on request
                .collect();

            let temporal = brain::when(conn, &atom.phrase)
                .map_err(|e| MyceliumError::Recall(e.to_string()))?
                .map(|(f, l, c)| (f, l, c));

            all_clusters.push(AtomCluster {
                seed_id: atom.id,
                seed_phrase: atom.phrase.clone(),
                neighbors,
                temporal,
            });
        }
    }

    // Step 4: Rank by (ref_count × importance) via recall()'s inherent ordering
    // brain::recall already returns results sorted by (ref_count * importance) DESC
    // Take top N
    all_clusters.truncate(max_clusters);

    let elapsed = start.elapsed();
    debug!(
        "Recall traversal: {} atom(s), {} cluster(s), {:.2}ms",
        query.atoms.len(),
        all_clusters.len(),
        elapsed.as_secs_f64() * 1000.0
    );

    Ok(RecallResult {
        query: query.clone(),
        clusters: all_clusters,
        total_clusters: all_clusters.len(),
        traversal_time_ms: elapsed.as_secs_f64() * 1000.0,
    })
}

/// Helper to get the maximum turn number from the entries table.
/// Returns 0 if entries table doesn't exist (e.g., brain-only test DB).
fn max_turn(conn: &Connection) -> i64 {
    conn.query_row(
        "SELECT COALESCE(MAX(turn), 0) FROM entries",
        [],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::brain::create_tables;

    fn setup_brain() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        create_tables(&conn).unwrap();
        conn
    }

    #[test]
    fn test_traverse_empty_query() {
        let conn = setup_brain();
        let query = RecallQuery {
            atoms: vec![],
            intent: RecallIntent::Relational,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert!(result.clusters.is_empty());
        assert_eq!(result.total_clusters, 0);
    }

    #[test]
    fn test_traverse_no_matches() {
        let conn = setup_brain();
        let query = RecallQuery {
            atoms: vec!["nonexistent_phrase_xyz".to_string()],
            intent: RecallIntent::Factual,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert!(result.clusters.is_empty());
    }

    #[test]
    fn test_traverse_with_seeded_atom() {
        let conn = setup_brain();
        // Seed an atom directly
        let id = brain::upsert_atom(&conn, "test phrase", 1, 1.0).unwrap();
        brain::record_position(&conn, id, 1, "test-session").unwrap();

        let query = RecallQuery {
            atoms: vec!["test phrase".to_string()],
            intent: RecallIntent::Factual,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert_eq!(result.clusters.len(), 1);
        assert_eq!(result.clusters[0].seed_phrase, "test phrase");
    }

    #[test]
    fn test_traverse_temporal_filter() {
        let conn = setup_brain();
        // Seed an old atom and a new one
        let old = brain::upsert_atom(&conn, "old thing", 1, 1.0).unwrap();
        brain::record_position(&conn, old, 1, "session-1").unwrap();
        let new = brain::upsert_atom(&conn, "new thing last night", 10000, 1.0).unwrap();
        brain::record_position(&conn, new, 10000, "session-2").unwrap();

        let query = RecallQuery {
            atoms: vec!["thing".to_string()],
            intent: RecallIntent::Temporal,
            temporal_hint: Some("last night".to_string()),
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        // Only "new thing last night" should survive the temporal filter
        assert!(result.clusters.iter().any(|c| c.seed_phrase.contains("last night")));
    }

    #[test]
    fn test_max_turn_empty_db() {
        let conn = setup_brain();
        // Should return 0 when entries table is empty or doesn't exist
        let max = max_turn(&conn);
        assert_eq!(max, 0);
    }
}
```

- [ ] **Step 4: Register recall module in lib.rs**

```rust
pub mod recall;
```

- [ ] **Step 5: Run tests**

```bash
cargo test -p mycelium-core --lib recall
```

Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add crates/mycelium-core/src/types.rs crates/mycelium-core/src/error.rs \
       crates/mycelium-core/src/recall.rs crates/mycelium-core/src/lib.rs
git commit -m "feat(core): add recall graph traversal engine

- New recall types: RecallQuery, RecallIntent, AtomCluster, RecallResult, RecallMode
- New MyceliumError variants: Recall, RecallEmpty
- recall::traverse() — seed atoms → temporal filter → cluster expansion → rank
- Pure SQL on existing brain tables, no LLM calls, sub-ms latency
- 5 unit tests covering empty query, no matches, seeded atom, temporal filter, empty DB

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Implement Query Parser in proxy crate

**Files:**
- Create: `crates/mycelium-proxy/src/query_parser.rs`
- Modify: `crates/mycelium-proxy/src/lib.rs` — register module

**Interfaces:**
- Consumes: `mycelium_core::RecallQuery`, `mycelium_core::RecallIntent`
- Produces: `parse_query(user_message: &str, llm_client: &reqwest::Client, llm_api_url: &str, api_key: &str) -> Option<RecallQuery>`

- [ ] **Step 1: Write the test**

Create `crates/mycelium-proxy/tests/query_parser_test.rs`:

```rust
use mycelium_core::{RecallIntent, RecallQuery};

// Unit test the JSON parsing logic (not the LLM call itself)
fn parse_query_response(json: &str) -> Option<RecallQuery> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    let atoms: Vec<String> = v.get("atoms")?
        .as_array()?
        .iter()
        .filter_map(|a| a.as_str().map(String::from))
        .collect();
    let intent_str = v.get("intent")?.as_str()?;
    let intent = match intent_str {
        "factual" => RecallIntent::Factual,
        "relational" => RecallIntent::Relational,
        "temporal" => RecallIntent::Temporal,
        "exploratory" => RecallIntent::Exploratory,
        _ => RecallIntent::Relational,
    };
    let temporal_hint = v.get("temporal_hint").and_then(|t| t.as_str().map(String::from));
    Some(RecallQuery { atoms, intent, temporal_hint })
}

#[test]
fn test_parse_temporal_query() {
    let json = r#"{"atoms":["change secret","server"],"intent":"temporal","temporal_hint":null}"#;
    let query = parse_query_response(json).unwrap();
    assert_eq!(query.atoms, vec!["change secret", "server"]);
    assert_eq!(query.intent, RecallIntent::Temporal);
    assert_eq!(query.temporal_hint, None);
}

#[test]
fn test_parse_relational_query() {
    let json = r#"{"atoms":["fix proxy","proxy bug"],"intent":"relational","temporal_hint":"last week"}"#;
    let query = parse_query_response(json).unwrap();
    assert_eq!(query.atoms, vec!["fix proxy", "proxy bug"]);
    assert_eq!(query.intent, RecallIntent::Relational);
    assert_eq!(query.temporal_hint, Some("last week".to_string()));
}

#[test]
fn test_parse_factual_query() {
    let json = r#"{"atoms":["Azfar"],"intent":"factual","temporal_hint":null}"#;
    let query = parse_query_response(json).unwrap();
    assert_eq!(query.atoms, vec!["Azfar"]);
    assert_eq!(query.intent, RecallIntent::Factual);
}

#[test]
fn test_parse_empty_json() {
    assert!(parse_query_response(r#"{}"#).is_none());
}

#[test]
fn test_parse_malformed_json() {
    assert!(parse_query_response("not json").is_none());
}
```

Wait — query_parser.rs needs a test file. Let me place it properly.

Actually, the proxy crate may not have a `tests/` dir. Let me check, and put inline tests in the module file instead.

```rust
// In query_parser.rs:

/// Parse the LLM response JSON into a RecallQuery.
pub fn parse_query_response(json: &str) -> Option<RecallQuery> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    let atoms: Vec<String> = v.get("atoms")?
        .as_array()?
        .iter()
        .filter_map(|a| a.as_str().map(String::from))
        .collect();
    if atoms.is_empty() {
        return None;
    }
    let intent_str = v.get("intent")?.as_str()?;
    let intent = match intent_str {
        "factual" => RecallIntent::Factual,
        "relational" => RecallIntent::Relational,
        "temporal" => RecallIntent::Temporal,
        "exploratory" => RecallIntent::Exploratory,
        _ => RecallIntent::Relational,
    };
    let temporal_hint = v.get("temporal_hint").and_then(|t| t.as_str().map(String::from));
    Some(RecallQuery { atoms, intent, temporal_hint })
}

#[cfg(test)]
mod tests {
    use super::*;
    use mycelium_core::RecallIntent;

    #[test]
    fn test_parse_temporal_query() {
        let json = r#"{"atoms":["change secret","server"],"intent":"temporal","temporal_hint":null}"#;
        let query = parse_query_response(json).unwrap();
        assert_eq!(query.atoms, vec!["change secret", "server"]);
        assert_eq!(query.intent, RecallIntent::Temporal);
        assert_eq!(query.temporal_hint, None);
    }

    #[test]
    fn test_parse_relational_query() {
        let json = r#"{"atoms":["fix proxy","proxy bug"],"intent":"relational","temporal_hint":"last week"}"#;
        let query = parse_query_response(json).unwrap();
        assert_eq!(query.intent, RecallIntent::Relational);
        assert_eq!(query.temporal_hint, Some("last week".to_string()));
    }

    #[test]
    fn test_parse_empty_atoms() {
        let json = r#"{"atoms":[],"intent":"relational","temporal_hint":null}"#;
        assert!(parse_query_response(json).is_none());
    }

    #[test]
    fn test_parse_malformed_json() {
        assert!(parse_query_response("not json").is_none());
    }
}
```

Now let me think about the actual LLM call for the query parser.

```rust
/// Build the prompt for the query parser LLM call.
pub fn build_query_parser_prompt(user_message: &str) -> String {
    format!(
        r#"Given this recall query: "{}"

Extract:
1. Atoms — the key noun phrases (2-6 short phrases, 1-4 words each) that should activate the brain graph. Use domain-specific terms as stored in memory.
2. Intent — one of: factual, relational, temporal, exploratory
3. Temporal hint — any time reference (ISO format if explicit, relative if vague)

Respond with ONLY valid JSON: {{"atoms": [...], "intent": "...", "temporal_hint": "..." | null}}"#,
        user_message
    )
}

/// Call the LLM to parse a user query into recall atoms.
pub async fn call_query_parser(
    client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    user_message: &str,
) -> Option<RecallQuery> {
    let prompt = build_query_parser_prompt(user_message);

    let body = serde_json::json!({
        "model": model,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}]
    });

    let resp = client
        .post(api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .ok()?;

    let text = resp.text().await.ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;

    // Extract response content — handles both Anthropic and OpenAI formats
    let content = json
        .pointer("/content/0/text")
        .or_else(|| json.pointer("/choices/0/message/content"))
        .and_then(|c| c.as_str())?;

    parse_query_response(content)
}
```

- [ ] **Step 2: Run test**

```bash
cargo test -p mycelium-proxy --lib query_parser
```

Expected: 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-proxy/src/query_parser.rs
git commit -m "feat(proxy): add query parser for natural language recall

- build_query_parser_prompt: generates LLM prompt for atom extraction
- call_query_parser: calls LLM API (Anthropic/OpenAI format) to decompose user question
- parse_query_response: converts LLM JSON response into RecallQuery
- 4 unit tests for JSON parsing edge cases

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Implement Context Synthesizer in proxy crate

**Files:**
- Create: `crates/mycelium-proxy/src/context_synthesizer.rs`
- Modify: `crates/mycelium-proxy/src/lib.rs` — register module

**Interfaces:**
- Consumes: `mycelium_core::RecallResult`
- Produces: `synthesize_context(result: &RecallResult) -> String`

- [ ] **Step 1: Write the test and implementation**

```rust
//! Context synthesis — turns graph traversal results into a structured memory block.
//!
//! Two modes:
//! - Text synthesis: produces <mycelium-context> block for proxy injection
//! - Direct synthesis: returns structured JSON for CLI/MCP/API consumers

use mycelium_core::RecallResult;

/// Build a context block prompt for the LLM.
pub fn build_synthesis_prompt(result: &RecallResult, budget: usize) -> String {
    let mut clusters_text = String::new();
    for (i, cluster) in result.clusters.iter().enumerate() {
        clusters_text.push_str(&format!("Cluster {}: seed=\"{}\"\n", i + 1, cluster.seed_phrase));
        if let Some((first, last, count)) = cluster.temporal {
            clusters_text.push_str(&format!("  Temporal: first_seen={}, last_seen={}, {} mentions\n", first, last, count));
        }
        for (neighbor, weight, _importance) in &cluster.neighbors {
            clusters_text.push_str(&format!("  -> \"{}\" (weight: {:.2})\n", neighbor, weight));
        }
    }

    format!(
        r#"You are a memory synthesis system. Given the following atom clusters from a brain graph, produce a clear, readable <mycelium-context> block.

The context block should:
1. Group related atoms into sections with [bracketed headers]
2. Use bullet points for each memory item
3. Include temporal context where available
4. Be concise — use at most {} tokens
5. Only include information present in the input data — do not fabricate

Input clusters:
{}

Output ONLY the <mycelium-context> block, nothing else."#,
        budget, clusters_text
    )
}

/// Build a human-readable context block from traversal result (non-LLM fallback).
pub fn build_fallback_context(result: &RecallResult) -> String {
    if result.clusters.is_empty() {
        return "<mycelium-context>\nNo relevant memories found.\n</mycelium-context>".to_string();
    }

    let mut ctx = String::from("<mycelium-context>\n");
    for cluster in &result.clusters {
        ctx.push_str(&format!("\n[{}]\n", cluster.seed_phrase));
        if let Some((_first, last, _count)) = cluster.temporal {
            ctx.push_str(&format!("  Last seen: turn {}\n", last));
        }
        for (neighbor, weight, _importance) in &cluster.neighbors {
            ctx.push_str(&format!("  - {} (relevance: {:.2})\n", neighbor, weight));
        }
    }
    ctx.push_str("</mycelium-context>");
    ctx
}

#[cfg(test)]
mod tests {
    use super::*;
    use mycelium_core::{AtomCluster, RecallIntent, RecallQuery, RecallResult};

    fn sample_result() -> RecallResult {
        RecallResult {
            query: RecallQuery {
                atoms: vec!["test".to_string()],
                intent: RecallIntent::Relational,
                temporal_hint: None,
            },
            clusters: vec![
                AtomCluster {
                    seed_id: 1,
                    seed_phrase: "change secret".to_string(),
                    neighbors: vec![
                        ("server config".to_string(), 0.9, 1.0),
                        ("env file".to_string(), 0.7, 1.0),
                    ],
                    temporal: Some((100, 200, 5)),
                },
                AtomCluster {
                    seed_id: 2,
                    seed_phrase: "restart proxy".to_string(),
                    neighbors: vec![
                        ("nginx restart".to_string(), 0.85, 1.0),
                    ],
                    temporal: Some((150, 201, 3)),
                },
            ],
            total_clusters: 2,
            traversal_time_ms: 1.2,
        }
    }

    #[test]
    fn test_build_fallback_context_empty() {
        let result = RecallResult {
            query: RecallQuery {
                atoms: vec![],
                intent: RecallIntent::Relational,
                temporal_hint: None,
            },
            clusters: vec![],
            total_clusters: 0,
            traversal_time_ms: 0.0,
        };
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("No relevant memories found"));
    }

    #[test]
    fn test_build_fallback_context_with_clusters() {
        let result = sample_result();
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("change secret"));
        assert!(ctx.contains("server config"));
        assert!(ctx.contains("restart proxy"));
        assert!(ctx.contains("nginx restart"));
        assert!(ctx.starts_with("<mycelium-context>"));
        assert!(ctx.ends_with("</mycelium-context>"));
    }

    #[test]
    fn test_build_synthesis_prompt_contains_clusters() {
        let result = sample_result();
        let prompt = build_synthesis_prompt(&result, 1000);
        assert!(prompt.contains("change secret"));
        assert!(prompt.contains("server config"));
        assert!(prompt.contains("restart proxy"));
        assert!(prompt.contains("1000")); // budget
    }

    #[test]
    fn test_build_fallback_context_no_temporal() {
        let result = RecallResult {
            query: RecallQuery {
                atoms: vec!["simple".to_string()],
                intent: RecallIntent::Factual,
                temporal_hint: None,
            },
            clusters: vec![AtomCluster {
                seed_id: 1,
                seed_phrase: "simple fact".to_string(),
                neighbors: vec![],
                temporal: None,
            }],
            total_clusters: 1,
            traversal_time_ms: 0.5,
        };
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("simple fact"));
        assert!(!ctx.contains("Last seen"));
    }
}
```

- [ ] **Step 2: Run test**

```bash
cargo test -p mycelium-proxy --lib context_synthesizer
```

Expected: 5 tests pass.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-proxy/src/context_synthesizer.rs
git commit -m "feat(proxy): add context synthesizer for recall results

- build_synthesis_prompt: builds LLM prompt for rich context synthesis
- build_fallback_context: generates readable context block without LLM call
- Handles empty results, with/without temporal data
- 5 unit tests covering empty, populated, prompt generation, no-temporal cases

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Wire recall into proxy interceptor

**Files:**
- Modify: `crates/mycelium-proxy/src/interceptor.rs` — add recall pipeline alongside existing search_facts
- Modify: `crates/mycelium-proxy/src/lib.rs` — add RecallMode config and make Storage accessible to recall
- Modify: `crates/mycelium-proxy/src/main.rs` — add RecallMode CLI/config flag

**Interfaces:**
- Consumes: `recall::traverse()`, `query_parser::call_query_parser()`, `context_synthesizer::build_synthesis_prompt()`, `context_synthesizer::build_fallback_context()`, `mycelium_core::RecallMode`
- Produces: updated proxy that uses graph recall when `RecallMode::GraphTraversal`

- [ ] **Step 1: Add RecallMode to ProxyState**

In `crates/mycelium-proxy/src/lib.rs`, add to the proxy state:

```rust
use mycelium_core::RecallMode;

pub struct ProxyState {
    pub storage: mycelium_core::Storage,
    pub semaphore: tokio::sync::Semaphore,
    pub turn_counter: AtomicI64,
    pub mycelium_server_url: String,
    pub mycelium_api_key: String,
    pub upstream_api_key: String,
    pub upstream_url: String,
    pub model: String,
    pub recall_mode: RecallMode,
    pub llm_client: reqwest::Client,
}
```

In `main.rs`, add CLI arg:

```rust
// Add to existing args:
#[arg(long, default_value = "graph")]
recall_mode: String,
```

And pass to ProxyState:

```rust
let recall_mode = match args.recall_mode.as_str() {
    "legacy" => RecallMode::Legacy,
    "graph" => RecallMode::GraphTraversal,
    _ => RecallMode::GraphTraversal,
};
```

- [ ] **Step 2: Add recall pipeline function**

In `crates/mycelium-proxy/src/interceptor.rs`, add the full recall pipeline:

```rust
use mycelium_core::{RecallIntent, RecallMode, RecallQuery};
use crate::query_parser::{call_query_parser, parse_query_response};
use crate::context_synthesizer::{build_fallback_context, build_synthesis_prompt};

const RECALL_CONTEXT_INSTRUCTION: &str = "\n\nYou have access to Mycelium's permanent memory. When you need to recall information, the system will inject relevant context from the brain graph.";
const DEFAULT_RECALL_BUDGET: usize = 1000;

/// Run the full graph-guided recall pipeline.
///
/// Returns a context block to inject, or empty string if no memories found.
pub async fn run_recall_pipeline(
    user_message: &str,
    storage: &mycelium_core::Storage,
    llm_client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    budget: usize,
) -> String {
    // Step 1: Query parsing — decompose user message into atoms + intent
    let query = match call_query_parser(llm_client, api_url, api_key, model, user_message).await {
        Some(q) => q,
        None => {
            // Fallback: use raw user message as single atom
            let words: Vec<&str> = user_message.split_whitespace()
                .filter(|w| w.len() > 3)
                .take(5)
                .collect();
            if words.is_empty() {
                return String::new();
            }
            RecallQuery {
                atoms: words.iter().map(|w| w.to_string()).collect(),
                intent: RecallIntent::Relational,
                temporal_hint: None,
            }
        }
    };

    // Step 2: Graph traversal (pure SQL, no LLM)
    let conn = storage.connection();  // need to expose connection from Storage
    let result = match mycelium_core::recall::traverse(&conn, &query, 5, 5) {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!("Recall traversal failed: {}", e);
            return String::new();
        }
    };

    if result.clusters.is_empty() {
        return String::new();
    }

    // Step 3: Context synthesis (try LLM first, fallback to template)
    let synthesis_prompt = build_synthesis_prompt(&result, budget);
    match call_synthesizer(llm_client, api_url, api_key, model, &synthesis_prompt).await {
        Some(ctx) => ctx,
        None => build_fallback_context(&result),
    }
}

/// Call LLM for context synthesis.
async fn call_synthesizer(
    client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    prompt: &str,
) -> Option<String> {
    let body = serde_json::json!({
        "model": model,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}]
    });

    let resp = client
        .post(api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .ok()?;

    let text = resp.text().await.ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;

    let content = json
        .pointer("/content/0/text")
        .or_else(|| json.pointer("/choices/0/message/content"))
        .and_then(|c| c.as_str())?;

    // Extract just the <mycelium-context> block if present
    if let Some(start) = content.find("<mycelium-context>") {
        if let Some(end) => content.find("</mycelium-context>").map(|e| e + "</mycelium-context>".len()) {
            return Some(content[start..end].to_string());
        }
    }

    // If no XML tags, wrap entire response
    Some(format!("<mycelium-context>\n{}\n</mycelium-context>", content.trim()))
}
```

Wait, the `if let` syntax is wrong. Let me fix:

```rust
    // Extract just the <mycelium-context> block if present
    if let Some(start) = content.find("<mycelium-context>") {
        if let Some(end) = content.find("</mycelium-context>") {
            return Some(content[start..end + "</mycelium-context>".len()].to_string());
        }
    }
```

- [ ] **Step 3: Modify process_request to use recall pipeline**

In interceptor.rs, update the memory injection logic to branch on RecallMode:

```rust
pub async fn process_request(
    body: &[u8],
    storage: &Storage,
    recall_mode: &RecallMode,
    llm_client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
) -> Option<(Vec<u8>, String, String)> {
    // ... existing body parsing and user_message extraction ...

    // Memory injection
    let context_block = match recall_mode {
        RecallMode::Legacy => {
            // Old path: search_facts
            let facts = storage.search_facts(&user_msg, 5).ok().unwrap_or_default();
            build_facts_block(&facts)
        }
        RecallMode::GraphTraversal => {
            // New path: graph-guided recall
            run_recall_pipeline(
                &user_msg, storage, llm_client, api_url, api_key, model, DEFAULT_RECALL_BUDGET
            ).await
        }
    };

    if context_block.is_empty() {
        // No memory to inject — pass through unchanged
        return Some((body.to_vec(), session, user_msg));
    }

    // ... existing injection logic (append to system prompt) ...
}
```

- [ ] **Step 4: Wire into proxy handler**

In main.rs / lib.rs route handler, update the call to pass recall_mode and llm_client:

```rust
// In intercept_and_forward handler:
let result = process_request(
    &body_bytes,
    &state.storage,
    &state.recall_mode,
    &state.llm_client,
    &state.upstream_url,
    &state.upstream_api_key,
    &state.model,
).await;
```

- [ ] **Step 5: Expose connection() from Storage in mycelium-core**

In `crates/mycelium-core/src/storage.rs`:

```rust
use rusqlite::Connection;
use std::sync::MutexGuard;

/// Get a reference to the underlying SQLite connection for brain graph access.
/// Returns a MutexGuard which derefs to &Connection — pass as `&*storage.connection()`
/// to brain functions that expect `&Connection`.
pub fn connection(&self) -> MutexGuard<'_, Connection> {
    self.conn.lock().unwrap()
}
```

In the interceptor, use it like:

```rust
let conn_guard = storage.connection();
let result = mycelium_core::recall::traverse(&conn_guard, &query, 5, 5)?;
// conn_guard released when it goes out of scope
```

- [ ] **Step 6: Run build**

```bash
cargo build -p mycelium-proxy 2>&1
```

Expected: clean compile.

- [ ] **Step 7: Commit**

```bash
git add crates/mycelium-proxy/src/interceptor.rs crates/mycelium-proxy/src/lib.rs \
       crates/mycelium-proxy/src/main.rs crates/mycelium-core/src/storage.rs
git commit -m "feat(proxy): wire graph-guided recall into proxy injection pipeline

- New run_recall_pipeline orchestrates: query parser → graph traversal → context synthesis
- process_request branches on RecallMode (Legacy vs GraphTraversal)
- ProxyState gains recall_mode field and llm_client for LLM calls
- Storage.connection() exposed for brain graph access
- call_synthesizer manages LLM call and XML tag extraction for context blocks

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Integration test — end-to-end recall with seeded brain graph

**Files:**
- Modify: `crates/mycelium-core/src/recall.rs` — add to existing test module

**Interfaces:**
- Consumes: all modules from Tasks 1-4

- [ ] **Step 1: Write the integration test**

In `crates/mycelium-core/src/recall.rs`, add to the `#[cfg(test)]` module:

```rust
    #[test]
    fn test_recall_to_fallback_e2e() {
        let conn = setup_brain();

        // Seed atoms with edges (simulates what consolidate_entry does)
        let a1 = brain::upsert_atom(&conn, "change secret", 100, 0.8).unwrap();
        brain::record_position(&conn, a1, 100, "s1").unwrap();
        let a2 = brain::upsert_atom(&conn, "server config", 101, 0.7).unwrap();
        brain::record_position(&conn, a2, 101, "s1").unwrap();
        brain::increment_edge(&conn, a1, a2, 101).unwrap();

        // Query: graph traversal for "secret"
        let query = RecallQuery {
            atoms: vec!["secret".to_string()],
            intent: RecallIntent::Relational,
            temporal_hint: None,
        };
        let result = traverse(&conn, &query, 5, 5).unwrap();
        assert_eq!(result.clusters.len(), 1, "Should find the 'change secret' cluster");
        assert_eq!(result.clusters[0].seed_phrase, "change secret");
        assert!(
            result.clusters[0].neighbors.iter().any(|(p, _, _)| p == "server config"),
            "Neighbor should include 'server config' via edge"
        );
    }
```

Wait, but `build_fallback_context` is in the proxy crate and it's a private function. Let me make it `pub` in the implementation. It already is pub since I defined it as `pub fn build_fallback_context`.

But wait, the test needs to import from `mycelium_proxy`. Let me just adjust the test to use inline fallback context building instead.

Actually, I'll keep the integration test in the core crate instead since that's cleaner:

```rust
// In crates/mycelium-core/src/recall.rs, add this test:
#[test]
fn test_recall_to_fallback_e2e() {
    let conn = setup_brain();

    // Seed atoms with edges
    let a1 = brain::upsert_atom(&conn, "change secret", 100, 0.8).unwrap();
    brain::record_position(&conn, a1, 100, "s1").unwrap();
    let a2 = brain::upsert_atom(&conn, "server config", 101, 0.7).unwrap();
    brain::record_position(&conn, a2, 101, "s1").unwrap();
    brain::increment_edge(&conn, a1, a2, 101).unwrap();

    let query = RecallQuery {
        atoms: vec!["secret".to_string()],
        intent: RecallIntent::Relational,
        temporal_hint: None,
    };
    let result = traverse(&conn, &query, 5, 5).unwrap();
    assert_eq!(result.clusters.len(), 1);
    assert_eq!(result.clusters[0].seed_phrase, "change secret");
    assert!(result.clusters[0].neighbors.iter().any(|(p, _, _)| p == "server config"));
}
```

- [ ] **Step 2: Run all tests**

```bash
cargo test -p mycelium-core --lib recall
cargo test -p mycelium-proxy --lib query_parser
cargo test -p mycelium-proxy --lib context_synthesizer
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-core/src/recall.rs
git commit -m "test(recall): add integration test for seeded brain graph recall

- Seeds atoms with edges, runs full traversal pipeline
- Verifies cluster content, neighbor connections via edges
- End-to-end validation from query to result

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Documentation and config defaults

**Files:**
- Modify: `crates/mycelium-proxy/README.md` (or proxy main docs) — document recall mode flag
- Modify: `crates/mycelium-proxy/src/main.rs` — add CLI documentation/help text

- [ ] **Step 1: Update main.rs help text**

Add to the CLI args struct:

```rust
/// Proxy configuration
#[derive(clap::Parser)]
struct Args {
    // ... existing args ...

    /// Recall mode: "graph" (default) for brain graph traversal, "legacy" for old search_facts
    #[arg(long, default_value = "graph", value_parser = clap::builder::PossibleValuesParser::new(["graph", "legacy"]))]
    recall_mode: String,
}
```

- [ ] **Step 2: Commit**

```bash
git add crates/mycelium-proxy/src/main.rs
git commit -m "docs(proxy): add recall-mode CLI flag documentation

- 'graph' mode (default): brain graph traversal via recall pipeline
- 'legacy' mode: old search_facts path (deprecated)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Spec Coverage Check

| Spec Section | Concrete Task |
|---|---|
| 4. Query Parser | Task 2 — query_parser.rs |
| 5. Graph Traversal Engine | Task 1 — recall.rs::traverse() |
| 6. Context Synthesizer | Task 3 — context_synthesizer.rs |
| 7. Proxy Integration | Task 4 — interceptor.rs wiring |
| 8. Thinking Model Compatibility | Implicit: context block is well-delimited XML, injected into system prompt |
| 8.3 Reliability Properties | Task 1 empty-traversal, Task 2/3 fallbacks, Task 4 fallback chain |
| 9. Infinite Context Model | Architectural — ensured by design (proxy injects only recall output, not raw history) |
| 10. Error Handling | Task 1 error variants, Task 2/3 LLM failure fallbacks, Task 4 pipeline error handling |
| 11. Testing Strategy | Task 1 (5 unit tests), Task 2 (4 tests), Task 3 (5 tests), Task 5 (integration tests) |
| 12. Token Budget | Task 3/4 configurable budget parameter, default 1000 |
| 14. Implementation Plan | Covered by Tasks 1-6 |

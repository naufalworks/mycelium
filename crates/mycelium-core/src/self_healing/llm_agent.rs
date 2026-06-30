//! LLM-driven hash-chain repair agent.
//!
//! Orchestrates a tool-calling loop: snapshot → ask LLM → dispatch tool → repeat → verify.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};

use crate::Storage;
use crate::self_healing::llm_provider::LLMProvider;
use crate::self_healing::policy::Policy;
use crate::self_healing::safety::SafetyHarness;
use crate::self_healing::tools;

/// Result of a single repair session.
#[derive(Debug, Serialize)]
pub struct RepairLog {
    /// Snapshot UUID taken before repair began.
    pub snapshot_id: String,
    /// Turn numbers that were modified.
    pub repaired_turns: Vec<i64>,
    /// Total number of tool calls made.
    pub total_tool_calls: usize,
    /// Errors encountered during tool execution.
    pub errors: Vec<String>,
    /// Number of broken entries remaining after repair.
    pub final_broken_count: usize,
    /// Wall-clock duration of the repair session.
    pub duration: Duration,
}

/// The LLM repair agent.
pub struct LLMAgent {
    pub provider: LLMProvider,
    pub storage: Arc<Storage>,
    pub policy: Arc<Policy>,
    pub safety: Arc<SafetyHarness>,
    pub max_tool_calls: usize,
    pub timeout: Duration,
}

impl LLMAgent {
    /// Run a complete repair session.
    ///
    /// 1. Snapshot the database
    /// 2. Loop: LLM picks a tool → dispatch → feed result back
    /// 3. Break on commit_repair or max_tool_calls
    /// 4. Verify entry count invariant
    /// 5. Return RepairLog
    pub async fn run(&self) -> anyhow::Result<RepairLog> {
        let start = Instant::now();

        // Take a snapshot before any mutations.
        // Guard is scoped in a block so the MutexGuard (which is !Send) is
        // dropped before any .await — keeping the future Send-safe.
        let snapshot_id = {
            let conn_guard = self.storage.conn().lock().unwrap();
            self.safety.snapshot(&conn_guard)?
        };

        let mut messages: Vec<Value> = Vec::new();
        let mut tool_call_count: usize = 0;
        let mut repaired_turns: Vec<i64> = Vec::new();
        let mut errors: Vec<String> = Vec::new();
        let mut done = false;

        // System prompt
        let system = format!(
            "You are a hash-chain repair agent. Your job is to fix broken prev_hash links in the entries table.\n\
             Use the available tools to inspect and repair the chain.\n\
             When the chain is fully repaired, call commit_repair.\n\n\
             Policy:\n{}",
            self.policy.policy_md()
        );

        let defs = tools::tool_definitions();

        while !done && tool_call_count < self.max_tool_calls {
            // Check timeout
            if start.elapsed() > self.timeout {
                errors.push("timeout exceeded".into());
                break;
            }

            // Ask LLM
            let response = match self.provider.chat(&system, &messages, &defs).await {
                Ok(r) => r,
                Err(e) => {
                    errors.push(format!("LLM call failed: {e}"));
                    break;
                }
            };

            // Extract the assistant message
            let assistant_msg = response
                .get("choices")
                .and_then(|c| c.as_array())
                .and_then(|arr| arr.first())
                .and_then(|c| c.get("message"))
                .cloned()
                .unwrap_or_else(|| json!({"role": "assistant"}));

            // Check for tool calls
            let tool_calls = assistant_msg
                .get("tool_calls")
                .and_then(|tc| tc.as_array())
                .cloned()
                .unwrap_or_default();

            // Append assistant message to conversation
            messages.push(assistant_msg.clone());

            if tool_calls.is_empty() {
                // No tool calls — LLM is done or confused
                done = true;
                continue;
            }

            // Dispatch each tool call
            for tc in &tool_calls {
                if tool_call_count >= self.max_tool_calls {
                    errors.push("max_tool_calls reached".into());
                    done = true;
                    break;
                }

                let fn_obj = tc.get("function");
                let fn_name = fn_obj
                    .and_then(|f| f.get("name"))
                    .and_then(|n| n.as_str())
                    .unwrap_or("");
                let fn_args = fn_obj
                    .and_then(|f| f.get("arguments"))
                    .cloned()
                    .unwrap_or(json!({}));

                // Parse arguments if they're a string (OpenAI format)
                let args: Value = if fn_args.is_string() {
                    serde_json::from_str(fn_args.as_str().unwrap_or("{}")).unwrap_or(json!({}))
                } else {
                    fn_args
                };

                let tc_id = tc
                    .get("id")
                    .and_then(|id| id.as_str())
                    .unwrap_or("")
                    .to_string();

                tool_call_count += 1;

                // Dispatch the tool — don't hold storage lock across the call
                // (tools re-acquire storage.conn() internally; std Mutex is not recursive)
                let result = tools::dispatch_tool(
                    fn_name,
                    &args,
                    &self.storage,
                    None,
                    &self.safety,
                );

                let result_json = match result {
                    Ok(v) => {
                        // Track modified turns from set_prev_hash
                        if fn_name == "set_prev_hash" {
                            if let Some(turn) = args.get("turn").and_then(|v| v.as_i64()) {
                                repaired_turns.push(turn);
                            }
                        }
                        // Signal done on commit_repair
                        if fn_name == "commit_repair" {
                            done = true;
                        }
                        v
                    }
                    Err(e) => {
                        errors.push(format!("{fn_name}: {e}"));
                        json!({ "error": e })
                    }
                };

                // Feed tool result back as a message
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": result_json.to_string()
                }));
            }
        }

        // Verify entry count invariant — guard scoped in a block.
        {
            let conn_guard = self.storage.conn().lock().unwrap();
            if let Err(e) = self.safety.verify_entry_count(&conn_guard) {
                errors.push(format!("entry count invariant violated: {e}"));
            }
        }

        // Get final broken count
        let final_failures = self.storage.verify_hash_chain().unwrap_or_default();
        let final_broken_count = final_failures.len();

        repaired_turns.sort_unstable();
        repaired_turns.dedup();

        Ok(RepairLog {
            snapshot_id,
            repaired_turns,
            total_tool_calls: tool_call_count,
            errors,
            final_broken_count,
            duration: start.elapsed(),
        })
    }
}

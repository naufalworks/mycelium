//! LLM tool handlers for self-healing hash-chain repair.
//!
//! Each handler is called by the LLM agent via function-calling.
//! All handlers are synchronous; the agent loop is async.

use rusqlite::Connection;
use serde_json::{json, Value};

use crate::Storage;
use crate::self_healing::policy::Policy;
use crate::self_healing::safety::SafetyHarness;

/// Return all 6 tool definitions in OpenAI function-call format.
pub fn tool_definitions() -> Vec<Value> {
    vec![
        json!({
            "type": "function",
            "function": {
                "name": "list_broken_segments",
                "description": "List all broken hash-chain segments. Groups consecutive broken turns into segments.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_entry",
                "description": "Get the full entry record for a given turn number.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "turn": {
                            "type": "integer",
                            "description": "The turn number to retrieve"
                        }
                    },
                    "required": ["turn"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "get_entry_content",
                "description": "Get the user and assistant text content for a given turn number.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "turn": {
                            "type": "integer",
                            "description": "The turn number to retrieve content for"
                        }
                    },
                    "required": ["turn"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "verify_chain",
                "description": "Verify the hash chain and return the current number of broken entries.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "set_prev_hash",
                "description": "Set the prev_hash for a given turn. Validates hash format before applying.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "turn": {
                            "type": "integer",
                            "description": "The turn number to update"
                        },
                        "hash": {
                            "type": "string",
                            "description": "The new prev_hash value (16 hex characters)"
                        }
                    },
                    "required": ["turn", "hash"]
                }
            }
        }),
        json!({
            "type": "function",
            "function": {
                "name": "commit_repair",
                "description": "Finalize the repair. Verifies chain integrity and returns success/failure.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "Human-readable description of the repair performed"
                        },
                        "affected_turns": {
                            "type": "array",
                            "items": { "type": "integer" },
                            "description": "List of turn numbers that were modified"
                        }
                    },
                    "required": ["description", "affected_turns"]
                }
            }
        }),
    ]
}

/// Dispatch a tool call by name. Returns the tool result as JSON.
/// `conn` is only needed by `set_prev_hash` — other tools acquire their own
/// connections internally to avoid lock contention with the caller.
pub fn dispatch_tool(
    name: &str,
    args: &Value,
    storage: &Storage,
    _conn: Option<&Connection>,
    safety: &SafetyHarness,
) -> Result<Value, String> {
    match name {
        "list_broken_segments" => list_broken_segments(storage),
        "get_entry" => get_entry(args, storage),
        "get_entry_content" => get_entry_content(args, storage),
        "verify_chain" => verify_chain(storage),
        "set_prev_hash" => set_prev_hash(args, storage),
        "commit_repair" => commit_repair(storage),
        _ => Err(format!("unknown tool: {name}")),
    }
}

/// Group consecutive broken turns into segments.
fn list_broken_segments(storage: &Storage) -> Result<Value, String> {
    let failures = storage
        .verify_hash_chain()
        .map_err(|e| format!("verify_hash_chain failed: {e}"))?;

    if failures.is_empty() {
        return Ok(json!({ "segments": [], "total_broken": 0 }));
    }

    let mut turns: Vec<i64> = failures.iter().map(|(t, _, _)| *t).collect();
    turns.sort_unstable();

    // Group into contiguous segments
    let mut segments: Vec<Value> = Vec::new();
    let mut seg_start = turns[0];
    let mut seg_end = turns[0];

    for &t in &turns[1..] {
        if t == seg_end + 1 {
            seg_end = t;
        } else {
            segments.push(json!({ "start": seg_start, "end": seg_end }));
            seg_start = t;
            seg_end = t;
        }
    }
    segments.push(json!({ "start": seg_start, "end": seg_end }));

    Ok(json!({
        "segments": segments,
        "total_broken": failures.len(),
        "details": failures.iter().map(|(turn, expected, actual)| {
            json!({ "turn": turn, "expected": expected, "actual": actual })
        }).collect::<Vec<_>>()
    }))
}

/// Return the full entry record as JSON.
fn get_entry(args: &Value, storage: &Storage) -> Result<Value, String> {
    let turn = args
        .get("turn")
        .and_then(|v| v.as_i64())
        .ok_or("missing or invalid 'turn' argument")?;

    let entry = storage
        .get_entry(turn)
        .map_err(|e| format!("get_entry failed: {e}"))?;

    match entry {
        Some(e) => serde_json::to_value(&e).map_err(|e| format!("serialization failed: {e}")),
        None => Ok(json!(null)),
    }
}

/// Return user/assistant text for a turn.
fn get_entry_content(args: &Value, storage: &Storage) -> Result<Value, String> {
    let turn = args
        .get("turn")
        .and_then(|v| v.as_i64())
        .ok_or("missing or invalid 'turn' argument")?;

    let entry = storage
        .get_entry(turn)
        .map_err(|e| format!("get_entry failed: {e}"))?;

    match entry {
        Some(e) => Ok(json!({
            "turn": e.turn,
            "user": e.user,
            "assistant": e.assistant,
        })),
        None => Ok(json!(null)),
    }
}

/// Return current broken entry count.
fn verify_chain(storage: &Storage) -> Result<Value, String> {
    let failures = storage
        .verify_hash_chain()
        .map_err(|e| format!("verify_hash_chain failed: {e}"))?;

    Ok(json!({ "broken_count": failures.len() }))
}

/// Update prev_hash for a turn. Validates hash format via Policy::validate_hash_format.
fn set_prev_hash(args: &Value, storage: &Storage) -> Result<Value, String> {
    let turn = args
        .get("turn")
        .and_then(|v| v.as_i64())
        .ok_or("missing or invalid 'turn' argument")?;

    let hash = args
        .get("hash")
        .and_then(|v| v.as_str())
        .ok_or("missing or invalid 'hash' argument")?;

    Policy::validate_hash_format(hash).map_err(|e| format!("invalid hash: {e}"))?;

    let updated = storage
        .update_prev_hash(turn, hash)
        .map_err(|e| format!("UPDATE failed: {e}"))?;

    if !updated {
        return Err(format!("no entry found at turn {turn}"));
    }

    Ok(json!({ "ok": true, "turn": turn, "updated": updated }))
}

/// Finalize repair — verify the chain and return status.
fn commit_repair(storage: &Storage) -> Result<Value, String> {
    let failures = storage
        .verify_hash_chain()
        .map_err(|e| format!("verify_hash_chain failed: {e}"))?;

    if failures.is_empty() {
        Ok(json!({ "ok": true, "message": "chain fully repaired" }))
    } else {
        Ok(json!({
            "ok": false,
            "message": format!("chain still has {} broken entries", failures.len()),
            "remaining": failures.len()
        }))
    }
}

//! Request interceptor — parses Anthropic API requests, injects memory context.
//!
//! The interceptor:
//! 1. Parses the request body (Anthropic Messages API format)
//! 2. Queries memory facts from the storage engine
//! 3. Injects `<mycelium-facts>` into the system prompt
//! 4. Returns the modified body + session + user message

use mycelium_core::Storage;
use serde_json::Value;
use tracing::debug;

const FACTS_BLOCK_HEADER: &str = "\n<mycelium-facts>\nVerified facts from permanent memory:\n";

/// Process an intercepted request body — inject memory context.
///
/// Returns `(modified_body, session, user_message)` if the request should be intercepted,
/// or `None` if it should pass through unchanged.
pub fn process_request(body: &[u8], storage: &Storage) -> Option<(Vec<u8>, String, String)> {
    // Parse the request body as JSON
    let mut req: Value = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => {
            debug!("Failed to parse request body: {}", e);
            return None;
        }
    };

    // Extract user message from the content blocks
    let user_msg = extract_user_message(&req)?;

    // Extract or generate a session ID
    let session = req
        .get("session_id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
            // Generate session ID from metadata or use default
            req.get("metadata")
                .and_then(|m| m.get("session_id"))
                .and_then(|v| v.as_str())
                .map(|s| format!("rust-proxy:{}", s))
                .unwrap_or_else(|| "rust-proxy:default".to_string())
        });

    // Query memory facts relevant to the user's message
    let facts = storage.search_facts(&user_msg, 5).ok().unwrap_or_default();

    if !facts.is_empty() {
        debug!("Injecting {} memory facts for: {}", facts.len(), user_msg.chars().take(60).collect::<String>());

        // Build the <mycelium-facts> block
        let mut fact_lines = String::from(FACTS_BLOCK_HEADER);
        for fact in &facts {
            let value = if fact.value.len() > 80 {
                format!("{}...", &fact.value[..80])
            } else {
                fact.value.clone()
            };
            fact_lines.push_str(&format!("  [{}] {}.{} = {}\n", fact.fact_type, fact.entity, fact.attribute, value));
        }
        fact_lines.push_str("</mycelium-facts>");

        // Inject into the system prompt
        let block = fact_lines;
        if let Some(system) = req.get_mut("system") {
            if let Some(s) = system.as_str() {
                *system = Value::String(format!("{}\n\n{}", s, block));
            }
        } else {
            req["system"] = Value::String(block);
        }
    }

    // Serialize the modified request
    let modified_body = serde_json::to_vec(&req).unwrap_or_else(|_| body.to_vec());

    Some((modified_body, session, user_msg))
}

/// Extract the last user message from the request.
/// Returns None if no user message is found (e.g., tool_use requests, system pings).
fn extract_user_message(req: &Value) -> Option<String> {
    // Look for the last user turn in the messages array
    let messages = req.get("messages")?.as_array()?;

    for msg in messages.iter().rev() {
        let role = msg.get("role")?.as_str()?;
        if role != "user" {
            continue;
        }

        // Extract content — can be a string or an array of content blocks
        let content = msg.get("content")?;
        match content {
            Value::String(s) => {
                let s = s.trim();
                if !s.is_empty() && !s.starts_with("[SUGGESTION MODE:") && !s.starts_with("<system-reminder>") {
                    return Some(s.to_string());
                }
            }
            Value::Array(blocks) => {
                for block in blocks {
                    if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                        let t = text.trim();
                        if !t.is_empty() && !t.starts_with("[SUGGESTION MODE:") && !t.starts_with("<system-reminder>") {
                            return Some(t.to_string());
                        }
                    }
                }
            }
            _ => {}
        }
    }

    None
}

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
    let mut req: Value = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => {
            debug!("Failed to parse request body: {}", e);
            return None;
        }
    };

    let user_msg = extract_user_message(&req)?;

    let session = req
        .get("session_id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| {
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

        let block = build_facts_block(&facts);

        if let Some(system) = req.get_mut("system") {
            if let Some(s) = system.as_str() {
                *system = Value::String(format!("{}\n\n{}", s, block));
            }
        } else {
            req["system"] = Value::String(block);
        }
    }

    let modified_body = serde_json::to_vec(&req).unwrap_or_else(|_| body.to_vec());
    Some((modified_body, session, user_msg))
}

/// Extract the last user message from an Anthropic request.
pub fn extract_user_message(req: &Value) -> Option<String> {
    let messages = req.get("messages")?.as_array()?;

    for msg in messages.iter().rev() {
        let role = msg.get("role")?.as_str()?;
        if role != "user" {
            continue;
        }

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

/// Parse an SSE response body from the Anthropic API and extract the assistant message.
///
/// Supports both streaming (SSE events) and non-streaming (JSON) responses.
/// Returns the full response text accumulated from content_block_delta events.
pub fn extract_assistant_response(body: &[u8]) -> String {
    // Try non-streaming JSON first
    if let Ok(resp) = serde_json::from_slice::<Value>(body) {
        let msg = extract_assistant_from_json(&resp);
        if !msg.is_empty() {
            return msg;
        }
    }

    // Streaming: parse SSE events
    let text = String::from_utf8_lossy(body);
    let mut full_text = String::new();

    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with("data: ") {
            continue;
        }

        let data = line.strip_prefix("data: ").unwrap_or("");
        if data == "[DONE]" {
            break;
        }

        if let Ok(event) = serde_json::from_str::<Value>(data) {
            // Anthropic: content_block_delta with delta.text
            if event.get("type").and_then(|v| v.as_str()) == Some("content_block_delta") {
                if let Some(delta_text) = event
                    .pointer("/delta/text")
                    .or_else(|| event.pointer("/delta/partial_json"))
                    .and_then(|v| v.as_str())
                {
                    full_text.push_str(delta_text);
                }
            }

            // OpenAI-compatible: choices[0].delta.content
            if let Some(choices) = event.get("choices").and_then(|v| v.as_array()) {
                if let Some(choice) = choices.first() {
                    if let Some(delta_text) = choice
                        .pointer("/delta/content")
                        .and_then(|v| v.as_str())
                    {
                        full_text.push_str(delta_text);
                    }
                }
            }
        }
    }

    full_text
}

/// Extract assistant message from a non-streaming JSON response.
fn extract_assistant_from_json(resp: &Value) -> String {
    // Anthropic format: content[{type: "text", text: "..."}]
    if let Some(content) = resp.get("content").and_then(|v| v.as_array()) {
        let mut text = String::new();
        for block in content {
            if block.get("type").and_then(|v| v.as_str()) == Some("text") {
                if let Some(t) = block.get("text").and_then(|v| v.as_str()) {
                    text.push_str(t);
                }
            }
        }
        if !text.is_empty() {
            return text;
        }
    }

    // OpenAI format: choices[0].message.content
    if let Some(msg) = resp.pointer("/choices/0/message/content").and_then(|v| v.as_str()) {
        return msg.to_string();
    }

    String::new()
}

/// Process an OpenAI /v1/chat/completions request — inject memory context.
///
/// Returns `(modified_body, session, user_message)` if interceptable,
/// or `None` for pass-through.
pub fn process_openai(body: &[u8], storage: &Storage) -> Option<(Vec<u8>, String, String)> {
    let mut req: Value = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => {
            debug!("Failed to parse OpenAI request body: {}", e);
            return None;
        }
    };

    // Extract user message from messages array
    let user_msg = extract_openai_user_message(&req)?;

    // Generate session ID from model + user field
    let session = req
        .get("user")
        .and_then(|v| v.as_str())
        .map(|s| format!("openai:{}", s))
        .unwrap_or_else(|| format!("openai:{}", user_msg.chars().take(16).collect::<String>()));

    // Query memory facts
    let facts = storage.search_facts(&user_msg, 5).ok().unwrap_or_default();

    if !facts.is_empty() {
        debug!("Injecting {} memory facts for OpenAI request", facts.len());

        let block = build_facts_block(&facts);

        // Inject as a system message in the messages array
        if let Some(messages) = req.get_mut("messages").and_then(|v| v.as_array_mut()) {
            // Prepend a system message with memory context
            let sys_msg = serde_json::json!({
                "role": "system",
                "content": block
            });
            messages.insert(0, sys_msg);
        }
    }

    let modified_body = serde_json::to_vec(&req).unwrap_or_else(|_| body.to_vec());
    Some((modified_body, session, user_msg))
}

/// Extract the last user message from an OpenAI request array.
fn extract_openai_user_message(req: &Value) -> Option<String> {
    let messages = req.get("messages")?.as_array()?;

    for msg in messages.iter().rev() {
        let role = msg.get("role")?.as_str()?;
        if role != "user" {
            continue;
        }

        // Content can be a string or array of content blocks
        let content = msg.get("content")?;
        match content {
            Value::String(s) => {
                let s = s.trim();
                if !s.is_empty() {
                    return Some(s.to_string());
                }
            }
            Value::Array(blocks) => {
                for block in blocks {
                    if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                        let t = text.trim();
                        if !t.is_empty() {
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

/// Extract assistant response from an OpenAI response (streaming or non-streaming).
pub fn extract_openai_response(body: &[u8]) -> String {
    // Try non-streaming JSON first
    if let Ok(resp) = serde_json::from_slice::<Value>(body) {
        if let Some(msg) = resp.pointer("/choices/0/message/content").and_then(|v| v.as_str()) {
            return msg.to_string();
        }
    }

    // Streaming SSE: parse choices[0].delta.content events
    let text = String::from_utf8_lossy(body);
    let mut full_text = String::new();

    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with("data: ") {
            continue;
        }

        let data = line.strip_prefix("data: ").unwrap_or("");
        if data == "[DONE]" {
            break;
        }

        if let Ok(event) = serde_json::from_str::<Value>(data) {
            if let Some(delta) = event.pointer("/choices/0/delta/content").and_then(|v| v.as_str()) {
                full_text.push_str(delta);
            }
        }
    }

    full_text
}

/// Build the `<mycelium-facts>` XML block from memory facts.
pub fn build_facts_block(facts: &[mycelium_core::MemoryFact]) -> String {
    if facts.is_empty() {
        return String::new();
    }

    let mut block = String::from(FACTS_BLOCK_HEADER);
    for fact in facts {
        let value = if fact.value.len() > 80 {
            format!("{}...", &fact.value[..80])
        } else {
            fact.value.clone()
        };
        block.push_str(&format!("  [{}] {}.{} = {}\n", fact.fact_type, fact.entity, fact.attribute, value));
    }
    block.push_str("</mycelium-facts>");
    block
}

/// Filter upstream response body — strips unsupported content blocks.
///
/// When `filter_enabled` is true, strips "thinking" blocks by default.
/// MYCELIUM_PROXY_STRIP_BLOCKS env var can override the block types list.
/// Handles both SSE streaming and JSON non-streaming formats.
pub fn filter_response(body: &[u8]) -> Vec<u8> {
    let strip_types = get_strip_types();
    if strip_types.is_empty() {
        return body.to_vec(); // No filtering configured
    }

    let text = String::from_utf8_lossy(body);
    let strip_types = get_strip_types();
    if strip_types.is_empty() {
        return body.to_vec();
    }

    if text.contains("\ndata: ") || text.starts_with("data: ") {
        filter_sse(&text, &strip_types).into_bytes()
    } else {
        filter_json(&text, &strip_types).into_bytes()
    }
}

/// Read strip types from environment (comma-separated).
/// Example: MYCELIUM_PROXY_STRIP_BLOCKS=thinking,thinking_redacted
fn get_strip_types() -> Vec<String> {
    let raw = match std::env::var("MYCELIUM_PROXY_STRIP_BLOCKS") {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    raw.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
}

/// Filter SSE response body — strips content blocks matching strip_types.
///
/// State machine:
/// - idle: waiting for content_block_start
/// - skip(index): in a content block we want to strip, skip events until its stop
fn filter_sse(text: &str, strip_types: &[String]) -> String {
    let mut output = String::new();
    let mut skip_index: Option<usize> = None;

    for line in text.lines() {
        let trimmed = line.trim();

        // Forward non-event lines (separators, comments, empty)
        if !trimmed.starts_with("data: ") && !trimmed.starts_with("event: ") {
            output.push_str(line);
            output.push('\n');
            continue;
        }

        // Check if we need to skip this event
        if let Some(skip) = skip_index {
            // Check for content_block_stop at this index
            if trimmed.starts_with("data: ") {
                let data = trimmed.strip_prefix("data: ").unwrap_or("");
                if let Ok(event) = serde_json::from_str::<serde_json::Value>(data) {
                    let event_type = event.get("type").and_then(|v| v.as_str());
                    if event_type == Some("content_block_stop") {
                        let idx = event.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
                        if idx == skip {
                            skip_index = None;
                            continue; // Skip the stop event
                        }
                    }
                }
            }
            // All events for other indices pass through
            output.push_str(line);
            output.push('\n');
            continue;
        }

        // Parse data events to check content block type
        if trimmed.starts_with("data: ") {
            let data = trimmed.strip_prefix("data: ").unwrap_or("");
            if let Ok(event) = serde_json::from_str::<serde_json::Value>(data) {
                let event_type = event.get("type").and_then(|v| v.as_str());

                // Check for content_block_start with type we want to strip
                if event_type == Some("content_block_start") {
                    let block_type = event
                        .pointer("/content_block/type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if strip_types.contains(&block_type.to_string()) {
                        let idx = event.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
                        skip_index = Some(idx);
                        continue; // Skip the start event
                    }
                }
            }
        }

        output.push_str(line);
        output.push('\n');
    }

    output
}

/// Filter JSON response body — removes content blocks matching strip_types.
fn filter_json(text: &str, strip_types: &[String]) -> String {
    let mut resp: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return text.to_string(), // Not valid JSON, return as-is
    };

    // Try Anthropic format: content[{type, text}]
    if let Some(content) = resp.get_mut("content").and_then(|v| v.as_array_mut()) {
        content.retain(|block| {
            let block_type = block.get("type").and_then(|v| v.as_str()).unwrap_or("");
            !strip_types.contains(&block_type.to_string())
        });
    }

    // OpenAI format: choices[0].message.content — this is a string, no blocks to strip

    serde_json::to_string(&resp).unwrap_or_else(|_| text.to_string())
}

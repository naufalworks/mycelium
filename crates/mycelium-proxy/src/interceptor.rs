//! Request interceptor — parses Anthropic API requests, injects memory context.
//!
//! The interceptor:
//! 1. Parses the request body (Anthropic Messages API format)
//! 2. Queries memory facts from the storage engine
//! 3. Injects `<mycelium-facts>` into the system prompt
//! 4. Returns the modified body + session + user message

use mycelium_core::recall::traverse;
use mycelium_core::{RecallIntent, RecallQuery, Storage};
use serde_json::Value;
use tracing::debug;
use tracing::warn;

use crate::context_synthesizer::build_fallback_context;
use crate::query_parser::call_query_parser;
use crate::cortex;

/// Build a <mycelium-context> block from write-time snippets (pre-stored during consolidation).
fn build_snippet_context(result: &mycelium_core::RecallResult) -> String {
    let mut ctx = String::new();
    for cluster in &result.clusters {
        if let Some(ref snippet) = cluster.snippet {
            if ctx.is_empty() {
                ctx.push_str("<mycelium-context>
");
            }
            ctx.push_str(&format!("
[{}]
  {}
", cluster.seed_phrase, snippet));
        }
    }
    if !ctx.is_empty() {
        ctx.push_str("</mycelium-context>");
    }
    ctx
}

/// Find the first text block in an Anthropic content array.
/// Handles thinking blocks by iterating through all content blocks.
fn find_text_content(json: &serde_json::Value) -> Option<String> {
    if let Some(blocks) = json.get("content").and_then(|c| c.as_array()) {
        for block in blocks {
            if block.get("type").and_then(|t| t.as_str()) == Some("text") {
                if let Some(text) = block.get("text").and_then(|t| t.as_str()) {
                    return Some(text.to_string());
                }
            }
        }
    }
    None
}

const FACTS_BLOCK_HEADER: &str = "\n<mycelium-facts>\nVerified facts from permanent memory:\n";

/// Instruction injected into the system prompt to request a memory annotation.
const MEMORY_INSTRUCTION: &str = "\n\nAfter your response, emit a <memory> block containing JSON with: phrases (canonical noun phrases to remember, each with text and importance 1-5), actions (key actions taken/fixed/explained, each with text and importance 1-5), entities (named things mentioned, each with name, type, aliases, and importance 1-5). Keep the block under 200 tokens.";

/// Instruction about recall context block.
const RECALL_CONTEXT_INSTRUCTION: &str = "\n\nYou have access to Mycelium's permanent memory. When you need to recall information, the system will inject relevant context from the brain graph.";

// ── Constants ──
/// Number of memory clusters to retrieve per recall query.
const MAX_RECALL_CLUSTERS: usize = 5;
const MAX_RECALL_NEIGHBORS: usize = 5;
/// Maximum tokens for synthesis LLM call (keeps thinking short).
const SYNTHESIS_MAX_TOKENS: u64 = 256;
/// Fallback token budget for build_synthesis_prompt.
const SYNTHESIS_BUDGET: usize = 10000;
/// Anthropic API version header value.
const ANTHROPIC_API_VERSION: &str = "2023-06-01";
/// Minimum confidence for Cortex skill matching.
const CORTEX_MATCH_THRESHOLD: f64 = 0.3;
/// Characters to truncate log previews.
/// Max raw message length before falling back to word extraction.
/// Threshold for system message detection (skip recall).

/// Find the first text block in an Anthropic content array.
async fn call_synthesizer(
    client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    prompt: &str,
) -> Option<String> {
    let body = serde_json::json!({
        "model": model,
        "max_tokens": SYNTHESIS_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": prompt}]
        }]
    });
    let resp = client
        .post(api_url)
        .header("x-api-key", api_key)
        .header("anthropic-version", ANTHROPIC_API_VERSION)
        .json(&body)
        .send()
        .await
        .ok()?;
    let text = resp.text().await.ok()?;
    let json: serde_json::Value = serde_json::from_str(&text).ok()?;
    let content = find_text_content(&json)
        .or_else(|| json.pointer("/choices/0/message/content").and_then(|c| c.as_str()).map(String::from))?;
    if let Some(start) = content.find("<mycelium-context>") {
        if let Some(end) = content.find("</mycelium-context>") {
            return Some(content[start..end + "</mycelium-context>".len()].to_string());
        }
    }
    Some(format!("<mycelium-context>\n{}\n</mycelium-context>", content.trim()))
}

///
/// Returns a context block to inject, or empty string if no memories found.
pub async fn run_recall_pipeline(
    user_message: &str,
    storage: &Storage,
    llm_client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    cortex_skills: &[cortex::Skill],
    cortex_enabled: bool,
) -> String {
    debug!("🧠 Recall pipeline: processing \"{}\"", user_message);
    let start = std::time::Instant::now();

    // Step 1: Query parsing — decompose user message into atoms + intent
    let query = match call_query_parser(llm_client, api_url, api_key, model, user_message).await {
        Some(q) => {
            debug!("  Query parser: {} atoms, intent={:?} => {:?}", q.atoms.len(), q.intent, q.atoms);
            q
        }
        None => {
            warn!("  Query parser failed — falling back to raw message as atom");
            // Filter out known system/internal messages — they have no recall value
            let cleaned = user_message
                .trim()
                .trim_matches(|c: char| c == '"' || c == '\'' || c == '[' || c == ']')
                .to_string();

            // If it looks like a system message, skip recall entirely
            if cleaned.starts_with("Your previous response")
                || cleaned.starts_with("The user stepped away")
                || cleaned.starts_with("[Your previous")
                || cleaned.len() > 500
            {
                debug!("  Skipping recall — message looks like system/internal prompt");
                return String::new();
            }

            // Extract short meaningful phrases (2-5 words max) for the fallback atom
            let fallback_atoms: Vec<String> = if cleaned.len() > 80 {
                cleaned.split_whitespace()
                    .filter(|w| w.len() > 3)
                    .take(5)
                    .collect::<Vec<&str>>()
                    .chunks(2)
                    .map(|chunk| chunk.join(" "))
                    .collect()
            } else {
                vec![cleaned]
            };

            RecallQuery {
                atoms: fallback_atoms,
                intent: RecallIntent::Factual,
                temporal_hint: None,
            }
        }
    };

    // Step 2: Graph traversal — search the brain graph (synchronous block)
    let result = {
        let conn = storage.connection();
        let conn_guard = conn.lock().unwrap();
        traverse(&conn_guard, &query, MAX_RECALL_CLUSTERS, MAX_RECALL_NEIGHBORS, Some(storage.hot_graph().as_ref()))
    };
    let result = match result {
        Ok(r) => {
            debug!("  Traversal: {} clusters in {:.2}ms — {:?}", 
                r.total_clusters, r.traversal_time_ms,
                r.clusters.iter().map(|c| &c.seed_phrase).collect::<Vec<_>>());
            r
        }
        Err(e) => {
            warn!("  Traversal failed: {}", e);
            return String::new();
        }
    };

    if result.clusters.is_empty() {
        debug!("  No matching clusters found — empty context");
        return String::new();
    }
    // Implicit Attention Graph: reinforce edges between co-occurring atoms
    // The LLM "attended" these atoms together → boost their connection
    {
        let conn_guard = storage.connection();
        let conn = conn_guard.lock().unwrap();
        let atom_ids: Vec<i64> = result.clusters.iter().map(|c| c.seed_id).collect();
        if atom_ids.len() > 1 {
            let _ = mycelium_core::brain::reinforce_cooccurrence(&conn, &atom_ids, 0);
        }
    }

    // Step 3: Context synthesis — try LLM first, fallback to template
    let elapsed = start.elapsed();
    debug!("  Recall pipeline complete in {:.2}ms — synthesizing context", elapsed.as_secs_f64() * 1000.0);
    // Try write-time snippets first (stored during consolidation, zero LLM cost)
    let mut context = build_snippet_context(&result);

    // Fallback: try LLM synthesis if no snippets available
    if context.is_empty() {
        let synthesis_prompt = crate::context_synthesizer::build_synthesis_prompt(&result, SYNTHESIS_BUDGET);
        context = match call_synthesizer(llm_client, api_url, api_key, model, &synthesis_prompt).await {
            Some(ctx) => {
                let total_elapsed = start.elapsed();
                let preview: String = ctx.chars().take(200).collect();
                debug!("  ✅ Recall context generated in {:.2}ms (LLM synthesis): {}", total_elapsed.as_secs_f64() * 1000.0, preview);
                ctx
            }
            None => {
                let total_elapsed = start.elapsed();
                debug!("  ⚠️  LLM synthesis failed, using fallback template ({:.2}ms)", total_elapsed.as_secs_f64() * 1000.0);
                build_fallback_context(&result)
            }
        };
    } else {
        let total_elapsed = start.elapsed();
        debug!("  ✅ Recall context from write-time snippets ({:.2}ms)", total_elapsed.as_secs_f64() * 1000.0);
    }

    // Step 4: Cortex — append skill suggestion if matched
    if cortex_enabled && !cortex_skills.is_empty() && !query.atoms.is_empty() {
        if let Some(matched) = cortex::match_skill(&query.atoms, cortex_skills, CORTEX_MATCH_THRESHOLD) {
            context.push_str("\n");
            context.push_str(&cortex::build_cortex_block(&matched));
            debug!("  Cortex matched: {} (conf={:.2})", matched.skill.name, matched.confidence);
        }
    }

    context
}

/// Process an intercepted request body — inject memory context.
///
/// Returns `(modified_body, session, user_message)` if the request should be intercepted,
/// or `None` if it should pass through unchanged.
pub fn process_request(
    body: &[u8],
    _storage: &Storage,
    context_block: &str,
) -> Option<(Vec<u8>, String, String)> {
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

    // Build the injection string from the pre-computed context block
    let mut injection = String::new();
    if context_block.is_empty() {
        injection.push_str(MEMORY_INSTRUCTION);
    } else {
        injection.push_str(&format!("\n\n{}", context_block));
        injection.push_str(RECALL_CONTEXT_INSTRUCTION);
        injection.push_str(MEMORY_INSTRUCTION);
    }

    if let Some(system) = req.get_mut("system") {
        match system {
            Value::String(s) => {
                // Legacy string format: append to system prompt
                *system = Value::String(format!("{}{}", s, injection));
            }
            Value::Array(blocks) => {
                // Modern array format: add a text block with the injection
                blocks.push(serde_json::json!({
                    "type": "text",
                    "text": injection.trim().to_string()
                }));
            }
            _ => {}
        }
    } else {
        req["system"] = Value::String(injection.trim_start().to_string());
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

/// Extract the <memory> annotation block from an assistant response.
/// Returns (cleaned_response_text, optional_annotation_json).
pub fn extract_memory_block(response: &str) -> (String, Option<String>) {
    use regex::Regex;
    // Match <memory>...</memory> with optional newlines
    let re = Regex::new(r"(?s)<memory>(.*?)</memory>").unwrap();
    if let Some(caps) = re.captures(response) {
        let annotation = caps.get(1).map(|m| m.as_str().trim().to_string());
        let cleaned = re.replace_all(response, "").trim().to_string();
        (cleaned, annotation)
    } else {
        (response.to_string(), None)
    }
}

/// Parse an SSE response body from the Anthropic API and extract the assistant message.
///
/// Supports both streaming (SSE events) and non-streaming (JSON) responses.
/// Returns the full response text accumulated from content_block_delta events.
pub fn extract_assistant_response(body: &[u8]) -> (String, Option<String>) {
    // Try non-streaming JSON first
    if let Ok(resp) = serde_json::from_slice::<Value>(body) {
        let msg = extract_assistant_from_json(&resp);
        if !msg.is_empty() {
            return extract_memory_block(&msg);
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

    extract_memory_block(&full_text)
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

    // Always inject memory annotation instruction (regardless of facts)
    let mut content = String::new();
    if !facts.is_empty() {
        debug!("Injecting {} memory facts for OpenAI request", facts.len());
        content.push_str(&build_facts_block(&facts));
        content.push('\n');
    }
    content.push_str(MEMORY_INSTRUCTION);

    // Inject as a system message in the messages array
    if let Some(messages) = req.get_mut("messages").and_then(|v| v.as_array_mut()) {
        let sys_msg = serde_json::json!({
            "role": "system",
            "content": content.trim()
        });
        messages.insert(0, sys_msg);
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
pub fn extract_openai_response(body: &[u8]) -> (String, Option<String>) {
    // Try non-streaming JSON first
    if let Ok(resp) = serde_json::from_slice::<Value>(body) {
        if let Some(msg) = resp.pointer("/choices/0/message/content").and_then(|v| v.as_str()) {
            return extract_memory_block(msg);
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

    extract_memory_block(&full_text)
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

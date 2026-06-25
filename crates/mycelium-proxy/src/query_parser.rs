//! Query Parser — extracts structured recall queries from natural language messages.
//!
//! Uses an LLM to canonicalize user questions into a [`RecallQuery`] containing:
//! - **atoms**: canonical noun-phrase search terms
//! - **intent**: the retrieval strategy (factual, relational, temporal, exploratory)
//! - **temporal_hint**: optional time-bound (ISO date, relative phrase, or event name)
//!
//! # Architecture
//!
//! ```text
//! user_message  ──►  build_query_parser_prompt()
//!                         │
//!                         ▼
//!                  call_query_parser()
//!                     (LLM call)
//!                         │
//!                         ▼
//!                  parse_query_response()
//!                         │
//!                         ▼
//!                   Option<RecallQuery>
//! ```

use mycelium_core::{RecallIntent, RecallQuery};
use reqwest::Client;
use serde_json::Value;
use tracing::{debug, warn};

/// Build the LLM prompt to extract atoms, intent, and temporal hint.
///
/// The prompt asks the model to output a single JSON object with three keys:
/// - `atoms`: array of canonical noun phrases (the important entities, terms, names)
/// - `intent`: one of `"factual"`, `"relational"`, `"temporal"`, `"exploratory"`
/// - `temporal_hint`: ISO date string, relative time phrase, or `null`
pub fn build_query_parser_prompt(user_message: &str) -> String {
    format!(
        r#"You are a query parser for a memory retrieval system. Extract structured information from the user's question.

Return a JSON object with exactly these fields:
- "atoms": an array of strings — the canonical noun phrases (important entities, terms, names) that should be searched for. Extract 1-5 atoms.
- "intent": one of "factual", "relational", "temporal", or "exploratory".
  - "factual": the user wants a specific fact, definition, or piece of information
  - "relational": the user wants connections, relationships, or links between entities
  - "temporal": the user wants events ordered by time, sequences, or time-bound information
  - "exploratory": the user wants to explore broadly, see what's available, or discover
- "temporal_hint": if there is a time reference (absolute or relative), include it as a string. If none, use null.

Examples:
{{"atoms": ["Alice", "project deadline"], "intent": "factual", "temporal_hint": null}}
{{"atoms": ["server migration", "downtime"], "intent": "temporal", "temporal_hint": "last week"}}
{{"atoms": ["API design", "frontend team"], "intent": "relational", "temporal_hint": null}}
{{"atoms": ["meeting notes"], "intent": "exploratory", "temporal_hint": "yesterday"}}

Respond with ONLY the JSON object, no other text.

User question: {}"#,
        user_message
    )
}

/// Parse the LLM response JSON into a [`RecallQuery`].
///
/// Supports both response formats:
/// - OpenAI-style: `{"choices": [{"message": {"content": "... json ..."}}]}`
/// - Anthropic-style: `{"content": [{"text": "... json ..."}]}`
/// - Direct JSON: the top-level value is the query object itself
///
/// Returns `None` if the JSON is malformed, the content can't be parsed,
/// or the extracted atoms list is empty.
pub fn parse_query_response(json: &str) -> Option<RecallQuery> {
    let root: Value = serde_json::from_str(json).ok()?;

    // Try to extract JSON text from various LLM response formats
    let json_text: String = extract_json_text(&root)?;

    // Parse the inner JSON as a query object
    let query_obj: Value = serde_json::from_str(&json_text).ok()?;

    let obj = query_obj.as_object()?;

    // Extract atoms — required, must be non-empty
    let atoms: Vec<String> = obj
        .get("atoms")?
        .as_array()?
        .iter()
        .filter_map(|v| v.as_str().map(String::from))
        .collect();

    if atoms.is_empty() {
        warn!("Query parser returned empty atoms");
        return None;
    }

    // Extract intent — default to Relational
    let intent = match obj.get("intent").and_then(|v| v.as_str()) {
        Some("factual") => RecallIntent::Factual,
        Some("relational") => RecallIntent::Relational,
        Some("temporal") => RecallIntent::Temporal,
        Some("exploratory") => RecallIntent::Exploratory,
        _ => {
            debug!("Unknown or missing intent, defaulting to Relational");
            RecallIntent::Relational
        }
    };

    // Extract optional temporal hint
    let temporal_hint = obj
        .get("temporal_hint")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from);

    Some(RecallQuery {
        atoms,
        intent,
        temporal_hint,
    })
}

/// Extract the JSON text payload from common LLM response formats.
fn extract_json_text(root: &Value) -> Option<String> {
    // Anthropic format: root["content"][0]["text"]
    if let Some(content_arr) = root.get("content").and_then(|v| v.as_array()) {
        if let Some(first) = content_arr.first() {
            if let Some(text) = first.get("text").and_then(|v| v.as_str()) {
                return Some(clean_json_string(text));
            }
        }
    }

    // OpenAI format: root["choices"][0]["message"]["content"]
    if let Some(choices) = root.get("choices").and_then(|v| v.as_array()) {
        if let Some(first) = choices.first() {
            if let Some(msg) = first.get("message") {
                if let Some(text) = msg.get("content").and_then(|v| v.as_str()) {
                    return Some(clean_json_string(text));
                }
            }
        }
    }

    // Direct format: root itself is the query object — serialize back to string
    // (Guarded: only if root has the expected keys)
    if root.get("atoms").is_some() {
        return serde_json::to_string(root).ok();
    }

    None
}

/// Strip markdown code fences and surrounding whitespace from a JSON string.
fn clean_json_string(s: &str) -> String {
    let s = s.trim();
    // Remove ```json ... ``` fences
    if let Some(stripped) = s
        .strip_prefix("```json")
        .or_else(|| s.strip_prefix("```"))
        .and_then(|s| s.strip_suffix("```"))
    {
        return stripped.trim().to_string();
    }
    // Remove single backtick fences
    if s.starts_with('`') && s.ends_with('`') && s.len() > 1 {
        return s[1..s.len() - 1].trim().to_string();
    }
    s.to_string()
}

/// Call the LLM to parse a user message into a [`RecallQuery`].
///
/// Sends the query parser prompt to the specified LLM endpoint
/// (OpenAI-compatible API) and parses the structured response.
///
/// # Arguments
///
/// * `client` — a [`reqwest::Client`] for making HTTP requests
/// * `api_url` — the LLM API endpoint URL (e.g. `"https://api.openai.com/v1/chat/completions"`)
/// * `api_key` — the API key for authentication
/// * `model` — the model name/ID to use
/// * `user_message` — the raw user message to parse
pub async fn call_query_parser(
    client: &Client,
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

    debug!("Calling query parser LLM at {}", api_url);

    let response = client
        .post(api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| {
            warn!("Query parser LLM request failed: {}", e);
            e
        })
        .ok()?;

    let status = response.status();
    if !status.is_success() {
        warn!("Query parser LLM returned status {}", status);
        return None;
    }

    let response_text = response.text().await.map_err(|e| {
        warn!("Failed to read query parser response body: {}", e);
        e
    }).ok()?;

    parse_query_response(&response_text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_temporal_query() {
        let json = r#"{
            "atoms": ["deployment", "production", "incident"],
            "intent": "temporal",
            "temporal_hint": "last night"
        }"#;

        let query = parse_query_response(json);
        assert!(query.is_some(), "Expected a valid RecallQuery");

        let q = query.unwrap();
        assert_eq!(q.atoms, vec!["deployment", "production", "incident"]);
        assert_eq!(q.intent, RecallIntent::Temporal);
        assert_eq!(q.temporal_hint.as_deref(), Some("last night"));
    }

    #[test]
    fn test_parse_relational_query() {
        let json = r#"{
            "atoms": ["API design", "frontend team"],
            "intent": "relational",
            "temporal_hint": "yesterday"
        }"#;

        let query = parse_query_response(json);
        assert!(query.is_some(), "Expected a valid RecallQuery");

        let q = query.unwrap();
        assert_eq!(q.atoms, vec!["API design", "frontend team"]);
        assert_eq!(q.intent, RecallIntent::Relational);
        assert_eq!(q.temporal_hint.as_deref(), Some("yesterday"));
    }

    #[test]
    fn test_parse_empty_atoms() {
        let json = r#"{
            "atoms": [],
            "intent": "factual",
            "temporal_hint": null
        }"#;

        let query = parse_query_response(json);
        assert!(query.is_none(), "Empty atoms should return None");
    }

    #[test]
    fn test_parse_malformed_json() {
        let json = r#"this is not valid json"#;

        let query = parse_query_response(json);
        assert!(query.is_none(), "Malformed JSON should return None");
    }
}

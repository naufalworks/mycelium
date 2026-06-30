//! Mycelium Proxy — Axum-based reverse proxy for Anthropic API interception.
//!
//! Listens on :8443, intercepts /v1/messages, injects memory context,
//! logs conversations, and forwards to upstream.

pub mod cortex;

use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use mycelium_core::{EntryType, MyceliumConfig, RecallMode, Storage, Tier};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::Semaphore;
use tracing::{debug, error, info, warn};

pub mod interceptor;
pub mod query_parser;
pub mod context_synthesizer;

const CONTEXT_HEADER: &str = "\n<mycelium-memory>\nRecent memory context:\n";

/// Shared proxy state.
pub struct ProxyState {
    pub storage: Storage,
    pub config: MyceliumConfig,
    pub semaphore: Semaphore,
    pub turn_counter: AtomicI64,
    pub http_client: reqwest::Client,
    pub session_loaded: Mutex<HashMap<String, bool>>,
    pub injected_turns: Mutex<HashSet<i64>>,
    pub session_topics: Mutex<HashMap<String, Vec<String>>>,
    pub recall_mode: RecallMode,
    pub upstream_api_key: String,
    pub llm_url: String,
    pub model: String,
    pub llm_client: reqwest::Client,
    pub cortex_enabled: bool,
    pub cortex_skills: Vec<crate::cortex::Skill>,
}

/// Start the proxy server.
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = Storage::open(db_path)?;
    let start_turn = storage.count_entries().unwrap_or(0) + 1;

    let recall_mode = match std::env::var("MYCELIUM_RECALL_MODE")
        .unwrap_or_else(|_| "graph".to_string())
        .as_str()
    {
        "legacy" => RecallMode::Legacy,
        _ => RecallMode::GraphTraversal,
    };
    let upstream_api_key =
        std::env::var("MYCELIUM_UPSTREAM_API_KEY").unwrap_or_else(|_| String::new());
    let model = std::env::var("MYCELIUM_MODEL")
        .unwrap_or_else(|_| "claude-sonnet-4-20250514".to_string());
    // Always use the configured upstream URL for the query parser LLM
    // Ignore MYCELIUM_LLM_URL env var — it was causing stale URLs after config changes
    let llm_url = format!("{}/v1/messages", config.upstream_url);
    let llm_client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()?;

    let cortex_enabled = std::env::var("MYCELIUM_CORTEX_ENABLED")
        .unwrap_or_else(|_| "true".to_string())
        == "true";
    let mut cortex_skills = Vec::new();
    if cortex_enabled {
        let skills_path = std::env::var("MYCELIUM_CORTEX_SKILLS_PATH")
            .unwrap_or_else(|_| format!("{}/skills.yaml", config.root_dir.display()));
        cortex_skills = crate::cortex::load_skills(&std::path::Path::new(&skills_path));
        if cortex_skills.is_empty() {
            tracing::warn!("Cortex enabled but no skills loaded from {}", skills_path);
        } else {
            tracing::info!("Cortex loaded {} skills", cortex_skills.len());
        }
    }

    let state = Arc::new(ProxyState {
        storage,
        config: config.clone(),
        semaphore: Semaphore::new(config.max_concurrent),
        turn_counter: AtomicI64::new(start_turn),
        http_client: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()?,
        session_loaded: Mutex::new(HashMap::new()),
        injected_turns: Mutex::new(HashSet::new()),
        session_topics: Mutex::new(HashMap::new()),
        recall_mode,
        upstream_api_key,
        llm_url,
        model,
        llm_client,
        cortex_enabled,
        cortex_skills,
    });

    let addr = format!("127.0.0.1:{}", config.proxy_port);
    let recall_log = format!("{:?}", state.recall_mode);
    let model_log = state.model.clone();

    let app = Router::new()
        .route("/{*path}", any(proxy_router))
        .with_state(state);

    info!("Proxy listening on {}", addr);
    info!("Recall mode: {}", recall_log);
    info!("Recall model: {}", model_log);
    println!("🧬 Mycelium Proxy → {}", config.upstream_url);
    println!("   Listening on :{}", config.proxy_port);
    println!("   Max concurrent: {}", config.max_concurrent);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

/// Universal proxy router — inspects path and dispatches to the right handler.
/// Handles both `/v1/messages` and `/v1/v1/messages` (for clients with /v1 in base URL).
async fn proxy_router(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let raw_path = uri.path();
    let user_agent = headers.get("user-agent")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("unknown");
    let request_id = uuid::Uuid::new_v4().to_string();

    // Normalize path: strip duplicate /v1 prefix
    // Claude Code sends /v1/v1/messages when base URL is http://host:8443/v1
    let path = if raw_path.starts_with("/v1/v1/") {
        &raw_path[3..]  // Strip first "/v1" → "/v1/messages"
    } else {
        raw_path
    };

    tracing::info!(
        request_id = %request_id,
        method = %method,
        path = %path,
        raw_path = %raw_path,
        user_agent = %user_agent,
        "incoming request"
    );

    let response = if path.ends_with("/v1/messages") {
        tracing::debug!(request_id = %request_id, "routing to intercept_and_forward (Anthropic format)");
        intercept_and_forward(State(state), method, uri, headers, body).await
    } else if path.ends_with("/v1/chat/completions") {
        tracing::debug!(request_id = %request_id, "routing to handle_openai (OpenAI format)");
        handle_openai(State(state), method, uri, headers, body).await
    } else {
        tracing::debug!(request_id = %request_id, "routing to passthrough");
        passthrough(State(state), method, uri, headers, body).await
    };

    tracing::info!(
        request_id = %request_id,
        status = %response.status().as_u16(),
        "response sent"
    );

    response
}

/// Intercepts POST /v1/messages — injects memory context, logs exchanges.
async fn intercept_and_forward(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let request_id = uuid::Uuid::new_v4().to_string();
    tracing::info!(request_id = %request_id, "intercept_and_forward: starting");

    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            warn!(request_id = %request_id, "Too many concurrent requests — returning 503");
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };
    drop(_permit);

    // Read the request body
    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!(request_id = %request_id, "Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "failed to read body").into_response();
        }
    };

    // Log request details
    if let Ok(req_json) = serde_json::from_slice::<serde_json::Value>(&body_bytes) {
        let model = req_json.get("model").and_then(|v| v.as_str()).unwrap_or("unknown");
        let max_tokens = req_json.get("max_tokens").and_then(|v| v.as_i64()).unwrap_or(0);
        let messages = req_json.get("messages").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
        tracing::info!(
            request_id = %request_id,
            model = %model,
            max_tokens = max_tokens,
            message_count = messages,
            body_size = body_bytes.len(),
            "request parsed"
        );

        // Log first user message
        if let Some(msgs) = req_json.get("messages").and_then(|v| v.as_array()) {
            for msg in msgs {
                if let (Some(role), Some(content)) = (msg.get("role").and_then(|v| v.as_str()), msg.get("content").and_then(|v| v.as_str())) {
                    if role == "user" {
                        tracing::debug!(request_id = %request_id, "user message: {}", content.chars().take(200).collect::<String>());
                        break;
                    }
                }
            }
        }
    }

    // Extract user message + session via JSON parsing (no LLM needed)
    let context_block = String::new();
    let (mut req_body, session, user_msg) = match interceptor::process_request(
        &body_bytes,
        &state.storage,
        &context_block,
    ) {
        Some(result) => result,
        None => {
            let (result, _) = forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await;
            return result;
        }
    };

    // Search memory facts via SQLite and inject if found
    if !user_msg.is_empty() {
        let facts = state.storage.search_facts(&user_msg, 5).ok().unwrap_or_default();
        if !facts.is_empty() {
            info!(request_id = %request_id, "Found {} memory facts for: {}", facts.len(), user_msg.chars().take(60).collect::<String>());

            let fact_block = interceptor::build_facts_block(&facts);
            if let Ok(mut json) = serde_json::from_slice::<serde_json::Value>(&req_body) {
                let injection = format!("\n\n{}", fact_block);
                match json.get_mut("system") {
                    Some(serde_json::Value::String(s)) => {
                        *s = format!("{}{}", s, injection);
                    }
                    Some(serde_json::Value::Array(blocks)) => {
                        blocks.push(serde_json::json!({"type": "text", "text": injection.trim()}));
                    }
                    _ => {}
                }
                let modified = serde_json::to_vec(&json).unwrap_or(req_body);
                req_body = modified;
            }
        } else {
            debug!(request_id = %request_id, "No memory facts found for: {}", user_msg.chars().take(60).collect::<String>());
        }
    }

    // Forward to upstream, capture response
    let (upstream_resp, resp_body) = forward_to_upstream(&state, method, &uri, &headers, &req_body).await;

    // Extract assistant message — /v1/messages returns Anthropic format
    let (assistant_msg, annotation) = interceptor::extract_assistant_response(&resp_body);

    // Log the conversation if we have both user and assistant messages
    log_conversation(&state, &session, &user_msg, &assistant_msg, annotation);

    upstream_resp
}

/// Shared helper to log a user↔assistant exchange to storage.
fn log_conversation(state: &ProxyState, session: &str, user_msg: &str, assistant_msg: &str, annotation: Option<String>) {
    if user_msg.is_empty() || assistant_msg.is_empty() {
        return;
    }

    let turn = state.turn_counter.fetch_add(1, Ordering::SeqCst);
    let now = chrono::Utc::now();

    let entry = mycelium_core::Entry {
        turn,
        tier: Tier::Ephemeral,
        entry_type: EntryType::Conversation,
        session: session.to_string(),
        ts: now,
        user: user_msg.chars().take(500).collect(),
        assistant: assistant_msg.chars().take(2000).collect(),
        entities: Vec::new(),
        prev_hash: String::new(),
        hash: String::new(),
        finding: None,
        verdict: None,
        annotation,
    };

    if let Err(e) = state.storage.append_entry(&entry) {
        error!("Failed to log conversation: {}", e);
    } else {
        info!("Logged exchange for session {}", session);
    }
}

/// Handles OpenAI-format requests (/v1/chat/completions) — injects memory, logs.
async fn handle_openai(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let request_id = uuid::Uuid::new_v4().to_string();
    tracing::info!(request_id = %request_id, "handle_openai: starting");

    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            tracing::warn!(request_id = %request_id, "handle_openai: too many concurrent requests");
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!(request_id = %request_id, "Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "failed to read body").into_response();
        }
    };

    // Log request
    if let Ok(req_json) = serde_json::from_slice::<serde_json::Value>(&body_bytes) {
        let model = req_json.get("model").and_then(|v| v.as_str()).unwrap_or("unknown");
        let messages = req_json.get("messages").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0);
        tracing::info!(
            request_id = %request_id,
            model = %model,
            messages = messages,
            body_size = body_bytes.len(),
            "handle_openai: request parsed"
        );
    }

    // Intercept with OpenAI handler
    let (req_body, session, user_msg) = match interceptor::process_openai(&body_bytes, &state.storage) {
        Some(result) => {
            tracing::debug!(request_id = %request_id, "handle_openai: intercepted and injected memory context");
            result
        },
        None => {
            tracing::debug!(request_id = %request_id, "handle_openai: no interception, forwarding raw");
            let (result, _) = forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await;
            return result;
        }
    };

    // Forward to upstream
    let (upstream_resp, resp_body) = forward_to_upstream(&state, method, &uri, &headers, &req_body).await;

    // Extract assistant message
    let (assistant_msg, annotation) = interceptor::extract_openai_response(&resp_body);

    tracing::info!(
        request_id = %request_id,
        assistant_msg_len = assistant_msg.len(),
        has_annotation = annotation.is_some(),
        "handle_openai: response extracted"
    );

    // Log conversation
    log_conversation(&state, &session, &user_msg, &assistant_msg, annotation);

    upstream_resp
}

/// Passthrough handler — forwards all other requests without modification.
async fn passthrough(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let request_id = uuid::Uuid::new_v4().to_string();
    tracing::info!(request_id = %request_id, method = %method, path = %uri.path(), "passthrough: starting");

    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            tracing::warn!(request_id = %request_id, "passthrough: too many concurrent requests");
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b.to_vec(),
        Err(_) => {
            tracing::error!(request_id = %request_id, "passthrough: failed to read body");
            return (StatusCode::BAD_REQUEST).into_response();
        }
    };

    tracing::debug!(request_id = %request_id, body_size = body_bytes.len(), "passthrough: forwarding raw request");
    forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await.0
}

/// Forward a request to the upstream API.
/// Returns (Response, response_body_bytes).
async fn forward_to_upstream(
    state: &ProxyState,
    method: Method,
    uri: &Uri,
    headers: &HeaderMap,
    body: &[u8],
) -> (Response, Vec<u8>) {
    // Normalize path: strip duplicate /v1 prefix
    // Claude Code sends /v1/v1/messages when base URL is http://host:8443/v1
    let raw_path = uri.path_and_query().map(|pq| pq.as_str()).unwrap_or("");
    let normalized_path = if raw_path.starts_with("/v1/v1/") {
        &raw_path[3..]  // Strip first "/v1" → "/v1/messages"
    } else {
        raw_path
    };
    let upstream_url = format!("{}{}", state.config.upstream_url, normalized_path);

    tracing::debug!(
        upstream_url = %upstream_url,
        method = %method,
        body_size = body.len(),
        "forward_to_upstream: preparing request"
    );

    // Log model from request body if present
    if let Ok(req_json) = serde_json::from_slice::<serde_json::Value>(body) {
        let model = req_json.get("model").and_then(|v| v.as_str()).unwrap_or("unknown");
        tracing::info!("forward_to_upstream: model={}, upstream={}", model, upstream_url);
    }

    // Build upstream request — use the original HTTP method, not hardcoded POST
    let reqwest_method = match method {
        Method::GET => reqwest::Method::GET,
        Method::POST => reqwest::Method::POST,
        Method::PUT => reqwest::Method::PUT,
        Method::DELETE => reqwest::Method::DELETE,
        Method::PATCH => reqwest::Method::PATCH,
        Method::HEAD => reqwest::Method::HEAD,
        Method::OPTIONS => reqwest::Method::OPTIONS,
        _ => reqwest::Method::POST,
    };
    let mut upstream_req = reqwest::Request::new(
        reqwest_method,
        upstream_url.parse().unwrap(),
    );

    // Copy headers except Host
    for (key, value) in headers.iter() {
        if key.as_str() != "host" && key.as_str() != "content-length" {
            upstream_req.headers_mut().insert(key.clone(), value.clone());
        }
    }

    *upstream_req.body_mut() = Some(body.to_vec().into());

    tracing::debug!("forward_to_upstream: sending request to {}", upstream_url);
    let start = std::time::Instant::now();

    match state.http_client.execute(upstream_req).await {
        Ok(resp) => {
            let status = resp.status();
            let elapsed = start.elapsed();
            let resp_headers = resp.headers().clone();
            let resp_body = resp.bytes().await.unwrap_or_default();
            let mut body_vec = resp_body.to_vec();

            tracing::info!(
                upstream_url = %upstream_url,
                status = %status.as_u16(),
                elapsed_ms = elapsed.as_millis(),
                response_size = body_vec.len(),
                "forward_to_upstream: response received"
            );

            // Log error responses
            if !status.is_success() {
                let body_str = String::from_utf8_lossy(&body_vec);
                tracing::warn!(
                    upstream_url = %upstream_url,
                    status = %status.as_u16(),
                    "upstream error: {}",
                    body_str.chars().take(500).collect::<String>()
                );
            }

            // Log model from response
            if let Ok(resp_json) = serde_json::from_slice::<serde_json::Value>(&body_vec) {
                let model = resp_json.get("model").and_then(|v| v.as_str()).unwrap_or("unknown");
                let content = resp_json.pointer("/choices/0/message/content")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let finish_reason = resp_json.pointer("/choices/0/finish_reason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown");
                tracing::info!(
                    upstream_url = %upstream_url,
                    response_model = %model,
                    finish_reason = %finish_reason,
                    content_length = content.len(),
                    content_preview = %content.chars().take(50).collect::<String>(),
                    "forward_to_upstream: response content"
                );
            }

            // Filter response — strips thinking blocks if header or env var is set
            body_vec = interceptor::filter_response(&body_vec);

            let mut response = Response::builder().status(status);
            for (key, value) in resp_headers.iter() {
                if key.as_str() != "transfer-encoding" {
                    response = response.header(key.clone(), value.clone());
                }
            }
            let response = response
                .body(Body::from(body_vec.clone()))
                .unwrap_or_else(|_| Response::new(Body::from("proxy error")));

            (response, body_vec)
        }
        Err(e) => {
            let elapsed = start.elapsed();
            error!(
                upstream_url = %upstream_url,
                elapsed_ms = elapsed.as_millis(),
                error = %e,
                error_debug = ?e,
                "forward_to_upstream: request FAILED"
            );
            let body = format!("upstream error: {}", e).into_bytes();
            let response = (StatusCode::BAD_GATEWAY, format!("upstream error: {}", e)).into_response();
            (response, body)
        }
    }
}


//! Mycelium Proxy — Axum-based reverse proxy for Anthropic API interception.
//!
//! Listens on :8443, intercepts /v1/messages, injects memory context,
//! logs conversations, and forwards to upstream.

use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{any, post},
    Router,
};
use mycelium_core::{EntryType, MyceliumConfig, Storage, Tier};
use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::Semaphore;
use tracing::{error, info, warn};

pub mod interceptor;

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
}

/// Start the proxy server.
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = Storage::open(db_path)?;
    let start_turn = storage.count_entries().unwrap_or(0) + 1;

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
    });

    let app = Router::new()
        .route("/{*path}", any(proxy_router))
        .with_state(state);

    let addr = format!("127.0.0.1:{}", config.proxy_port);
    info!("Proxy listening on {}", addr);
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
    let path = uri.path();
    if path.ends_with("/v1/messages") {
        intercept_and_forward(State(state), method, uri, headers, body).await
    } else if path.ends_with("/v1/chat/completions") {
        handle_openai(State(state), method, uri, headers, body).await
    } else {
        passthrough(State(state), method, uri, headers, body).await
    }
}

/// Intercepts POST /v1/messages — injects memory context, logs exchanges.
async fn intercept_and_forward(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            warn!("Too many concurrent requests — returning 503");
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    // Read the request body
    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!("Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "failed to read body").into_response();
        }
    };

    // Try to intercept: parse, inject memory context
    let (mut req_body, session, user_msg) = match interceptor::process_request(&body_bytes, &state.storage) {
        Some(result) => result,
        None => {
            let (result, _) = forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await;
            return result;
        }
    };

    // A1/A2: Session context injection
    {
        let mut is_loaded = state.session_loaded.lock().unwrap();
        let loaded = is_loaded.entry(session.clone()).or_insert(false);
        if !*loaded {
            let context_entries = state.storage
                .entries_for_session(&session, 5)
                .ok()
                .unwrap_or_default();
            if !context_entries.is_empty() {
                let mut ctx_lines = String::from(CONTEXT_HEADER);
                for entry in &context_entries {
                    let preview: String = entry.user.chars().take(100).collect();
                    ctx_lines.push_str(&format!("  [#{}] {}: {}\n", entry.turn, entry.session, preview));
                }
                ctx_lines.push_str("</mycelium-memory>");
                if let Ok(mut json) = serde_json::from_slice::<serde_json::Value>(&req_body) {
                    let existing = json.get("system").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    json["system"] = serde_json::Value::String(format!("{}\n\n{}", existing, ctx_lines));
                    req_body = serde_json::to_vec(&json).unwrap_or(req_body);
                    let mut injected = state.injected_turns.lock().unwrap();
                    for entry in &context_entries { injected.insert(entry.turn); }
                }
            }
            *loaded = true;
        }
    }

    // Forward to upstream, capture response
    let (upstream_resp, resp_body) = forward_to_upstream(&state, method, &uri, &headers, &req_body).await;

    // Extract assistant message
    let assistant_msg = interceptor::extract_assistant_response(&resp_body);

    // Log the conversation if we have both user and assistant messages
    log_conversation(&state, &session, &user_msg, &assistant_msg);

    upstream_resp
}

/// Shared helper to log a user↔assistant exchange to storage.
fn log_conversation(state: &ProxyState, session: &str, user_msg: &str, assistant_msg: &str) {
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
    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!("Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "failed to read body").into_response();
        }
    };

    // Intercept with OpenAI handler
    let (req_body, session, user_msg) = match interceptor::process_openai(&body_bytes, &state.storage) {
        Some(result) => result,
        None => {
            let (result, _) = forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await;
            return result;
        }
    };

    // Forward to upstream
    let (upstream_resp, resp_body) = forward_to_upstream(&state, method, &uri, &headers, &req_body).await;

    // Extract assistant message
    let assistant_msg = interceptor::extract_openai_response(&resp_body);

    // Log conversation
    log_conversation(&state, &session, &user_msg, &assistant_msg);

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
    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b.to_vec(),
        Err(_) => return (StatusCode::BAD_REQUEST).into_response(),
    };

    forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await.0
}

/// Forward a request to the upstream API.
/// Returns (Response, response_body_bytes).
async fn forward_to_upstream(
    state: &ProxyState,
    _method: Method,
    uri: &Uri,
    headers: &HeaderMap,
    body: &[u8],
) -> (Response, Vec<u8>) {
    let upstream_url = format!(
        "{}{}",
        state.config.upstream_url,
        uri.path_and_query().map(|pq| pq.as_str()).unwrap_or("")
    );

    // Build upstream request
    let mut upstream_req = reqwest::Request::new(
        reqwest::Method::POST,
        upstream_url.parse().unwrap(),
    );

    // Copy headers except Host
    for (key, value) in headers.iter() {
        if key.as_str() != "host" && key.as_str() != "content-length" {
            upstream_req.headers_mut().insert(key.clone(), value.clone());
        }
    }

    *upstream_req.body_mut() = Some(body.to_vec().into());

    match state.http_client.execute(upstream_req).await {
        Ok(resp) => {
            let status = resp.status();
            let resp_headers = resp.headers().clone();
            let resp_body = resp.bytes().await.unwrap_or_default();
            let mut body_vec = resp_body.to_vec();

            // Filter response — strips thinking blocks if header or env var is set
            // Filter response — strips thinking blocks on filtered port
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
            error!("Upstream request failed: {}", e);
            let body = format!("upstream error: {}", e).into_bytes();
            let response = (StatusCode::BAD_GATEWAY, format!("upstream error: {}", e)).into_response();
            (response, body)
        }
    }
}


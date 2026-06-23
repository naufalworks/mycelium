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
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Arc;
use tokio::sync::Semaphore;
use tracing::{error, info, warn};

pub mod interceptor;

/// Shared proxy state.
pub struct ProxyState {
    pub storage: Storage,
    pub config: MyceliumConfig,
    pub semaphore: Semaphore,
    pub turn_counter: AtomicI64,
    pub http_client: reqwest::Client,
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
    });

    let app = Router::new()
        .route("/v1/messages", post(intercept_and_forward))
        .route("/{*path}", any(passthrough))
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

    // Try to intercept: parse, inject memory context, forward
    let (modified_body, session, user_msg) = match interceptor::process_request(&body_bytes, &state.storage) {
        Some(result) => result,
        None => {
            // Request doesn't need interception (no user message, etc.)
            // Forward unchanged
            let result = forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await;
            return result;
        }
    };

    // Forward the modified request and capture the response
    let upstream_resp = forward_to_upstream(&state, method, &uri, &headers, &modified_body).await;

    // Extract assistant message from the response
    let assistant_msg = extract_assistant_message(&upstream_resp);

    // Log the conversation if we have both user and assistant messages
    if !user_msg.is_empty() && !assistant_msg.is_empty() {
        let turn = state.turn_counter.fetch_add(1, Ordering::SeqCst);
        let now = chrono::Utc::now();

        let entry = mycelium_core::Entry {
            turn,
            tier: Tier::Ephemeral,
            entry_type: EntryType::Conversation,
            session: session.clone(),
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

    forward_to_upstream(&state, method, &uri, &headers, &body_bytes).await
}

/// Forward a request to the upstream API and return the response.
async fn forward_to_upstream(
    state: &ProxyState,
    _method: Method,
    uri: &Uri,
    headers: &HeaderMap,
    body: &[u8],
) -> Response {
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

            let mut response = Response::builder().status(status);
            for (key, value) in resp_headers.iter() {
                if key.as_str() != "transfer-encoding" {
                    response = response.header(key.clone(), value.clone());
                }
            }
            response
                .body(Body::from(resp_body.to_vec()))
                .unwrap_or_else(|_| Response::new(Body::from("proxy error")))
        }
        Err(e) => {
            error!("Upstream request failed: {}", e);
            (StatusCode::BAD_GATEWAY, format!("upstream error: {}", e)).into_response()
        }
    }
}

/// Try to extract the assistant message from an upstream response.
fn extract_assistant_message(_resp: &Response) -> String {
    // For SSE streaming responses, we can't easily extract from Response
    // For non-streaming, try to parse as Anthropic Messages API format
    String::new()
}

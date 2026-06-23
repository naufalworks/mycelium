//! Mycelium Proxy — Axum-based reverse proxy for Anthropic API interception.
//!
//! Listens on :8443, intercepts /v1/messages, injects memory context,
//! logs conversations, and forwards to upstream.

use axum::{
    body::Body,
    extract::State,
    http::{HeaderMap, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::any,
    Router,
};
use mycelium_core::MyceliumConfig;
use std::sync::Arc;
use tokio::sync::Semaphore;
use tracing::{error, info, warn};

/// Shared proxy state.
pub struct ProxyState {
    pub config: MyceliumConfig,
    pub semaphore: Semaphore,
    pub http_client: reqwest::Client,
}

/// Start the proxy server.
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let state = Arc::new(ProxyState {
        config: config.clone(),
        semaphore: Semaphore::new(config.max_concurrent),
        http_client: reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()?,
    });

    let app = Router::new()
        .route("/{*path}", any(proxy_handler))
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

/// Generic proxy handler — forwards all requests to the upstream.
async fn proxy_handler(
    State(state): State<Arc<ProxyState>>,
    method: Method,
    uri: axum::http::Uri,
    headers: HeaderMap,
    body: Body,
) -> Response {
    // Acquire concurrency slot
    let _permit = match state.semaphore.try_acquire() {
        Ok(permit) => permit,
        Err(_) => {
            warn!("Too many concurrent requests — returning 503");
            return (StatusCode::SERVICE_UNAVAILABLE, "too many requests").into_response();
        }
    };

    // Build upstream URL
    let upstream_url = format!("{}{}", state.config.upstream_url, uri.path_and_query().map(|pq| pq.as_str()).unwrap_or(""));

    // Read the request body
    let body_bytes = match axum::body::to_bytes(body, 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!("Failed to read request body: {}", e);
            return (StatusCode::BAD_REQUEST, "failed to read body").into_response();
        }
    };

    // Build upstream request
    let mut upstream_req = reqwest::Request::new(
        match method {
            Method::GET => reqwest::Method::GET,
            Method::POST => reqwest::Method::POST,
            Method::PUT => reqwest::Method::PUT,
            Method::DELETE => reqwest::Method::DELETE,
            Method::PATCH => reqwest::Method::PATCH,
            _ => reqwest::Method::GET,
        },
        upstream_url.parse().unwrap(),
    );

    // Copy headers (skip Host)
    for (key, value) in headers.iter() {
        if key.as_str() != "host" {
            upstream_req.headers_mut().insert(key.clone(), value.clone());
        }
    }

    *upstream_req.body_mut() = Some(body_bytes.to_vec().into());

    // Send the request
    match state.http_client.execute(upstream_req).await {
        Ok(upstream_resp) => {
            let status = upstream_resp.status();
            let resp_headers = upstream_resp.headers().clone();
            let resp_body = upstream_resp.bytes().await.unwrap_or_default();

            // Build the response
            let mut response = Response::builder().status(status);
            for (key, value) in resp_headers.iter() {
                response = response.header(key.clone(), value.clone());
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

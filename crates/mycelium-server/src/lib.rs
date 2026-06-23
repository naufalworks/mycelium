//! Mycelium HTTP Server — Axum-based REST API.
//!
//! Serves the web frontend and REST API for all memory operations.
//! Replaces the existing Python FastAPI backend.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{sse::Event, Json, Sse},
    routing::{delete, get},
    Router,
};
use mycelium_core::{MemoryFact, MyceliumConfig, Storage};
use serde::Deserialize;
use std::sync::Arc;
use tokio::sync::broadcast;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt as _;
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;
use tracing::{error, info};

pub struct AppState {
    pub storage: Storage,
    pub config: MyceliumConfig,
    pub event_tx: broadcast::Sender<String>,
}

/// Start the HTTP server on the configured port.
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = Storage::open(db_path)?;
    let (event_tx, _) = broadcast::channel(1024);

    let state = Arc::new(AppState {
        storage,
        config: config.clone(),
        event_tx,
    });

    let app = Router::new()
        .route("/api/health", get(health))
        .route("/api/status", get(status))
        .route("/api/config", get(get_config))
        .route("/api/stream", get(stream))
        .route("/api/sessions", get(list_sessions))
        .route("/api/sessions/{name}", get(get_session))
        .route("/api/entries", get(list_entries))
        .route("/api/entries/{turn}", get(get_entry))
        .route("/api/memory/facts", get(search_facts).post(create_fact))
        .route("/api/memory/facts/{id}", delete(delete_fact))
        .route("/api/memory/snapshots", get(list_snapshots).post(create_snapshot))
        .route("/api/memory/snapshots/{id}", delete(delete_snapshot))
        .route("/api/daemon", get(daemon_status))
        .route("/api/artifacts", get(list_artifacts))
        .route("/api/search", get(search_all))
        .route("/api/backups", get(list_backups).post(create_backup))
        .route("/api/verify", get(verify_chain))
        .layer(CorsLayer::permissive())
        .with_state(state)
        .fallback_service(ServeDir::new(
            std::env::var("MYCELIUM_WEB_ROOT")
                .unwrap_or_else(|_| {
                    let root = std::env::var("MYCELIUM_ROOT")
                        .unwrap_or_else(|_| ".".to_string());
                    format!("{}/web", root)
                })
        ).append_index_html_on_directories(true));

    let addr = format!("127.0.0.1:{}", config.server_port);
    info!("Server listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    Ok(())
}

// ── Handlers ──

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "ok"}))
}

async fn status(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let count = state.storage.count_entries().unwrap_or(0);
    let sessions = state.storage.count_sessions().unwrap_or(0);
    let tiers = state.storage.tier_distribution().unwrap_or_default();
    let types = state.storage.type_distribution().unwrap_or_default();
    let db_size = state.storage.db_size().unwrap_or(0);
    let last = state.storage.last_entry().ok().flatten();

    Json(serde_json::json!({
        "total_turns": count,
        "total_sessions": sessions,
        "tiers": tiers,
        "types": types,
        "storage_bytes": db_size,
        "last_turn": last.map(|e| serde_json::json!({
            "turn": e.turn,
            "ts": e.ts.to_rfc3339(),
            "tier": e.tier.as_str(),
        })),
    }))
}

async fn get_config(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "root_dir": state.config.root_dir,
        "proxy_port": state.config.proxy_port,
        "server_port": state.config.server_port,
        "max_concurrent": state.config.max_concurrent,
    }))
}

async fn stream(
    State(state): State<Arc<AppState>>,
) -> Sse<impl tokio_stream::Stream<Item = Result<Event, std::convert::Infallible>>> {
    let rx = state.event_tx.subscribe();
    let stream = BroadcastStream::new(rx).filter_map(|result| match result {
        Ok(data) => Some(Ok(Event::default().data(data))),
        Err(_) => None,
    });
    Sse::new(stream).keep_alive(axum::response::sse::KeepAlive::new())
}

async fn list_sessions(
    State(state): State<Arc<AppState>>,
) -> Json<Vec<String>> {
    match state.storage.recent_sessions(100) {
        Ok(sessions) => Json(sessions),
        Err(e) => {
            error!("list_sessions: {}", e);
            Json(Vec::new())
        }
    }
}

async fn get_session(
    State(state): State<Arc<AppState>>,
    Path(name): Path<String>,
) -> Json<serde_json::Value> {
    let entries = state.storage.entries_for_session(&name, 50).unwrap_or_default();
    Json(serde_json::json!({
        "session": name,
        "entry_count": entries.len(),
        "entries": entries,
    }))
}

async fn list_entries(
    State(state): State<Arc<AppState>>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let limit = params.get("limit").and_then(|v| v.parse().ok()).unwrap_or(20);
    let offset: i64 = params.get("offset").and_then(|v| v.parse().ok()).unwrap_or(0);
    let total = state.storage.count_entries().unwrap_or(0);
    let entries = match state.storage.recent_entries_offset(limit, offset) {
        Ok(e) => e,
        Err(e) => { error!("list_entries: {}", e); vec![] }
    };
    Json(serde_json::json!({"entries": entries, "total": total}))
}

async fn get_entry(
    State(state): State<Arc<AppState>>,
    Path(turn): Path<i64>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state.storage.get_entry(turn) {
        Ok(Some(entry)) => Ok(Json(serde_json::json!(entry))),
        Ok(None) => Err(StatusCode::NOT_FOUND),
        Err(_) => Err(StatusCode::INTERNAL_SERVER_ERROR),
    }
}

#[derive(Deserialize)]
struct FactQuery {
    q: Option<String>,
    limit: Option<i64>,
}

async fn search_facts(
    State(state): State<Arc<AppState>>,
    Query(params): Query<FactQuery>,
) -> Json<serde_json::Value> {
    let query = params.q.unwrap_or_default();
    let limit = params.limit.unwrap_or(20);
    let facts = match state.storage.search_facts(&query, limit) {
        Ok(f) => f,
        Err(e) => { error!("search_facts: {}", e); vec![] }
    };
    Json(serde_json::json!({"facts": facts, "count": facts.len()}))
}

#[derive(Deserialize)]
struct CreateFactPayload {
    entity: String,
    attribute: String,
    value: String,
    confidence: Option<f64>,
    source_session: Option<String>,
}

async fn create_fact(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<CreateFactPayload>,
) -> (StatusCode, Json<serde_json::Value>) {
    let fact = MemoryFact {
        id: None,
        entity: payload.entity,
        attribute: payload.attribute,
        value: payload.value,
        fact_type: "fact".into(),
        confidence: payload.confidence.unwrap_or(0.8),
        source_session: payload.source_session,
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };
    match state.storage.upsert_fact(&fact) {
        Ok(id) => (StatusCode::CREATED, Json(serde_json::json!({"id": id}))),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({"error": e.to_string()}))),
    }
}

async fn delete_fact(
    State(state): State<Arc<AppState>>,
    Path(id): Path<i64>,
) -> StatusCode {
    match state.storage.delete_fact(id) {
        Ok(true) => StatusCode::NO_CONTENT,
        Ok(false) => StatusCode::NOT_FOUND,
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

async fn list_snapshots(
    State(state): State<Arc<AppState>>,
) -> Json<serde_json::Value> {
    match state.storage.list_snapshots(50) {
        Ok(snapshots) => Json(serde_json::json!(snapshots)),
        Err(e) => {
            error!("list_snapshots: {}", e);
            Json(serde_json::json!([]))
        }
    }
}

async fn create_snapshot(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<serde_json::Value>,
) -> (StatusCode, Json<serde_json::Value>) {
    let session_id = payload.get("session_id").and_then(|v| v.as_str()).unwrap_or("default");
    let summary = payload.get("summary").and_then(|v| v.as_str()).unwrap_or("");
    match state.storage.create_snapshot(session_id, summary, &[], &[], &[], &[]) {
        Ok(id) => (StatusCode::CREATED, Json(serde_json::json!({"id": id}))),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({"error": e.to_string()}))),
    }
}

async fn delete_snapshot(
    State(state): State<Arc<AppState>>,
    Path(id): Path<i64>,
) -> StatusCode {
    match state.storage.delete_snapshot(id) {
        Ok(true) => StatusCode::NO_CONTENT,
        Ok(false) => StatusCode::NOT_FOUND,
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR,
    }
}

async fn daemon_status(
    State(state): State<Arc<AppState>>,
) -> Json<serde_json::Value> {
    let db_size = state.storage.db_size().unwrap_or(0);
    let running = true; // We're running if we can respond

    Json(serde_json::json!({
        "running": running,
        "pid": std::process::id(),
        "db_size_mb": db_size as f64 / 1048576.0,
        "version": "0.1.0",
    }))
}

async fn list_artifacts(
    State(state): State<Arc<AppState>>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let session = params.get("session").map(|s| s.as_str()).unwrap_or("");
    let artifacts = if session.is_empty() {
        vec![]
    } else {
        state.storage.list_artifacts(session).unwrap_or_default()
    };
    Json(serde_json::json!(artifacts))
}

async fn search_all(
    State(state): State<Arc<AppState>>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let query = params.get("q").map(|s| s.as_str()).unwrap_or("");
    let limit = params.get("limit").and_then(|v| v.parse().ok()).unwrap_or(20);
    let offset: i64 = params.get("offset").and_then(|v| v.parse().ok()).unwrap_or(0);

    let total = state.storage.count_search_entries(query).unwrap_or(0);
    let entries = state.storage.search_fts_offset(query, limit, offset).unwrap_or_default();
    let facts = state.storage.search_facts(query, limit).unwrap_or_default();

    Json(serde_json::json!({
        "entries": entries,
        "total": total,
        "facts": facts,
    }))
}

async fn list_backups() -> Json<serde_json::Value> {
    Json(serde_json::json!({"backups": []}))
}

async fn create_backup() -> Json<serde_json::Value> {
    Json(serde_json::json!({"status": "not implemented"}))
}



async fn verify_chain(
    State(state): State<Arc<AppState>>,
) -> Json<serde_json::Value> {
    match state.storage.verify_hash_chain() {
        Ok(failures) => Json(serde_json::json!({
            "intact": failures.is_empty(),
            "failures": failures.len(),
            "details": failures.iter().map(|(t, e, a)| serde_json::json!({"turn": t, "expected": e, "actual": a})).collect::<Vec<_>>(),
        })),
        Err(e) => Json(serde_json::json!({"error": e.to_string()})),
    }
}

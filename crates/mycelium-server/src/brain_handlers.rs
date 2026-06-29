//! REST handlers for brain status and atom queries.
use axum::{extract::State, Json};
use mycelium_core::brain;
use std::sync::Arc;

use crate::AppState;

/// Response for GET /api/brain/heat
#[derive(serde::Serialize)]
pub struct BrainHeatResponse {
    pub metrics: mycelium_core::hot_graph::HeatMetricsSnapshot,
    pub top_atoms: Vec<mycelium_core::hot_graph::HotAtomSnapshot>,
}

/// GET /api/brain/heat — returns heat metrics and top atoms.
pub async fn brain_heat(
    State(state): State<Arc<AppState>>,
) -> Json<BrainHeatResponse> {
    let metrics = state.storage.hot_graph().metrics().snapshot();
    let top_atoms = state.storage.hot_graph().top_atoms(10);
    Json(BrainHeatResponse { metrics, top_atoms })
}

/// Response for GET /api/brain/status
#[derive(serde::Serialize)]
pub struct BrainStatusResponse {
    pub atom_count: i64,
    pub edge_count: i64,
    pub position_count: i64,
    pub pending_count: i64,
}

/// GET /api/brain/status — returns aggregate atom/edge counts.
pub async fn brain_status(
    State(state): State<Arc<AppState>>,
) -> Json<BrainStatusResponse> {
    let conn = state.storage.connection().lock().unwrap();
    let status = brain::brain_status(&conn).unwrap();
    Json(BrainStatusResponse {
        atom_count: status.atom_count,
        edge_count: status.edge_count,
        position_count: status.position_count,
        pending_count: status.pending_count,
    })
}

/// GET /api/brain/atoms — returns all atoms ordered by weight.
pub async fn brain_atoms(
    State(state): State<Arc<AppState>>,
) -> Json<Vec<serde_json::Value>> {
    let conn = state.storage.connection().lock().unwrap();
    let atoms = brain::recall(&conn, "", 200).unwrap_or_default();
    let json: Vec<serde_json::Value> = atoms
        .iter()
        .map(|a| {
            serde_json::json!({
                "id": a.id,
                "phrase": a.phrase,
                "first_seen": a.first_seen,
                "last_seen": a.last_seen,
                "ref_count": a.ref_count,
                "importance": a.importance,
            })
        })
        .collect();
    Json(json)
}

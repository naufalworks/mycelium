use leptos::prelude::*;

#[derive(Clone)]
pub struct Alert {
    pub kind: String,
    pub message: String,
    pub node_id: Option<usize>,
}

#[derive(Clone)]
pub struct AppState {
    pub atom_count: RwSignal<i64>,
    pub edge_count: RwSignal<i64>,
    pub connected: RwSignal<bool>,
    pub alerts: RwSignal<Vec<Alert>>,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            atom_count: RwSignal::new(0),
            edge_count: RwSignal::new(0),
            connected: RwSignal::new(false),
            alerts: RwSignal::new(vec![]),
        }
    }
}

use leptos::prelude::*;

#[derive(Clone, Debug, PartialEq)]
pub enum AlertKind {
    Pattern,
    Contradiction,
    Merge,
    Evolve,
}

impl AlertKind {
    pub fn label(&self) -> &'static str {
        match self {
            AlertKind::Pattern => "Pattern",
            AlertKind::Contradiction => "Contradiction",
            AlertKind::Merge => "Merged",
            AlertKind::Evolve => "Branched",
        }
    }

    pub fn css_class(&self) -> &'static str {
        match self {
            AlertKind::Pattern => "pattern",
            AlertKind::Contradiction => "contradiction",
            AlertKind::Merge => "merge",
            AlertKind::Evolve => "evolve",
        }
    }
}

#[derive(Clone)]
pub struct Alert {
    pub kind: AlertKind,
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

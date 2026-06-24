use crate::state::AppState;
use leptos::prelude::*;

#[component]
pub fn StatsPanel(state: AppState) -> impl IntoView {
    let atoms = move || state.atom_count.get();
    let edges = move || state.edge_count.get();
    let findings = move || state.alerts.get().len();

    view! {
        <div id="stats">
            <div class="stat">
                <div class="num">{move || atoms().to_string()}</div>
                <div class="label">Concepts</div>
            </div>
            <div class="stat">
                <div class="num">{move || edges().to_string()}</div>
                <div class="label">Edges</div>
            </div>
            <div class="stat">
                <div class="num">{move || findings().to_string()}</div>
                <div class="label">Findings</div>
            </div>
        </div>
    }
}

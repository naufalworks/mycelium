//! Graph view — entity relationship visualization (placeholder).

use leptos::prelude::*;

/// Entity relationship graph (nice-to-have feature).
#[component]
pub fn GraphView() -> impl IntoView {
    view! {
        <div class="view graph-view">
            <h2>"Entity Graph"</h2>
            <div class="placeholder-card">
                <p>"Graph visualization will appear here..."</p>
            </div>
        </div>
    }
}

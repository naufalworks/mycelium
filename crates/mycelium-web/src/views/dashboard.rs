//! Dashboard view — brain stats, stream, and health.

use leptos::prelude::*;

/// Dashboard showing brain status, entry stream, and daemon health.
#[component]
pub fn DashboardView() -> impl IntoView {
    view! {
        <div class="view dashboard-view">
            <h2>"Dashboard"</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>"Total Entries"</h3>
                    <p class="stat-value">"—"</p>
                </div>
                <div class="stat-card">
                    <h3>"Sessions"</h3>
                    <p class="stat-value">"—"</p>
                </div>
                <div class="stat-card">
                    <h3>"Memory Facts"</h3>
                    <p class="stat-value">"—"</p>
                </div>
                <div class="stat-card">
                    <h3>"DB Size"</h3>
                    <p class="stat-value">"—"</p>
                </div>
            </div>
            <div class="stream-panel">
                <h3>"Live Stream"</h3>
                <p class="placeholder">"Stream will appear here..."</p>
            </div>
        </div>
    }
}

//! Artifacts view — browse, upload, and download artifacts.

use leptos::prelude::*;

/// Artifact browser and manager.
#[component]
pub fn ArtifactsView() -> impl IntoView {
    view! {
        <div class="view artifacts-view">
            <h2>"Artifacts"</h2>
            <div class="placeholder-card">
                <p>"Artifacts will appear here..."</p>
            </div>
        </div>
    }
}

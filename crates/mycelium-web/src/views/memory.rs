//! Memory view — browse and search memory facts.

use leptos::prelude::*;

/// Memory facts viewer with search and CRUD.
#[component]
pub fn MemoryView() -> impl IntoView {
    view! {
        <div class="view memory-view">
            <h2>"Memory Facts"</h2>
            <div class="placeholder-card">
                <p>"Memory facts will appear here..."</p>
            </div>
        </div>
    }
}

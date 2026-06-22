//! Sidebar navigation component.

use leptos::prelude::*;
use leptos_router::components::A;

/// Sidebar with navigation links for all views.
#[component]
pub fn Sidebar() -> impl IntoView {
    let nav_items = vec![
        ("/", "Dashboard", "📊"),
        ("/memory", "Memory", "🧠"),
        ("/artifacts", "Artifacts", "📦"),
        ("/workflows", "Workflows", "⚡"),
        ("/graph", "Graph", "🔗"),
        ("/settings", "Settings", "⚙️"),
    ];

    view! {
        <nav class="sidebar">
            <div class="sidebar-header">
                <h1 class="sidebar-title">"🍄 mycelium"</h1>
            </div>
            <ul class="sidebar-nav">
                {nav_items
                    .into_iter()
                    .map(|(path, label, icon)| {
                        view! {
                            <li class="sidebar-item">
                                <A href=path class="sidebar-link">
                                    <span class="sidebar-icon">{icon}</span>
                                    <span class="sidebar-label">{label}</span>
                                </A>
                            </li>
                        }
                    })
                    .collect::<Vec<_>>()}
            </ul>
        </nav>
    }
}

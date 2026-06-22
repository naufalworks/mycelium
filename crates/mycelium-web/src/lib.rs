//! Mycelium Web Frontend — Leptos SPA.
//!
//! Server-side rendered with hydration (SSR + WASM).
//! Provides dashboard, memory, artifacts, workflows, and settings views.

pub mod components;
pub mod views;

use leptos::prelude::*;
use leptos_router::{
    components::{Outlet, Route, Router, Routes},
    path,
};

/// Application entry point — sets up routing and shell layout.
#[component]
pub fn App() -> impl IntoView {
    view! {
        <Router>
            <div class="app-shell">
                <components::Sidebar />
                <main class="main-content">
                    <components::TopBar />
                    <Routes fallback=|| view! { <h1>"404 — Not Found"</h1> }>
                        <Route path=path!("/") view=views::DashboardView />
                        <Route path=path!("/memory") view=views::MemoryView />
                        <Route path=path!("/artifacts") view=views::ArtifactsView />
                        <Route path=path!("/workflows") view=views::WorkflowsView />
                        <Route path=path!("/settings") view=views::SettingsView />
                        <Route path=path!("/graph") view=views::GraphView />
                    </Routes>
                </main>
            </div>
        </Router>
    }
}

/// Hook up the client-side WASM entry point.
#[cfg(not(feature = "ssr"))]
pub fn main() {
    leptos::mount::mount_to_body(App);
}

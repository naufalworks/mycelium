//! Settings view — configuration and daemon control.

use leptos::prelude::*;

/// Application settings and daemon management.
#[component]
pub fn SettingsView() -> impl IntoView {
    view! {
        <div class="view settings-view">
            <h2>"Settings"</h2>
            <div class="settings-grid">
                <div class="setting-card">
                    <h3>"Daemon"</h3>
                    <p class="setting-status">"Status: Unknown"</p>
                </div>
                <div class="setting-card">
                    <h3>"Database"</h3>
                    <p class="setting-value">"Path: —"</p>
                </div>
            </div>
        </div>
    }
}

//! Top bar component with search and status.

use leptos::prelude::*;

/// Top bar showing current view title and global search.
#[component]
pub fn TopBar() -> impl IntoView {
    let (search, set_search) = signal(String::new());

    view! {
        <div class="topbar">
            <div class="topbar-search">
                <input
                    type="text"
                    placeholder="Search memory..."
                    class="search-input"
                    bind:value=(search, set_search)
                    on:keydown=move |ev| {
                        if ev.key() == "Enter" {
                            let _ = &search.get();
                            // TODO: navigate to search results
                        }
                    }
                />
            </div>
            <div class="topbar-status">
                <span class="status-dot"></span>
                <span class="status-text">"Connected"</span>
            </div>
        </div>
    }
}

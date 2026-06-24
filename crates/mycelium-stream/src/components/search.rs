use leptos::prelude::*;

#[component]
pub fn Search() -> impl IntoView {
    view! {
        <div id="search">
            <div class="trigger">
                <kbd>"⌘K"</kbd>
                " Search memory…"
            </div>
        </div>
    }
}

use leptos::prelude::*;

#[component]
pub fn Timeline() -> impl IntoView {
    view! {
        <div id="timeline">
            <span class="label">"3d ago"</span>
            <div class="track">
                <div class="fill"></div>
                <div class="now"></div>
            </div>
            <span class="label">now</span>
        </div>
    }
}

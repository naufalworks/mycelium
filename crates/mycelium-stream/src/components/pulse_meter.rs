use leptos::prelude::*;

#[component]
pub fn PulseMeter() -> impl IntoView {
    view! {
        <div id="pulse-meter">
            <span class="label">Pulse</span>
            <div class="wave">
                <div class="fill" id="pulse-fill"></div>
            </div>
        </div>
    }
}

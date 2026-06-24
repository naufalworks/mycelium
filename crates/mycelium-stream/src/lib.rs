use leptos::prelude::*;

#[component]
pub fn App() -> impl IntoView {
    view! {
        <canvas id="canvas"></canvas>
        <div id="ui">
            <div id="brand">
                <div class="organism">"mycelium " <em>"stream"</em></div>
                <div class="sub">"living memory · live"</div>
            </div>
        </div>
    }
}

use crate::state::AppState;
use leptos::prelude::*;

#[component]
pub fn AlertFeed(state: AppState) -> impl IntoView {
    let alerts = move || state.alerts.get();

    view! {
        <div id="alerts">
            {move || {
                let items = alerts();
                items
                    .into_iter()
                    .map(|alert| {
                        let css_class = alert.kind.css_class().to_string();
                        let label = alert.kind.label().to_string();
                        let message = alert.message.clone();
                        view! {
                            <div class="alert">
                                <div class={format!("icon {}", css_class)}></div>
                                <div>
                                    <strong>{label}</strong>
                                    " "
                                    {message}
                                </div>
                            </div>
                        }
                    })
                    .collect::<Vec<_>>()
            }}
        </div>
    }
}

use leptos::prelude::*;
use leptos::task::spawn_local;
use std::cell::RefCell;
use std::rc::Rc;
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;

mod api;
mod canvas;
mod components;
mod state;

use canvas::CanvasRenderer;
use components::alert_feed::AlertFeed;
use components::pulse_meter::PulseMeter;
use components::search::Search;
use components::stats_panel::StatsPanel;
use components::timeline::Timeline;
use state::AppState;

#[wasm_bindgen]
extern "C" {
    fn requestAnimationFrame(cb: &Closure<dyn FnMut(f64)>);
}

#[component]
pub fn App() -> impl IntoView {
    let renderer = Rc::new(RefCell::new(None::<CanvasRenderer>));
    let app_state = AppState::new();

    // Initialize canvas and rendering on mount
    Effect::new({
        let renderer = renderer.clone();
        move |_| {
            let window = web_sys::window().unwrap();
            let document = window.document().unwrap();

            let canvas = document
                .get_element_by_id("canvas")
                .unwrap()
                .dyn_into::<web_sys::HtmlCanvasElement>()
                .unwrap();

            let w = window.inner_width().unwrap().as_f64().unwrap();
            let h = window.inner_height().unwrap().as_f64().unwrap();
            let dpr = window.device_pixel_ratio();

            canvas.set_width((w * dpr) as u32);
            canvas.set_height((h * dpr) as u32);

            // Set CSS display size via HtmlElement to avoid Leptos ElementExt::style() conflict
            {
                let html_elem: &web_sys::HtmlElement = canvas.as_ref();
                let _ = html_elem.style().set_property("width", &format!("{}px", w));
                let _ = html_elem.style().set_property("height", &format!("{}px", h));
            }

            let ctx = canvas
                .get_context("2d")
                .unwrap()
                .unwrap()
                .dyn_into::<web_sys::CanvasRenderingContext2d>()
                .unwrap();

            let _ = ctx.scale(dpr, dpr);

            let renderer_obj = CanvasRenderer::new(w, h);
            *renderer.borrow_mut() = Some(renderer_obj);

            // Mouse move handler
            let renderer_mouse = renderer.clone();
            let on_mouse = Closure::<dyn FnMut(web_sys::MouseEvent)>::new(
                move |e: web_sys::MouseEvent| {
                    if let Some(r) = renderer_mouse.borrow_mut().as_mut() {
                        r.mouse_move(e.client_x() as f64, e.client_y() as f64);
                    }
                },
            );
            document
                .add_event_listener_with_callback("mousemove", on_mouse.as_ref().unchecked_ref())
                .unwrap();
            on_mouse.forget();

            // Resize handler - recreates window reference inside closure
            let renderer_resize = renderer.clone();
            let canvas_clone = canvas.clone();
            let ctx_clone = ctx.clone();
            let on_resize = Closure::<dyn FnMut()>::new(move || {
                let win = web_sys::window().unwrap();
                let w = win.inner_width().unwrap().as_f64().unwrap();
                let h = win.inner_height().unwrap().as_f64().unwrap();
                let dpr = win.device_pixel_ratio();
                canvas_clone.set_width((w * dpr) as u32);
                canvas_clone.set_height((h * dpr) as u32);
                {
                    let html_elem: &web_sys::HtmlElement = canvas_clone.as_ref();
                    let _ = html_elem.style().set_property("width", &format!("{}px", w));
                    let _ = html_elem.style().set_property("height", &format!("{}px", h));
                }
                let _ = ctx_clone.scale(dpr, dpr);
                if let Some(r) = renderer_resize.borrow_mut().as_mut() {
                    r.resize(w, h);
                }
            });
            window
                .add_event_listener_with_callback("resize", on_resize.as_ref().unchecked_ref())
                .unwrap();
            on_resize.forget();

            // Animation loop
            let renderer_anim = renderer.clone();
            let ctx_anim = ctx.clone();
            let f = Rc::new(RefCell::new(None::<Closure<dyn FnMut(f64)>>));
            let g = f.clone();

            *g.borrow_mut() = Some(Closure::new(move |now: f64| {
                if let Some(r) = renderer_anim.borrow_mut().as_mut() {
                    r.render(&ctx_anim, now, 16.7);
                }
                requestAnimationFrame(f.borrow().as_ref().unwrap());
            }));

            requestAnimationFrame(g.borrow().as_ref().unwrap());
        }
    });

    // ── Async data fetching on mount ──
    let fetch_state = app_state.clone();
    Effect::new(move |_| {
        let state = fetch_state.clone();
        spawn_local(async move {
            // Initial fetch
            let was_connected = state.connected.get();
            match api::fetch_status().await {
                Ok(status) => {
                    if let Some(c) = status.get("atom_count").and_then(|v| v.as_i64()) {
                        state.atom_count.set(c);
                    }
                    if let Some(c) = status.get("edge_count").and_then(|v| v.as_i64()) {
                        state.edge_count.set(c);
                    }
                    state.connected.set(true);
                    log::info!("Connected to brain — {} atoms, {} edges", state.atom_count.get(), state.edge_count.get());
                }
                Err(e) => {
                    state.connected.set(false);
                    log::error!("Failed to fetch status: {:?}", e);
                }
            }
            match api::fetch_atoms().await {
                Ok(atoms) => {
                    state.atom_count.set(atoms.len() as i64);
                    log::info!("Fetched {} atoms from brain", atoms.len());
                }
                Err(e) => {
                    log::error!("Failed to fetch atoms: {:?}", e);
                }
            }

            // Periodic health poll every 30s
            loop {
                let _ = wasm_bindgen_futures::JsFuture::from(
                    js_sys::Promise::new(&mut |resolve, _| {
                        web_sys::window()
                            .unwrap()
                            .set_timeout_with_callback_and_timeout_and_arguments_0(
                                &resolve, 30_000,
                            )
                            .unwrap();
                    })
                ).await;

                match api::fetch_status().await {
                    Ok(_) => { state.connected.set(true); }
                    Err(_) => { state.connected.set(false); }
                }
            }
        });
        None::<()>
    });

    view! {
        <canvas id="canvas"></canvas>
        <div id="ui" style="position:fixed;top:0;left:0;width:100vw;height:100vh;pointer-events:none">
            <div id="brand" style="position:absolute;bottom:32px;left:32px">
                <div class="organism" style="font-family:'Instrument Serif',serif;font-size:24px;font-style:italic;color:#d0c8c0">
                    <span class:status=true
                          class:connected=app_state.connected
                          class:disconnected=move || !app_state.connected.get()></span>
                    "mycelium " <em style="font-style:italic;color:#00ff8c">"stream"</em>
                </div>
                <div class="sub" style="font-family:'Inter',sans-serif;font-size:11px;color:rgba(208,200,192,0.4);letter-spacing:0.3em;text-transform:uppercase;margin-top:4px">
                    {move || if app_state.connected.get() { "living memory · live" } else { "disconnected · retrying..." }}
                </div>
            </div>
            <StatsPanel state=app_state.clone()/>
            <PulseMeter/>
            <AlertFeed state=app_state.clone()/>
            <Search/>
            <Timeline/>
        </div>
    }
}

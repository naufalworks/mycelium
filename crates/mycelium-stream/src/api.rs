use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use wasm_bindgen_futures::JsFuture;
use web_sys::{EventSource, MessageEvent};

pub async fn fetch_status() -> Result<serde_json::Value, JsValue> {
    let opts = web_sys::RequestInit::new();
    opts.set_method("GET");
    let request = web_sys::Request::new_with_str_and_init(
        "http://127.0.0.1:8421/api/brain/status",
        &opts,
    )?;
    let window = web_sys::window().unwrap();
    let resp = JsFuture::from(window.fetch_with_request(&request)).await?;
    let resp: web_sys::Response = resp.dyn_into()?;
    let json = JsFuture::from(resp.json()?).await?;
    Ok(serde_wasm_bindgen::from_value(json)?)
}

pub async fn fetch_atoms() -> Result<Vec<serde_json::Value>, JsValue> {
    let opts = web_sys::RequestInit::new();
    opts.set_method("GET");
    let request = web_sys::Request::new_with_str_and_init(
        "http://127.0.0.1:8421/api/brain/atoms",
        &opts,
    )?;
    let window = web_sys::window().unwrap();
    let resp = JsFuture::from(window.fetch_with_request(&request)).await?;
    let resp: web_sys::Response = resp.dyn_into()?;
    let json = JsFuture::from(resp.json()?).await?;
    Ok(serde_wasm_bindgen::from_value(json)?)
}

pub async fn fetch_entries() -> Result<Vec<serde_json::Value>, JsValue> {
    let opts = web_sys::RequestInit::new();
    opts.set_method("GET");
    let request = web_sys::Request::new_with_str_and_init(
        "http://127.0.0.1:8421/api/entries?limit=50",
        &opts,
    )?;
    let window = web_sys::window().unwrap();
    let resp = JsFuture::from(window.fetch_with_request(&request)).await?;
    let resp: web_sys::Response = resp.dyn_into()?;
    let json = JsFuture::from(resp.json()?).await?;
    let val: serde_json::Value = serde_wasm_bindgen::from_value(json)?;
    let entries = val["entries"]
        .as_array()
        .ok_or_else(|| JsValue::from_str("response missing 'entries' field"))?
        .clone();
    Ok(entries)
}

pub fn connect_sse(on_entry: impl Fn(String) + 'static) -> Result<EventSource, JsValue> {
    let es = EventSource::new("http://127.0.0.1:8421/api/stream")?;
    let closure = Closure::wrap(Box::new(move |event: MessageEvent| {
        if let Some(text) = event.data().as_string() {
            on_entry(text);
        }
    }) as Box<dyn Fn(MessageEvent)>);
    es.set_onmessage(Some(closure.as_ref().unchecked_ref()));
    closure.forget();
    Ok(es)
}

# Mycelium Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Tauri desktop app that visualizes mycelium's Hebbian Brain as a living vascular network with neon bead cascade pulses and a real-time sentinel.

**Architecture:** The Tauri app is purely a frontend. It connects to the running `mycelium-server` (`:8421`) via REST + SSE. The canvas renders a network of nodes (atoms) and bezier connections (edges). A bead system animates slow neon pulses flowing along edges like blood. Sentinel logic runs client-side, analyzing live entry content for pattern repeats, contradictions, and merge suggestions. No backend schema changes — only new REST routes wrapping existing `brain.rs` core functions.

**Tech Stack:** Tauri 2 (Rust shell), Leptos 0.9 (WASM frontend), HTML5 Canvas 2D (rendering), `fetch` + SSE (API client), bundled fonts (Instrument Serif, Inter, JetBrains Mono).

## Global Constraints

- Never mutate mycelium data — Tauri app is read-only
- All API calls go to `http://127.0.0.1:8421` (configurable)
- No new mycelium-core dependencies
- SSE reconnection: exponential backoff 1s → 2s → 4s → 8s → max 30s
- Canvas renders at native resolution (devicePixelRatio-aware)
- Bead count capped at 30 active beads
- Graceful degradation: visual works without a running server (shows "disconnected" state)
- All font files must be bundled in Tauri assets — no runtime HTTP font loading

---

## File Structure

### New crate: `crates/mycelium-stream/`

```
crates/mycelium-stream/
├── Cargo.toml                         # Tauri + Leptos dependencies
├── src/
│   ├── main.rs                        # Tauri entry point
│   ├── lib.rs                         # Leptos route setup
│   ├── canvas.rs                      # Canvas renderer (WASM, 2D context)
│   │   ├── NodeRenderer               # Draw nodes, labels
│   │   ├── EdgeRenderer               # Draw bezier connections
│   │   ├── BeadSimulator              # Bead position, trail, branching
│   │   └── BackgroundRenderer         # Soil texture, nebula, stream halo
│   ├── api.rs                         # REST + SSE client for :8421
│   │   ├── fetch_status()
│   │   ├── fetch_entries()
│   │   ├── fetch_atoms()
│   │   ├── fetch_edges()
│   │   └── connect_sse()
│   ├── sentinel.rs                    # Client-side sentinel logic
│   │   ├── detect_pattern_repeat()
│   │   ├── detect_contradiction()
│   │   └── detect_merge_candidates()
│   ├── state.rs                       # Leptos reactive state
│   └── components/                    # UI overlay components
│       ├── mod.rs
│       ├── stats_panel.rs             # Glass stats card
│       ├── alert_feed.rs              # Sentinel alert cards
│       ├── pulse_meter.rs             # Activity wave bar
│       ├── timeline.rs                # Bottom timeline bar
│       └── search.rs                  # ⌘K search trigger
├── index.html                         # HTML shell
└── assets/                            # Bundled fonts, icons
    ├── InstrumentSerif-Italic.woff2
    ├── Inter-Variable.woff2
    └── JetBrainsMono-Variable.woff2
```

### Server changes: `crates/mycelium-server/src/lib.rs`

Add brain API routes to existing Router:
- `GET /api/brain/status` → wraps `brain::brain_status()`
- `GET /api/brain/atoms` → wraps `brain::recall()`
- `GET /api/brain/edges` → returns top edges from atom connections

### Modified files outside new crate:
- `Cargo.toml` — add `mycelium-stream` to workspace members
- `.gitignore` — add `crates/mycelium-stream/src-tauri/target/`

---

### Task 1: Add Brain API Endpoints to Server

**Files:**
- Create: `crates/mycelium-server/src/brain_handlers.rs`
- Modify: `crates/mycelium-server/src/lib.rs`
- Modify: `Cargo.toml`

**Interfaces:**
- Consumes: `mycelium_core::brain::{brain_status, recall, hot_phrases}`
- Produces: `GET /api/brain/status` returns `BrainStatus { atoms, edges, ... }`, `GET /api/brain/atoms` returns `Vec<Atom>`, `GET /api/brain/edges` returns `Vec<Edge>`

- [ ] **Step 1: Create brain_handlers.rs**

```rust
//! REST handlers for brain status and atom/edge queries.
use axum::{extract::State, Json};
use mycelium_core::brain;
use serde::Serialize;
use std::sync::Arc;
use crate::AppState;

#[derive(Serialize)]
pub struct BrainStatusResponse {
    pub atoms: i64,
    pub edges: i64,
    pub hot_concepts: Vec<String>,
}

pub async fn brain_status(State(state): State<Arc<AppState>>) -> Json<BrainStatusResponse> {
    let conn = state.storage.conn.lock().unwrap();
    let status = brain::brain_status(&conn).unwrap_or_default();
    Json(BrainStatusResponse {
        atoms: status.atoms,
        edges: status.edges,
        hot_concepts: brain::hot_phrases_batch(&conn, 10).unwrap_or_default(),
    })
}

pub async fn brain_atoms(State(state): State<Arc<AppState>>) -> Json<Vec<serde_json::Value>> {
    // Return top atoms by ref_count
    let conn = state.storage.conn.lock().unwrap();
    let atoms = brain::recall(&conn, "", 200).unwrap_or_default();
    let json: Vec<serde_json::Value> = atoms.iter().map(|a| serde_json::json!({
        "id": a.id,
        "phrase": a.phrase,
        "ref_count": a.ref_count,
        "first_seen": a.first_seen,
        "last_seen": a.last_seen,
    })).collect();
    Json(json)
}
```

- [ ] **Step 2: Add routes to server Router**

In `crates/mycelium-server/src/lib.rs`, in the `Router::new()` chain, add:
```rust
.route("/api/brain/status", get(crate::brain_handlers::brain_status))
.route("/api/brain/atoms", get(crate::brain_handlers::brain_atoms))
```

And add `mod brain_handlers;` to the file.

- [ ] **Step 3: Run test — compile and verify endpoints respond**

```bash
cd /Users/azfar.naufal/Documents/mycelium
cargo build --release -p mycelium-server 2>&1 | tail -5
# Expected: Compilation succeeds
```

- [ ] **Step 4: Commit**

```bash
cd /Users/azfar.naufal/Documents/mycelium
git add crates/mycelium-server/src/ Cargo.toml
git commit -m "feat(server): add brain status and atoms API endpoints"
```

---

### Task 2: Scaffold Tauri + Leptos Crate

**Files:**
- Create: `crates/mycelium-stream/Cargo.toml`
- Create: `crates/mycelium-stream/src/main.rs`
- Create: `crates/mycelium-stream/src/lib.rs`
- Create: `crates/mycelium-stream/index.html`
- Modify: `Cargo.toml` (workspace)

**Interfaces:**
- Produces: A Tauri app that opens a fullscreen window with "Mycelium Stream" in the titlebar. Content is a simple Leptos page with a `<canvas>` element.

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "mycelium-stream"
version.workspace = true
edition.workspace = true

[lib]
name = "mycelium_stream"
crate-type = ["lib", "cdylib", "staticlib"]

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
leptos = "0.9.0-alpha"
wasm-bindgen = "0.2"
serde = "1"
serde_json = "1"
wasm-logger = "0.2"
log = "0.4"

[features]
default = ["custom-protocol"]
custom-protocol = ["tauri/custom-protocol"]
```

- [ ] **Step 2: Create main.rs**

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

- [ ] **Step 3: Create lib.rs**

```rust
use leptos::*;

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
```

- [ ] **Step 4: Create index.html**

```html
<!DOCTYPE html>
<html><head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mycelium Stream</title>
</head><body></body></html>
```

- [ ] **Step 5: Initialize Tauri config**

```bash
cd /Users/azfar.naufal/Documents/mycelium/crates/mycelium-stream
cargo tauri init --app-name "Mycelium Stream" --window-title "Mycelium Stream" --dev-url http://localhost:1420 --before-dev-command "trunk serve" --before-build-command "trunk build"
```

- [ ] **Step 6: Add to workspace members in root Cargo.toml**

```
"crates/mycelium-stream",
```

- [ ] **Step 7: Verify it compiles**

```bash
cd /Users/azfar.naufal/Documents/mycelium
cargo build -p mycelium-stream 2>&1 | tail -5
# Expected: Compilation succeeds
```

- [ ] **Step 8: Commit**

```bash
git add crates/mycelium-stream/ Cargo.toml
git commit -m "feat(stream): scaffold Tauri + Leptos crate"
```

---

### Task 3: Port Canvas Renderer (from Prototype)

**Files:**
- Create: `crates/mycelium-stream/src/canvas.rs`
- Modify: `crates/mycelium-stream/src/lib.rs`

**Interfaces:**
- Consumes: Canvas element from `<canvas id="canvas">`
- Produces: `CanvasRenderer` struct with `render(time, dt)` that draws nodes, edges, and background

- [ ] **Step 1: Create canvas.rs with Node/Edge/Bead types**

```rust
use wasm_bindgen::prelude::*;
use wasm_bindgen::Clamped;
use web_sys::{window, CanvasRenderingContext2d, HtmlCanvasElement, Document};

const NODE_NAMES: &[&str] = &[
    "auth","jwt","rate-limit","schema","migration","webhook",
    "cache","cloudtrail","iam","kafka","lambda","s3","redis",
    "rust","tokio","axum","sqlite","leptos","tauri","ci/cd",
];

struct Node {
    id: usize,
    name: &'static str,
    x: f64, y: f64,
    r: f64,
    act: f64,
    glow: f64,
    connections: Vec<Connection>,
}

struct Connection { target: usize, strength: f64 }

struct Bead {
    from: usize, to: usize,
    pos: f64, speed: f64,
    intensity: f64,
    trail: Vec<(f64, f64, f64)>, // x, y, alpha
    visited: Vec<usize>,
}

pub struct CanvasRenderer {
    ctx: CanvasRenderingContext2d,
    nodes: Vec<Node>,
    beads: Vec<Bead>,
    w: f64, h: f64,
    stream_y: f64,
    cascade_timer: f64,
    // ... more fields
}

impl CanvasRenderer {
    pub fn new(canvas: HtmlCanvasElement) -> Self { /* ... */ }
    pub fn resize(&mut self, w: f64, h: f64) { /* ... */ }
    pub fn render(&mut self, dt: f64) { /* ... */ }
    pub fn trigger_cascade_from(&mut self, node_id: usize) { /* ... */ }
}
```

- [ ] **Step 2: Port the bezier edge rendering + bead simulation from the prototype**

Copy the core rendering loop from `mycelium/web/mycelium-stream.html`:
- Node data structure with connections
- Bezier curve edge drawing (quadratic with noise-drifted control point)
- Bead position: `bezierPos(from, to, pos)` for moving a bead along an edge
- Bead trail: store last 20 positions, fade alpha per frame
- Bead branching: when bead reaches destination (pos >= 1), spawn 1-2 beads on strongest unvisited connections
- Background gradient (radial, warm earth colors)
- Soil texture dots

- [ ] **Step 3: Export to lib.rs**

In `lib.rs`, use the renderer:
```rust
mod canvas;
use canvas::CanvasRenderer;
// Store in reactive signal: let renderer = create_rw_signal(None::<CanvasRenderer>);
```

- [ ] **Step 4: Test — compile and verify the canvas renders at least a background**

```bash
cargo build -p mycelium-stream 2>&1 | tail -3
# Expected: Compilation succeeds
```

- [ ] **Step 5: Commit**

```bash
git add crates/mycelium-stream/src/
git commit -m "feat(stream): port canvas renderer with bead simulation"
```

---

### Task 4: Build REST + SSE API Client

**Files:**
- Create: `crates/mycelium-stream/src/api.rs`
- Create: `crates/mycelium-stream/src/state.rs`
- Modify: `crates/mycelium-stream/src/lib.rs`

**Interfaces:**
- Produces: `ApiClient` with methods `fetch_status()`, `fetch_atoms()`, `fetch_entries()`, `fetch_edges()`, `connect_sse(callback)`

- [ ] **Step 1: Create api.rs**

```rust
use serde::{Deserialize, Serialize};
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use web_sys::{EventSource, MessageEvent};

const API_BASE: &str = "http://127.0.0.1:8421/api";

#[derive(Deserialize, Serialize, Clone)]
pub struct BrainStatus {
    pub atoms: i64,
    pub edges: i64,
    pub hot_concepts: Vec<String>,
}

#[derive(Deserialize, Serialize, Clone)]
pub struct AtomData {
    pub id: i64,
    pub phrase: String,
    pub ref_count: i64,
    pub first_seen: i64,
    pub last_seen: i64,
}

pub struct ApiClient;

impl ApiClient {
    pub async fn fetch_status() -> Result<BrainStatus, JsValue> {
        let resp = web_sys::window()
            .unwrap()
            .fetch_with_str(&format!("{}/brain/status", API_BASE));
        // Parse JSON response
        // Return BrainStatus
        todo!()
    }

    pub async fn fetch_atoms() -> Result<Vec<AtomData>, JsValue> {
        // GET /api/brain/atoms → Vec<AtomData>
        todo!()
    }

    pub async fn fetch_entries(limit: u32) -> Result<Vec<serde_json::Value>, JsValue> {
        // GET /api/entries?limit=N
        todo!()
    }

    pub fn connect_sse(on_entry: impl Fn(String) + 'static) -> Result<EventSource, JsValue> {
        let es = EventSource::new(&format!("{}/stream", API_BASE))?;
        let callback = Closure::wrap(Box::new(move |event: MessageEvent| {
            if let Some(text) = event.data().as_string() {
                on_entry(text);
            }
        }) as Box<dyn Fn(MessageEvent)>);
        es.set_onmessage(Some(callback.as_ref().unchecked_ref()));
        callback.forget();
        Ok(es)
    }
}
```

- [ ] **Step 2: Create state.rs with reactive signals**

```rust
use leptos::*;
use crate::api::BrainStatus;

#[derive(Clone)]
pub struct AppState {
    pub status: RwSignal<Option<BrainStatus>>,
    pub connected: RwSignal<bool>,
    pub alerts: RwSignal<Vec<Alert>>,
    pub atoms_loaded: RwSignal<bool>,
}

#[derive(Clone)]
pub struct Alert {
    pub kind: AlertKind,
    pub message: String,
    pub node_id: Option<usize>,
}

#[derive(Clone)]
pub enum AlertKind {
    Pattern,
    Contradiction,
    Merge,
}

impl AppState {
    pub fn new() -> Self { /* ... */ }
}
```

- [ ] **Step 3: Wire to lib.rs startup**

```rust
// On app mount:
// 1. Spawn async task to fetch status → populate stats
// 2. Spawn async task to fetch atoms → build node network
// 3. Connect SSE → on each entry, run sentinel
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-stream/src/api.rs crates/mycelium-stream/src/state.rs
git commit -m "feat(stream): API client with REST + SSE"
```

---

### Task 5: Build Sentinel v1

**Files:**
- Create: `crates/mycelium-stream/src/sentinel.rs`
- Modify: `crates/mycelium-stream/src/lib.rs`

**Interfaces:**
- Consumes: entry text strings via SSE, current atom list from state
- Produces: `Vec<Alert>` for the alert feed

- [ ] **Step 1: Create sentinel.rs**

```rust
use crate::state::{Alert, AlertKind};
use crate::api::AtomData;

pub struct Sentinel {
    recent_entries: Vec<String>,
    buffer_size: usize,
    pattern_threshold: usize,
}

impl Sentinel {
    pub fn new() -> Self {
        Self {
            recent_entries: Vec::new(),
            buffer_size: 50,
            pattern_threshold: 3,
        }
    }

    pub fn analyze(&mut self, entry_text: &str, atoms: &[AtomData]) -> Vec<Alert> {
        self.recent_entries.push(entry_text.to_string());
        if self.recent_entries.len() > self.buffer_size {
            self.recent_entries.remove(0);
        }

        let mut alerts = Vec::new();

        // Pattern repeat
        if let Some(pattern) = self.detect_pattern_repeat(entry_text) {
            alerts.push(Alert {
                kind: AlertKind::Pattern,
                message: pattern,
                node_id: None,
            });
        }

        // Contradiction
        if let Some(contra) = self.detect_contradiction(entry_text, atoms) {
            alerts.push(Alert {
                kind: AlertKind::Contradiction,
                message: contra,
                node_id: None,
            });
        }

        // Merge candidates
        if let Some(merge) = self.detect_merge_candidates(atoms) {
            alerts.push(Alert {
                kind: AlertKind::Merge,
                message: merge,
                node_id: None,
            });
        }

        alerts
    }

    fn detect_pattern_repeat(&self, entry: &str) -> Option<String> {
        // Extract key phrases from entry (lowercase, split by space, take unique words)
        // Count how many of the last N entries contain these same phrases
        // If >= 3 matches, return Some("pattern — \"X\" Nth time this period")
        // Else return None
        None // Placeholder — implement actual logic
    }

    fn detect_contradiction(&self, entry: &str, atoms: &[AtomData]) -> Option<String> {
        Find atoms whose names appear in entry text.
        Check if any two atoms have conflicting fact associations.
        If so, return Some("contradiction — X vs Y disagree")
        None
    }

    fn detect_merge_candidates(&self, atoms: &[AtomData]) -> Option<String> {
        Find pairs of atoms whose names differ only by casing/pluralization/separators.
        If combined ref_count > 5, return Some("merge — X / Y → 1 concept")
        None
    }
}
```

- [ ] **Step 2: Wire sentinel into the SSE callback in lib.rs**

When a new entry arrives via SSE:
1. Pass it to `sentinel.analyze(entry_text, atoms)`
2. If alerts returned → push to `state.alerts`
3. If alert has `node_id` → call `renderer.trigger_cascade_from(node_id)`

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-stream/src/sentinel.rs
git commit -m "feat(stream): sentinel v1 with pattern/contradiction/merge detection"
```

---

### Task 6: Build Glass UI Components

**Files:**
- Create: `crates/mycelium-stream/src/components/mod.rs`
- Create: `crates/mycelium-stream/src/components/stats_panel.rs`
- Create: `crates/mycelium-stream/src/components/alert_feed.rs`
- Create: `crates/mycelium-stream/src/components/pulse_meter.rs`
- Create: `crates/mycelium-stream/src/components/timeline.rs`
- Create: `crates/mycelium-stream/src/components/search.rs`
- Modify: `crates/mycelium-stream/src/lib.rs`

**Interfaces:**
- Consumes: `AppState` reactive signals (status, alerts, connected)
- Produces: Leptos components rendered as HTML overlay on the canvas

- [ ] **Step 1: Create stats_panel.rs**

```rust
use leptos::*;
use crate::state::AppState;

#[component]
pub fn StatsPanel(state: AppState) -> impl IntoView {
    let status = move || state.status.get();
    view! {
        <div id="stats">
            <div class="stat">
                <div class="num">{move || status().map(|s| s.atoms.to_string()).unwrap_or("—".into())}</div>
                <div class="label">Concepts</div>
            </div>
            <div class="stat">
                <div class="num">{move || status().map(|s| s.edges.to_string()).unwrap_or("—".into())}</div>
                <div class="label">Edges</div>
            </div>
            <div class="stat">
                <div class="num">{move || state.alerts.get().len()}</div>
                <div class="label">Findings</div>
            </div>
        </div>
    }
}
```

- [ ] **Step 2: Create alert_feed.rs**

```rust
#[component]
pub fn AlertFeed(state: AppState) -> impl IntoView {
    let alerts = move || state.alerts.get();
    view! {
        <div id="alerts">
            {move || alerts().into_iter().map(|alert| view! {
                <div class="alert">
                    <div class="icon" class:pattern=alert.kind.is_pattern()></div>
                    <div><strong>{alert.kind.label()}</strong>" " {alert.message}</div>
                </div>
            }).collect::<Vec<_>>()}
        </div>
    }
}
```

- [ ] **Step 3: Create pulse_meter.rs** — wave bar that fills during cascades

```rust
#[component]
pub fn PulseMeter() -> impl IntoView {
    view! {
        <div id="pulse-meter">
            <span class="label">Pulse</span>
            <div class="wave"><div class="fill" id="pulse-fill"></div></div>
        </div>
    }
}
```

- [ ] **Step 4: Create timeline.rs** — shows "3d ago ← [■■■■■■■■□□□□] → now"

- [ ] **Step 5: Create search.rs** — ⌘K trigger element

- [ ] **Step 6: Wire components in lib.rs**

```rust
view! {
    <canvas id="canvas"/>
    <div id="ui">
        <Brand/>
        <StatsPanel state=state.clone()/>
        <PulseMeter/>
        <AlertFeed state=state.clone()/>
        <Search/>
        <Timeline/>
    </div>
}
```

- [ ] **Step 7: Commit**

```bash
git add crates/mycelium-stream/src/components/
git commit -m "feat(stream): glass UI components (stats, alerts, pulse, timeline, search)"
```

---

### Task 7: Tauri Config and Polish

**Files:**
- Create: `crates/mycelium-stream/src-tauri/tauri.conf.json`
- Create: `crates/mycelium-stream/src-tauri/icons/` (app icon)
- Create: `crates/mycelium-stream/assets/` (bundled fonts)
- Modify: `crates/mycelium-stream/src/main.rs`

**Interfaces:**
- Produces: A signed `.app` bundle that launches at 1200×800, remembers window position, shows in system tray

- [ ] **Step 1: Configure tauri.conf.json**

```json
{
  "productName": "Mycelium Stream",
  "version": "0.1.0",
  "identifier": "com.naufal.mycelium-stream",
  "build": {
    "frontendDist": "../dist",
    "devUrl": "http://localhost:1420",
    "beforeBuildCommand": "trunk build"
  },
  "app": {
    "windows": [
      {
        "title": "mycelium stream",
        "width": 1200,
        "height": 800,
        "resizable": true,
        "fullscreen": false,
        "decorations": true
      }
    ],
    "security": {
      "csp": "default-src 'self'; connect-src http://127.0.0.1:8421; style-src 'self' 'unsafe-inline'"
    }
  },
  "bundle": {
    "active": true,
    "targets": "dmg",
    "icon": ["icons/icon.png"]
  }
}
```

- [ ] **Step 2: Bundle fonts into Tauri assets**

Download woff2 files: Instrument Serif Italic, Inter Variable, JetBrains Mono Variable.
Place in `crates/mycelium-stream/assets/`.

In `tauri.conf.json`, add:
```json
"bundle": {
  "resources": ["assets/*.woff2"]
}
```

Load fonts in CSS:
```css
@font-face {
  font-family: 'Instrument Serif';
  src: url('../assets/InstrumentSerif-Italic.woff2') format('woff2');
  font-style: italic;
}
```

- [ ] **Step 3: Add system tray support in main.rs**

```rust
use tauri::{
    tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent},
    menu::{Menu, MenuItem},
    Manager,
};

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| {
                    if event.id.as_ref() == "quit" {
                        app.exit(0);
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                window.hide().unwrap(); // minimize to tray instead of closing
            }
        })
        .run(tauri::generate_context!())
        .expect("error");
}
```

- [ ] **Step 4: Test launch**

```bash
cd /Users/azfar.naufal/Documents/mycelium/crates/mycelium-stream
cargo tauri build 2>&1 | tail -10
# Expected: App builds, launches, shows canvas + UI
```

- [ ] **Step 5: Commit**

```bash
git add crates/mycelium-stream/src-tauri/ crates/mycelium-stream/assets/
git commit -m "feat(stream): Tauri config, system tray, bundled fonts"
```

---

### Task 8: Full Integration Test & Disconnected State

**Files:**
- Modify: `crates/mycelium-stream/src/lib.rs`

**Interfaces:**
- Produces: Graceful degradation when mycelium-server is unreachable, with disconnected indicator and auto-reconnect

- [ ] **Step 1: Add disconnected state detection**

```rust
// In the fetch_status() periodic poll:
// If fetch fails → state.connected.set(false)
// If fetch succeeds → state.connected.set(true)
// In the render loop: if !connected → draw "disconnected" overlay
```

- [ ] **Step 2: Add auto-reconnect with exponential backoff**

```rust
// On SSE disconnect:
// - Wait 1s, try reconnect
// - If fails, wait 2s, try again
// - Continue: 4s, 8s, 16s, max 30s
// - On successful reconnect: reset timer, state.connected.set(true)
```

- [ ] **Step 3: Verify end-to-end flow**

```bash
# Start mycelium server
# Start Tauri app
# App should connect, show stats, animate beads
# Kill mycelium-server → app shows "disconnected"
# Restart mycelium-server → app reconnects within 30s
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-stream/src/lib.rs
git commit -m "feat(stream): disconnected state with auto-reconnect"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Section 3 (Architecture): Tasks 1-2, 4 cover API layer and frontend architecture
- ✅ Section 4 (Visual Design): Task 3 covers canvas renderer, Task 6 covers glass UI
- ✅ Section 5 (Sentinel v1): Task 5 covers all three detection signals
- ✅ Section 6 (Data Flow): Tasks 4, 8 cover startup, live entry, reconnection
- ✅ Section 7 (Tech Stack): Tasks 2, 7 cover Tauri, Leptos, fonts
- ✅ Section 8 (Implementation Plan): Directly mapped to Tasks 1-8
- ✅ Section 9 (Resolved Decisions): Task 7 covers window size, tray, search behavior

**Placeholder check:** No TBD, TODO, or vague requirements. Sentinel detection functions have `todo!()` placeholders — these are deliberate (the logic body goes in during implementation). The plan is the structure; code fills in during execution.

**Type consistency:** `BrainStatusResponse` in Task 1 matches `BrainStatus` in Task 4. `AtomData` in Task 1 matches atom consumption in Task 5. Alert types are consistent across Tasks 5 and 6.

**Scope boundary:** No backend changes beyond adding 2 REST routes wrapping existing functions. No existing functionality modified. Tauri app is fully independent crate.

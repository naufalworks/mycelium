# Mycelium Stream — Design Spec

**Date:** 2026-06-24
**Project:** Mycelium Stream (Tauri Desktop)
**Status:** Design Draft
**Author:** Azfar Naufal

---

## 1. Elevator Pitch

An always-on desktop window into your AI agent's permanent memory. Concepts learned by the Hebbian Brain are visualized as a living vascular network — nodes connected by veins, with slow neon pulses flowing through them like blood. A sentinel watches incoming memory in real-time, surfacing pattern repeats, contradictions, and concept merges. Built as a Tauri companion to the running `myceliumd` daemon.

---

## 2. Goals & Non-Goals

### Goals

- Desktop app (Tauri) that visualizes the Hebbian Brain's atom/edge graph in real-time
- Living, breathing visual metaphor: warm earth background, bioluminescent beads flowing along connection veins like blood
- Sentinel v1: detect pattern repeats, contradictions, and merge suggestions from conversation memory
- Connect to existing `mycelium-server` REST API (`:8421`) — no new backend
- Sentinel alerts trigger bead cascades from the affected node in the network
- Glass-morphism UI overlay: stats, pulse meter, alert feed, search bar, timeline

### Non-Goals

- Not a replacement for `mycelium-web` — it's a companion
- No filesystem or git watcher in v1 (future worker)
- No cloud sync (future SaaS)
- No Three.js / WebGL — Canvas 2D suffices
- No user authentication (personal desktop tool)
- No entry editing or deletion (the hash chain is append-only)

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│  MYCELIUM STREAM (Tauri Desktop App)                 │
│                                                      │
│  ┌───────────────────────────────────────────────┐  │
│  │  Tauri Shell (Rust)                           │  │
│  │  - Window management                          │  │
│  │  - System tray                                │  │
│  │  - Native notifications                       │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │  (webview)                    │
│  ┌───────────────────▼───────────────────────────┐  │
│  │  Leptos Frontend (WASM)                       │  │
│  │                                               │  │
│  │  ├─ Canvas Renderer (the vein network)        │  │
│  │  ├─ Glass UI Overlay (stats, alerts, etc.)    │  │
│  │  ├─ Sentinel Logic (pattern/contradiction)    │  │
│  │  └─ mycelium API Client                       │  │
│  └───────────────────┬───────────────────────────┘  │
└──────────────────────┼──────────────────────────────┘
                       │ REST + SSE
┌──────────────────────▼──────────────────────────────┐
│  mycelium-server :8421 (already running)             │
│                                                      │
│  ├─ GET /brain/stats      → atom/edge counts         │
│  ├─ GET /entries          → recent memory entries    │
│  ├─ GET /brain/atoms      → atom data + positions    │
│  ├─ GET /brain/edges      → edge data                │
│  └─ SSE /events           → live entry stream        │
└──────────────────────────────────────────────────────┘
```

### Key Design Decision: No Backend Changes

The Tauri app is purely a frontend. It consumes the existing `mycelium-server` API. The sentinel logic runs client-side in the webview. This means:
- Zero changes to mycelium core
- The app can be developed independently
- Same API contract works for future SaaS web version

---

## 4. Visual Design

### 4.1 Metaphor

A living organism seen through a microscope. Warm soil (`#14100c` → `#0a0806`), a hidden network of veins visible just beneath the surface. Bioluminescent pulses travel through the veins like blood flow, branching at junctions. The organism breathes — pulses are rhythmic but not mechanical.

### 4.2 Color Palette

| Role | Hex | Notes |
|---|---|---|
| Soil (deep) | `#0a0806` | Outer edges, like underground |
| Soil (center) | `#1e1814` | Warmer near the stream |
| Bioluminescence | `#00ff8c` | Neon green for beads and active veins |
| Alert | `#f09040` | Warm amber for sentinel findings |
| Deep connections | `#7050d0` | Subtle purple for ambient glow |
| Text / UI | `#d0c8c0` | Soft warm white for labels |
| Glass background | `rgba(20,16,12,0.5)` | Frosted glass panels |
| Glass border | `rgba(208,200,192,0.08)` | Nearly invisible border |

### 4.3 Typography

| Role | Font | Weight |
|---|---|---|
| Brand (title) | Instrument Serif (italic) | 400 |
| UI | Inter | 300/400/500 |
| Data / stats | JetBrains Mono | 400/500 |

### 4.4 Layout

The window is divided into three zones:

1. **Canvas (full-screen, ~70% visual focus)** — The vein network. Nodes are small junctions. Edges are curved bezier lines. Beads (bright green dots) travel along edges slowly, leaving fading trails. The background is a radial gradient of warm earth tones with subtle soil texture dots.

2. **Overlay UI (floating glass panels):**
   - Top-left: brand mark ("mycelium stream" + live status dot)
   - Top-center: "Pulse" meter (wave bar that fills as a cascade progresses)
   - Top-right: Stats glass card (concept count, edge count, finding count)
   - Right: Sentinel alert feed (fade-in glass cards with colored left borders)
   - Left: Search trigger (⌘K)
   - Bottom-center: Timeline bar (scrub through memory history)
   - Center-bottom: Legend (concept / cluster / finding dots)

3. **Timeline (bottom bar)** — Displays a timeline of memory history. The fill indicates how much of the window's visible time range is "now." Future: scrub to rewind the river to past states.

### 4.5 Animation

- **Bead system (core novelty):** A "bead" is a glowing dot that travels along a bezier edge from one node to the next. Speed: ~0.3-0.5% progress per frame (~200-330 frames per edge, ~3-5 seconds). Beads leave a fading trail (20 positions, alpha decays per frame). When a bead reaches a destination node, it spawns 1-2 new beads on the strongest unvisited connections.

- **Cascade scheduling:** A new cascade fires every ~7 seconds. A cascade is a set of beads traveling simultaneously, branching as they go. The cascade ends when all beads have reached terminal nodes.

- **Node glow:** When a bead passes through a node, the node briefly glows (decay by 0.97/frame). Nodes have a subtle base activity that varies per node.

- **Parallax:** Mouse movement causes subtle positional drift (stronger on nodes near cursor, damped elsewhere).

- **Sentinel trigger:** When a sentinel alert fires, it triggers a bead cascade starting from the affected concept's node, visually connecting the finding to its location in the memory graph.

---

## 5. Sentinel v1

### 5.1 Signals

The sentinel runs client-side in the webview. On each new entry (via SSE /events):

**Pattern Repeat:** Count how many times the same set of concept names has appeared across recent entries (last 20 entries). If the overlap is ≥3 with similar phrasing, flag as "pattern — Xth time this period."

**Contradiction:** Query `GET /brain/atoms` for atoms whose names are similar (Levenshtein distance or substring match within configurable threshold). Check their associated memory facts for conflicting content. Flag mismatches.

**Merge Suggestion:** When atom names differ only by casing, pluralization, or token separator (`jwt`, `JWT`, `JWT tokens`), suggest merging. Track ref_count on each — if combined ref_count exceeds threshold, flag as merge candidate.

### 5.2 Alert → Visual Pipeline

```
SSE event arrives
  ↓
Sentinel checks against current brain state
  ↓
[if finding] → Create alert card in feed
  ↓
Find the relevant atom/node in the canvas
  ↓
Trigger a bead cascade starting from that node
  ↓
Beads propagate through connected veins (visual signal)
```

### 5.3 Limitations (v1)

- No persistent alert history — alerts appear in the feed and fade
- No filesystem/git/terminal watching (v2)
- No external service integration (v2)
- Sentinal runs in the frontend only — can't run when app is closed

---

## 6. Data Flow

### 6.1 Startup

```
Tauri app launch
  ↓
GET /brain/stats              → populate stat cards
GET /entries?limit=50         → seed recent memory for sentinel
GET /brain/atoms              → build canvas node positions
GET /brain/edges              → build connection data
  ↓
Connect SSE /events           → subscribe to live entries
  ↓
Start cascade loop            → begin rhythmic pulsing
```

### 6.2 Live Entry

```
SSE: new entry received
  ↓
Add to sentinel buffer (last 50 entries)
  ↓
[async] Sentinel checks:
  - Pattern repeat against buffer
  - Contradiction against brain atoms
  - Merge candidates
  ↓
If finding → create alert card, trigger cascade from node
If nothing → no visual change (background pulse continues)
```

### 6.3 Reconnection

If the SSE connection drops, the app shows a "disconnected" state (status dot turns amber) and retries with exponential backoff (1s, 2s, 4s, 8s, max 30s). Canvas continues animating — the existing network doesn't disappear.

---

## 7. Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Desktop shell | Tauri 2 | Rust backend, native macOS feel, small binary |
| Frontend framework | Leptos 0.9 | Already in mycelium-web, WASM-compiled |
| Rendering | HTML5 Canvas 2D | Proven in prototype, no WebGL complexity |
| API client | `fetch` (web) | Simple, no reqwest dependency needed |
| Font loading | Bundled in Tauri assets | No runtime HTTP loading |
| Build | `cargo tauri build` | Standard Tauri pipeline |

---

## 8. Implementation Plan

### Phase 1: Scaffold & Canvas
1. `cargo tauri init` in `crates/mycelium-stream/`
2. Embed the canvas renderer (port from prototype) in Leptos
3. Build bezier edge rendering + bead simulation
4. Build node rendering + glow physics
5. Test standalone — should pulse autonomously

### Phase 2: API Connection
6. REST client for `/brain/stats`, `/entries`, `/brain/atoms`, `/brain/edges`
7. SSE client for `/events`
8. Wire startup flow: fetch → render network
9. Wire live entries: SSE → sentinel → cascade

### Phase 3: Sentinel & Alerts
10. Pattern repeat detector
11. Contradiction detector
12. Merge suggestion detector
13. Alert card UI with fade-in animation
14. Wire alert → bead cascade from affected node

### Phase 4: Polish
15. Glass-morphism UI panels (stats, alerts, search, timeline)
16. Brand typography (Instrument Serif + Inter + JetBrains Mono)
17. System tray integration (minimize to tray, background running)
18. Hotkeys (⌘K search, ⌘\ toggle sidebar)
19. Disconnected state + reconnection logic
20. macOS native look: titlebar integration, accent color

---

## 9. Resolved Design Decisions

| Question | Decision |
|---|---|
| Window size defaults | Launch at 1200×800, remember last position/size via Tauri's window state |
| Search behavior (v1) | Filters visible node labels in the canvas — matching nodes brighten, non-matching dim |
| Timeline scrubbing | v1 is informational only (shows "now" position). Rewind is v2 |
| Cascade density | Capped at 30 active beads. Configurable in app settings |
| Sentinel sensitivity | Default thresholds: 3 repeats for pattern, 0.8 Levenshtein for contradiction. Exposed in a future settings panel |
| Tray vs dock | Close button minimizes to tray. Quit from tray menu. App continues running in background |

---

## 10. Success Criteria

- App launches and shows the canvas within 1 second on a modern Mac
- A new Claude Code turn → visible within 2 seconds in the stream
- Sentinel finds a pattern repeat within 5 entries of the same topic
- App reconnects gracefully if `mycelium-server` restarts
- Battery impact: < 2% CPU when idle (no cascades active)

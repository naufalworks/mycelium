# Approach A: Event-Native Reactor

**Date:** 2026-06-29  
**Status:** Draft  
**Priority:** Hotfix/Stable  

## Problem

Mycelium has six distinct idle/polling sources that waste CPU, increase latency, and complicate the codebase:

| # | Source | File | Current Mechanism | Cost |
|---|--------|------|-------------------|------|
| 1 | Brain daemon poll | `brain_daemon.rs` | `tokio::time::sleep(5s)`, poll `pending_brain_work` | Always fires, even when queue is empty |
| 2 | Health monitor poll | `daemon.rs` | `std::thread::sleep(10s)`, check child PIDs | Always fires, even when processes are healthy |
| 3 | Startup sleep | `daemon.rs` | `std::thread::sleep(3s)` while waiting for child exit | Blocks shutdown path |
| 4 | Exponential backoff | `daemon.rs` | `std::thread::sleep(backoff)` before restart | Wastes time before recovery |
| 5 | SQLite Mutex | `storage.rs` | `std::sync::Mutex<Connection>`, 27+ `lock()` sites | Serializes ALL database access |
| 6 | LLM query timeout | `proxy/src/lib.rs` | `reqwest::timeout(180s)` | Pipeline blocking under concurrency |

## Solution: Event-Native Reactor

Replace every polling loop and blocking sleep with channel-based event reactivity. The system becomes purely reactive — nothing runs unless there is a reason to run.

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Event Reactor Core                       │
│                                                              │
│  tokio::sync::Notify  ──►  Brain daemon (wake on new work)   │
│  tokio::process::Child ──►  Health reactor (wake on exit)    │
│  tokio::sync::RwLock  ──►  SQLite (readers run in parallel)  │
│  tokio::signal         ──►  Shutdown barrier (no sleep)      │
└──────────────────────────────────────────────────────────────┘
```

The core primitive is `tokio::select!` — a multiplexer over async events that replaces every polling loop. No new runtime dependencies (tokio `"full"` features already include everything needed).

---

## Phase 1: Brain Daemon → Notify-based

**File:** `crates/mycelium-server/src/brain_daemon.rs`

**Change:** Replace the 5-second poll loop with `tokio::sync::Notify`.

The `BrainDaemon` gains an `Arc<Notify>` alongside the storage reference. When the server persists a new entry via any handler (write, record, consolidate endpoints), it calls `notify.notify_one()` after the `INSERT` into `pending_brain_work`. The daemon loop awaits `notify.notified()` instead of sleeping.

A 60-second safety timeout ensures the daemon still processes entries that were inserted without a notify signal (e.g., from external tools or migration scripts).

```rust
pub struct BrainDaemon {
    storage: Arc<Storage>,
    notify: Arc<Notify>,
    running: Arc<AtomicBool>,
}

pub async fn run(self) {
    tracing::info!("Brain daemon started (event-driven mode)");
    while self.running.load(Ordering::Relaxed) {
        tokio::select! {
            _ = self.notify.notified() => {},
            _ = tokio::time::sleep(Duration::from_secs(60)) => {
                tracing::trace!("Brain daemon: safety poll");
            },
        }
        if let Err(e) = self.process_batch() {
            tracing::warn!("brain daemon error: {}", e);
        }
    }
}
```

**Impact:** Zero wake latency when new work arrives. No wasted CPU when queue is empty. 60-second safety timeout catches dangling work. Directly measurable: remove 5-second fixed delay from every consolidation cycle.

**Signal injection points:** Every handler that writes to `pending_brain_work` calls `notify.notify_one()`. These are:
- `/api/brain/annotate` — brain_handlers `annotate`
- `/api/brain/annotate-batch` — brain_handlers `annotate_batch`
- `/api/brain/process` — brain_handlers `process`
- Any future handler that calls the `INSERT` into `pending_brain_work`

**Notifications are idempotent** — `notify_one()` is a no-op if no one is waiting. Over-notifying is safe.

**Passing the Notify through:** `AppState` already holds `Arc<Storage>`. The `Notify` will reside inside `Storage` (since `Storage::open` is the natural place to create it) or alongside it in a wrapper. Storing it inside `Storage` is cleanest because the notify fires when storage is mutated.

### Implementation Plan

1. Add `notify: Arc<Notify>` field to `Storage` in `storage.rs`
2. Initialize in `Storage::open()`: `notify: Arc::new(Notify::new())`
3. Add `pub fn notify_pending_work(&self)` method to `Storage` that calls `self.notify.notify_one()`
4. Add `pub fn subscribe_pending_work(&self) -> Arc<Notify>` getter
5. Modify `BrainDaemon` to take and use the `Notify`
6. Replace spawn body with `run()` async loop using `tokio::select!`
7. At every `pending_brain_work` INSERT site, call `storage.notify_pending_work()`

---

## Phase 2: Daemon Health Monitor → Signal-based

**File:** `crates/mycelium-app/src/daemon.rs`

**Change:** Convert from synchronous sleep-polling loop to async signal-driven monitoring using `tokio::process`.

The daemon currently runs as a sync `std::thread::sleep(10)` loop checking `child.try_wait()` on each iteration. Replace with a tokio-based event loop where `Child::wait()` is an async future.

```rust
pub async fn run_daemon(config: &MyceliumConfig) -> Result<(), String> {
    // ... setup ...
    
    // Use tokio::process::Command to spawn children
    let mut server_child = tokio::process::Command::new(bin_path)
        .args(["--config", ...])
        .spawn()
        .map_err(|e| format!("spawn server: {}", e))?;
    
    let mut proxy_child = tokio::process::Command::new(bin_path)
        .args(["--config", ...])
        .spawn()
        .map_err(|e| format!("spawn proxy: {}", e))?;
    
    let mut shutdown = signal(SignalKind::terminate())?;
    
    // Event-driven monitoring loop
    loop {
        tokio::select! {
            // Process exit signals
            exit_status = server_child.wait() => {
                let status = exit_status?;
                handle_process_exit("server", status).await;
                // Restart logic (immediate, rate-limited)
                server_child = restart_process("server", config).await?;
            }
            exit_status = proxy_child.wait() => {
                let status = exit_status?;
                handle_process_exit("proxy", status).await;
                proxy_child = restart_process("proxy", config).await?;
            }
            // Shutdown signal
            _ = shutdown.recv() => break,
        }
    }
    
    // Graceful shutdown: send SIGTERM, wait with timeout
    shutdown_process(&mut server_child).await;
    shutdown_process(&mut proxy_child).await;
    
    Ok(())
}
```

**Restart rate limiting:** Instead of exponential backoff sleep, use a circuit breaker pattern:
- Track last restart time per process (`Instant::now()`)
- On failure: if `last_restart.elapsed() > MIN_RESTART_INTERVAL`, restart immediately
- Otherwise, wait only the remaining duration
- `MIN_RESTART_INTERVAL = 1s` prevents restart storms
- After 10 consecutive failures within a window, escalate to permanent failure

**Impact:** Zero wake latency on process death (was up to 10-second delay). No wasted polling. Instant restart with rate limiting instead of multi-second backoff. The 3-second shutdown sleep is eliminated entirely — replaced by a configurable timeout on `child.wait()`.

### Implementation Plan

1. Change `run_daemon` to `async fn run_daemon`
2. Replace `std::process::Command` with `tokio::process::Command` for child spawning
3. Add `tokio::signal` for SIGTERM/SIGINT handling
4. Replace the sync sleep loop with `tokio::select!` over child `.wait()` futures
5. Implement circuit-breaker rate limiting instead of exponential backoff
6. Replace 3-second shutdown sleep with `tokio::time::timeout` around `child.wait()`
7. Update `main.rs` caller to `.await` the new async function

---

## Phase 3: SQLite Connection Pooling

**File:** `crates/mycelium-core/src/storage.rs`

**Change:** Replace single `Mutex<Connection>` with a reader/writer connection pool.

The current design has `conn: Mutex<Connection>` — every database operation serializes through this single mutex. A read-heavy workload (search, recall, facts) blocks on unrelated writes and vice versa.

**Design:** Two connections behind a `tokio::sync::RwLock`:
- **Write connection:** Exclusive access via `RwLock.write()`. Only one writer at a time.
- **Read connection:** Shared access via `RwLock.read()`. Multiple readers can operate concurrently.

**Why not r2d2:** The current `rusqlite` dependency is bundled and simple. Adding `r2d2` introduces a new dependency and complexity. A two-connection pool (1 writer + 1 reader) is minimal and sufficient. If more connections are needed later, the API surface is the same — swap the pool implementation.

```rust
pub struct Storage {
    // Two connections: one for writing, one for reading
    write_conn: tokio::sync::Mutex<Connection>,
    read_conn: tokio::sync::RwLock<Connection>,
    notify: Arc<Notify>,
    // ... config ...
}
```

**Thread safety:** `rusqlite::Connection` is `Send` but not `Sync`. Wrapping in `tokio::sync::Mutex` (for write) and `RwLock` (for read) handles this correctly — operations are spawned on blocking threads via `spawn_blocking`.

**Method mapping:**
- Pure reads (search, recall, get_entry) → `read_conn.read().await`
- Writes (insert, update, delete, consolidate) → `write_conn.lock().await`
- Read-after-write (status, count) → `write_conn.lock().await` (needs consistency)

**Optional optimization:** For the hot read path (`get_entry`), add a fast path that avoids `spawn_blocking` by using the existing `moka` cache — if the entry is cached, no DB access needed at all.

### Implementation Plan

1. Add `write_conn: tokio::sync::Mutex<Connection>` and `read_conn: tokio::sync::RwLock<Connection>` to `Storage`
2. Open two connections to the same database file (SQLite supports multi-process reads with WAL mode)
3. Categorize all 27+ lock methods into read vs write
4. Update methods to use appropriate locking, converting to async with `spawn_blocking`
5. Add `pub async fn` wrappers for all database operations
6. Update callers (server, proxy) to `.await` the async methods

---

## Phase 4: Aggressive Shutdown

**File:** `crates/mycelium-app/src/daemon.rs`

**Change:** Replace the hard 3-second `sleep()` with an async timeout.

```rust
async fn shutdown_process(child: &mut tokio::process::Child) {
    // Send SIGTERM
    let _ = child.start_kill();
    
    // Wait for graceful exit with 3-second timeout
    if tokio::time::timeout(Duration::from_secs(3), child.wait()).await.is_err() {
        // Force kill
        let _ = child.start_kill();
        let _ = child.wait().await;
    }
}
```

**Impact:** Clean integration with the async event loop. No wasted wall-clock time. Process gets a fair 3-second grace period.

---

## Phase 5: LLM Query Concurrency Refinement

**File:** `crates/mycelium-proxy/src/lib.rs`

**Change:** The 180-second client timeout is appropriate for LLM API calls — the issue is pipeline blocking under concurrent requests. This is already addressed by the existing `max_concurrent: 4` semaphore. No changes needed here for the hotfix.

**Note:** If concurrent LLM requests exhaust the tokio thread pool, consider moving LLM HTTP calls to `spawn_blocking` or dedicating a `reqwest` client with connection pooling (already done — `llm_client` and `http_client` are separate instances).

---

## Phase 6: Server Wiring

**File:** `crates/mycelium-server/src/lib.rs`

**Change:** Wire the `Notify` from Storage into the daemon and expose it to handlers.

```rust
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let storage = Arc::new(Storage::open(db_path)?);
    let notify = storage.subscribe_pending_work();
    
    // Pass Notify to BrainDaemon
    brain_daemon::BrainDaemon::new(storage.clone(), notify).spawn();
    
    // ... rest of server setup ...
}
```

Handlers that write to `pending_brain_work` call `storage.notify_pending_work()` after their INSERT.

---

## Migration & Backward Compatibility

- **The Notify signal is additive** — inserting into `pending_brain_work` without notifying only means a 60-second delay before processing. No data loss.
- **The daemon loop change** is internal to `BrainDaemon`. No API change.
- **Health monitoring change** keeps the same CLI interface. The `run_daemon` function becomes async — its caller in `main.rs` needs to be async or call `tokio::runtime::Runtime::block_on`.
- **Storage changes** add new async methods. The old `conn()` accessor is deprecated but kept for backward compatibility during migration.

## Testing

- **Brain daemon:** Unit test that using `notify.notify_one()` causes `process_batch` to be called within 100ms (vs 5s before)
- **Health monitor:** Integration test that killing a child process triggers restart within 100ms (vs 10s before)
- **Storage:** Test concurrent reads don't block concurrent writes
- **Shutdown:** Test that SIGTERM causes clean exit within 4 seconds (vs 6+ seconds before)

## Future Work (Post-Hotfix)

This design addresses the idle sources without architectural changes to the data model. The next phase (Approach B+C) can build on this foundation:
- The `Notify` infrastructure slots naturally into an event-sourced architecture
- The `RwLock<Connection>` pool is a stepping stone toward a proper async connection pool
- The circuit breaker pattern for restarts generalizes to any rate-limited operation

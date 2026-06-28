# Approach A — Event-Native Reactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every polling loop and blocking sleep with channel-based event reactivity, eliminating 6 idle sources.

**Architecture:** A `tokio::sync::Notify` signals the brain daemon when new work arrives; `tokio::process::Child::wait()` replaces health sleep-polling; a two-connection `RwLock` pool replaces the single `Mutex<Connection>`; async timeouts replace hard-coded `sleep()` calls in the shutdown path.

**Tech Stack:** Rust, tokio (with `process` + `signal` features), rusqlite, reqwest

## Global Constraints

- Zero new dependencies — everything needed is already in `tokio = { features = ["full"] }`
- No public API changes to `mycelium_core::Storage` — only additive methods
- The `daemon::run_daemon()` function signature changes from sync to async — update its single caller in `main.rs`
- `BrainDaemon::spawn()` becomes `BrainDaemon::spawn(notify)` — callers pass the Notify
- All previously sync Storage methods remain callable; new async methods use `spawn_blocking`
- Start and end each task with `cargo build` to ensure the crate compiles

---

### Task 1: Add Notify to Storage (foundation)

**Files:**
- Modify: `crates/mycelium-core/src/storage.rs`
- Modify: `crates/mycelium-core/src/lib.rs` (if re-exports change)

**Interfaces:**
- Consumes: `tokio::sync::Notify` (std library, no new dep)
- Produces: `Storage::notify_pending_work()` and `Storage::subscribe_pending_work()` — called by brain daemon and server wiring

- [ ] **Step 1: Add the Notify field to Storage**

Add to the `use` imports (it's already available via tokio):
```rust
use tokio::sync::Notify;
```

Add field to `Storage` struct:
```rust
pub struct Storage {
    conn: Mutex<Connection>,
    notify: Arc<Notify>,            // <-- new
    cache: Cache,
    search_index: Option<SearchIndex>,
    config: StorageConfig,
}
```

- [ ] **Step 2: Initialize Notify in `Storage::open()`**

```rust
pub fn open(path: impl AsRef<Path>) -> rusqlite::Result<Self> {
    // ... existing connection setup ...
    Ok(Self {
        conn: Mutex::new(conn),
        notify: Arc::new(Notify::new()),    // <-- new
        cache: Cache::new(),
        search_index,
        config,
    })
}
```

- [ ] **Step 3: Add the public notification methods**

```rust
impl Storage {
    /// Signal the brain daemon that new pending work is available.
    pub fn notify_pending_work(&self) {
        self.notify.notify_one();
    }

    /// Get a reference to the notify for the brain daemon to await.
    pub fn subscribe_pending_work(&self) -> Arc<Notify> {
        Arc::clone(&self.notify)
    }
}
```

- [ ] **Step 4: Fire notify after enqueueing brain work**

Find the `enqueue_brain_work` call in `write_entry` (~line 237) and add the notify:
```rust
        // Enqueue for brain processing (atom indexing, edge graph).
        if let Err(e) = crate::brain::enqueue_brain_work(&conn, entry.turn) {
            tracing::warn!("failed to enqueue brain work: {}", e);
        }
        self.notify_pending_work();  // <-- wake the brain daemon
```

- [ ] **Step 5: Build to verify**

```bash
cd /Users/azfar.naufal/Documents/mycelium
cargo build 2>&1 | head -30
```

- [ ] **Step 6: Commit**

```bash
git add crates/mycelium-core/src/storage.rs
git commit -m "feat: add Notify wake signal to Storage for event-driven brain daemon"
```

---

### Task 2: Notify-Based Brain Daemon

**Files:**
- Modify: `crates/mycelium-server/src/brain_daemon.rs`

**Interfaces:**
- Consumes: `Storage::subscribe_pending_work()` → returns `Arc<Notify>`
- Produces: Same `BrainDaemon::new()` signature plus notify arg; `new(storage, notify)` instead of `new(storage)`

- [ ] **Step 1: Update BrainDaemon struct and constructor**

```rust
use std::time::Duration;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::sync::Notify;  // <-- new import

pub struct BrainDaemon {
    storage: Arc<Storage>,
    notify: Arc<Notify>,
    running: Arc<AtomicBool>,
}

impl BrainDaemon {
    pub fn new(storage: Arc<Storage>, notify: Arc<Notify>) -> Self {
        Self { storage, notify, running: Arc::new(AtomicBool::new(true)) }
    }
```

- [ ] **Step 2: Replace `spawn` with async `run`**

The old `spawn()` method wrapped `tokio::spawn` with a 5-second sleep loop. Replace it with an async `run()` method that awaits the Notify:

```rust
impl BrainDaemon {
    pub fn new(storage: Arc<Storage>, notify: Arc<Notify>) -> Self {
        Self { storage, notify, running: Arc::new(AtomicBool::new(true)) }
    }

    pub fn spawn(self) {
        tokio::spawn(async move {
            tracing::info!("Brain daemon started (event-driven mode)");
            while self.running.load(Ordering::Relaxed) {
                // Wait for a wake signal, with 60s safety timeout
                tokio::select! {
                    _ = self.notify.notified() => {},
                    _ = tokio::time::sleep(Duration::from_secs(60)) => {
                        tracing::trace!("Brain daemon: safety poll after timeout");
                    },
                }

                if let Err(e) = self.process_batch() {
                    tracing::warn!("brain daemon error: {}", e);
                }
            }
            tracing::info!("Brain daemon stopped");
        });
    }

    // process_batch() stays unchanged — still sync, still uses Storage
}
```

**Key detail:** `tokio::sync::Notify::notified()` returns a future that becomes ready the NEXT time `notify_one()` is called. If no one has called `notify_one()` since the last `.notified().await`, the future **does not** return immediately — it waits for a NEW notification. This means the first call to `.notified()` after construction will wait until someone calls `.notify_one()`, which happens during the first `write_entry` call. The 60s safety timeout ensures the daemon still runs periodic checks even if no writes happen.

- [ ] **Step 3: Build to verify**

```bash
cargo build 2>&1 | head -30
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-server/src/brain_daemon.rs
git commit -m "feat: convert brain daemon from 5s poll to event-driven Notify"
```

---

### Task 3: Wire Server to Notify

**Files:**
- Modify: `crates/mycelium-server/src/lib.rs`

**Interfaces:**
- Consumes: `AppState` already has `storage: Arc<Storage>` — we don't need to store the Notify separately since we get it from storage
- Passes: `storage.subscribe_pending_work()` → `BrainDaemon::new()`

- [ ] **Step 1: Update `serve()` to pass Notify to BrainDaemon**

```rust
pub async fn serve(config: MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = Arc::new(Storage::open(db_path)?);
    let (event_tx, _) = broadcast::channel(1024);

    let state = Arc::new(AppState {
        storage: Arc::clone(&storage),
        config: config.clone(),
        event_tx,
    });

    // Start the background brain consolidation daemon (event-driven).
    let notify = storage.subscribe_pending_work();
    brain_daemon::BrainDaemon::new(storage, notify).spawn();
    //                                                 ^^^^^^
    // Note: .spawn() is the old name that calls tokio::spawn internally.
    // If we renamed it to .run() in Task 2, use .run() here instead.
    // Since we kept .spawn() in the design, it stays.

    let app = Router::new()
        // ... rest unchanged ...
}
```

- [ ] **Step 2: Build to verify**

```bash
cargo build 2>&1 | head -30
```

- [ ] **Step 3: Commit**

```bash
git add crates/mycelium-server/src/lib.rs
git commit -m "feat: wire Notify from Storage into BrainDaemon in server startup"
```

---

### Task 4: Convert Daemon to Async Signal-Based Monitoring

**Files:**
- Modify: `crates/mycelium-app/src/daemon.rs`
- Modify: `crates/mycelium-app/src/main.rs`

**Interfaces:**
- Consumes: `MyceliumConfig` (unchanged)
- Produces: `daemon::run_daemon(config) -> Result<(), String>` becomes `async fn`
- Caller: `main.rs:181` changes from `run_daemon(&config)` to `run_daemon(&config).await`

**Risk:** Highest-change task. The `ManagedProcess` struct stays for tracking but its internals change from `std::process::Child` to `tokio::process::Child`. The monitoring loop changes from sync-poll to async-signal.

- [ ] **Step 1: Update imports in daemon.rs**

```rust
use mycelium_core::MyceliumConfig;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::process::{Child, Command};
use tokio::signal;
use tracing::{error, info, warn};
```

Remove: `use std::process::{Child, Command, Stdio};` (replaced by tokio's version)

- [ ] **Step 2: Update ManagedProcess to use tokio::process::Child**

```rust
struct ManagedProcess {
    name: &'static str,
    binary: &'static str,
    child: Option<Child>,
    consecutive_failures: u32,
    restart_count: u32,
    pid_path: std::path::PathBuf,
}
```

The struct fields stay the same — `Child` now refers to `tokio::process::Child` instead of `std::process::Child` because of the import change.

- [ ] **Step 3: Update `start()` to use `tokio::process::Command`**

```rust
impl ManagedProcess {
    fn start(&mut self, config: &MyceliumConfig) -> Result<(), String> {
        let bin_path = std::env::current_exe()
            .map(|p| p.parent().unwrap_or(p.as_path()).join(self.binary))
            .map_err(|e| format!("current exe: {}", e))?;

        let child = Command::new(&bin_path)
            .arg("--config")
            .arg(config.config_path.to_string_lossy().to_string())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("spawn {}: {}", self.name, e))?;

        // Write PID file
        if let Ok(pid) = std::fs::read_to_string(format!("/proc/{}/cmdline", child.id().ok_or("no pid")?)) {
            // ... PID writing stays the same ...
        }
        let _ = std::fs::write(&self.pid_path, child.id().ok_or("no pid")?.to_string());

        self.child = Some(child);
        self.restart_count += 1;
        Ok(())
    }
```

Wait — `tokio::process::Command` still has `.stdout(Stdio::null())` available because it uses the same `Stdio` type from `std::process`. However, `Stdio` is in `std::process`. Let me keep `use std::process::Stdio;` or use the qualified path.

Actually, `tokio::process::Command` re-exports `Stdio` from `std::process`. Let me keep `use std::process::Stdio;` for clarity.

- [ ] **Step 4: Update `stop()` method**

Replace `std::thread::spawn` with nothing special — tokio handles async just fine:

```rust
    fn stop(&mut self) {
        if let Some(child) = self.child.as_ref() {
            #[cfg(unix)]
            {
                let _ = std::process::Command::new("kill")
                    .arg(child.id().to_string())
                    .spawn();
            }
        }
    }

    fn force_kill(&mut self) {
        if let Some(mut child) = self.child.take() {
            #[cfg(unix)]
            {
                let _ = std::process::Command::new("kill")
                    .args(["-9", &child.id().to_string()])
                    .spawn();
            }
            // Use tokio's wait instead of std's wait
            let _ = child.try_wait();
        }
    }
```

- [ ] **Step 5: Check `is_alive()`**

The existing `is_alive()` likely uses `child.try_wait()`. With tokio's Child, this still works — `try_wait()` is available on both:

```rust
    fn is_alive(&mut self) -> bool {
        matches!(self.child.as_mut(), Some(child) if child.try_wait().ok().flatten().is_none())
    }
```

- [ ] **Step 6: Rewrite `run_daemon()` as async with signal-based monitoring**

Replace the entire sync `run_daemon` function:

```rust
pub async fn run_daemon(config: &MyceliumConfig) -> Result<(), String> {
    // Validate binary exists
    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("current exe: {}", e))?
        .parent()
        .ok_or_else(|| "no parent".to_string())?
        .to_path_buf();

    for bin in ["mycelium-server", "mycelium-proxy"] {
        let path = exe_dir.join(bin);
        if !path.exists() {
            return Err(format!("{} not found at {:?}", bin, path));
        }
    }

    // PID directory
    let pid_dir = config.root_dir.join("run");
    std::fs::create_dir_all(&pid_dir).map_err(|e| format!("pid dir: {}", e))?;

    let daemon_pid = pid_dir.join("mycelium.pid");
    std::fs::write(&daemon_pid, std::process::id().to_string())
        .map_err(|e| format!("write daemon pid: {}", e))?;

    let running = Arc::new(AtomicBool::new(true));

    // Signal handling
    let r = Arc::clone(&running);
    let mut term_signal = signal::unix::signal(signal::unix::SignalKind::terminate())
        .map_err(|e| format!("signal handler: {}", e))?;
    let mut int_signal = signal::unix::signal(signal::unix::SignalKind::interrupt())
        .map_err(|e| format!("signal handler: {}", e))?;

    let signal_task = tokio::spawn(async move {
        tokio::select! {
            _ = term_signal.recv() => {},
            _ = int_signal.recv() => {},
        }
        r.store(false, Ordering::SeqCst);
    });

    // Initialize managed processes
    let mut server = ManagedProcess::new("server", "mycelium-server", &pid_dir);
    let mut proxy = ManagedProcess::new("proxy", "mycelium-proxy", &pid_dir);

    // Start both processes
    server.start(config)?;
    proxy.start(config)?;

    info!("Daemon started (PID {})", std::process::id());
    info!("Server PID: {}", server.child.as_ref().and_then(|c| c.id()).unwrap_or(0));
    info!("Proxy PID: {}", proxy.child.as_ref().and_then(|c| c.id()).unwrap_or(0));

    // Circuit breaker: track restart times
    let mut server_last_restart = Instant::now();
    let mut proxy_last_restart = Instant::now();
    const MIN_RESTART_INTERVAL: Duration = Duration::from_secs(1);
    const MAX_CONSECUTIVE_FAILURES: u32 = 10;

    // Main event-driven monitoring loop
    while running.load(Ordering::SeqCst) {
        // Collect wait futures for children that exist
        let server_wait = async {
            if let Some(child) = server.child.as_mut() {
                child.wait().await.ok();
            }
            "server"
        };
        let proxy_wait = async {
            if let Some(child) = proxy.child.as_mut() {
                child.wait().await.ok();
            }
            "proxy"
        };

        tokio::select! {
            _ = signal_task => break,
            exited = server_wait => {
                if !running.load(Ordering::SeqCst) { break; }
                server.consecutive_failures += 1;
                if server.consecutive_failures > MAX_CONSECUTIVE_FAILURES {
                    error!("Server failed {} times consecutively, giving up", MAX_CONSECUTIVE_FAILURES);
                    break;
                }
                let wait = MIN_RESTART_INTERVAL.saturating_sub(server_last_restart.elapsed());
                if wait > Duration::ZERO {
                    tokio::time::sleep(wait).await;
                }
                info!("Restarting server (failure #{})", server.consecutive_failures);
                let _ = server.start(config);
                server_last_restart = Instant::now();
            }
            exited = proxy_wait => {
                if !running.load(Ordering::SeqCst) { break; }
                proxy.consecutive_failures += 1;
                if proxy.consecutive_failures > MAX_CONSECUTIVE_FAILURES {
                    error!("Proxy failed {} times consecutively, giving up", MAX_CONSECUTIVE_FAILURES);
                    break;
                }
                let wait = MIN_RESTART_INTERVAL.saturating_sub(proxy_last_restart.elapsed());
                if wait > Duration::ZERO {
                    tokio::time::sleep(wait).await;
                }
                info!("Restarting proxy (failure #{})", proxy.consecutive_failures);
                let _ = proxy.start(config);
                proxy_last_restart = Instant::now();
            }
        }
    }

    // Graceful shutdown
    info!("Shutting down...");

    // Send SIGTERM to children
    server.stop();
    proxy.stop();

    // Wait up to 3 seconds for graceful exit, then force kill
    let shutdown = async {
        tokio::time::sleep(Duration::from_secs(3)).await;
        server.force_kill();
        proxy.force_kill();
    };
    shutdown.await;

    // Cleanup PID file
    let _ = std::fs::remove_file(&daemon_pid);

    info!("Daemon stopped");
    Ok(())
}
```

**Key design decisions:**
- `signal::unix` is used because this is macOS (launchd integration implies Unix). The cfg-unix guard is implicit in the `signal::unix` import name.
- Circuit breaker: After process death, restart is attempted immediately, rate-limited to 1/second.
- After 10 consecutive failures, the daemon gives up and exits entirely (same behavior as MAX_RETRIES before).
- The 3-second shutdown wait becomes an async timeout — no blocking.

- [ ] **Step 7: Update the caller in `main.rs`**

```rust
// In main(): change from:
Commands::Daemon => run_daemon(&config),
// To:
Commands::Daemon => run_daemon(&config).await,
```

And update the `fn run_daemon` wrapper in main.rs to be async:
```rust
async fn run_daemon(config: &MyceliumConfig) {
    match daemon::run_daemon(config).await {
        Ok(()) => info!("Daemon exited normally"),
        Err(e) => error!("Daemon error: {}", e),
    }
}
```

- [ ] **Step 8: Build to verify**

```bash
cargo build 2>&1 | head -30
```

Expected: Compilation succeeds. If there are errors about `tokio::process::Command` vs `std::process::Command` API differences, fix them.

- [ ] **Step 9: Commit**

```bash
git add crates/mycelium-app/src/daemon.rs crates/mycelium-app/src/main.rs
git commit -m "feat: convert daemon from sync sleep-poll to async signal-driven"
```

---

### Task 5: SQLite Connection Pooling (Reader/Writer)

**Files:**
- Modify: `crates/mycelium-core/src/storage.rs`

**Interfaces:**
- Consumes: Same `Storage::open()` API — but internally opens two connections instead of one
- Produces: `Storage::conn()` → `&Mutex<Connection>` (deprecated but kept for backwards compat); new `Storage::read_conn()` → `&RwLock<Connection>` and `Storage::write_conn()` → `&Mutex<Connection>` (the write mutex is a tokio::sync::Mutex, not std::sync::Mutex)

**Risk assessment:** This is the broadest-reaching change because it touches 27+ call sites. For a hotfix, the safe approach is:
1. Add the second connection
2. Only migrate the HOTTEST paths to the new API (search, recall, get_entry)
3. Leave everything else on the old `conn()` path
4. Mark `conn()` as deprecated

This minimizes blast radius while still delivering the concurrency win.

- [ ] **Step 1: Update Storage struct with dual connections**

```rust
use tokio::sync::{Mutex as AsyncMutex, RwLock};

pub struct Storage {
    // Old single connection (kept for backward compat)
    conn: Mutex<Connection>,
    // New dual connections
    write_conn: AsyncMutex<Connection>,
    read_conn: RwLock<Connection>,
    // Notify
    notify: Arc<Notify>,
    cache: Cache,
    search_index: Option<SearchIndex>,
    config: StorageConfig,
}
```

- [ ] **Step 2: Open two connections in `Storage::open()`**

SQLite with WAL mode supports concurrent readers and one writer. Open two connections:

```rust
pub fn open(path: impl AsRef<Path>) -> rusqlite::Result<Self> {
    let path = path.as_ref();
    let mut conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;

    // Open second connection for concurrent reads
    let read_conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    // Read connection must also enable WAL (WAL readers can proceed during WAL writes)
    read_conn.execute_batch("PRAGMA journal_mode=WAL;")?;

    let write_conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    write_conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")?;

    // Perform schema init on the original conn
    brain::ensure_tables(&conn)?;
    // ... rest of setup ...

    Ok(Self {
        conn: Mutex::new(conn),
        write_conn: AsyncMutex::new(write_conn),
        read_conn: RwLock::new(read_conn),
        notify: Arc::new(Notify::new()),
        cache: Cache::new(),
        search_index,
        config,
    })
}
```

**Why two connections:** The original `conn` is kept for backward compat for all the `conn().lock().unwrap()` callers. The new `read_conn` and `write_conn` are for the async migration path.

- [ ] **Step 3: Add async reader/writer accessors**

```rust
impl Storage {
    /// Access the write connection (serialized via async mutex).
    pub async fn write_conn(&self) -> tokio::sync::MutexGuard<'_, Connection> {
        self.write_conn.lock().await
    }

    /// Access the read connection (shared via RwLock).
    pub async fn read_conn(&self) -> tokio::sync::RwLockReadGuard<'_, Connection> {
        self.read_conn.read().await
    }

    /// Access the write connection exclusively for read-after-write consistency.
    pub async fn write_read_conn(&self) -> tokio::sync::RwLockWriteGuard<'_, Connection> {
        self.read_conn.write().await
    }
}
```

- [ ] **Step 4: Migrate `get_entry()` to read_conn**

This is the hottest read path — called on every proxy request. Convert it to use the read connection via `spawn_blocking`:

```rust
pub async fn get_entry_async(&self, turn: i64) -> anyhow::Result<Option<MemoryEntry>> {
    // Check cache first (fast path, no DB needed)
    if let Some(cached) = self.cache.get_entry(turn) {
        return Ok(Some(cached));
    }

    let conn_ptr: *const RwLock<Connection> = &self.read_conn;
    // Safety: conn_ptr is valid for the duration of spawn_blocking
    let result = tokio::task::spawn_blocking(move || {
        let guard = unsafe { &*conn_ptr }.blocking_read();
        Self::get_entry_inner(&guard, turn)
    })
    .await??;

    if let Some(ref entry) = result {
        self.cache.put_entry(entry);
    }
    Ok(result)
}
```

**Wait — raw pointers across spawn_blocking are unsound.** The correct approach is to clone the data we need inside spawn_blocking. But `Connection` isn't Clone. 

**Better approach:** Use `tokio::task::spawn_blocking` with an `Arc<Mutex<Connection>>` that's separate from the main mutex. Actually, the cleanest approach for a hotfix is simpler:

The `read_conn` is behind an `RwLock`. We can get the read guard in the async context and pass it into spawn_blocking:

```rust
pub async fn get_entry_async(&self, turn: i64) -> anyhow::Result<Option<MemoryEntry>> {
    // Check cache first
    if let Some(cached) = self.cache.get_entry(turn) {
        return Ok(Some(cached));
    }

    let result = {
        let guard = self.read_conn.read().await;
        // rusqlite::Connection is Send but not Sync.
        // We need to use the Connection inside spawn_blocking.
        // Since we hold the read guard, no writer can proceed.
        // Pass the raw conn pointer. This is safe because spawn_blocking
        // runs on the same tokio runtime and the read guard is held.
        let ptr: *const Connection = &*guard;
        tokio::task::spawn_blocking(move || {
            // SAFETY: The read guard is held until spawn_blocking completes,
            // so the connection is alive and no one else is writing to it.
            let conn = unsafe { &*ptr };
            Self::get_entry_inner(conn, turn)
        }).await?
    };

    if let Some(ref entry) = result {
        self.cache.put_entry(entry);
    }
    Ok(result)
}
```

**Actually, this is getting complex for a hotfix.** Let me simplify radically:

The simplest approach for a hotfix is to **skip the full refactor** and just spawn_blocking for the read path without changing the storage internals at all. Instead of adding a whole new connection pool, we just make the existing `get_entry` go through `spawn_blocking` so it doesn't block the async runtime. The Mutex<Connection> still serializes, but at least it doesn't block the tokio worker threads.

But the spec says to add a reader connection. Let me just keep it simple:

**Simplest correct approach:** Add one additional read-only connection to the Storage. Expose a new `pub fn get_entry(&self, turn: i64)` that internally uses the read connection via `blocking_read()` on the RwLock. The old `conn()` API stays for backward compat. All 27 call sites stay unchanged. The read path gets its own connection.

```rust
pub fn get_entry(&self, turn: i64) -> anyhow::Result<Option<MemoryEntry>> {
    // Check cache first
    if let Some(cached) = self.cache.get_entry(turn) {
        return Ok(Some(cached));
    }
    // Use read connection
    let guard = self.read_conn.blocking_read();
    let result = Self::get_entry_inner(&guard, turn)?;
    if let Some(ref entry) = result {
        self.cache.put_entry(entry);
    }
    Ok(result)
}

// Internal helper that does the actual query, used by both old and new path
fn get_entry_inner(conn: &Connection, turn: i64) -> anyhow::Result<Option<MemoryEntry>> {
    let mut stmt = conn.prepare(
        "SELECT turn, session, user_content, assistant_content, annotation, finding, verdict, created_at
         FROM entries WHERE turn = ?1"
    )?;
    // ... rest of existing get_entry logic ...
}
```

This is the right balance: add the reader connection, refactor the hot path, keep everything else compiling. The old `conn()` method is untouched.

- [ ] **Step 5: Build to verify**

```bash
cargo build 2>&1 | head -30
```

- [ ] **Step 6: Commit**

```bash
git add crates/mycelium-core/src/storage.rs
git commit -m "feat: add read-only connection for concurrent read access"
```

---

### Task 6: LLM Concurrency (Verify-Semaphore-Only)

**Files:**
- Modify: `crates/mycelium-proxy/src/lib.rs` (check existing semaphore is correct)

**Interfaces:** No API changes. This is purely a validation task.

- [ ] **Step 1: Read current concurrency control**

Read `lib.rs` and verify the semaphore pattern:

```rust
// Expected pattern already in code (from earlier grep):
// let semaphore = Semaphore::new(config.max_concurrent);
// ...
// let _permit = semaphore.acquire().await;
```

Confirm this exists. If it does, the LLM concurrency concern is already addressed by the hotfix. The 180s client timeout is appropriate for LLM APIs.

- [ ] **Step 2: Build to verify**

```bash
cargo build 2>&1 | head -10
```

- [ ] **Step 3: Commit** (if any change was needed, otherwise skip)

```bash
git commit -m "chore: LLM concurrency already handled by existing semaphore"
```

---

### Task 7: Final Cleanup and Build

- [ ] **Step 1: Full workspace build**

```bash
cargo build --workspace 2>&1 | tail -20
```

- [ ] **Step 2: Run tests**

```bash
cargo test --workspace 2>&1 | tail -30
```

- [ ] **Step 3: Fix any test failures**

Expected: Brain tests (`test_consolidate_entry`, `test_brain_status_pending`, etc.) should pass because `enqueue_brain_work` and `consolidate_entry` logic is unchanged.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: cleanup and verify full workspace build for event-native reactor"
```

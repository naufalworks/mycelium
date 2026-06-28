//! Mycelium Daemon — async signal-driven process manager.
//!
//! Manages the server and proxy as child processes with:
//! - tokio::process-based spawning
//! - Event-driven monitoring via tokio::select! over child.wait()
//! - Circuit-breaker restart with rate limiting (1s min interval)
//! - Graceful shutdown with async timeout
//! - PID file management
//! - launchd integration

use mycelium_core::MyceliumConfig;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::process::{Child, Command};
use tokio::signal::unix::{signal, SignalKind};
use tracing::{error, info, warn};

/// Managed child process with restart tracking.
struct ManagedProcess {
    name: &'static str,
    binary: &'static str,
    child: Option<Child>,
    consecutive_failures: u32,
    restart_count: u32,
    pid_path: std::path::PathBuf,
}

impl ManagedProcess {
    fn new(name: &'static str, binary: &'static str, pid_dir: &std::path::Path) -> Self {
        Self {
            name,
            binary,
            child: None,
            consecutive_failures: 0,
            restart_count: 0,
            pid_path: pid_dir.join(format!("{}.pid", name)),
        }
    }

    /// Start the process.
    fn start(&mut self, config: &MyceliumConfig) -> Result<(), String> {
        let bin_path = std::env::current_exe()
            .map(|p| p.parent().unwrap_or(p.as_path()).join(self.binary))
            .unwrap_or_else(|_| self.binary.into());

        let log_dir = config.root_dir.join("daemon");
        std::fs::create_dir_all(&log_dir).map_err(|e| format!("create log dir: {}", e))?;

        let out = std::fs::File::create(log_dir.join(format!("{}.log", self.name)))
            .map_err(|e| format!("create log: {}", e))?;
        let err = out.try_clone().map_err(|e| format!("clone log: {}", e))?;

        let child = Command::new(&bin_path)
            .stdout(Stdio::from(out))
            .stderr(Stdio::from(err))
            .env("MYCELIUM_WEB_ROOT", config.root_dir.join("web"))
            .spawn()
            .map_err(|e| format!("spawn {}: {}", self.name, e))?;

        info!("Started {} (PID {})", self.name, child.id().unwrap_or(0));
        self.child = Some(child);
        self.restart_count += 1;
        let _ = std::fs::write(
            &self.pid_path,
            self.child
                .as_ref()
                .and_then(|c| c.id())
                .unwrap_or(0)
                .to_string(),
        );
        Ok(())
    }

    /// Check if the process is alive.
    #[allow(dead_code)]
    fn is_alive(&mut self) -> bool {
        if let Some(ref mut child) = self.child {
            match child.try_wait() {
                Ok(Some(status)) => {
                    info!("{} exited with status: {}", self.name, status);
                    self.child = None;
                    false
                }
                Ok(None) => true, // Still running
                Err(e) => {
                    warn!("{} wait error: {}", self.name, e);
                    self.child = None;
                    false
                }
            }
        } else {
            false
        }
    }

    /// Stop the process gracefully (send SIGTERM).
    fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            info!("Stopping {} (PID {})", self.name, child.id().unwrap_or(0));

            #[cfg(unix)]
            if let Some(pid) = child.id() {
                let _ = std::process::Command::new("kill")
                    .arg(pid.to_string())
                    .spawn();
            }

            // Drop child — try_wait reaps zombie if already exited,
            // otherwise the kernel will clean up when SIGTERM takes effect.
            let _ = child.try_wait();
        }
    }

    /// Force kill the process (send SIGKILL).
    fn force_kill(&mut self) {
        if let Some(mut child) = self.child.take() {
            #[cfg(unix)]
            if let Some(pid) = child.id() {
                let _ = std::process::Command::new("kill")
                    .args(["-9", &pid.to_string()])
                    .spawn();
            }
            let _ = child.try_wait();
        }
    }
}

/// Run the daemon — async signal-driven server and proxy manager.
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

    // Signal handling via tokio::signal::unix
    let r = Arc::clone(&running);
    let mut term_signal =
        signal(SignalKind::terminate()).map_err(|e| format!("signal handler: {}", e))?;
    let mut int_signal =
        signal(SignalKind::interrupt()).map_err(|e| format!("signal handler: {}", e))?;

    let mut signal_task = tokio::spawn(async move {
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
    info!(
        "Server PID: {}",
        server.child.as_ref().and_then(|c| c.id()).unwrap_or(0)
    );
    info!(
        "Proxy PID: {}",
        proxy.child.as_ref().and_then(|c| c.id()).unwrap_or(0)
    );

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
                let _ = child.wait().await;
            }
            "server"
        };
        let proxy_wait = async {
            if let Some(child) = proxy.child.as_mut() {
                let _ = child.wait().await;
            }
            "proxy"
        };

        tokio::select! {
            _ = &mut signal_task => break,
            _exited = server_wait => {
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
            _exited = proxy_wait => {
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

/// Install launchd plist for auto-start on boot.
pub fn install_launchd(config: &MyceliumConfig) -> Result<(), String> {
    let binary_path = std::env::current_exe()
        .map_err(|e| format!("current exe: {}", e))?
        .to_string_lossy()
        .to_string();

    let log_dir = config.root_dir.join("daemon");
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("create log dir: {}", e))?;

    let plist_content = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.naufal.mycelium-rust</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary}</string>
        <string>daemon</string>
    </array>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{root}</string>
    <key>StandardOutPath</key>
    <string>{log}/daemon.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{log}/daemon.stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>MYCELIUM_ROOT</key>
        <string>{root}</string>
    </dict>
</dict>
</plist>
"#,
        binary = binary_path,
        root = config.root_dir.display(),
        log = log_dir.display(),
    );

    let plist_path = dirs::home_dir()
        .ok_or("no home dir")?
        .join("Library/LaunchAgents/com.naufal.mycelium-rust.plist");

    std::fs::write(&plist_path, plist_content)
        .map_err(|e| format!("write plist: {}", e))?;

    info!("launchd plist installed at {}", plist_path.display());
    info!(
        "Run: launchctl bootstrap gui/$(id -u) {} to activate",
        plist_path.display()
    );

    Ok(())
}

/// Uninstall launchd plist.
pub fn uninstall_launchd() -> Result<(), String> {
    let plist_path = dirs::home_dir()
        .ok_or("no home dir")?
        .join("Library/LaunchAgents/com.naufal.mycelium-rust.plist");

    if plist_path.exists() {
        // Unload from launchd
        #[cfg(unix)]
        {
            let _ = std::process::Command::new("launchctl")
                .args(["bootout", "gui/$(id -u)", &plist_path.to_string_lossy()])
                .spawn();
        }
        std::fs::remove_file(&plist_path).map_err(|e| format!("remove plist: {}", e))?;
        info!("launchd plist removed");
    } else {
        info!("No launchd plist found");
    }

    Ok(())
}

/// Show daemon and services status.
pub fn show_status(config: &MyceliumConfig) -> Result<(), String> {
    let pid_dir = config.root_dir.join("run");

    println!("🧬 Mycelium Daemon Status");
    println!();

    for (name, file) in [
        ("Daemon", "daemon.pid"),
        ("Server", "server.pid"),
        ("Proxy", "proxy.pid"),
    ] {
        let path = pid_dir.join(file);
        if path.exists() {
            let pid_str =
                std::fs::read_to_string(&path).map_err(|e| format!("read {}: {}", file, e))?;
            let pid = pid_str.trim().parse::<u32>().unwrap_or(0);
            let alive = if pid > 0 {
                // Check if process exists on Unix
                let status = std::process::Command::new("ps")
                    .args(["-p", &pid.to_string()])
                    .output();
                status.map(|o| o.status.success()).unwrap_or(false)
            } else {
                false
            };
            if alive {
                println!("   ✅ {} running (PID {})", name, pid);
            } else {
                println!("   ❌ {} stale PID {} (process dead)", name, pid);
            }
        } else {
            println!("   ⬜ {} not started", name);
        }
    }

    Ok(())
}

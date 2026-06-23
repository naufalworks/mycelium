//! Mycelium Daemon — robust background process manager.
//!
//! Manages the server and proxy as child processes with:
//! - Auto-restart with exponential backoff
//! - Health monitoring every 10 seconds
//! - Graceful shutdown on signals
//! - PID file management
//! - launchd integration

use mycelium_core::MyceliumConfig;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};

/// Maximum number of restart attempts before giving up.
const MAX_RETRIES: u32 = 10;
/// Initial backoff delay (seconds).
const INITIAL_BACKOFF: u64 = 1;
/// Maximum backoff delay (seconds).
const MAX_BACKOFF: u64 = 30;

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

        info!("Started {} (PID {})", self.name, child.id());
        self.child = Some(child);
        self.restart_count += 1;
        let _ = std::fs::write(&self.pid_path, self.child.as_ref().map(|c| c.id()).unwrap_or(0).to_string());
        Ok(())
    }

    /// Check if the process is alive.
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

    /// Stop the process gracefully, then force kill.
    fn stop(&mut self) {
        if let Some(mut child) = self.child.take() {
            info!("Stopping {} (PID {})", self.name, child.id());

            // Try graceful shutdown
            #[cfg(unix)]
            {
                let _ = Command::new("kill").arg(child.id().to_string()).spawn();
            }

            // Wait up to 5 seconds
            let _ = std::thread::spawn(move || {
                let _ = child.wait();
            });
        }
    }

    /// Force kill the process.
    fn force_kill(&mut self) {
        if let Some(mut child) = self.child.take() {
            #[cfg(unix)]
            {
                let _ = Command::new("kill").args(["-9", &child.id().to_string()]).spawn();
            }
            let _ = child.wait();
        }
    }
}

/// Run the daemon — manages server and proxy processes.
pub fn run_daemon(config: &MyceliumConfig) -> Result<(), String> {
    let pid_dir = config.root_dir.join("run");
    std::fs::create_dir_all(&pid_dir).map_err(|e| format!("create pid dir: {}", e))?;

    // Write daemon PID
    let daemon_pid = pid_dir.join("daemon.pid");
    std::fs::write(&daemon_pid, std::process::id().to_string())
        .map_err(|e| format!("write daemon pid: {}", e))?;

    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();

    // Set up signal handler
    ctrlc::set_handler(move || {
        info!("Shutdown signal received");
        r.store(false, Ordering::SeqCst);
    })
    .map_err(|e| format!("signal handler: {}", e))?;

    // Initialize managed processes
    let mut server = ManagedProcess::new("server", "mycelium-server", &pid_dir);
    let mut proxy = ManagedProcess::new("proxy", "mycelium-proxy", &pid_dir);

    // Start both processes
    if let Err(e) = server.start(config) {
        error!("Failed to start server: {}", e);
    }
    if let Err(e) = proxy.start(config) {
        error!("Failed to start proxy: {}", e);
    }

    info!("Daemon started (PID {})", std::process::id());
    info!("Server PID: {}", server.child.as_ref().map(|c| c.id()).unwrap_or(0));
    info!("Proxy PID: {}", proxy.child.as_ref().map(|c| c.id()).unwrap_or(0));

    // Main monitoring loop
    while running.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_secs(10));

        // Check server
        if !server.is_alive() && running.load(Ordering::SeqCst) {
            server.consecutive_failures += 1;
            if server.restart_count < MAX_RETRIES {
                let backoff = backoff_delay(server.restart_count);
                info!("Server down, restarting in {}s (attempt {}/{})",
                    backoff, server.restart_count + 1, MAX_RETRIES);
                std::thread::sleep(Duration::from_secs(backoff));
                let _ = server.start(config);
            } else {
                error!("Server failed {} times, giving up", MAX_RETRIES);
            }
        } else {
            server.consecutive_failures = 0;
        }

        // Check proxy
        if !proxy.is_alive() && running.load(Ordering::SeqCst) {
            proxy.consecutive_failures += 1;
            if proxy.restart_count < MAX_RETRIES {
                let backoff = backoff_delay(proxy.restart_count);
                info!("Proxy down, restarting in {}s (attempt {}/{})",
                    backoff, proxy.restart_count + 1, MAX_RETRIES);
                std::thread::sleep(Duration::from_secs(backoff));
                let _ = proxy.start(config);
            } else {
                error!("Proxy failed {} times, giving up", MAX_RETRIES);
            }
        } else {
            proxy.consecutive_failures = 0;
        }
    }

    // Graceful shutdown
    info!("Shutting down...");
    // Send SIGTERM to children first
    server.stop();
    proxy.stop();

    // Wait for them to exit
    std::thread::sleep(Duration::from_secs(3));

    // Force kill any remaining
    server.force_kill();
    proxy.force_kill();

    // Cleanup PID file
    let _ = std::fs::remove_file(&daemon_pid);

    info!("Daemon stopped");
    Ok(())
}

/// Calculate exponential backoff delay.
fn backoff_delay(attempt: u32) -> u64 {
    let delay = INITIAL_BACKOFF * (2u64.pow(attempt));
    delay.min(MAX_BACKOFF)
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
    info!("Run: launchctl bootstrap gui/$(id -u) {} to activate", plist_path.display());

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
            let _ = Command::new("launchctl")
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

    for (name, file) in [("Daemon", "daemon.pid"), ("Server", "server.pid"), ("Proxy", "proxy.pid")] {
        let path = pid_dir.join(file);
        if path.exists() {
            let pid_str = std::fs::read_to_string(&path).map_err(|e| format!("read {}: {}", file, e))?;
            let pid = pid_str.trim().parse::<u32>().unwrap_or(0);
            let alive = if pid > 0 {
                // Check if process exists on Unix
                let status = Command::new("ps").args(["-p", &pid.to_string()]).output();
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

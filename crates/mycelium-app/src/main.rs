//! Mycelium CLI — permanent memory for AI agents.
//!
//! Usage:
//!   mycelium status          — Brain status summary
//!   mycelium search <query>  — Search across all memory
//!   mycelium verify          — Hash chain integrity check
//!   mycelium resume [session]— Recent context for session resumption
//!   mycelium start           — Start daemon + server + proxy
//!   mycelium stop            — Stop all services
//!   mycelium backup          — Create full backup
//!   mycelium fact <sub>      — Memory fact CRUD
//!   mycelium migrate         — Import existing go/python data

use clap::{Parser, Subcommand};
use mycelium_core::MyceliumConfig;
use std::process::{Command, Stdio};
use tracing::{error, info};

mod daemon;

#[derive(Parser)]
#[command(name = "mycelium", about = "Permanent memory for AI agents", version)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Mycelium root directory (default: $MYCELIUM_ROOT or ~/.hermes/myceliumd/runtime)
    #[arg(short = 'r', long, global = true)]
    root: Option<String>,
}

#[derive(Subcommand)]
enum Commands {
    /// Brain status summary
    Status,
    /// Search across all memory
    Search {
        /// Search query
        query: String,
    },
    /// Hash chain integrity check
    Verify,
    /// Recent context for session resumption
    Resume {
        /// Optional session name
        session: Option<String>,
    },
    /// Start daemon + server + proxy
    Start,
    /// Stop all services
    Stop,
    /// Create full backup
    Backup {
        /// Output directory
        #[arg(default_value = ".")]
        dir: String,
    },
    /// Display configuration
    Config,
    /// Migrate existing data from Go/Python
    Migrate,
    /// Run health checks
    Precheck,
    /// Full context bundle for a session
    Context {
        /// Session name
        session: String,
    },
    /// Show findings/insights
    Findings,
    /// Predict likely next questions from context
    Infer {
        /// Context text to analyze
        context: String,
    },
    /// Read a URL into memory
    Read {
        /// URL to fetch
        url: String,
    },
    /// Run the daemon process (foreground)
    Daemon,
    /// Start daemon in background
    DaemonStart,
    /// Stop daemon + all services
    DaemonStop,
    /// Show daemon and services status
    DaemonStatus,
    /// Install launchd plist for auto-start
    DaemonInstall,
    /// Remove launchd plist
    DaemonUninstall,
    /// Memory fact operations
    Fact {
        #[command(subcommand)]
        command: FactCommands,
    },
    /// Context snapshot operations
    Snapshot {
        #[command(subcommand)]
        command: SnapshotCommands,
    },
    /// Brain management operations
    Brain {
        #[command(subcommand)]
        command: BrainCommands,
    },
}

#[derive(Subcommand)]
enum FactCommands {
    /// List all facts
    List,
    /// Search facts
    Search { query: String },
    /// Add a fact
    Add {
        entity: String,
        attribute: String,
        value: String,
    },
    /// Delete a fact
    Delete { id: i64 },
}

#[derive(Subcommand)]
enum SnapshotCommands {
    /// List recent snapshots
    List,
    /// Get snapshots for a session
    Session { session: String },
    /// Create a snapshot for a session
    Create { session: String, summary: String },
}

#[derive(Subcommand)]
enum BrainCommands {
    /// Process annotated entries through the brain (one-shot consolidation)
    Annotated,
    /// Enqueue all entries for brain processing (backfill)
    Backfill {
        /// Processing order: newest-first (default) or oldest-first
        #[arg(default_value = "newest-first")]
        order: String,
    },
    /// Detect and register stop words from atom frequency data
    StopWords,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let cli = Cli::parse();
    let mut config = MyceliumConfig::default();

    if let Some(root) = cli.root {
        config.root_dir = std::path::PathBuf::from(root);
    }

    match &cli.command {
        Commands::Status => cmd_status(&config).await?,
        Commands::Search { query } => cmd_search(&config, query).await?,
        Commands::Verify => cmd_verify(&config).await?,
        Commands::Resume { session } => cmd_resume(&config, session.as_deref()).await?,
        Commands::Start => cmd_start(&config).await?,
        Commands::Stop => cmd_stop(&config).await?,
        Commands::Backup { dir } => cmd_backup(&config, dir).await?,
        Commands::Config => cmd_config(&config).await?,
        Commands::Migrate => cmd_migrate(&config).await?,
        Commands::Precheck => cmd_precheck(&config).await?,
        Commands::Context { session } => cmd_context(&config, session).await?,
        Commands::Findings => cmd_findings(&config).await?,
        Commands::Infer { context } => cmd_infer(&config, context).await?,
        Commands::Read { url } => cmd_read(&config, url).await?,
        Commands::Daemon => run_daemon(&config).await,
        Commands::DaemonStart => daemon_start(&config),
        Commands::DaemonStop => daemon_stop(&config),
        Commands::DaemonStatus => daemon_status(&config),
        Commands::DaemonInstall => daemon_install(&config),
        Commands::DaemonUninstall => daemon_uninstall(),
        Commands::Fact { command } => cmd_fact(&config, command).await?,
        Commands::Snapshot { command } => cmd_snapshot(&config, command).await?,
        Commands::Brain { command } => cmd_brain(&config, command)?,
    }

    Ok(())
}

async fn cmd_status(config: &MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    if !db_path.exists() {
        println!("❌ No mycelium database found at {}", db_path.display());
        println!("   Run `mycelium migrate` to import existing data.");
        return Ok(());
    }

    let storage = mycelium_core::Storage::open(db_path)?;
    let count = storage.count_entries()?;
    let sessions = storage.count_sessions()?;
    let tiers = storage.tier_distribution()?;
    let types = storage.type_distribution()?;
    let last = storage.last_entry()?;
    let db_size = storage.db_size()?;

    println!("🧬 Mycelium Brain");
    println!("   Database: {}", storage.path().display());
    println!("   Schema v{}", storage.schema_version()?);
    println!("   Size:     {} KB", db_size / 1024);
    println!("   Entries:  {}", count);
    println!("   Sessions: {}", sessions);
    if let Some(last) = last {
        println!("   Last:     turn {} from {} ({})", last.turn, last.session, last.ts);
    }
    println!();
    println!("   Tiers:");
    for (tier, n) in &tiers {
        println!("     {}: {}", tier, n);
    }
    println!("   Types:");
    for (t, n) in &types {
        println!("     {}: {}", t, n);
    }

    Ok(())
}

async fn cmd_search(config: &MyceliumConfig, query: &str) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    if !db_path.exists() {
        println!("❌ No database found. Run `mycelium migrate` first.");
        return Ok(());
    }

    let storage = mycelium_core::Storage::open(db_path)?;
    let entries = storage.search_fts(query, 20)?;

    if entries.is_empty() {
        println!("No results for: {}", query);
        return Ok(());
    }

    println!("🔍 Results for \"{}\" ({} found):", query, entries.len());
    for entry in &entries {
        let user_preview = if entry.user.len() > 80 {
            format!("{}...", &entry.user[..80])
        } else {
            entry.user.clone()
        };
        println!("   [#{}] {} — {}: {}", entry.turn, entry.session, entry.entry_type.as_str(), user_preview);
    }

    Ok(())
}

async fn cmd_verify(config: &MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    if !db_path.exists() {
        println!("❌ No database found at {}", db_path.display());
        return Ok(());
    }

    let storage = mycelium_core::Storage::open(db_path)?;
    let count = storage.count_entries()?;
    println!("📋 Hash Chain Verification ({} entries)", count);
    println!();

    let failures = storage.verify_hash_chain()?;
    if failures.is_empty() {
        println!("✅ Hash chain is intact — all {} entries verified", count);
    } else {
        println!("❌ {} hash chain failure(s) found:", failures.len());
        for (turn, expected, actual) in &failures {
            println!("   Turn {}: {} | actual: {}", turn, expected, actual);
        }
    }

    Ok(())
}

async fn cmd_resume(config: &MyceliumConfig, session: Option<&str>) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    let session_name = match session {
        Some(s) => s.to_string(),
        None => {
            let sessions = storage.recent_sessions(1)?;
            sessions.into_iter().next().unwrap_or_default()
        }
    };

    if session_name.is_empty() {
        println!("No sessions found.");
        return Ok(());
    }

    println!("📝 Session: {}", session_name);
    let entries = storage.entries_for_session(&session_name, 10)?;
    println!("   Last {} entries: {}", entries.len(), entries.len());

    for entry in entries.iter().rev() {
        let user_preview = entry.user.chars().take(80).collect::<String>();
        println!();
        println!("   [#{}] {}", entry.turn, entry.ts);
        println!("   User: {}", user_preview);
        println!("   Entities: {}", entry.entities.join(", "));
    }

    Ok(())
}

async fn cmd_start(config: &MyceliumConfig) -> anyhow::Result<()> {
    let pid_dir = config.root_dir.join("run");
    std::fs::create_dir_all(&pid_dir)?;

    let server_pid = pid_dir.join("server.pid");
    if server_pid.exists() {
        println!("⚠️  Server PID file exists — might already be running");
    } else {
        let server_bin = std::env::current_exe()?
            .parent().map(|p| p.join("mycelium-server"))
            .unwrap_or_else(|| "mycelium-server".into());
        let child = std::process::Command::new(&server_bin)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()?;
        std::fs::write(&server_pid, child.id().to_string())?;
        println!("✅ Server started (PID {})", child.id());
    }

    let proxy_pid = pid_dir.join("proxy.pid");
    if proxy_pid.exists() {
        println!("⚠️  Proxy PID file exists — might already be running");
    } else {
        let proxy_bin = std::env::current_exe()?
            .parent().map(|p| p.join("mycelium-proxy"))
            .unwrap_or_else(|| "mycelium-proxy".into());
        let child = std::process::Command::new(&proxy_bin)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()?;
        std::fs::write(&proxy_pid, child.id().to_string())?;
        println!("✅ Proxy started (PID {})", child.id());
    }

    println!("\n📋 Status:  mycelium status");
    println!("📋 Stop:    mycelium stop");
    Ok(())
}

async fn cmd_stop(config: &MyceliumConfig) -> anyhow::Result<()> {
    let pid_dir = config.root_dir.join("run");
    let mut any_stopped = false;

    for (name, file) in [("Server", "server.pid"), ("Proxy", "proxy.pid")] {
        let path = pid_dir.join(file);
        if path.exists() {
            let pid_str = std::fs::read_to_string(&path)?;
            if let Ok(pid) = pid_str.trim().parse::<u32>() {
                #[cfg(unix)]
                { std::process::Command::new("kill").arg(pid.to_string()).spawn().ok(); }
                std::fs::remove_file(&path)?;
                println!("✅ {} stopped (PID {})", name, pid);
                any_stopped = true;
            }
        }
    }

    if !any_stopped {
        println!("ℹ️  No services were running");
    }
    Ok(())
}

async fn cmd_backup(config: &MyceliumConfig, dir: &str) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    if !db_path.exists() {
        println!("❌ No database found at {}", db_path.display());
        return Ok(());
    }

    let out_dir = std::path::Path::new(dir);
    std::fs::create_dir_all(out_dir)?;

    let ts = chrono::Utc::now().format("%Y%m%d_%H%M%S");
    let backup_name = format!("mycelium_backup_{}.tar.gz", ts);
    let backup_path = out_dir.join(&backup_name);

    let file = std::fs::File::create(&backup_path)?;
    let mut archive = tar::Builder::new(flate2::write::GzEncoder::new(file, flate2::Compression::default()));
    archive.append_path_with_name(&db_path, "mycelium.db")?;
    archive.finish()?;

    let metadata = std::fs::metadata(&backup_path)?;
    println!("✅ Backup created: {}", backup_path.display());
    println!("   Size: {} KB", metadata.len() / 1024);

    Ok(())
}

async fn cmd_config(config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("📋 Mycelium Configuration");
    println!("   Root dir:     {}", config.root_dir.display());
    println!("   Proxy port:   {}", config.proxy_port);
    println!("   Server port:  {}", config.server_port);
    println!("   Upstream URL: {}", config.upstream_url);
    println!("   Max concurrent: {}", config.max_concurrent);
    Ok(())
}
async fn cmd_infer(config: &MyceliumConfig, context: &str) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    let facts = storage.search_facts(context, 5)?;
    let entries = storage.search_fts(context, 5)?;

    println!("🔮 Context analysis: \"{}\"", context.chars().take(60).collect::<String>());
    println!();
    println!("   Related facts ({}):", facts.len());
    for f in &facts {
        println!("     [{}] {}.{} = {}", f.fact_type, f.entity, f.attribute, f.value.chars().take(60).collect::<String>());
    }
    println!();
    println!("   Related entries ({}):", entries.len());
    for e in &entries {
        let preview: String = e.user.chars().take(80).collect();
        println!("     [#{}] {}", e.turn, preview);
    }

    Ok(())
}

async fn cmd_read(config: &MyceliumConfig, url: &str) -> anyhow::Result<()> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()?;

    println!("📖 Reading: {}", url);
    let resp = client.get(url).send().await?;
    let text = resp.text().await?;
    let preview: String = text.chars().take(200).collect();

    println!("   Content ({}/{})", text.len(), text.len());
    println!("   Preview: {}", preview);

    // Store as artifact
    let artifact = mycelium_core::Artifact {
        id: uuid::Uuid::new_v4(),
        session: "rust-cli".to_string(),
        filename: url.trim_start_matches("https://").trim_start_matches("http://").replace("/", "_"),
        content_type: "text/html".to_string(),
        content: text.into_bytes(),
        description: Some(format!("Read from {}", url)),
        artifact_type: "web-page".to_string(),
        created_at: chrono::Utc::now(),
    };

    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;
    storage.store_artifact(&artifact)?;
    println!("✅ Stored as artifact: {}", artifact.id);

    Ok(())
}

async fn run_daemon(config: &MyceliumConfig) {
    match daemon::run_daemon(config).await {
        Ok(()) => info!("Daemon exited normally"),
        Err(e) => error!("Daemon error: {}", e),
    }
}

fn daemon_start(config: &MyceliumConfig) {
    let pid_dir = config.root_dir.join("run");
    let daemon_pid = pid_dir.join("daemon.pid");
    if daemon_pid.exists() {
        println!("⚠️  Daemon already running? Remove {} to force", daemon_pid.display());
        return;
    }

    let bin_path = std::env::current_exe().unwrap();
    match Command::new(&bin_path)
        .arg("daemon")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .stdin(Stdio::null())
        .spawn()
    {
        Ok(child) => {
            println!("✅ Daemon started (PID {})", child.id());
        }
        Err(e) => println!("❌ Failed to start daemon: {}", e),
    }
}

fn daemon_stop(config: &MyceliumConfig) {
    let pid_dir = config.root_dir.join("run");
    let daemon_pid = pid_dir.join("daemon.pid");
    if daemon_pid.exists() {
        let pid = std::fs::read_to_string(&daemon_pid)
            .ok()
            .and_then(|s| s.trim().parse::<u32>().ok());
        if let Some(pid) = pid {
            #[cfg(unix)]
            {
                let _ = Command::new("kill").arg(pid.to_string()).spawn();
            }
            println!("✅ Daemon stopping (PID {})", pid);
        }
        let _ = std::fs::remove_file(&daemon_pid);
    } else {
        // Fallback to old start/stop mechanism
        println!("ℹ️  No daemon PID file found, stopping server + proxy directly...");
        let _ = Command::new("kill")
            .args(["-9", &std::fs::read_to_string(pid_dir.join("server.pid")).unwrap_or_default().trim()])
            .spawn();
        let _ = Command::new("kill")
            .args(["-9", &std::fs::read_to_string(pid_dir.join("proxy.pid")).unwrap_or_default().trim()])
            .spawn();
        let _ = std::fs::remove_file(pid_dir.join("server.pid"));
        let _ = std::fs::remove_file(pid_dir.join("proxy.pid"));
    }
}

fn daemon_status(config: &MyceliumConfig) {
    match daemon::show_status(config) {
        Ok(()) => {}
        Err(e) => println!("❌ Status error: {}", e),
    }
}

fn daemon_install(config: &MyceliumConfig) {
    match daemon::install_launchd(config) {
        Ok(()) => println!("✅ launchd plist installed"),
        Err(e) => println!("❌ Install error: {}", e),
    }
}

fn daemon_uninstall() {
    match daemon::uninstall_launchd() {
        Ok(()) => println!("✅ launchd plist removed"),
        Err(e) => println!("❌ Uninstall error: {}", e),
    }
}

async fn cmd_context(config: &MyceliumConfig, session: &str) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    println!("📝 Session: {}", session);
    let entries = storage.entries_for_session(session, 20)?;
    println!("   Entries: {}", entries.len());
    let facts = storage.search_facts(session, 10)?;
    println!("   Memory facts: {}", facts.len());
    let snapshots = storage.snapshots_for_session(session, 5)?;
    println!("   Snapshots: {}", snapshots.len());

    if !entries.is_empty() {
        println!("\nRecent activity:");
        for entry in entries.iter().rev().take(5) {
            let preview: String = entry.user.chars().take(80).collect();
            println!("   [#{}] {}", entry.turn, preview);
        }
    }

    Ok(())
}

async fn cmd_findings(config: &MyceliumConfig) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    let entries = storage.search_fts("finding", 50)?;
    let findings: Vec<_> = entries.iter().filter(|e| e.finding.is_some()).collect();

    if findings.is_empty() {
        println!("No findings found.");
        return Ok(());
    }

    println!("📋 Findings ({}):", findings.len());
    for entry in &findings {
        let finding = entry.finding.as_deref().unwrap_or("");
        println!("   [#{}] {} — {}", entry.turn, entry.session, finding.chars().take(100).collect::<String>());
    }

    Ok(())
}

async fn cmd_precheck(config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("🔍 Mycelium Health Check");
    println!();

    let db_path = config.root_dir.join("mycelium.db");
    if db_path.exists() {
        match mycelium_core::Storage::open(db_path.clone()) {
            Ok(storage) => {
                println!("✅ Database: {} ({} KB, v{})", db_path.display(), storage.db_size().unwrap_or(0) / 1024, storage.schema_version().unwrap_or(0));
                println!("✅ Entries: {}", storage.count_entries().unwrap_or(0));
                println!("✅ Sessions: {}", storage.count_sessions().unwrap_or(0));
            }
            Err(e) => println!("❌ Database: {} — {}", db_path.display(), e),
        }
    } else {
        println!("❌ Database not found at {}", db_path.display());
    }

    let log_path = config.root_dir.join("log.jsonl");
    if log_path.exists() {
        println!("✅ Legacy log: {} ({} KB)", log_path.display(), std::fs::metadata(&log_path).map(|m| m.len() / 1024).unwrap_or(0));
    } else {
        println!("ℹ️  Legacy log: not found (migrated?)", );
    }

    // Check if server port is in use
    if std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", config.server_port).parse().unwrap(),
        std::time::Duration::from_secs(1),
    ).is_ok() {
        println!("✅ Server: running on :{}", config.server_port);
    } else {
        println!("ℹ️  Server: not running on :{}", config.server_port);
    }

    Ok(())
}

async fn cmd_migrate(config: &MyceliumConfig) -> anyhow::Result<()> {
    let log_path = config.root_dir.join("log.jsonl");
    if !log_path.exists() {
        println!("❌ No log.jsonl found at {}", log_path.display());
        println!("   Run `mycelium config` to see current root.");
        return Ok(());
    }

    let db_path = config.root_dir.join("mycelium.db");
    println!("📦 Migrating {} → {}", log_path.display(), db_path.display());

    let storage = mycelium_core::Storage::open(db_path.clone())?;

    let content = std::fs::read_to_string(&log_path)?;
    let mut count = 0i64;
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(raw) = serde_json::from_str::<serde_json::Value>(line) {
            let turn = raw.get("turn").and_then(|v| v.as_i64()).unwrap_or(count + 1);
            let session = raw.get("session").and_then(|v| v.as_str()).unwrap_or("migrated").to_string();
            let tier = raw.get("tier").and_then(|v| v.as_str()).unwrap_or("ephemeral");
            let entry_type = raw.get("entry_type").or_else(|| raw.get("type")).and_then(|v| v.as_str()).unwrap_or("conversation");
            let ts_str = raw.get("ts").and_then(|v| v.as_str()).unwrap_or("");
            let user = raw.get("user").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let assistant = raw.get("assistant").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let prev_hash = raw.get("prev_hash").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let hash = raw.get("hash").and_then(|v| v.as_str()).unwrap_or("").to_string();

            let entities: Vec<String> = raw.get("entities")
                .and_then(|v| serde_json::from_value(v.clone()).ok())
                .unwrap_or_default();

            let ts = chrono::DateTime::parse_from_rfc3339(ts_str)
                .map(|d| d.to_utc())
                .unwrap_or_else(|_| chrono::Utc::now());

            let entry = mycelium_core::Entry {
                turn,
                tier: mycelium_core::Tier::from_str(tier).unwrap_or(mycelium_core::Tier::Ephemeral),
                entry_type: mycelium_core::EntryType::from_str(entry_type).unwrap_or(mycelium_core::EntryType::Conversation),
                session,
                ts,
                user,
                assistant,
                entities,
                prev_hash,
                hash,
                finding: None,
                verdict: None,
                annotation: None,
            };

            if let Err(e) = storage.append_entry(&entry) {
                eprintln!("   ⚠️  Error on turn {}: {}", entry.turn, e);
            }
            count = entry.turn;
        }
    }

    println!("✅ Migration complete — {} entries imported", count);
    Ok(())
}

async fn cmd_fact(config: &MyceliumConfig, command: &FactCommands) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    match command {
        FactCommands::List => {
            let facts = storage.search_facts("", 50)?;
            if facts.is_empty() {
                println!("No facts found.");
                return Ok(());
            }
            for fact in &facts {
                println!(
                    "   [{}] {} | {} | {} (conf: {:.2})",
                    fact.id.unwrap_or(0),
                    fact.entity,
                    fact.attribute,
                    fact.value,
                    fact.confidence,
                );
            }
        }
        FactCommands::Search { query } => {
            let facts = storage.search_facts(query, 20)?;
            if facts.is_empty() {
                println!("No facts matching: {}", query);
                return Ok(());
            }
            for fact in &facts {
                println!(
                    "   [{}] {} | {} | {} (conf: {:.2})",
                    fact.id.unwrap_or(0),
                    fact.entity,
                    fact.attribute,
                    fact.value,
                    fact.confidence,
                );
            }
        }
        FactCommands::Add { entity, attribute, value } => {
            let fact = mycelium_core::MemoryFact {
                id: None,
                entity: entity.clone(),
                attribute: attribute.clone(),
                value: value.clone(),
                fact_type: "fact".to_string(),
                confidence: 0.8,
                source_session: None,
                created_at: chrono::Utc::now(),
                updated_at: chrono::Utc::now(),
            };
            let id = storage.upsert_fact(&fact)?;
            println!("✅ Added fact #{}: {} | {} | {}", id, entity, attribute, value);
        }
        FactCommands::Delete { id } => {
            match storage.delete_fact(*id)? {
                true => println!("✅ Deleted fact #{}", id),
                false => println!("❌ Fact #{} not found", id),
            }
        }
    }

    Ok(())
}

async fn cmd_snapshot(config: &MyceliumConfig, command: &SnapshotCommands) -> anyhow::Result<()> {
    let db_path = config.root_dir.join("mycelium.db");
    let storage = mycelium_core::Storage::open(db_path)?;

    match command {
        SnapshotCommands::List => {
            let snapshots = storage.list_snapshots(20)?;
            if snapshots.is_empty() {
                println!("No snapshots found.");
                return Ok(());
            }
            for s in &snapshots {
                println!("   [{}] {} — {}", s.id, s.session_id, s.summary.chars().take(60).collect::<String>());
            }
        }
        SnapshotCommands::Session { session } => {
            let snapshots = storage.snapshots_for_session(session, 10)?;
            if snapshots.is_empty() {
                println!("No snapshots for session: {}", session);
                return Ok(());
            }
            for s in &snapshots {
                println!("   [{}] {} at {}", s.id, s.session_id, s.created_at);
            }
        }
        SnapshotCommands::Create { session, summary } => {
            let id = storage.create_snapshot(session, summary, &[], &[], &[], &[])?;
            println!("✅ Created snapshot #{} for session {}", id, session);
        }
    }

    Ok(())
}

/// Handle brain management commands.
fn cmd_brain(config: &MyceliumConfig, command: &BrainCommands) -> anyhow::Result<()> {
    match command {
        BrainCommands::Annotated => {
            let db_path = config.root_dir.join("mycelium.db");
            let conn = rusqlite::Connection::open(&db_path)?;
            mycelium_core::brain::create_tables(&conn)?;

            // Find entries with annotations
            let mut stmt = conn.prepare(
                "SELECT turn, session, user, assistant, annotation FROM entries
                 WHERE annotation IS NOT NULL AND annotation != ''
                 ORDER BY turn ASC"
            )?;
            let entries: Vec<(i64, String, String, String, String)> = stmt.query_map([], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                ))
            })?.filter_map(|r| r.ok()).collect();

            if entries.is_empty() {
                println!("No annotated entries found.");
                return Ok(());
            }

            println!("Processing {} annotated entries...", entries.len());
            for (turn, session, user_msg, asst_msg, ann_json) in &entries {
                let text = format!("{} {}", user_msg, asst_msg);
                let annotation: mycelium_core::types::MemoryAnnotation =
                    serde_json::from_str(ann_json)?;

                mycelium_core::brain::consolidate_entry(
                    &conn, *turn, session, &text, Some(&annotation),
                )?;

                let phrase_count = annotation.phrases.len();
                let entity_count = annotation.entities.len();
                println!("  Turn {}: {} phrases, {} entities — consolidated ✓", turn, phrase_count, entity_count);
            }

            // Show results
            let status = mycelium_core::brain::brain_status(&conn)?;
            println!();
            println!("=== Brain Status ===");
            println!("Atoms:    {}", status.atom_count);
            println!("Edges:    {}", status.edge_count);
            println!("Entities: {}", count_entities(&conn));
            println!("Pending:  {}", status.pending_count);
            println!();
            println!("✅ Annotation pipeline verified — {} entries processed", entries.len());

            Ok(())
        }
        BrainCommands::Backfill { order } => {
            let db_path = config.root_dir.join("mycelium.db");
            let conn = rusqlite::Connection::open(&db_path)?;
            mycelium_core::brain::create_tables(&conn)?;

            let order_clause = match order.as_str() {
                "oldest-first" => "ORDER BY turn ASC",
                _ => "ORDER BY turn DESC",
            };

            let sql = format!(
                "INSERT OR IGNORE INTO pending_brain_work (turn, created_at)
                 SELECT turn, datetime('now') FROM entries {}",
                order_clause
            );
            let inserted = conn.execute(&sql, [])?;
            println!("Enqueued {} entries for brain processing (order: {})", inserted, order);

            let total: i64 = conn.query_row("SELECT COUNT(*) FROM pending_brain_work", [], |row| row.get(0))?;
            println!("Total pending: {}", total);
            println!("Run 'mycelium daemon' to start processing, or restart the server.");

            Ok(())
        }
        BrainCommands::StopWords => {
            let db_path = config.root_dir.join("mycelium.db");
            let conn = rusqlite::Connection::open(&db_path)?;
            mycelium_core::brain::create_tables(&conn)?;

            // Build atom frequency map
            let mut stmt = conn.prepare("SELECT phrase, ref_count FROM atoms")?;
            let atom_counts: std::collections::HashMap<String, usize> = stmt
                .query_map([], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)? as usize))
                })?
                .filter_map(|r| r.ok())
                .collect();

            // Count entries with positions (brain-processed entries)
            let total_entries: usize = conn
                .query_row("SELECT COUNT(DISTINCT turn) FROM positions", [], |row| row.get(0))
                .unwrap_or(0);

            println!("Analyzing {} atoms across {} entries...", atom_counts.len(), total_entries);
            let before: i64 = conn.query_row("SELECT COUNT(*) FROM brain_stop_words", [], |row| row.get(0)).unwrap_or(0);
            println!("Existing stop words: {}", before);

            mycelium_core::brain::detect_stop_words(&conn, &atom_counts, total_entries)?;

            let after: i64 = conn.query_row("SELECT COUNT(*) FROM brain_stop_words", [], |row| row.get(0)).unwrap_or(0);
            let added = after - before;
            println!("Added {} new stop words (total: {})", added, after);

            if added > 0 {
                let mut stmt = conn.prepare("SELECT phrase, frequency FROM brain_stop_words ORDER BY frequency DESC")?;
                println!("---");
                for row in stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?)))? {
                    let (phrase, freq) = row?;
                    println!("  '{}' (in {:.0}% of entries)", phrase, freq * 100.0);
                }
            }

            Ok(())
        }
    }
}

/// Count registered entities in the entity_registry table.
fn count_entities(conn: &rusqlite::Connection) -> i64 {
    conn.query_row("SELECT COUNT(*) FROM entity_registry", [], |row| row.get(0)).unwrap_or(0)
}

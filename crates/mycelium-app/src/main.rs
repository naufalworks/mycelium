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
    /// Memory fact operations
    Fact {
        #[command(subcommand)]
        command: FactCommands,
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
        Commands::Fact { command } => cmd_fact(&config, command).await?,
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
    let entries = storage.search_entries(query, 20)?;

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

async fn cmd_verify(_config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("⚠️  Hash chain verification not yet implemented");
    println!("   (Coming in Phase 2)");
    Ok(())
}

async fn cmd_resume(_config: &MyceliumConfig, _session: Option<&str>) -> anyhow::Result<()> {
    println!("⚠️  Resume not yet implemented");
    println!("   (Coming in Phase 2)");
    Ok(())
}

async fn cmd_start(_config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("⚠️  Start daemon not yet implemented");
    println!("   (Coming in Phase 3)");
    Ok(())
}

async fn cmd_stop(_config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("⚠️  Stop not yet implemented");
    Ok(())
}

async fn cmd_backup(_config: &MyceliumConfig, _dir: &str) -> anyhow::Result<()> {
    println!("⚠️  Backup not yet implemented");
    println!("   (Coming in Phase 2)");
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

async fn cmd_migrate(_config: &MyceliumConfig) -> anyhow::Result<()> {
    println!("⚠️  Migration from existing Go/Python data not yet implemented");
    println!("   (Coming in Phase 7)");
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
        FactCommands::Delete { id: _id } => {
            println!("⚠️  Fact delete not yet implemented");
        }
    }

    Ok(())
}

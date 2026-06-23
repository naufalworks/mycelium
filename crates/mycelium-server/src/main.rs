//! Mycelium Server binary — starts the Axum HTTP server.

use mycelium_core::MyceliumConfig;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config = MyceliumConfig::default();
    println!("🧬 Mycelium Server starting on 127.0.0.1:{}", config.server_port);
    println!("   Root: {}", config.root_dir.display());

    mycelium_server::serve(config).await?;

    Ok(())
}

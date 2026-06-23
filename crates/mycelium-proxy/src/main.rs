//! Mycelium Proxy binary — starts the reverse proxy server.

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
    mycelium_proxy::serve(config).await?;

    Ok(())
}

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

    let mut config = MyceliumConfig::default();
    if let Ok(port) = std::env::var("MYCELIUM_PROXY_PORT") {
        if let Ok(p) = port.parse::<u16>() {
            config.proxy_port = p;
        }
    }
    if let Ok(url) = std::env::var("MYCELIUM_UPSTREAM_URL") {
        config.upstream_url = url;
    }
    mycelium_proxy::serve(config).await?;

    Ok(())
}

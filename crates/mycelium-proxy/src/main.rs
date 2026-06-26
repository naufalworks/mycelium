//! Mycelium Proxy binary — starts the reverse proxy server.
//!
//! # Environment Variables
//!
//! | Variable | Default | Description |
//! |----------|---------|-------------|
//! | `MYCELIUM_PROXY_PORT` | `8443` | Port to listen on |
//! | `MYCELIUM_UPSTREAM_URL` | `http://localhost:8080` | Upstream LLM API URL |
//! | `MYCELIUM_UPSTREAM_API_KEY` | `""` | API key for upstream LLM |
//! | `MYCELIUM_MODEL` | `claude-sonnet-4-20250514` | Model name for LLM calls (used by recall query parser + synthesizer) |
//! | `MYCELIUM_RECALL_MODE` | `graph` | Memory recall mode: `graph` (brain graph traversal, default) or `legacy` (old search_facts) |
//! | `MYCELIUM_MODEL` | `claude-sonnet-4-20250514` | Model for recall LLM calls (query parser + context synthesis) |
//! | `MYCELIUM_LLM_URL` | `{upstream_url}/v1/messages` | LLM API endpoint for recall calls (defaults to upstream Anthropic endpoint) |
//! | `MYCELIUM_CORTEX_ENABLED` | `true` | Enable Cortex intent-to-skill matching |
//! | `MYCELIUM_CORTEX_SKILLS_PATH` | `{root_dir}/skills.yaml` | Path to skills registry |
//!
//! # Recall Mode
//!
//! - **graph** (default): Uses the Hebbian Crystal Brain graph to find relevant memories via
//!   atom matching and cluster traversal. The query parser and context synthesizer use the
//!   model specified by `MYCELIUM_MODEL` for LLM-based processing (~200-5000 tokens per recall).
//! - **legacy**: Falls back to the old `search_facts` SQL LIKE query on the memory_facts table.
//!   This mode is deprecated and will be removed in a future release.

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

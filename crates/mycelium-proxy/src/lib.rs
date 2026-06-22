//! Mycelium Proxy — Reverse proxy for intercepting LLM API calls.
//!
//! Intercepts Anthropic API calls to inject memory context and log conversations.
//! Replaces the existing Go proxy.

pub fn placeholder() -> &'static str {
    "mycelium-proxy"
}

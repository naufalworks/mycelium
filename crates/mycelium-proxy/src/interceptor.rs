//! Request interceptor module.
//!
//! Handles parsing Anthropic API requests, injecting memory context,
//! and logging exchanges. Currently a stub — full implementation in Phase 3.

/// Check if a request path should be intercepted (e.g., /v1/messages).
pub fn should_intercept(_path: &str) -> bool {
    // TODO: Full interception logic
    false
}

/// Process an intercepted request body — inject memory context.
pub fn process_request(
    _body: &[u8],
    _storage: &mycelium_core::Storage,
) -> Vec<u8> {
    // TODO: Inject <mycelium-facts> from memory context
    Vec::new()
}

/// Log an exchange to the storage engine after proxying.
pub fn log_exchange(
    _storage: &mycelium_core::Storage,
    _turn: i64,
    _session: &str,
    _user: &str,
    _assistant: &str,
) {
    // TODO: Create Entry and append to storage
}

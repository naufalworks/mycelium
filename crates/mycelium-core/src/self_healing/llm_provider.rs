use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde_json::Value;

/// Models in order of preference.
const MODELS: &[&str] = &["kimi-k2.7", "minimax-m3"];

/// Configuration for the LLM provider.
pub struct LLMConfig {
    pub endpoint: String,
    pub timeout: Duration,
    pub max_retries: u32,
    pub retry_backoff: Duration,
}

impl Default for LLMConfig {
    fn default() -> Self {
        Self {
            endpoint: "http://127.0.0.1:9099".into(),
            timeout: Duration::from_secs(60),
            max_retries: 3,
            retry_backoff: Duration::from_secs(2),
        }
    }
}

/// Circuit breaker for LLM provider health.
///
/// Tracks consecutive failures and prevents requests when the threshold
/// is exceeded, allowing a cooldown period before retrying.
pub struct CircuitBreaker {
    consecutive_failures: AtomicU32,
    last_failure: Mutex<Instant>,
    failure_threshold: u32,
    cooldown: Duration,
}

impl CircuitBreaker {
    pub fn new(failure_threshold: u32, cooldown: Duration) -> Self {
        Self {
            consecutive_failures: AtomicU32::new(0),
            last_failure: Mutex::new(Instant::now()),
            failure_threshold,
            cooldown,
        }
    }

    /// Returns `true` if the circuit is closed (allowing requests)
    /// or half-open (cooldown has elapsed).
    pub fn is_allowed(&self) -> bool {
        let failures = self.consecutive_failures.load(Ordering::Relaxed);
        if failures < self.failure_threshold {
            return true;
        }
        let elapsed = self.last_failure.lock().elapsed();
        elapsed >= self.cooldown
    }

    /// Record a successful call — resets the failure count.
    pub fn record_success(&self) {
        self.consecutive_failures.store(0, Ordering::Relaxed);
    }

    /// Record a failed call — increments the failure count and
    /// updates the last-failure timestamp.
    pub fn record_failure(&self) {
        self.consecutive_failures.fetch_add(1, Ordering::Relaxed);
        *self.last_failure.lock() = Instant::now();
    }
}

/// LLM provider with retry, model fallback, and circuit breaker.
///
/// Communicates with an OpenAI-compatible endpoint.  Tries `MODELS[0]`
/// first, falling back to `MODELS[1]` after exhausting retries.
pub struct LLMProvider {
    config: LLMConfig,
    client: reqwest::Client,
    circuit_breaker: CircuitBreaker,
}

impl LLMProvider {
    /// Create a new `LLMProvider` with the given config.
    ///
    /// The circuit breaker is initialised with a threshold of 3 failures
    /// and a 30-second cooldown.
    pub fn new(config: LLMConfig) -> Self {
        let timeout = config.timeout;
        let client = reqwest::Client::builder()
            .timeout(timeout)
            .build()
            .expect("valid reqwest client");
        let circuit_breaker = CircuitBreaker::new(3, Duration::from_secs(30));
        Self {
            config,
            client,
            circuit_breaker,
        }
    }

    /// Send a chat request to the LLM with optional tools.
    ///
    /// Tries `MODELS[0]` first with up to `max_retries` attempts,
    /// then falls back to `MODELS[1]` if all retries are exhausted.
    /// Returns the full JSON response body on success.
    pub async fn chat(
        &self,
        system: &str,
        messages: &[Value],
        tools: &[Value],
    ) -> anyhow::Result<Value> {
        if !self.circuit_breaker.is_allowed() {
            return Err(anyhow::anyhow!("circuit breaker open"));
        }

        let mut last_error = anyhow::anyhow!("all models failed");
        for model in MODELS {
            for attempt in 0..self.config.max_retries {
                match self.try_call(model, system, messages, tools).await {
                    Ok(response) => {
                        self.circuit_breaker.record_success();
                        return Ok(response);
                    }
                    Err(e) => {
                        tracing::warn!(
                            "LLM call failed (model={model}, attempt={attempt}): {e}"
                        );
                        last_error = e;
                        if attempt + 1 < self.config.max_retries {
                            tokio::time::sleep(self.config.retry_backoff).await;
                        }
                    }
                }
            }
        }

        self.circuit_breaker.record_failure();
        Err(last_error)
    }

    /// Execute a single HTTP request to the LLM endpoint.
    async fn try_call(
        &self,
        model: &str,
        system: &str,
        messages: &[Value],
        tools: &[Value],
    ) -> anyhow::Result<Value> {
        let url = format!("{}/v1/chat/completions", self.config.endpoint);

        let body = serde_json::json!({
            "model": model,
            "messages": std::iter::once(serde_json::json!({"role": "system", "content": system}))
                .chain(messages.iter().cloned())
                .collect::<Vec<Value>>(),
            "tools": tools,
        });

        let resp = self.client.post(&url).json(&body).send().await?;
        let status = resp.status();
        let text = resp.text().await?;

        if !status.is_success() {
            return Err(anyhow::anyhow!(
                "LLM returned {}: {}",
                status,
                text.chars().take(200).collect::<String>()
            ));
        }

        let json: Value = serde_json::from_str(&text)?;
        Ok(json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn circuit_breaker_starts_closed() {
        let cb = CircuitBreaker::new(3, Duration::from_secs(30));
        assert!(cb.is_allowed());
    }

    #[test]
    fn circuit_breaker_opens_after_threshold() {
        let cb = CircuitBreaker::new(3, Duration::from_secs(30));
        for _ in 0..3 {
            cb.record_failure();
        }
        assert!(!cb.is_allowed());
    }

    #[test]
    fn circuit_breaker_resets_on_success() {
        let cb = CircuitBreaker::new(3, Duration::from_secs(30));
        for _ in 0..3 {
            cb.record_failure();
        }
        cb.record_success();
        assert!(cb.is_allowed());
    }

    #[test]
    fn config_defaults() {
        let cfg = LLMConfig::default();
        assert_eq!(cfg.endpoint, "http://127.0.0.1:9099");
        assert_eq!(cfg.max_retries, 3);
    }
}

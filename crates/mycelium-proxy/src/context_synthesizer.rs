//! Context synthesis — turns graph traversal results into a structured memory block.
//!
//! Two modes:
//! - Text synthesis: produces <mycelium-context> block for proxy injection
//! - Direct synthesis: returns structured JSON for CLI/MCP/API consumers

use mycelium_core::RecallResult;

/// Build a context block prompt for the LLM.
pub fn build_synthesis_prompt(result: &RecallResult, budget: usize) -> String {
    let mut clusters_text = String::new();
    for (i, cluster) in result.clusters.iter().enumerate() {
        clusters_text.push_str(&format!("Cluster {}: seed=\"{}\"\n", i + 1, cluster.seed_phrase));
        if let Some((first, last, count)) = cluster.temporal {
            clusters_text.push_str(&format!("  Temporal: first_seen={}, last_seen={}, {} mentions\n", first, last, count));
        }
        for (neighbor, weight, _importance) in &cluster.neighbors {
            clusters_text.push_str(&format!("  -> \"{}\" (weight: {:.2})\n", neighbor, weight));
        }
    }

    format!(
        r#"You are a memory synthesis system. Given the following atom clusters from a brain graph, produce a clear, readable <mycelium-context> block.

The context block should:
1. Group related atoms into sections with [bracketed headers]
2. Use bullet points for each memory item
3. Include temporal context where available
4. Be concise — use at most {} tokens
5. Only include information present in the input data — do not fabricate

Input clusters:
{}

Output ONLY the <mycelium-context> block, nothing else."#,
        budget, clusters_text
    )
}

/// Build a human-readable context block from traversal result (non-LLM fallback).
pub fn build_fallback_context(result: &RecallResult) -> String {
    if result.clusters.is_empty() {
        return "<mycelium-context>\nNo relevant memories found.\n</mycelium-context>".to_string();
    }

    let mut ctx = String::from("<mycelium-context>\n");
    for cluster in &result.clusters {
        ctx.push_str(&format!("\n[{}]\n", cluster.seed_phrase));
        if let Some((_first, last, _count)) = cluster.temporal {
            ctx.push_str(&format!("  Last seen: turn {}\n", last));
        }
        for (neighbor, weight, _importance) in &cluster.neighbors {
            ctx.push_str(&format!("  - {} (relevance: {:.2})\n", neighbor, weight));
        }
    }
    ctx.push_str("</mycelium-context>");
    ctx
}

#[cfg(test)]
mod tests {
    use super::*;
    use mycelium_core::{AtomCluster, RecallIntent, RecallQuery, RecallResult};

    fn sample_result() -> RecallResult {
        RecallResult {
            query: RecallQuery {
                atoms: vec!["test".to_string()],
                intent: RecallIntent::Relational,
                temporal_hint: None,
            },
            clusters: vec![
                AtomCluster {
                    seed_id: 1,
                    seed_phrase: "change secret".to_string(),
                    neighbors: vec![
                        ("server config".to_string(), 0.9, 1.0),
                        ("env file".to_string(), 0.7, 1.0),
                    ],
                    temporal: Some((100, 200, 5)),
                },
                AtomCluster {
                    seed_id: 2,
                    seed_phrase: "restart proxy".to_string(),
                    neighbors: vec![
                        ("nginx restart".to_string(), 0.85, 1.0),
                    ],
                    temporal: Some((150, 201, 3)),
                },
            ],
            total_clusters: 2,
            traversal_time_ms: 1.2,
        }
    }

    #[test]
    fn test_build_fallback_context_empty() {
        let result = RecallResult {
            query: RecallQuery {
                atoms: vec![],
                intent: RecallIntent::Relational,
                temporal_hint: None,
            },
            clusters: vec![],
            total_clusters: 0,
            traversal_time_ms: 0.0,
        };
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("No relevant memories found"));
    }

    #[test]
    fn test_build_fallback_context_with_clusters() {
        let result = sample_result();
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("change secret"));
        assert!(ctx.contains("server config"));
        assert!(ctx.contains("restart proxy"));
        assert!(ctx.contains("nginx restart"));
        assert!(ctx.starts_with("<mycelium-context>"));
        assert!(ctx.ends_with("</mycelium-context>"));
    }

    #[test]
    fn test_build_synthesis_prompt_contains_clusters() {
        let result = sample_result();
        let prompt = build_synthesis_prompt(&result, 1000);
        assert!(prompt.contains("change secret"));
        assert!(prompt.contains("server config"));
        assert!(prompt.contains("restart proxy"));
        assert!(prompt.contains("1000")); // budget
    }

    #[test]
    fn test_build_fallback_context_no_temporal() {
        let result = RecallResult {
            query: RecallQuery {
                atoms: vec!["simple".to_string()],
                intent: RecallIntent::Factual,
                temporal_hint: None,
            },
            clusters: vec![AtomCluster {
                seed_id: 1,
                seed_phrase: "simple fact".to_string(),
                neighbors: vec![],
                temporal: None,
            }],
            total_clusters: 1,
            traversal_time_ms: 0.5,
        };
        let ctx = build_fallback_context(&result);
        assert!(ctx.contains("simple fact"));
        assert!(!ctx.contains("Last seen"));
    }
}

use mycelium_proxy::cortex::{load_skills, match_skill, build_cortex_block, Skill, SkillStep};
use std::path::Path;

#[test]
fn test_cortex_e2e_with_atoms() {
    // Simulate what the proxy does: atoms from query parser → match → block
    // Look for skills.yaml relative to CARGO_MANIFEST_DIR (crates/mycelium-proxy/)
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let skills_path = manifest_dir.join("../../skills.yaml");
    if !skills_path.exists() {
        // If skills.yaml doesn't exist yet, skip
        return;
    }
    let skills = load_skills(&skills_path);
    assert!(!skills.is_empty(), "Should load skills from skills.yaml");

    let atoms = vec!["bug".to_string(), "debug".to_string()];
    let matched = match_skill(&atoms, &skills, 0.3);
    assert!(matched.is_some(), "Should match 'debug' or 'bug' to a skill");

    let block = build_cortex_block(&matched.unwrap());
    assert!(block.starts_with("<cortex-skill"));
    assert!(block.ends_with("suggestion"));
}

fn sample_skills() -> Vec<Skill> {
    vec![Skill {
        name: "diagnose-bugs".to_string(),
        description: "Find root cause".to_string(),
        triggers: vec!["bug".to_string(), "debug".to_string()],
        intent: "debugging".to_string(),
        provider: "deer-flow".to_string(),
        steps: vec![
            SkillStep { label: "Reproduce".to_string(), action: "Reproduce".to_string() },
        ],
    }]
}

#[test]
fn test_cortex_block_contains_instruction() {
    let skills = sample_skills();
    let atoms = vec!["debug".to_string()];
    let matched = match_skill(&atoms, &skills, 0.3).unwrap();
    let block = build_cortex_block(&matched);
    assert!(block.contains("<cortex-skill"));
    assert!(block.contains("How to use Cortex"));
    assert!(block.contains("diagnose-bugs"));
}

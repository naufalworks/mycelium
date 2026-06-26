//! Cortex — intent-to-skill matcher for Mycelium's proxy pipeline.
//!
//! Loads a YAML skills registry at startup, then matches user query atoms
//! against skill triggers using pure string scoring. Sub-millisecond, no LLM calls.

use serde::{Deserialize, Serialize};
use std::path::Path;

/// A single skill definition from skills.yaml.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub triggers: Vec<String>,
    pub intent: String,
    pub provider: String,
    pub steps: Vec<SkillStep>,
}

/// A single step within a skill workflow.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillStep {
    pub label: String,
    pub action: String,
}

/// A matched skill with confidence score.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillMatch {
    pub skill: Skill,
    pub confidence: f64,
}

/// Top-level config loaded from skills.yaml.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillsConfig {
    pub skills: Vec<Skill>,
}

/// Load skills from a YAML file. Returns empty vec on error (graceful fallback).
pub fn load_skills(path: &Path) -> Vec<Skill> {
    std::fs::File::open(path)
        .ok()
        .and_then(|f| serde_yaml::from_reader::<_, SkillsConfig>(f).ok())
        .map(|cfg| cfg.skills)
        .unwrap_or_default()
}

/// Match query atoms against skill triggers.
/// Returns the best skill match with confidence score, or None if below threshold.
pub fn match_skill(atoms: &[String], skills: &[Skill], threshold: f64) -> Option<SkillMatch> {
    if atoms.is_empty() || skills.is_empty() {
        return None;
    }

    let mut best: Option<SkillMatch> = None;

    for skill in skills {
        let matching = skill
            .triggers
            .iter()
            .filter(|trigger| atoms.iter().any(|atom| {
                let atom_lower = atom.to_lowercase();
                let trigger_lower = trigger.to_lowercase();
                atom_lower.contains(&trigger_lower) || trigger_lower.contains(&atom_lower)
            }))
            .count();

        if matching == 0 {
            continue;
        }

        let score = matching as f64 / skill.triggers.len() as f64;

        if score >= threshold {
            match &best {
                Some(b) if score > b.confidence => {
                    best = Some(SkillMatch {
                        skill: skill.clone(),
                        confidence: score,
                    });
                }
                None => {
                    best = Some(SkillMatch {
                        skill: skill.clone(),
                        confidence: score,
                    });
                }
                _ => {}
            }
        }
    }

    best
}

/// Build the <cortex-skill> XML block for a matched skill.
pub fn build_cortex_block(matched: &SkillMatch) -> String {
    let mut xml = format!(
        r#"<cortex-skill name="{}" confidence="{:.2}">
  <description>{}</description>
  <steps>"#,
        matched.skill.name, matched.confidence, matched.skill.description
    );
    for (i, step) in matched.skill.steps.iter().enumerate() {
        xml.push_str(&format!(
            r#"
    <step>{}. {} — {}</step>"#,
            i + 1,
            step.label,
            step.action
        ));
    }
    xml.push_str(
        r#"
  </steps>
</cortex-skill>

--- How to use Cortex skill suggestions ---
When a <cortex-skill> is present:
- If the user's request matches the skill's purpose, offer to use it
- Ask the user "Want me to run through this workflow?" before proceeding
- If the skill is not relevant, respond normally and ignore the suggestion
- Confidence > 0.7 = strong suggestion, < 0.4 = weak suggestion"#,
    );
    xml
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_skills() -> Vec<Skill> {
        vec![
            Skill {
                name: "diagnose-bugs".to_string(),
                description: "Find root cause of bugs".to_string(),
                triggers: vec!["bug".to_string(), "debug".to_string(), "root cause".to_string(), "failing".to_string()],
                intent: "debugging".to_string(),
                provider: "deer-flow".to_string(),
                steps: vec![
                    SkillStep { label: "Reproduce".to_string(), action: "Reproduce the issue".to_string() },
                    SkillStep { label: "Fix".to_string(), action: "Apply fix".to_string() },
                ],
            },
            Skill {
                name: "new-feature".to_string(),
                description: "Add a new feature with TDD".to_string(),
                triggers: vec!["new feature".to_string(), "add".to_string(), "implement".to_string()],
                intent: "development".to_string(),
                provider: "deer-flow".to_string(),
                steps: vec![
                    SkillStep { label: "Design".to_string(), action: "Design approach".to_string() },
                    SkillStep { label: "Build".to_string(), action: "Implement with tests".to_string() },
                ],
            },
        ]
    }

    #[test]
    fn test_match_skill_matches_bug() {
        let skills = sample_skills();
        let atoms = vec!["bug".to_string(), "test".to_string(), "failing".to_string()];
        let result = match_skill(&atoms, &skills, 0.3);
        assert!(result.is_some());
        assert_eq!(result.unwrap().skill.name, "diagnose-bugs");
    }

    #[test]
    fn test_match_skill_matches_feature() {
        let skills = sample_skills();
        let atoms = vec!["implement".to_string(), "new feature".to_string()];
        let result = match_skill(&atoms, &skills, 0.3);
        assert!(result.is_some());
        assert_eq!(result.unwrap().skill.name, "new-feature");
    }

    #[test]
    fn test_match_skill_no_match() {
        let skills = sample_skills();
        let atoms = vec!["weather".to_string(), "coffee".to_string()];
        let result = match_skill(&atoms, &skills, 0.3);
        assert!(result.is_none());
    }

    #[test]
    fn test_match_skill_empty_atoms() {
        let skills = sample_skills();
        let atoms = vec![];
        let result = match_skill(&atoms, &skills, 0.3);
        assert!(result.is_none());
    }

    #[test]
    fn test_load_skills_nonexistent_path() {
        let skills = load_skills(Path::new("/tmp/nonexistent-skills.yaml"));
        assert!(skills.is_empty());
    }

    #[test]
    fn test_build_cortex_block_contains_xml() {
        let skills = sample_skills();
        let atoms = vec!["bug".to_string()];
        let matched = match_skill(&atoms, &skills, 0.3).unwrap();
        let block = build_cortex_block(&matched);
        assert!(block.contains("<cortex-skill"));
        assert!(block.contains("diagnose-bugs"));
        assert!(block.contains("</cortex-skill>"));
        assert!(block.contains("Reproduce"));
        assert!(block.contains("How to use Cortex"));
    }

    #[test]
    fn test_match_skill_confidence_threshold() {
        let skills = sample_skills();
        let atoms = vec!["failing".to_string()];
        // Only 1 of 4 triggers match -> score = 0.25, below 0.3 threshold
        let result = match_skill(&atoms, &skills, 0.3);
        assert!(result.is_none());
    }

    #[test]
    fn test_load_skills_invalid_yaml() {
        // Write invalid YAML to a temp file
        let path = std::env::temp_dir().join("cortex_test_bad.yaml");
        std::fs::write(&path, "not: valid: yaml: [[[").ok();
        let skills = load_skills(&path);
        assert!(skills.is_empty());
        std::fs::remove_file(&path).ok();
    }
}

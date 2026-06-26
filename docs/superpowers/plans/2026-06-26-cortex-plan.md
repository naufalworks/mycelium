# Cortex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intent-to-skill matcher (Cortex) inside mycelium-proxy that matches user query atoms against a skills registry and injects workflow suggestions into the LLM context.

**Architecture:** A new `cortex.rs` module in the proxy crate loads `skills.yaml` at startup, reuses the query parser's atom output to score skills by trigger overlap (pure string matching, sub-ms), and appends a `<cortex-skill>` block after the `<mycelium-context>` block during recall synthesis. The LLM sees it as an optional workflow suggestion and offers to use it naturally.

**Tech Stack:** Rust, serde, serde_yaml (new dep), existing query parser atoms, mycelium-proxy crate

## Global Constraints

- No new binary or separate service — lives inside mycelium-proxy
- No additional LLM calls — reuses query parser atoms
- Zero new dependencies beyond `serde_yaml` for config parsing
- Pure string matching only — no database, no embeddings
- When no skill matches, Cortex produces nothing — pipeline unchanged
- Cortex errors → return empty string (never crash the pipeline)
- Match confidence threshold: 0.3 minimum
- All config via env vars: `MYCELIUM_CORTEX_ENABLED`, `MYCELIUM_CORTEX_SKILLS_PATH`

---

### Task 1: Add serde_yaml dependency and create Cortex data types

**Files:**
- Modify: `crates/mycelium-proxy/Cargo.toml` — add serde_yaml dep
- Create: `crates/mycelium-proxy/src/cortex.rs` — module with types + matcher
- Modify: `crates/mycelium-proxy/src/lib.rs` — add `pub mod cortex;`

**Interfaces:**
- Consumes: `mycelium_core::RecallIntent` (already in core)
- Produces: `Skill`, `SkillMatch`, `SkillsConfig`, `load_skills()`, `match_skill()`

- [ ] **Step 1: Update Cargo.toml**

Add to `crates/mycelium-proxy/Cargo.toml`:

```toml
serde_yaml = "0.9"
```

- [ ] **Step 2: Write the failing tests for Cortex matcher**

Create `crates/mycelium-proxy/src/cortex.rs`:

```rust
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
                Some(ref b) if score > b.confidence => {
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
        let atoms = vec!["bug".to_string()];
        // Only 1 of 4 triggers match → score = 0.25, below 0.3 threshold
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cargo test -p mycelium-proxy --lib cortex
```

Expected: FAIL — errors about missing module/crate.

- [ ] **Step 4: Add serde_yaml to proxy Cargo.toml**

Edit `crates/mycelium-proxy/Cargo.toml`, add after the existing deps:

```toml
serde_yaml = "0.9"
```

- [ ] **Step 5: Register cortex module in lib.rs**

In `crates/mycelium-proxy/src/lib.rs`, add:

```rust
pub mod cortex;
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cargo test -p mycelium-proxy --lib cortex
```

Expected: OK — 8 tests pass.

- [ ] **Step 7: Commit**

```bash
git add crates/mycelium-proxy/Cargo.toml crates/mycelium-proxy/src/cortex.rs crates/mycelium-proxy/src/lib.rs
git commit -m "feat(cortex): add intent-to-skill matcher module

- cortex.rs with Skill, SkillMatch, SkillsConfig types
- match_skill() — scores atoms against skill triggers
- build_cortex_block() — generates <cortex-skill> XML
- load_skills() — parses YAML, errors return empty vec
- 8 unit tests covering match, no-match, threshold, XML output
- serde_yaml dependency added to proxy crate

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Create skills config file and wire Cortex into proxy pipeline

**Files:**
- Create: `skills.yaml` (in daemon runtime dir or project root)
- Modify: `crates/mycelium-proxy/src/interceptor.rs` — add Cortex call after synthesis
- Modify: `crates/mycelium-proxy/src/lib.rs` — add env vars + load skills to ProxyState

**Interfaces:**
- Consumes: `cortex::load_skills()`, `cortex::match_skill()`, `cortex::build_cortex_block()`
- Produces: Updated pipeline that appends `<cortex-skill>` after `<mycelium-context>`

- [ ] **Step 1: Write failing test for Cortex integration**

In `crates/mycelium-proxy/tests/cortex_integration_test.rs`:

```rust
use mycelium_proxy::cortex::{load_skills, match_skill, build_cortex_block, Skill, SkillStep};
use std::path::Path;

#[test]
fn test_cortex_e2e_with_atoms() {
    // Simulate what the proxy does: atoms from query parser → match → block
    let skills_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("skills.yaml");
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
```

Actually, the test will fail if skills.yaml doesn't exist. Let me make it self-contained:

```rust
use mycelium_proxy::cortex::{match_skill, build_cortex_block, Skill, SkillStep};

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
```

- [ ] **Step 2: Create skills.yaml**

Create `skills.yaml` in the project config directory:

```yaml
skills:
  - name: diagnose-bugs
    description: "Find root cause of bugs or test failures"
    triggers: ["bug", "debug", "root cause", "failing test", "broken"]
    intent: debugging
    provider: deer-flow
    steps:
      - label: Reproduce
        action: "Identify minimal steps to reproduce the issue"
      - label: Hypothesize
        action: "Form a hypothesis about the root cause"
      - label: Fix
        action: "Apply the fix"
      - label: Verify
        action: "Run regression tests to confirm"

  - name: new-feature
    description: "Plan and build a new feature with tests"
    triggers: ["new feature", "add", "implement", "build", "create"]
    intent: development
    provider: deer-flow
    steps:
      - label: Requirements
        action: "Clarify what the feature should do"
      - label: Design
        action: "Design the implementation approach"
      - label: Implement
        action: "Write code following TDD"
      - label: Review
        action: "Self-review and verify"

  - name: handoff
    description: "Compact conversation into a handoff document"
    triggers: ["handoff", "save context", "document session"]
    intent: documentation
    provider: deer-flow
    steps:
      - label: Extract
        action: "Extract key decisions and changes"
      - label: Format
        action: "Write a structured handoff document"

  - name: architecture-review
    description: "Scan codebase for design issues"
    triggers: ["architecture", "refactor", "design issue"]
    intent: maintenance
    provider: deer-flow
    steps:
      - label: Scan
        action: "Run architecture analysis"
      - label: Report
        action: "Present findings and recommendations"
```

- [ ] **Step 3: Add Cortex config to ProxyState and env vars**

In `crates/mycelium-proxy/src/lib.rs`, add to `ProxyState`:

```rust
pub struct ProxyState {
    // ... existing fields ...
    pub cortex_enabled: bool,
    pub cortex_skills: Vec<crate::cortex::Skill>,
}
```

In the `serve` function, add before ProxyState construction:

```rust
let cortex_enabled = std::env::var("MYCELIUM_CORTEX_ENABLED")
    .unwrap_or_else(|_| "true".to_string())
    == "true";
let mut cortex_skills = Vec::new();
if cortex_enabled {
    let skills_path = std::env::var("MYCELIUM_CORTEX_SKILLS_PATH")
        .unwrap_or_else(|_| format!("{}/skills.yaml", config.root_dir.display()));
    cortex_skills = crate::cortex::load_skills(&std::path::Path::new(&skills_path));
    if cortex_skills.is_empty() {
        tracing::warn!("Cortex enabled but no skills loaded from {}", skills_path);
    } else {
        tracing::info!("Cortex loaded {} skills", cortex_skills.len());
    }
}
```

Add to ProxyState construction:

```rust
cortex_enabled,
cortex_skills,
```

- [ ] **Step 4: Update interceptor to call Cortex**

In `crates/mycelium-proxy/src/interceptor.rs`, modify `run_recall_pipeline` to accept skills and call Cortex:

First, update the function signature in interceptor.rs:

```rust
pub async fn run_recall_pipeline(
    user_message: &str,
    storage: &Storage,
    llm_client: &reqwest::Client,
    api_url: &str,
    api_key: &str,
    model: &str,
    cortex_skills: &[crate::cortex::Skill],
    cortex_enabled: bool,
) -> String {
```

Then, after the synthesis step, add Cortex matching:

```rust
    // Step 4: Cortex — match intent to skill (if enabled)
    let mut context_block = if result.clusters.is_empty() {
        match result {
            // Was fallback already returned? Check the synthesis result.
            r if r.clusters.is_empty() => synthesis_result,
            _ => synthesis_result,
        }
    } else {
        synthesis_result
    };

    // Append Cortex skill suggestion if a skill matches
    if cortex_enabled && !cortex_skills.is_empty() {
        let atoms = &query.atoms;
        if !atoms.is_empty() {
            if let Some(matched) = crate::cortex::match_skill(atoms, cortex_skills, 0.3) {
                let cortex_block = crate::cortex::build_cortex_block(&matched);
                context_block.push_str("\n");
                context_block.push_str(&cortex_block);
                debug!("  Cortex matched skill: {} (confidence {:.2})", matched.skill.name, matched.confidence);
            }
        }
    }

    context_block
}
```

Wait, the code structure is trickier since I already refactored the pipeline. Let me read the exact current state.

Actually, the current pipeline returns `build_fallback_context(&result)` from the synthesis match. I need to restructure so the return value incorporates the Cortex block. Let me simplify:

```rust
    // Step 3: Context synthesis — try LLM first, fallback to template
    let elapsed = start.elapsed();
    debug!("  Recall pipeline complete in {:.2}ms — synthesizing context", elapsed.as_secs_f64() * 1000.0);
    let synthesis_prompt = crate::context_synthesizer::build_synthesis_prompt(&result, 10000);
    let mut context = match call_synthesizer(llm_client, api_url, api_key, model, &synthesis_prompt).await {
        Some(ctx) => ctx,
        None => build_fallback_context(&result),
    };

    // Step 4: Cortex — append skill suggestion if matched
    if cortex_enabled && !cortex_skills.is_empty() && !query.atoms.is_empty() {
        if let Some(matched) = crate::cortex::match_skill(&query.atoms, cortex_skills, 0.3) {
            context.push_str("\n");
            context.push_str(&crate::cortex::build_cortex_block(&matched));
            debug!("  Cortex matched: {} (conf={:.2})", matched.skill.name, matched.confidence);
        }
    }

    context
```

And add at the top of interceptor.rs:

```rust
use crate::cortex;
```

- [ ] **Step 5: Update the call site in lib.rs**

In `crates/mycelium-proxy/src/lib.rs`, update the `run_recall_pipeline` call:

```rust
            interceptor::run_recall_pipeline(
                &user_msg,
                &state.storage,
                &state.llm_client,
                &state.llm_url,
                &state.upstream_api_key,
                &state.model,
                &state.cortex_skills,
                state.cortex_enabled,
            )
            .await
```

- [ ] **Step 6: Run build**

```bash
cargo build -p mycelium-proxy
```

Expected: clean compile, zero warnings.

- [ ] **Step 7: Run all tests**

```bash
cargo test -p mycelium-proxy --lib
```

Expected: all tests pass (8 cortex + 4 context_synthesizer + 4 query_parser + existing).

- [ ] **Step 8: Commit**

```bash
git add crates/mycelium-proxy/src/interceptor.rs crates/mycelium-proxy/src/lib.rs skills.yaml
git commit -m "feat(cortex): wire cortex skill matcher into proxy pipeline

- Add cortex_enabled + cortex_skills to ProxyState
- Load skills.yaml via CORTEX_ENABLED/CORTEX_SKILLS_PATH env vars
- run_recall_pipeline appends <cortex-skill> block after synthesis
- Skills matched by atom overlap, sub-ms, zero extra LLM calls

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Documentation and config defaults

**Files:**
- Create: `skills.example.yaml` — template with docs
- Modify: `crates/mycelium-proxy/src/main.rs` — document new env vars
- Modify: `README.md` — add Cortex section

- [ ] **Step 1: Copy skills.yaml as example**

```bash
cp skills.yaml skills.example.yaml
```

- [ ] **Step 2: Update main.rs env var documentation**

Add to the doc comment in `crates/mycelium-proxy/src/main.rs`:

```rust
//! | `MYCELIUM_CORTEX_ENABLED` | `true` | Enable Cortex intent-to-skill matching |
//! | `MYCELIUM_CORTEX_SKILLS_PATH` | `{root_dir}/skills.yaml` | Path to skills registry |
```

- [ ] **Step 3: Update README**

Add a Cortex section to README.md:

```markdown
## Cortex — Intent Router

Cortex is an intent-to-skill matcher embedded in the proxy pipeline. It reads your
natural language query, matches it against a skills registry (`skills.yaml`), and
injects relevant workflow suggestions into the LLM's context alongside memory.

**How it works:**
1. Your question is parsed into atoms (same step as memory recall)
2. Cortex scores atoms against skill triggers (sub-ms, zero LLM calls)
3. If a skill matches above threshold (0.3), a `<cortex-skill>` block is appended
4. The LLM sees the suggestion and offers to run it — optional, natural

**Configuration:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MYCELIUM_CORTEX_ENABLED` | `true` | Enable/disable Cortex |
| `MYCELIUM_CORTEX_SKILLS_PATH` | `{root_dir}/skills.yaml` | Path to skills YAML |

Edit `skills.yaml` to add, remove, or customize skills.
```

- [ ] **Step 4: Commit**

```bash
git add crates/mycelium-proxy/src/main.rs README.md skills.example.yaml
git commit -m "docs: add Cortex documentation and config templates

- Cortex section in README with config table
- skills.example.yaml template
- env var docs in main.rs

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Spec Coverage Check

| Spec Section | Concrete Task |
|---|---|
| 3. Architecture (diagram) | Task 2 — Cortex call after synthesis |
| 4. Cortex Matcher algorithm | Task 1 — match_skill() function |
| 5. Skill Registry (YAML format) | Task 2 — skills.yaml creation |
| 6. Injected Context Format | Task 1 — build_cortex_block() XML |
| 7. Configuration env vars | Task 2 — CORTEX_ENABLED + SKILLS_PATH |
| 8. Files Changed | Tasks 1-3 cover all files |
| 9. Non-Goals | Task 1 — no extra LLM calls, pure string |
| 10. Quality & Safety | Task 1 — errors return empty, Task 2 — fallback threshold |

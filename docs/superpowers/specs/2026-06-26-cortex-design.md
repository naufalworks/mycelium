# Cortex — Intent Router Plugin for Mycelium

**Date:** 2026-06-26
**Project:** Mycelium Proxy + Cortex
**Status:** Design Draft
**Author:** Azfar Naufal

---

## 1. Problem

Mycelium recalls relevant memory context but has no awareness of *what to do next*. Skills and workflows (diagnose bugs, plan features, handoff sessions) exist in isolation — the user must remember which slash command to run, or leave Claude Code to open a different tool.

Development tools (Matt Pocock's `/diagnosing-bugs`, `/tdd`, `/handoff`) and orchestration engines (DeerFlow) are powerful but disconnected from the memory system. The user switches context, searches for the right command, and wastes the continuity that permanent memory provides.

## 2. Solution

**Cortex** — a lightweight intent-to-skill matcher embedded directly in Mycelium's proxy pipeline. It reads the user's natural language, matches it against a skill registry (`skills.yaml`), and injects relevant workflow suggestions into the system prompt alongside memory context.

The LLM sees the suggestion and naturally offers to run it. No commands, no separate tools, no MCP. Just intent → match → offer.

### Key Insight

The query parser already extracts atoms and intent from the user's message for memory recall. Cortex reuses the same atoms to match skills — zero additional LLM calls, zero additional latency.

---

## 3. Architecture

```
Claude Code → Mycelium proxy (:8443)
                  │
                  ├── 1. Query parser → atoms + intent
                  │
                  ├── 2. Graph traversal → memory clusters
                  │         └── (continues to synthesis)
                  │
                  ├── 3. Cortex matcher (parallel, sub-ms)
                  │         └── atoms → skills.yaml → best match
                  │
                  ├── 4. Context synthesizer → <mycelium-context>
                  │
                  ├── 5. Merge: <mycelium-context> + <cortex-skill>
                  │
                  └── 6. Forward to Meshgate → LLM
```

## 4. Cortex Matcher

### Algorithm

Pure string scoring — no database, no embeddings, no LLM calls:

1. Receive atoms from query parser output
2. For each skill in `skills.yaml`:
   - Count how many trigger phrases overlap with atoms
   - Score = `(matching_triggers / total_triggers_in_skill)` × `(avg_match_word_length)`  
3. If highest score > 0.3 → it's a candidate
4. Return the best match + its confidence score
5. If no match → Cortex produces nothing (pipeline continues normally)

### Performance

- Latency: **sub-millisecond** for any config size
- Dependencies: **zero** — pure string matching, no crates beyond std
- Runs in parallel with graph traversal (no additional wall-clock time)

---

## 5. Skill Registry (`skills.yaml`)

### Location

`<config_dir>/skills.yaml` (same directory as Mycelium's `skills.toml` or `config.yaml`).

### Format

```yaml
skills:
  - name: diagnose-bugs
    description: "Find root cause of bugs or test failures"
    triggers: ["bug", "debug", "root cause", "failing test", "unexpected", "broken"]
    intent: debugging
    provider: deer-flow
    steps:
      - label: "Reproduce"
        action: "Identify the minimal steps to reproduce the issue"
      - label: "Hypothesize"
        action: "Form a hypothesis about the root cause"
      - label: "Fix"
        action: "Apply the fix"
      - label: "Verify"
        action: "Run regression tests to confirm the fix works"

  - name: new-feature
    description: "Plan and build a new feature with tests"
    triggers: ["new feature", "add", "implement", "build", "create"]
    intent: development
    provider: deer-flow
    steps:
      - label: "Requirements"
        action: "Clarify what the feature should do"
      - label: "Design"
        action: "Design the implementation approach"
      - label: "Implement"
        action: "Write code following TDD"
      - label: "Review"
        action: "Self-review and verify"

  - name: handoff
    description: "Compact conversation into a handoff document"
    triggers: ["handoff", "save context", "document session", "summarize work"]
    intent: documentation
    provider: deer-flow
    steps:
      - label: "Extract"
        action: "Extract key decisions, changes, and rationale"
      - label: "Format"
        action: "Write a structured handoff document"

  - name: architecture-review
    description: "Scan codebase for design issues"
    triggers: ["architecture", "design issue", "refactor", "code quality"]
    intent: maintenance
    provider: deer-flow
    steps:
      - label: "Scan"
        action: "Run architecture analysis"
      - label: "Report"
        action: "Present findings and recommendations"
```

---

## 6. Injected Context Format

When a skill is matched, Cortex injects this after the `<mycelium-context>` block:

```
<cortex-skill name="diagnose-bugs" confidence="0.85">
  <description>Find root cause of bugs or test failures</description>
  <steps>
    <step>1. Reproduce — Identify the minimal steps to reproduce the issue</step>
    <step>2. Hypothesize — Form a hypothesis about the root cause</step>
    <step>3. Fix — Apply the fix</step>
    <step>4. Verify — Run regression tests to confirm the fix works</step>
  </steps>
</cortex-skill>

--- How to use Cortex skill suggestions ---
When a <cortex-skill> is present:
- If the user's request matches the skill's purpose, offer to use it
- Ask the user "Want me to run through this workflow?" before proceeding
- If the skill is not relevant, respond normally and ignore the suggestion
- High confidence (>0.7) = strong suggestion, low confidence (<0.4) = weak suggestion
```

When no skill is matched, nothing is injected. The pipeline is unchanged.

---

## 7. Configuration

Cortex is configured via environment variables in Mycelium's daemon plist:

| Variable | Default | Description |
|----------|---------|-------------|
| `MYCELIUM_CORTEX_ENABLED` | `true` | Enable/disable Cortex |
| `MYCELIUM_CORTEX_SKILLS_PATH` | `./skills.yaml` | Path to the skills registry |
| `MYCELIUM_CORTEX_THRESHOLD` | `0.3` | Minimum match confidence |

---

## 8. Files Changed

### New files

| File | Description | Est. Lines |
|------|-------------|------------|
| `crates/mycelium-proxy/src/cortex.rs` | Cortex matcher module | ~100 |
| `skills.example.yaml` | Template skills config | ~40 |

### Modified files

| File | Change | Est. Lines |
|------|--------|------------|
| `crates/mycelium-proxy/src/interceptor.rs` | Call Cortex after synthesis, merge context | ~15 |
| `crates/mycelium-proxy/src/lib.rs` | Load skills config, add to proxy state | ~10 |

---

## 9. Non-Goals

- **No new binary or service.** Cortex lives inside mycelium-proxy.
- **No database.** The skills registry is a flat YAML file.
- **No additional LLM calls.** Cortex reuses query parser output (atoms + intent).
- **No MCP integration.** Cortex runs entirely within the proxy pipeline.
- **No forced execution.** The LLM decides whether to offer the skill — always optional.
- **No change to existing recall behavior.** Graph traversal, synthesis, fallback all remain identical.

---

## 10. Quality & Safety

| Concern | Mitigation |
|---------|------------|
| False positives (wrong skill suggested) | Low confidence (<0.3) → no suggestion. LLM can ignore |
| Skill list grows large | Match is sub-ms for any config size |
| Injection degrades response quality | <50 tokens added only when skill matches |
| Pipeline breakage | Cortex is a separate module — errors return nothing, not a crash |
| Skills become stale | Manual config update (same effort as installing the skill) |

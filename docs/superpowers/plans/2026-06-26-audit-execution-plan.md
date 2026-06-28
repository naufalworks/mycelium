# 100-Point Audit — Remaining Action Plan

**Goal:** Execute remaining items from the 100-point audit — code cleanup, constant extraction, feature completion, and proof testing.

## Can be parallelized (independent tasks)

### Task Group A: Extract constants from interceptor.rs
Replace hardcoded magic numbers with named consts.

| Constant | Current value | Used at |
|----------|--------------|---------|
| `MAX_CLUSTERS` | 5 | recall call, limit param |
| `CORTEX_MATCH_THRESHOLD` | 0.3 | match_skill call |
| `ANTHROPIC_API_VERSION` | "2023-06-01" | HTTP header |
| `SYNTHESIS_MAX_TOKENS` | 256 | API request body |
| `SYSTEM_MSG_FILTER_LEN` | 500 | system message filter |
| `FALLBACK_ATOM_MIN_LEN` | 80 | fallback atom threshold |
| `LOG_PREVIEW_LEN` | 200 | context block preview |

### Task Group B: Feature completion
Complete the 3 built-but-inert features.

| Feature | Current state | Missing piece |
|---------|--------------|---------------|
| Session pre-warming | Tracks topics | Never warms cache |
| Write-time snippets | Table exists | No data (needs + has no annotation flow test) |
| Cortex skill matching | Loads 5 skills | No observed match in logs |

### Task Group C: Proof testing
One-shot manual verification per feature.

| Feature | How to prove |
|---------|-------------|
| Pipeline e2e | Send query, show full log trail |
| Word index | Show cluster names (not garbage) |
| Synthesis | Show context block content |
| Fallback | Synthesize failure → template |
| Heat cache | Repeat query → faster |

## NOT parallelizable (sequential — depends on Task C)

### Task D: Write integration tests
Only after proofs pass — convert manual verifications into automated tests.

## Execution

1. Group A first (code changes, no runtime risk)
2. Group B second (runtime changes, needs deploy)
3. Group C third (verify everything)
4. Group D fourth (tests)

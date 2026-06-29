# Self-Healing Hash Chain Repair — Design Spec

**Date:** 2026-06-29
**Status:** Draft — Pre-Implementation
**Priority:** Reliability + Novelty

## Problem

Mycelium's hash chain is broken: 9,089 of 10,968 entries have `prev_hash` fields pointing to incorrect predecessors (all pointing to the hash of the latest entry, turn 11881). The chain was broken during a batch import from Go/Python to Rust.

The hash chain is a verifiable integrity check — it proves that no entry was tampered with since creation. A broken chain means `mycelium verify` fails and we lose the ability to prove data integrity.

This must be fixed while preserving:
1. **Permanent memory** — user data is never lost
2. **Autonomous operation** — system repairs itself without human intervention
3. **Reliability** — must survive Mac restarts, LLM model shortages, network blips

## Solution: Self-Healing LLM-Driven Repair

A self-healing daemon that runs as part of the brain decay cycle. When it detects broken chains, it spawns a constrained LLM agent that:
1. Reads a natural language policy file
2. Plans the repair (snapshot, walk, fix, verify)
3. Executes the repair with strict tool whitelist
4. Writes a git-trackable audit file with replay SQL

The LLM is one of two local models: **kimi-k2.7** or **minimax-m3**, served via OpenAI-compatible API at `127.0.0.1:8080` (or the `meshgate` proxy).

---

## LLM Provider Configuration

### Models
- **kimi-k2.7** — primary. Fast, good for routine repair planning.
- **minimax-m3** — fallback. Used when kimi-k2.7 is unavailable or rate-limited.

### Endpoint
```rust
struct LlmConfig {
    /// OpenAI-compatible endpoint URL.
    /// Default: http://127.0.0.1:8080 (meshgate proxy) or http://meshgate:80
    pub endpoint: String,
    /// Models to try in order of preference.
    pub models: Vec<String>,  // ["kimi-k2.7", "minimax-m3"]
    /// Request timeout per attempt.
    pub timeout: Duration,    // default: 60s
    /// Max retries per model before falling back.
    pub max_retries: u32,     // default: 3
    /// Backoff between retries.
    pub retry_backoff: Duration,  // default: 2s, doubling
}
```

### Retry & Fallback Logic

```
try repair with model = models[0]:
  attempt 1: timeout? error? retry with backoff
  attempt 2: ...
  attempt 3: ...
  if all retries fail:
    try model = models[1]  // fallback
    same retry logic
  if both fail:
    circuit_breaker.record_failure()
    skip this tick, log error
    try again next tick
```

### Circuit Breaker

```rust
struct CircuitBreaker {
    consecutive_failures: AtomicU32,
    last_failure: AtomicI64,  // unix timestamp
    /// Open the circuit after this many consecutive failures.
    failure_threshold: u32,  // default: 3
    /// Stay open for this long before allowing a probe.
    cooldown: Duration,      // default: 5 minutes
}
```

States:
- **Closed**: normal operation
- **Open**: skip LLM calls for `cooldown` duration after `failure_threshold` consecutive failures
- **Half-open**: allow one probe request

### Health Check

A `GET /v1/models` ping to verify the endpoint is responsive. Runs every 10 minutes. If unhealthy, the circuit breaker opens early.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Self-Healing Daemon                    │
│         (runs inside brain daemon, on schedule)          │
└────────────────────────┬────────────────────────────────┘
                         │ every 60s decay tick
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Chain Integrity Monitor                    │
│      runs verify_hash_chain(), diffs vs last state       │
│      state cached in .mycelium/chain-state.json          │
└────────────────────────┬────────────────────────────────┘
                         │ if broken
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Safety Harness                            │
│   1. Create snapshot → .mycelium/snapshots/<uuid>.db     │
│   2. Load policy.md + safety.md                         │
│   3. Verify LLM provider health                         │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│              LLM Repair Agent                           │
│  model: kimi-k2.7 (fallback: minimax-m3)                │
│  tools: 6 (4 read, 2 write — all whitelist-checked)     │
│  bounded: max 20 tool calls, 5-min wall-clock timeout    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│         Audit Trail → bugfixes/                         │
│  bugfixes/YYYY-MM-DD-hash-chain-repair-<uuid>.md         │
│  git-trackable, replayable SQL diff                     │
└─────────────────────────────────────────────────────────┘
```

---

## The Harness — Reliability & Safety

### Layer 1: Crash Safety

Every state transition is persisted to disk before the next step:
- `.mycelium/snapshots/<uuid>.db` — pre-repair snapshot
- `.mycelium/repair-state.json` — current repair session state
- `.mycelium/chain-state.json` — cached verify result (so we don't re-detect already-broken chains)

If the daemon crashes mid-repair, on restart:
1. Read `.mycelium/repair-state.json`
2. If a session was in progress, check the snapshot exists
3. Verify the chain — if still broken, offer to resume
4. If chain is now valid, complete the audit log

### Layer 2: LLM Provider Resilience

| Failure | Recovery |
|---|---|
| Network timeout | Retry with backoff (max 3 per model) |
| Rate limit (429) | Retry with exponential backoff up to 60s |
| Model unavailable (503/5xx) | Switch to fallback model |
| Auth failure (401) | Log error, circuit breaker opens for 1 hour |
| Both models fail | Open circuit, skip this tick, retry next decay |
| LLM response malformed | Re-prompt once, then abort |

### Layer 3: Mutation Safety

The LLM's `set_prev_hash` tool can only update two specific columns. The SQL query is hardcoded with column validation:

```rust
fn set_prev_hash(turn: i64, new_prev_hash: String) -> Result<()> {
    // Whitelist check
    if !ALLOWED_MUTATIONS.contains(&"entries.prev_hash") {
        return Err(anyhow!("column not in whitelist"));
    }
    // Length validation (SHA-256 hex = 16 chars)
    if new_prev_hash.len() != 16 {
        return Err(anyhow!("invalid hash length"));
    }
    // Hex validation
    if !new_prev_hash.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err(anyhow!("invalid hash characters"));
    }
    // Hardcoded SQL with parameterized values
    conn.execute(
        "UPDATE entries SET prev_hash = ?1 WHERE turn = ?2",
        params![new_prev_hash, turn],
    )?;
    Ok(())
}
```

### Layer 4: Pre/Post Verification

```rust
fn run_repair_cycle() -> Result<()> {
    // 1. Snapshot
    let snapshot_id = create_snapshot()?;
    
    // 2. Verify (should be broken)
    let pre_failures = verify_hash_chain()?.len();
    if pre_failures == 0 {
        return Ok(());  // nothing to repair
    }
    
    // 3. Run LLM agent
    let agent_result = llm_agent.run(...);
    
    // 4. Verify (should be 0 failures now)
    let post_failures = verify_hash_chain()?.len();
    if post_failures > 0 {
        // Rollback
        restore_snapshot(snapshot_id)?;
        log_failure("repair incomplete, rolled back");
        return Err(anyhow!("repair incomplete"));
    }
    
    // 5. Write audit trail
    write_audit_trail(agent_result, snapshot_id, pre_failures)?;
    
    Ok(())
}
```

---

## LLM Tools — Constrained Agent

```rust
#[derive(Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum LlmTool {
    // READ tools (4)
    ListBrokenSegments {
        max_segments: usize,
    },
    GetEntry {
        turn: i64,
    },
    GetEntryContent {
        turn: i64,
    },
    VerifyChain,
    
    // WRITE tools (2 — whitelist-checked)
    SetPrevHash {
        turn: i64,
        new_prev_hash: String,  // validated: 16 hex chars
    },
    CommitRepair {
        description: String,
        affected_turns: Vec<i64>,
    },
}
```

**Tool constraints**:
- `SetPrevHash`: can only set `entries.prev_hash`. Hash must be 16 hex chars.
- `CommitRepair`: must include at least one affected turn. Triggers post-verification.
- **No delete tool exists** — the LLM literally cannot remove entries.
- **No content edit tool exists** — user/assistant/ts/session are immutable.

---

## Policy File — Natural Language Conditions

`.mycelium/policy.md`:

```markdown
# Mycelium Self-Healing Policy

## Mission
The hash chain is a verifiable integrity check. When broken, repair it.
User content is permanent and immutable — repair ONLY the chain links.

## Repair Strategy (in order of preference)
1. **Walk in turn order**: For each broken entry, compute the correct prev_hash
   from the predecessor at turn-1. Use compute_hash() to verify.
2. **Fill gaps**: If turn-1 doesn't exist, link to the nearest preceding
   entry with a valid hash.
3. **Bridge batches**: If a long unbroken segment is missing (e.g., from
   import), link the broken chain to the LAST known good hash before the segment.
4. **Last resort**: If chain is unfixable (catastrophic corruption), log a
   critical error and skip — DO NOT delete data.

## Constraints
- Never delete an entry.
- Never modify user content (user, assistant, ts, session, entities).
- Never modify annotation, finding, or verdict (semantic content).
- Only repair prev_hash and hash.
- Max 20 tool calls per repair session.
- Always commit_repair when done, even if no changes were needed.

## Audit
Every repair must include: turns affected, before_hash, after_hash, reason.
The audit file will be committed to git as a bugfix record.
```

`.mycelium/safety.md` (separate file, machine-readable constraints):

```yaml
# Machine-readable safety constraints
allowed_columns:
  - entries.prev_hash
  - entries.hash
max_tool_calls: 20
max_wall_time_seconds: 300
forbidden_actions:
  - delete_entry
  - modify_user_content
  - modify_session_metadata
required_post_conditions:
  - verify_chain_returns_zero_failures
```

---

## Audit Trail — Git-Trackable Replay

Every repair session produces a markdown file in `bugfixes/`:

```markdown
# Hash Chain Repair — 2026-06-29 14:32:11

**Snapshot ID**: `a3f7b2c1-...`
**Trigger**: verify_hash_chain found 9,089 broken entries
**Model Used**: kimi-k2.7 (3 attempts succeeded on 2nd try)
**Tool Calls**: 12 (8 read, 4 write)
**Duration**: 47 seconds

## Diagnosis
- Turn 1867-11881: all entries have prev_hash = `433b12ac...` (hash of turn 11881)
  instead of their actual predecessor. Suggests batch migration bug.
- Turns 1865-1866: missing from database entirely.
- Pattern: every entry in segment 1867-11881 is off by N turns where N is the
  position within the segment.

## Repairs
| Turn  | Before          | After           | Reason                          |
|-------|-----------------|-----------------|---------------------------------|
| 1867  | prev=433b12ac...| prev=... (1866's hash) | Walk predecessors       |
| 1868  | prev=433b12ac...| prev=... (1867's new)  | Inherit 1867's corrected prev  |
| 1869  | prev=433b12ac...| prev=... (1868's new)  | Continue walk                  |
| ...   | ...             | ...             | ...                             |
| 1865  | (missing)       | (missing)       | Cannot fabricate; flagged gap   |
| 1866  | (missing)       | (missing)       | Cannot fabricate; flagged gap   |

## Verification
verify_hash_chain after repair: 10,966 / 10,968 entries valid (99.98%).

## Failed Turns (2)
- 1865, 1866 — missing from database. Cannot reconstruct. Flagged for manual review.

## Replay SQL
```sql
BEGIN IMMEDIATE;
UPDATE entries SET prev_hash = '...' WHERE turn = 1867;
UPDATE entries SET hash = '...' WHERE turn = 1867;
UPDATE entries SET prev_hash = '...' WHERE turn = 1868;
UPDATE entries SET hash = '...' WHERE turn = 1868;
-- ... continues for 8,917 more entries
COMMIT;
```

## Rollback
```bash
sqlite3 mycelium.db ".restore /path/to/snapshots/a3f7b2c1-....db mycelium.db"
```
```

This file is `.gitignore`'d by default but can be `git add bugfixes/...` to commit. Replayable. Reviewable.

---

## Data Flow — One Repair Cycle

```
1. Decay tick (every 60s)
   ↓
2. Chain Integrity Monitor
   - Read .mycelium/chain-state.json (cached result)
   - Run verify_hash_chain() → compare with cached
   - If same as cached: skip
   - If new breakage detected: trigger repair
   - Cache new state
   ↓
3. Safety Harness
   - Create snapshot → .mycelium/snapshots/<uuid>.db
   - Load .mycelium/policy.md + .mycelium/safety.md
   - Health-check LLM provider
   ↓
4. LLM Repair Agent
   - model = kimi-k2.7 (fallback: minimax-m3)
   - Context: policy + safety + broken_segments list
   - Agent loop (max 20 tool calls, 5 min timeout):
     - list_broken_segments → get_entry → set_prev_hash → commit_repair
   - Retry on transient failures
   ↓
5. Post Verification
   - Run verify_hash_chain() → expect 0 failures
   - If still broken: restore snapshot, log failure
   ↓
6. Audit Trail
   - Write bugfixes/<date>-<description>.md
   - Includes replay SQL and rollback command
   ↓
7. Notify via tracing: "hash chain self-healed, see bugfixes/<file>"
```

---

## Error Handling

| Failure | Recovery |
|---|---|
| LLM provider unreachable | Circuit breaker opens; retry next tick |
| kimi-k2.7 returns malformed JSON | Re-prompt once, then abort |
| kimi-k2.7 times out | Switch to minimax-m3 immediately |
| Both models fail | Skip tick, log error, retry next decay |
| Snapshot creation fails | Abort, no mutation |
| LLM tries to update non-whitelisted column | Tool returns `Err("not allowed")` |
| LLM tries to delete entry | Tool doesn't exist — physical impossibility |
| commit_repair but chain still broken | Restore snapshot, log critical error |
| LLM hangs (timeout) | Abort agent, restore snapshot |
| Database locked | Abort, retry next tick |
| Daemon crashes mid-repair | On restart, read repair-state.json, offer resume |
| Mac restarts | On boot, brain daemon starts; chain monitor runs in first decay tick |

---

## Self-Healing — Restart & Shortage Resilience

The system is **self-healing across multiple failure modes**:

### Mac Restart
- `.mycelium/snapshots/` and `.mycelium/*.json` are persistent
- On boot, brain daemon starts
- First decay tick runs chain monitor
- If chain was broken before restart, repair triggers automatically

### Model Shortage
- kimi-k2.7 becomes unavailable: circuit breaker detects, falls back to minimax-m3
- Both models unavailable: circuit breaker opens, repair skips
- When model comes back: circuit breaker half-open, probe succeeds, full operation resumes

### LLM Provider Restart
- Endpoint at 127.0.0.1:8080 restarts
- Next request times out → retry → succeeds
- No persistent state needed (each repair is independent)

### Repair in Progress Interrupted
- Snapshot exists, repair-state.json exists
- On restart, repair offers to resume from last commit_repair
- If chain is now valid, completes audit log normally

---

## File Structure

```
crates/mycelium-core/src/
├── self_healing/
│   ├── mod.rs            — public API
│   ├── chain_monitor.rs  — verify_hash_chain() runner + cache
│   ├── safety.rs         — whitelist, snapshot, rollback
│   ├── llm_agent.rs      — LLM tool dispatch + retry logic
│   ├── llm_provider.rs   — kimi/minimax client + circuit breaker
│   ├── audit.rs          — bugfixes/ file writer
│   ├── tools.rs          — the 6 LLM tools
│   └── policy.rs         — policy.md/safety.md loader

crates/mycelium-core/Cargo.toml
+ parking_lot (already added)
+ reqwest, serde_json (already there)

.mycelium/
├── policy.md             — natural language repair policy (created on first run)
├── safety.md             — machine-readable constraints (created on first run)
├── chain-state.json      — cached verify result
├── repair-state.json     — current repair session (transient)
├── snapshots/            — pre-repair DB snapshots
└── circuit-state.json    — circuit breaker state

bugfixes/
└── YYYY-MM-DD-hash-chain-repair-<uuid>.md
```

---

## Testing

| Test | What it verifies |
|---|---|
| `test_verify_chain_detects_breaks` | unit test |
| `test_chain_monitor_caches_results` | monitor doesn't re-detect same breakage |
| `test_snapshot_and_rollback` | snapshot → mutate → rollback → exact prior state |
| `test_set_prev_hash_validates_hash_format` | tool rejects invalid hash strings |
| `test_set_prev_hash_whitelist_blocks_other_columns` | tool refuses non-allowed columns |
| `test_no_delete_tool_exists` | LLM cannot delete entries (compile-time guarantee) |
| `test_bugfix_replay_sql_executes` | SQL in bugfix file runs without error |
| `test_circuit_breaker_opens_after_threshold` | 3 failures → circuit opens |
| `test_circuit_breaker_recovers_after_cooldown` | 5 min later → circuit half-open |
| `test_llm_provider_falls_back_on_failure` | kimi fails → minimax tries |
| `test_repair_state_resumes_after_crash` | repair-state.json preserved across restarts |
| `test_repair_dry_run_makes_zero_mutations` | dry-run mode works |
| `test_end_to_end_repair_with_mocked_llm` | full repair cycle with deterministic LLM |
| `test_repair_handles_missing_turns` | gaps (turns 1865-1866) flagged, not fabricated |
| `test_permanent_memory_never_lost` | count of entries never decreases during repair |

---

## Success Criteria

| Metric | Target |
|---|---|
| Hash chain integrity after repair | 99.99% (only missing/missing-edge cases fail) |
| User entries never lost during repair | 100% — count must be invariant |
| Repair latency | < 5 minutes for 10k entries |
| Model fallback latency | < 30 seconds (kimi fail → minimax attempt) |
| Restart recovery | < 60 seconds from daemon start to detect state |
| Bugfix file is git-trackable | yes, can be `git add bugfixes/` |
| Rollback recovery time | < 1 minute via `.restore` |
| Zero data loss | count of entries never decreases |

---

## Implementation Phases (Draft)

1. **Chain Monitor + State Cache** (1 file, ~150 lines)
2. **Safety Harness + Snapshot/Restore** (1 file, ~200 lines)
3. **LLM Provider + Circuit Breaker** (1 file, ~250 lines)
4. **LLM Agent + Tool Dispatch** (1 file, ~200 lines)
5. **Audit Trail Writer** (1 file, ~150 lines)
6. **Policy Loader** (1 file, ~80 lines)
7. **Wire into Brain Daemon** (modify existing, ~30 lines)
8. **Tests** (1 file, ~400 lines)

Estimated: 6 files new, 1 file modified, ~1,500 lines of code.

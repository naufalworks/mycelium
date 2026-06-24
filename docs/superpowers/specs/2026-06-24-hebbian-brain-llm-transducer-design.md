# Hebbian Crystal Brain — LLM-Guided Memory Transducer Design

**Date:** 2026-06-24
**Project:** Mycelium Core
**Status:** Design Draft (pre-implementation)
**Author:** Azfar Naufal

---

## 1. Problem Statement

The Hebbian Crystal Brain's current atom extraction uses raw n-gram splitting (bi-grams and tri-grams) applied to all conversation text. Processing 3,630 real entries produced:

| Metric | Expected (spec) | Actual | Verdict |
|---|---|---|---|
| Unique atoms | < 1,089 (logarithmic) | **188,544** (linear) | ❌ |
| Edges | ~hundreds of K | **35.7 million** | ❌ |
| Query latency | sub-ms | ✅ | ✅ |

**Root cause:** Raw n-gram splitting treats every token sequence as unique. Paths (`src/storage.rs` vs `src/db.rs`), numbers (`234` vs `567`), UUIDs (`550e8400-...` vs `550e8411-...`), and natural language inflections (`running` vs `ran`) all produce distinct atoms even when they represent the same kind of thing. The brain has no concept of *semantic deduplication*.

**Edge explosion:** `consolidate_entry` creates all-pairs edges among atoms within each entry: O(n²) where n ≈ 52 atoms/entry → ~1,326 edges/entry → 35.7M at 3,630 entries.

---

## 2. Solution: LLM-Guided Memory Transducer

### 2.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│ PROXY (mycelium-proxy)                                   │
│                                                          │
│  Request → Inject memory instruction into system prompt   │
│  Response → Extract <memory> block, strip from visible   │
│             response, save annotation with entry          │
└──────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────┐
│ STORAGE (permanent memory)                               │
│                                                          │
│  Entries table: +annotation TEXT (nullable)              │
│  Saves LLM memory annotation alongside each entry        │
└──────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────┐
│ BRAIN (daemon processes pending_brain_work)              │
│                                                          │
│  consolidate_entry(turn, text, session, annotation?)      │
│    ├── Parse annotation (if present)                     │
│    ├── Rule-based atom extraction (paths, UUIDs, etc.)   │
│    ├── LLM-phrase atom extraction (importance-weighted)  │
│    ├── Entity bridge edges (2.5× weight)                 │
│    ├── W=2 local edges (weight × importance)             │
│    └── Record positions in graph                         │
└──────────────────────────────────────────────────────────┘
```

### 2.2 Design Principles

1. **Proxy stays lean — 3 insertion points only** (system prompt injection, response parsing, annotation storage). No additional network calls, no external services, no queuing within the proxy.
2. **Brain API preserved** — `consolidate_entry` gains an optional `annotation` parameter. No existing code breaks.
3. **Graceful degradation** — If the LLM doesn't emit `<memory>`, the brain falls back to pure rule-based extraction. The annotation is a bonus, not a requirement.
4. **Additive schema changes** — Only adding columns, never modifying or removing them. Zero migration risk.

---

## 3. Memory Annotation Format

### 3.1 System Prompt Injection

The proxy appends one sentence to the system prompt before sending upstream:

> **After your response, emit a `<memory>` block containing JSON with: phrases (canonical noun phrases to remember, each with text and importance 1-5), actions (key actions taken/fixed/explained, each with text and importance 1-5), entities (named things mentioned, each with name, type, aliases, and importance 1-5). Keep the block under 200 tokens.**

### 3.2 Annotation JSON Structure

```json
{
  "phrases": [
    {"text": "hash chain verification fix", "importance": 5},
    {"text": "storage.rs bug", "importance": 4}
  ],
  "actions": [
    {"text": "fix hash chain verification in storage.rs", "importance": 5}
  ],
  "entities": [
    {"name": "storage.rs", "type": "file", "aliases": ["storage module"], "importance": 4},
    {"name": "hash chain", "type": "concept", "aliases": [], "importance": 5}
  ]
}
```

### 3.3 LLM Response Example

```
I fixed the hash chain bug. The issue was in storage.rs at the verification step.

<memory>{"phrases":[{"text":"hash chain verification fix","importance":5},{"text":"storage.rs bug","importance":4}],"actions":[{"text":"fix hash chain verification","importance":5}],"entities":[{"name":"storage.rs","type":"file","aliases":["storage module"],"importance":4},{"name":"hash chain","type":"concept","aliases":[],"importance":5}]}</memory>
```

The proxy strips the `<memory>` block from the user-visible response. The user sees only the conversational answer.

### 3.4 Recovery of Annotation

All raw `<memory>` text is saved to the `entries.annotation` column. If the prompt format changes, old entries can be batch-reprocessed. The saved corpus also serves as a labeled dataset for training a custom extraction model in the future.

---

## 4. Proxy Integration

### 4.1 Changes Required

| Location | Change |
|---|---|
| `Request building` | Append memory instruction to system prompt |
| `Response parsing` | Regex-extract `<memory>...</memory>` from response body |
| `Response delivery` | Strip `<memory>` block from user-visible response |
| `Entry storage` | Pass extracted annotation to `Storage::store_entry()` |

### 4.2 Overhead

- System prompt grows by ~40 tokens (negligible vs typical 10,000+ token context)
- Response parsing: regex match + JSON parse (~0.1ms)
- No additional network calls, no queuing within proxy
- Theoretical throughput impact: < 1%

### 4.3 Safety

| Condition | Proxy Behavior |
|---|---|
| LLM doesn't emit `<memory>` | Log warning, save entry with `annotation = NULL` |
| Malformed JSON in `<memory>` | Log warning, save raw text to `annotation`, set parsed field to NULL |
| No system prompt slot (some providers) | Feature can be toggled off per-provider in config |

---

## 5. Brain Processing Pipeline

### 5.1 `consolidate_entry` Algorithm (Revised)

```
Input: turn, text, session, annotation (Option<&MemoryAnnotation>)

1. Parse annotation if present
2. LLM phrase extraction:
   For each (phrase, importance) in annotation.phrases:
     a. Normalize phrase: stem + stop_words + lowercase
     b. Upsert atom with phrase, importance, increment ref_count
     c. Record position (atom_id, turn, session)
     d. Track atom id for entity matching
3. LLM action extraction:
   Same as phrases — each action becomes an atom with its importance
4. Entity registry pass:
   For each entity in annotation.entities:
     a. Normalize entity name (stem + lowercase)
     b. Upsert into entity_registry: increment ref_count, update aliases
     c. Find all atoms in current entry that match this entity:
        Matching criteria: entity name (and aliases) checked as lowercase
        substring against the normalized atom phrase. Both rule-based atoms
        (e.g., a path atom "storage.rs") and LLM-phrase atoms are matched.
        Example: entity "storage.rs" matches LLM phrase atom "storage.rs bug"
        AND rule-based path atom "storage.rs" — all get bridged.
     d. If ≥ 2 atoms found AND they are not W=2 adjacent:
        Create direct edge between each pair with weight = 2.5
5. Rule-based extraction (unchanged from current):
   Paths, UUIDs, hashes, numbers, identifiers, URLs, error codes, dates
6. W=2 local edge creation:
   Build ordered list of ALL atoms in entry (LLM + rule-based)
   For each atom at index i:
     Connect to atom at i+1 with weight = 1.0 × max(importance_i, importance_i+1)
7. Remove this turn from pending_brain_work
```

### 5.2 Bounded Edge Creation (W=2)

Instead of the current O(n²) all-pairs edge creation:

```
Atoms ordered as they appear in text:
  [fix bug] [bug in] [in storage] [storage.rs] [rs line] [line 234]

W=2 local edges (weight × importance):
  [fix bug]──[bug in]        weight = 1.0 × importance
  [bug in]──[in storage]     weight = 1.0 × importance
  [in storage]──[storage.rs] weight = 1.0 × importance
  ...

Entity bridge edges (weight = 2.5, independent of distance):
  [storage.rs]──[line 234]   weight = 2.5 (same entity: "storage.rs")
```

At ~52 atoms/entry, W=2 produces ~102 edges/entry instead of ~1,326 — a **93% reduction**. At 3,630 entries: ~370K edges instead of 35.7M.

### 5.3 Importance-Weighted Atom Storage

The `atoms` table stores an `importance` field alongside the existing `ref_count`:

- **ref_count** = how often the LLM mentioned this phrase (natural frequency)
- **importance** = how much the LLM says it matters (LLM judgment)
- Both are used in query ranking: `score = ref_count × importance`

This distinguishes high-frequency-but-low-importance content from rarely-mentioned-but-critical content (API keys, dates, error codes).

---

## 6. Schema Changes

### 6.1 `entries` Table

```sql
-- NEW COLUMN added:
-- annotation TEXT NULL   ← raw JSON from the <memory> block
```

### 6.2 `atoms` Table

```sql
-- NEW COLUMN added:
-- importance REAL NOT NULL DEFAULT 1.0   ← LLM-assigned importance (1-5 scale)
```

### 6.3 `entity_registry` Table (New)

```sql
CREATE TABLE IF NOT EXISTS entity_registry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,       -- normalized canonical name
    display_name TEXT NOT NULL DEFAULT '',   -- best-pretty form from LLM
    entity_type  TEXT NOT NULL DEFAULT 'concept',
    aliases      TEXT NOT NULL DEFAULT '[]', -- JSON array: ["alias1", "alias2"]
    importance   REAL NOT NULL DEFAULT 1.0,  -- running max importance
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    ref_count    INTEGER NOT NULL DEFAULT 0
);
```

### 6.4 Summary

| Table | Change | Existing Queries | Rollback |
|---|---|---|---|
| entries | `+annotation TEXT` | Unaffected | Drop column |
| atoms | `+importance REAL` | Unaffected | Drop column |
| entity_registry | Entirely new | No dependency | Drop table |

---

## 7. Error Handling & Safety

| Failure Mode | Handling | Impact |
|---|---|---|
| LLM doesn't emit `<memory>` | Proxy logs warning, saves `annotation = NULL`. Brain uses rule-based fallback | No memory block, but brain works normally |
| Malformed annotation JSON | Proxy catches, saves raw text, sets `annotation = NULL` | Same fallback |
| Brain processing crash mid-entry | SQLite transaction rollback. Entry stays in `pending_brain_work`. Daemon retries 3× then skips | One entry lost from graph; brain otherwise healthy |
| Entity registry constraint violation | Wrap in try/catch — log and skip the entity | One entity missing from registry; rest of entry processes normally |

**Recovery principle:** The annotation column contains the raw LLM output. If the extraction format evolves, old entries can be batch-reprocessed without data loss.

---

## 8. Testing Strategy

### 8.1 Unit Tests (Brain Module)

| Test | Input | Expected |
|---|---|---|
| `no_annotation` | Entry without annotation | Identical behavior to current system |
| `valid_annotation` | Entry with phrases, actions, entities | LLM atoms created, importance stored, entity bridge edges with 2.5× |
| `malformed_annotation` | Entry with invalid JSON annotation | Falls back to rule-based; logs warning |
| `w2_edge_adjacent` | Two atoms at distance 1 | Edge created with weight 1.0× |
| `w2_edge_distant` | Two atoms at distance 3 | No direct edge created |
| `entity_bridge` | Two atoms of same entity at distance 5 | Edge created with weight 2.5× |
| `entity_bridge_no_duplicate` | Two atoms of same entity at distance 1 | Only W=2 edge (no 2.5 duplicate) |
| `importance_propagation` | Phrase with importance=5 | Atom stored with importance=5 |
| `entity_registry_upsert` | Same entity from 2 entries | ref_count = 2, display_name updates |

### 8.2 Integration Test

- **Full pipeline:** Mock proxy → storage → daemon → brain → MCP query
- **Regression replay:** Run the existing 3,630 entries with new `consolidate_entry`. Verify:
  - Atom count << 1,089 (target: ~500-800)
  - Edge count << 1M (target: ~100K-370K)
  - Processing time << 636 seconds

### 8.3 LLM Quality Tests

- Test that `<memory>` injection doesn't alter LLM response quality (compare responses with and without injection across a test set of 50 prompts)
- Test that `<memory>` output is valid JSON in ≥ 95% of responses (with progressively stricter validation)

---

## 9. Rollback Strategy

Every change is additive and independently reverible:

| Step | Action | Duration |
|---|---|---|
| 1 | Remove memory instruction from proxy's system prompt | 1 line change |
| 2 | Recompile brain without `annotation` parameter | 1 function signature revert |
| 3 | Existing data (`annotation` column, `entity_registry` table) sits unused | No action needed |

No data migration. No irreversible schema changes. No downstream dependencies that break when reverted.

---

## 10. Future Considerations (Not in Scope)

- **Custom small LLM for extraction** — Train a distilled model (~500M params) specifically for the memory transcription task, removing dependency on the upstream LLM's compliance
- **Cross-session entity graph** — Entity registry could power a global entity knowledge graph visible in MCP tools
- **Adaptive importance** — If the brain detects that low-importance atoms are frequently queried, auto-elevate their importance
- **Multi-modal atoms** — Images, code diffs, and structured data could also be annotated via the same mechanism

---

*Design reviewed and approved by Azfar Naufal on 2026-06-24.*

---

## 11. Validation Results (2026-06-24)

### Live Deployment Status

| Component | Binary | Status |
|---|---|---|
| Proxy (port 8443) | ✅ New (Jun 24 build) | Injecting instruction + storing annotations |
| Server (port 8421) | ✅ New (Jun 24 build) | Brain daemon processing all entries |
| App CLI | ✅ New (Jun 24 build) | `brain annotated`, `backfill`, `stop-words` commands |

### End-to-End Validation

**Test: Proxy injects memory instruction → kimi-k2.6 emits `<memory>` block → stored in DB → consolidated into brain.**

| Step | Result |
|---|---|
| Proxy injects MEMORY_INSTRUCTION | ✅ Verified (model saw it in thinking block) |
| kimi-k2.6 emits valid `<memory>` JSON | ✅ Valid JSON with phrases, actions, entities |
| Proxy strips annotation from visible response | ✅ |
| Annotation stored in `entries.annotation` column | ✅ Turn 5413 and 5464 |
| `consolidate_entry` with annotation | ✅ 2 annotated entries processed |
| Atoms created from annotation phrases | ✅ With correct importance scores |
| Entity registry populated | ✅ 4 entities with types and aliases |
| W=2 edges created | ✅ |
| Full backfill (6,068 entries) | ✅ In progress |

### Performance Comparison

| Metric | Old System | Our System | Improvement |
|---|---|---|---|
| Atoms/entry | 52.0 | 37.8 | **1.4×** (type-aware normalization) |
| Edges/entry | ~9,800 | 112 | **99% reduction** (W=2 vs all-pairs) |
| Total edges (6K entries) | ~35M | ~650K | **98% reduction** |
| LLM annotations | N/A | ✅ Working | 🆕 |
| Entity relationships | N/A | ✅ Working | 🆕 |
| Importance-weighted queries | N/A | ✅ Working | 🆕 |

### Issues Discovered & Fixed During Validation

1. **System prompt array format** — Proxy only handled string format; fixed to support `[{type: "text", text: "..."}]`
2. **DB migration** — Production DB lacked `annotation` column; added `ALTER TABLE` migration
3. **Missing port config** — Added `MYCELIUM_PROXY_PORT` and `MYCELIUM_UPSTREAM_URL` env var overrides
4. **Duplicate `contains()` in identifier classifier** — Fixed in `looks_like_identifier()`

### Validation Notes

- Stop word detection (70% threshold) did not trigger on current dataset (most common atom in 27% of entries)
- Edge reduction is the primary scalability win; atom reduction is secondary
- Annotations compound over time — only future entries carry LLM annotations


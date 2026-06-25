# Graph-Guided Recall — Infinite Context for AI Agents

**Date:** 2026-06-25
**Project:** Mycelium Core
**Status:** Design Draft (pre-implementation)
**Author:** Azfar Naufal

---

## 1. Problem Statement

### 1.1 The Context Window Problem

LLM sessions (Claude Code, ChatGPT, etc.) have finite context windows. Over a long session:

- The model begins to forget early conversation turns
- Eventually the window fills up entirely — old context is evicted or truncated
- The user experiences a "reset" where the model no longer knows what was discussed 2 hours ago

Current mitigation strategies all have flaws:

| Approach | Problem |
|----------|---------|
| Carry full history in context | Expensive (100K+ tokens), hits window limit |
| Summarize periodically | Lossy, loses detail, costs tokens on every summarization |
| Embedding/RAG search | Expensive, hallucination-prone, requires embedding infra, vocabulary mismatch |
| Manual note-taking | Doesn't scale, breaks flow |

### 1.2 The Root Cause

**Memory processing happens at query time, not at write time.** Existing systems store raw text and search it later — this forces every recall to re-process the full corpus through embedding or keyword matching. The latency, cost, and quality challenges all stem from this architectural choice.

### 1.3 Our Approach: Write-Time Compression

The Hebbian Crystal Brain already solves the write side: it compresses every conversation turn into **atoms** (bi-/tri-grams from the LLM's own annotations), **weighted edges** (co-occurrence proximity), and **entities** (named references) — all stored in indexed SQLite tables.

This spec defines the **read side**: a recall system that leverages that graph to deliver relevant context on demand, without ever reading raw entries.

---

## 2. Design Philosophy

### 2.1 Three Core Principles

1. **Raw entries are sacred.** The hash-chained append-only log is the system of record — never read at query time, never searched directly, never consumed by the LLM. It exists for audit, verification, and replay — not for recall.

2. **The LLM reads only synthesis.** The brain graph is the sole query-time interface: raw text → atoms/edges at write time → graph traversal at query time → synthesis for consumption. The LLM sees a compressed, relevant memory block — never the raw conversation.

3. **Infinite context via recall.** Instead of carrying session history in the context window, the LLM recalls into it. The session carries only the current turn's input; everything outside that window comes from Mycelium's graph on demand. The context window never fills up because nothing accumulates.

### 2.2 Symmetry Principle

Storage and query use the same atom vocabulary. The LLM's `<memory>` annotations produce atoms like `"change secret"`, `"restart proxy"`, `"fix streaming"`. The query parser extracts the same kind of phrases from the user's natural language question. No embedding gap, no vocabulary mismatch — atoms are the universal interface.

### 2.3 Connection to Hebbian Crystal Brain

| Layer | Hebbian Brain (Write) | Graph Recall (Read) |
|-------|----------------------|---------------------|
| Input | LLM conversation turn | Natural language query |
| Processing | `consolidate_entry()` → atoms + edges + entities | Query parser → graph traversal → synthesis |
| Output | Brain graph tables | `<mycelium-context>` block |
| Novelty | Process at write time, not query time | Query via graph traversal, not text search |

---

## 3. Architecture

```
                    WRITE PATH                          READ PATH
                    ==========                          =========

Conversation turn                         User query ("when did I change the server secret?")
       │                                              │
       ▼                                              ▼
┌──────────────────┐                     ┌──────────────────────┐
│  <memory> block  │                     │   QUERY PARSER       │
│  (from LLM ann.) │                     │   ~200 tokens, LLM   │
└──────┬───────────┘                     │   → atoms + intent   │
       │                                 └──────┬───────────────┘
       ▼                                        ▼
┌──────────────────┐                     ┌──────────────────────┐
│  brain::consolid │                     │   GRAPH TRAVERSAL    │
│  _entry()        │    ┌──────────┐     │   Zero LLM tokens    │
│  → atoms, edges, │───▶│ BRAIN    │◀────│   SQL on indexed     │
│    entities      │    │ GRAPH    │     │   atoms/edges tables  │
└──────────────────┘    │ (SQLite) │     │   sub-ms per query   │
                        └──────────┘     └──────┬───────────────┘
                               ▲                │
                               │                ▼
                        ┌──────┴──────┐  ┌──────────────────────┐
                        │ context_    │  │  CONTEXT SYNTHESIZER │
                        │ snapshots   │  │  ~1K-20K tokens, LLM│
                        │ table       │  │  → structured block  │
                        └─────────────┘  └──────┬───────────────┘
                                                │
                                                ▼
                                     ┌──────────────────────┐
                                     │  <mycelium-context>   │
                                     │  (injected into LLM   │
                                     │   request via proxy)  │
                                     └──────────────────────┘
```

### 3.1 System Components

| Component | Lines | New? | Language | LLM Usage |
|-----------|-------|------|----------|-----------|
| Query Parser | ~60 | ✅ New | Rust, calls LLM | ~200 tokens per query |
| Graph Traversal Engine | ~100 | 🔄 Existing + wiring | Rust, pure SQL | Zero tokens |
| Context Synthesizer | ~80 | ✅ New | Rust, calls LLM | ~1K–20K tokens (configurable) |
| Proxy Integration | ~40 | 🔄 Update | Rust | N/A |

### 3.2 Data Sources

The recall system reads from existing brain tables only — no new tables needed:

| Table | Used For | Indexed On |
|-------|----------|------------|
| `atoms` | Seed matching, importance × frequency ranking | `phrase` (LIKE), `id` |
| `edges` | Cluster expansion (graph traversal) | `atom_a`, `atom_b` |
| `entity_annotations` | Entity-lookup queries | `name`, `entity_type` |
| `context_snapshots` | Session-level context (summary, topics, decisions) — contextual enrichment | `session_id`, `created_at` |

---

## 4. Query Parser

### 4.1 Purpose

Translate a natural language question into the atom vocabulary that the brain graph understands. This is the only entry point — no SQL, no tool names, no structured query language.

### 4.2 Input/Output

**Input:** A natural language string (the user's question).

**Output:** A JSON object with three fields:

```json
{
  "atoms": ["change secret", "server"],
  "intent": "temporal",
  "temporal_hint": null
}
```

### 4.3 Intent Classification

| Intent | Meaning | Example |
|--------|---------|---------|
| `factual` | Retrieve a specific fact or value | "what's my email?" |
| `relational` | What happened around / with X | "what did we fix on the proxy?" |
| `temporal` | When did X happen | "when did I change the secret?" |
| `exploratory` | What do we know about X | "what do we know about deployment?" |

### 4.4 Implementation

Single small LLM call with a structured prompt:

```
Given this recall query: "{user_question}"

Extract:
1. Atoms — the key noun phrases (2-6 short phrases, 1-4 words each) that should
   activate the brain graph. Use domain-specific terms as stored in memory.
2. Intent — one of: factual, relational, temporal, exploratory
3. Temporal hint — any time reference (ISO format if explicit, relative if vague)

Return JSON: {"atoms": [...], "intent": "...", "temporal_hint": "..." | null}
```

**Token cost:** ~200 tokens (input + output). Can use the same model as the main conversation.

### 4.5 Error Handling

- **Atoms too short/common** → fall back to full user question as seed phrase
- **No atoms extractable** → return empty → graph traversal returns nothing → synthesizer returns "No memories found"
- **Intent unclear** → default to `relational` (the most general intent)

---

## 5. Graph Traversal Engine

### 5.1 Purpose

Take the atoms from the query parser and traverse the brain graph to find relevant memory context. This is pure indexed SQL — zero LLM calls, sub-millisecond latency.

### 5.2 Algorithm

```
function traverse(atoms: List<Phrase>, intent: Intent, temporal_hint: Option<TimeRange>) -> List<AtomCluster>:

    // Step 1: Seed — find matching atoms
    //
    // For each phrase, call brain::recall() which does:
    //   SELECT id, phrase, ref_count, importance
    //   FROM atoms
    //   WHERE phrase LIKE '%phrase%'
    //   ORDER BY (ref_count * importance) DESC
    //   LIMIT 10
    //
    // This returns the most-used, most-important atoms matching each phrase.
    seeds = atoms.map(phrase => brain::recall(phrase, limit=10))

    if seeds is empty for all phrases:
        return empty  // No memories found for this query

    // Step 2: Temporal filter (if temporal_hint provided)
    //
    // brain::when() returns first_seen, last_seen, total_mentions for an atom.
    // For temporal intent or explicit time hints, filter out atoms outside window.
    if temporal_hint:
        for each seed:
            (first_seen, last_seen, count) = brain::when(seed.id)
            if last_seen < temporal_hint.start:
                discard seed
            if intent == temporal and count == 1:
                mark seed as "single-event" (return directly without expansion)

    // Step 3: Cluster expansion
    //
    // For each surviving seed, find neighbor atoms through weighted edges.
    // brain::clusters(atom_id, limit=5) does:
    //   SELECT atom_a, atom_b, weight FROM edges
    //   WHERE atom_a = ?1 OR atom_b = ?2
    //   ORDER BY weight DESC LIMIT 5
    //
    // Also fetch neighbor phrases via JOIN on atoms table.
    clusters = []
    for each seed in surviving_seeds:
        neighbors = brain::clusters(seed.id, limit=5)
        clusters.push(AtomCluster {
            seed: seed,
            neighbors: neighbors,
            temporal: brain::when(seed.id)
        })

    // Step 4: Rank
    //
    // Score = edge_weight × atom_importance × recency_decay(last_seen)
    // Top 3-5 clusters based on available synthesis budget.
    ranked = clusters.sort_by(score).take(top_n)

    return ranked
```

### 5.3 Edge Cases

| Condition | Behavior |
|-----------|----------|
| No matching atoms | Return empty — synthesizer responds "No memories found for that query" |
| Single atom match | Return just that atom's cluster (no neighbors) — rapid response |
| Temporal query, single mention | Return directly without expansion (you asked "when", not "what else") |
| Many matching atoms | Return top N by score, bounded by token budget |
| Stop words in query | Handle via existing `brain::is_stop_word()` — skip them during seeding |

### 5.4 Token Budget Integration

The traversal engine outputs raw clusters — the decision of how much to synthesize is deferred to the synthesizer. But the engine pre-computes the **estimated synthesis cost** for each cluster (based on neighbor count + temporal fields) so the synthesizer can decide which clusters to keep.

```
Estimated synthesis tokens per cluster ≈
    100 (header) +
    80 × neighbor_count +
    30 (temporal line)
```

This allows the budget to be enforced without wasted LLM calls.

---

## 6. Context Synthesizer

### 6.1 Purpose

Turn ranked atom clusters into a structured, readable context block — the `<mycelium-context>` block. This is what the LLM (Claude, GPT, kimi, etc.) reads. No raw text, no raw entries — only graph-synthesized meaning.

### 6.2 Input/Output

**Input:** Ranked list of `AtomCluster` objects from the traversal engine + token budget limit.

**Output:** A context block string, and optionally a structured JSON for non-LLM consumers.

### 6.3 Proxy Mode Output

```
<mycelium-context>
Memories relevant to your query:

[When you changed the server secret]
  - You modified the SECRET_KEY in docker-compose.yml (turn 1423)
  - This was part of the auth overhaul
  - Other context: deployment config, env files, proxy restart

[Services that restarted last night]
  - nginx restart at turn 1541, mycelium-proxy restart at turn 1542
  - Cause: config change to proxy port
  - Other context: proxy streaming fix, SSL cert rotation
</mycelium-context>
```

Each `[Header]` is a seed atom's cluster. Bullet points are:
- **Direct matches** (the atom itself + its top neighbor): what was remembered
- **Temporal context** (from `brain::when()`): when it happened
- **Cluster connections** (second-degree neighbors): what else was happening nearby

### 6.4 Direct Mode Output

```json
{
  "query": "what restarted last night",
  "total_tokens": 1250,
  "clusters": [
    {
      "seed": "restart service",
      "matches": [
        { "id": 1042, "phrase": "restart nginx", "turn": 1541, "relevance": 0.92 },
        { "id": 1045, "phrase": "restart proxy", "turn": 1542, "relevance": 0.88 }
      ],
      "neighbors": ["config change", "proxy port", "ssl cert"],
      "temporal": "2026-06-24T02:14 to 2026-06-25T02:15"
    }
  ]
}
```

### 6.5 Token Budget Management

The synthesizer receives a budget and fills up to it:

1. Start with highest-scored cluster — synthesize it fully
2. Subtract that cluster's token cost from budget
3. If remaining budget > 0, add next cluster
4. Repeat until budget exhausted or no clusters remain
5. If budget is still high and clusters are exhausted, optionally expand further cluster neighbors (deepen, don't widen)

The default budget is **1000 tokens**. The user can set it higher (up to 20000+). The budget is a **soft cap** — the synthesizer may slightly overshoot on the last cluster rather than truncating mid-sentence.

```
budget: 1000 (default), 5000 (recommended ceiling), 20000 (max)
```

### 6.6 Empty Results

If graph traversal returns no clusters:

```
<mycelium-context>
No relevant memories found for your query.
</mycelium-context>
```

This is not an error. It means the brain graph has no atoms matching the query. The LLM proceeds without injected memory context.

---

## 7. Proxy Integration

### 7.1 Current Behavior (Before)

The proxy intercepts LLM requests, calls `storage.search_facts(user_msg, 5)`, and injects raw facts from the `memory_facts` table.

### 7.2 New Behavior (After)

The proxy intercepts LLM requests and runs the full recall pipeline:

1. Extract user message from the request body (anthropic or openai format)
2. Call **Query Parser** → atoms + intent + temporal_hint
3. Call **Graph Traversal Engine** → ranked atom clusters
4. Call **Context Synthesizer** → `<mycelium-context>` block
5. Inject the context block into the system prompt (append after the memory instruction)
6. Forward the modified request upstream

```
Old: search_facts(user_msg, 5) → raw facts → <mycelium-facts>
New: recall(user_msg) → graph traversal → synthesis → <mycelium-context>
```

### 7.3 Backward Compatibility

The old `search_facts` path is deprecated but kept for transition. A config flag controls which recall mode to use:

```rust
pub enum RecallMode {
    Legacy,      // Old search_facts path (deprecated)
    GraphTraversal, // New brain graph path (default after migration)
}
```

### 7.4 Memory Instruction

The `<memory>` instruction (asking the LLM to annotate its response) is preserved unchanged. The recall system and the annotation system are independent:

- **Recall** (this spec): reads the brain graph to inject context *into* LLM requests
- **Annotation** (existing): extracts `<memory>` blocks *from* LLM responses to feed `consolidate_entry()`

---

## 8. Claude Code / Thinking Model Compatibility

### 8.1 The Thinking Token Protocol

Models with internal reasoning (Claude's `<thinking>` tags, etc.) have two token streams:
- **Thinking tokens** — internal reasoning, invisible to the user, consumed from the context window
- **Visible tokens** — the model's actual response

The recall system must work cleanly with both.

### 8.2 Design Rules for Thinking Models

1. **The `<mycelium-context>` block is injected into the system prompt**, not the visible message. This means it's available during both thinking and response phases, without the model having to reason about its own context injection.

2. **The context block is clearly delimited** with XML tags:
   ```
   <mycelium-context>
   ... synthesized content ...
   </mycelium-context>
   ```
   The model can reference it during thinking without confusion.

3. **Token-efficient format.** The context block uses minimal formatting:
   - Headers in `[brackets]` (1 line)
   - Bullet points for direct matches (2-3 lines each)
   - Optional temporal line per cluster

4. **The `relational` intent** is prioritized for broad queries because it maps naturally to associative reasoning — the LLM thinks better when given "here's what was happening around X" than a raw fact dump.

### 8.3 Reliability Properties

| Property | Mechanism |
|----------|-----------|
| **Deterministic core** | Graph traversal (SQL) is fully deterministic — same query, same atoms, same traversal, same clusters |
| **LLM surface area** | Only the query parser and context synthesizer use LLM calls — ~280 lines of prompt, nothing that can cascade-fail |
| **Graceful degradation** | Query parser fails → default atoms from raw user message. Traversal empty → "No memories found". Synthesizer fails → return raw cluster JSON |
| **Token-safe** | Hard budget cap prevents runaway token consumption. Traversal engine never produces more than 50 clusters regardless of graph size |
| **Idempotent reads** | Reading the brain graph never modifies it. Recall is safe to retry |
| **No raw entry access** | The recall system never reads or exposes raw entry text — it only reads atoms, edges, and summaries from the processed graph |

### 8.4 Fallback Chain

If any component fails, the system degrades gracefully:

```
Query Parser fails
  → Use raw user message as the single seed atom phrase
  → Continue with Graph Traversal

Graph Traversal returns empty
  → Respond "No memories found" → Context block is minimal
  → Skip synthesis entirely (zero additional tokens)

Context Synthesizer fails
  → Return raw cluster JSON as fallback context block
  → Ugly but functional — the LLM can still read it

All systems fail
  → Inject no context block → Proxy passes request through as-is
  → LLM gets no memory context but still operates normally
```

---

## 9. The Infinite Context Model

### 9.1 How It Achieves Infinite Context

The key architectural insight: **the context window carries only the current turn's context, not the session history.** Memory is externalized to the brain graph.

```
Conventional LLM session:
  context_window = [turn_1_text, turn_2_text, ..., turn_N_text]  ← grows unbounded
  
Mycelium session (with Graph Recall):
  context_window = [current_turn_input, <mycelium-context>]       ← fixed size
```

The `<mycelium-context>` block replaces the accumulated history with a *compressed, relevant slice*. This slice is recomputed fresh on every turn — it reflects what's relevant *now*, not what happened *then*.

### 9.2 What Changes for the LLM

The LLM's behavior adapts naturally:

- **Without recall:** The LLM carries full history in context — it can reference anything explicitly mentioned, but context grows linearly with session length
- **With recall:** The LLM receives a synthesized context block that is relevant to the current query — it can answer questions about anything in the brain graph, even from sessions before the current one started

### 9.3 What Doesn't Change

- **The hash chain** — raw entries are still recorded, timestamped, and hash-chained for tamper evidence
- **Session isolation** — each proxy session's entries are still tagged and queryable
- **The brain's write path** — `consolidate_entry()` continues to build atoms and edges from every annotated turn

### 9.4 Session Persistence

The brain graph persists across sessions. This means:

- Recall from session B can reference memories from session A
- "What did we do last week?" works even if "last week" was a different session entirely
- The graph is the unified memory — sessions are just organizational boundaries within it

---

## 10. Error Handling & Reliability

### 10.1 Error Matrix

| Failure Point | Symptom | Recovery | Latency Impact |
|---------------|---------|----------|----------------|
| Query parser LLM timeout | No atoms | Fall back to raw user message as seed | +100ms retry |
| Query parser returns malformed JSON | Parse error | Default to `{atoms: [], intent: "relational", temporal_hint: null}` | +10ms |
| SQL connection failure | Brain tables unreachable | Return empty result → no context injected | +5ms |
| SQL query timeout (>100ms) | Slow query | Kill query, return partial results | Configurable timeout |
| Synthesizer LLM timeout | No context block | Return raw cluster JSON as fallback | +200ms retry |
| All systems fail | No context injected | Proxy passes request through unmodified | N/A |

### 10.2 Rate Limiting

The recall system should not be callable faster than once per second per session. This prevents token waste on rapid-fire queries. The existing `Semaphore` in the proxy can be shared with the recall pipeline.

### 10.3 Concurrency

The brain graph is read-only during recall (reads never mutate). Multiple simultaneous recall queries are safe with SQLite's WAL mode — readers never block readers.

---

## 11. Testing Strategy

### 11.1 Unit Tests

| Test | What It Verifies |
|------|-----------------|
| Query parser empty input | Returns empty atoms, default intent |
| Query parser known patterns | Correctly extracts atoms from 10+ example queries |
| Graph traversal empty graph | Returns empty for any input |
| Graph traversal seeded graph | Returns expected clusters for known atoms |
| Graph traversal temporal filter | Correctly filters by time window |
| Context synthesizer empty | Returns "No memories found" |
| Context synthesizer budget | Stops adding clusters at budget limit |
| Proxy integration | Injects context block at correct position in system prompt |

### 11.2 Integration Tests

| Test | What It Verifies |
|------|-----------------|
| End-to-end recall | Full pipeline: LLM annotation → graph write → recall same atoms → context injection |
| Cross-session recall | Session B can recall atoms created in Session A |
| Token budget accuracy | Synthesis stays within configured budget |
| Fallback chain | Each fallback activates correctly on failure |
| Thinking model compatibility | Context block is parseable and well-formed |

### 11.3 Performance Benchmarks

| Metric | Target | Measurement |
|--------|--------|-------------|
| Query parser latency | <300ms (P95) | LLM call for atom extraction |
| Graph traversal latency | <10ms (P95) | SQL queries on indexed tables |
| Context synthesis latency | <1000ms (P95) per 1000 tokens | LLM call for prose generation |
| End-to-end recall latency | <1500ms (P95) at 5K budget | Total pipeline |
| Token budget accuracy | Within 10% of configured budget | Count actual tokens vs budget |
| Graph size scalability | <10ms at 1M atoms | Indexed read performance |

---

## 12. Token Budget Recommendations

| Use Case | Budget | Rationale |
|----------|--------|-----------|
| Default | 1000 | Good balance for most queries |
| Recommended ceiling | 5000 | Rich context, covers complex queries |
| Maximum | 20000 | For exploratory/deep-dive recall |
| Quick fact lookup | 200 | Query parser only, minimal synthesis |
| No results | ~0 | Skip synthesis entirely |

The user sets their preferred budget. At each recall, the system fills up to budget or cluster exhaustion, whichever comes first.

---

## 13. Future: Context Window Feature (Phase 2)

This spec defines the core recall pipeline. A future phase can extend it into a full **Context Window Management** system:

- **Automatic recall on session start** — when a new session begins, run recall on a system-defined query (e.g., "what was I working on?") and inject the result as initial context
- **Periodic recall refresh** — every N turns, re-run recall so the context stays fresh as the conversation drifts
- **Tiered recall** — inject a short context by default, defer rich context to explicit queries
- **Proactive recall** — the model can call the recall tool itself when it senses it needs context (via MCP or tool use)

These are not part of this spec. They will be designed in a follow-up.

---

## 14. Implementation Plan

### Phase 1: Core Pipeline (this spec)

1. Implement **Query Parser** — small LLM call, structured output, error handling
2. Implement **Graph Traversal Engine** — wire existing brain functions into a pipeline
3. Implement **Context Synthesizer** — LLM call with token budget control
4. Update **Proxy Integration** — replace `search_facts` call with recall pipeline
5. Add **config flag** (`RecallMode::GraphTraversal`) for migration
6. Write **unit tests + integration tests**

### Phase 2: Testing & Optimization

7. Performance benchmark against 10K, 100K, 1M atom graphs
8. Test with Claude Code (thinking models) for compatibility
9. Tune token budget defaults based on real usage
10. Write fallback integration tests (simulate each failure mode)

### Phase 3: Shipping

11. Default to `GraphTraversal` mode, keep `Legacy` as opt-out
12. Deprecate `search_facts` path
13. Document recall API for CLI / MCP / desktop app users

---

## 15. Open Questions

1. **Query parser model** — Should the parser use the same model as the main conversation, or a separate fast/cheap model? Recommendation: same model, for simplicity and quality. The 200 tokens are negligible.

2. **Context synthesizer model** — Same question. Recommendation: same model, for coherence. The synthesizer produces text the LLM will read — using the same model avoids style/tone mismatches.

3. **Cache frequently-asked queries** — Should we cache the results of `recall("what was I working on")` since it's likely to be asked repeatedly? Recommendation: yes, with a short TTL (30 seconds). Brain graphs don't change second-to-second during a session.

4. **Temporal hint parsing** — How far should we go with relative time parsing ("last night", "yesterday", "2 weeks ago", "in June")? Recommendation: support the 5 most common relative patterns initially, extend as needed.

5. **Claude Code MCP integration** — Should the recall system be exposed as an MCP tool for Claude Code to call directly? This would let Claude Code ask "do I know about X?" mid-conversation. Recommendation: yes, as a follow-up after the core proxy pipeline is stable.

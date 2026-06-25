# Graph-Guided Recall — Pending Work & Optimization Areas

**Date:** 2026-06-25
**Status:** Live but needs refinement

---

## Critical Fixes

### 1. Context synthesis LLM call is too slow (~60s timeout)

The context synthesizer calls the LLM to turn graph clusters into readable prose. With `kimi-k2.6`, this call takes **45–66 seconds**, often timing out the reqwest client (60s default).

**Symptoms:**
```
⚠️  LLM synthesis failed, using fallback template (48032ms)
```

**Root cause:** The synthesizer uses the same model as the main chat, which has heavy thinking overhead. The model spends most tokens on reasoning before producing the short `<mycelium-context>` text.

**Options:**
- **(A)** Use a smaller/faster model (e.g., `kimi-k2.6-lite` or `minimax-m3-fast`) specifically for the recall pipeline — separate `MYCELIUM_SYNTHESIS_MODEL` env var
- **(B)** Lower synthesizer timeout to fail fast and fall back to template (which already works well)
- **(C)** Skip LLM synthesis entirely and use `build_fallback_context()` by default — the template output is clean and readable

**Recommendation:** (C) for now — the fallback template produces perfectly usable `<mycelium-context>` blocks and adds zero latency. Revisit when a faster model is available.

### 2. Temporal hint heuristics are too basic

The graph traversal only handles `"night"` and `"yesterday"` as temporal hints. All other time references (`"last week"`, `"on June 24"`, `"in March"`) are silently ignored.

**Impact:** Temporal queries don't filter by time, returning results from any period instead of the requested window.

**Fix:** Add a proper time parser in the temporal filter step of `recall::traverse()` in `crates/mycelium-core/src/recall.rs`.

---

## Important Optimizations

### 3. Recall fires on every request, including system messages

The proxy currently calls `run_recall_pipeline()` on **every** intercepted LLM request, including internal system messages like `"[Your previous response had no visible output...]"`. This wastes tokens and adds latency.

**Fix:** Add a filter in `intercept_and_forward` to skip recall when the user message looks like a system/internal message (short, no meaningful content).

### 4. Query parser uses the same model as main chat

The query parser (atom extraction) is a simple task but uses the full model with thinking overhead. Each call costs ~200 input tokens + the model's thinking output.

**Fix:** Add `MYCELIUM_PARSER_MODEL` env var defaulting to a faster/cheaper model separate from the main chat.

### 5. No caching for repeated queries

If you ask the same question twice within a session, the recall pipeline runs twice — two LLM calls, two traversals.

**Fix:** Add a simple TTL cache (30s) for recall results. Short enough that fresh memories don't get stale, long enough to avoid duplicate work.

### 6. Brain work queue may not be processing

The `pending_brain_work` table has 1 or more items that haven't been consolidated into the atom graph. If the brain background worker isn't running, new annotations never become atoms.

**Check:**
```sql
SELECT COUNT(*) FROM pending_brain_work;
```

**Fix:** Ensure the brain daemon is actively processing the work queue via `dequeue_pending()` + `consolidate_entry()`.

---

## Nice-to-Have

### 7. Atom extraction quality depends on LLM annotation quality

The brain is only as good as the `<memory>` annotations the LLM produces. If annotations are sparse or generic, the graph has weak signal.

**Monitor:** Check average annotation length per session in the `entries` table.

### 8. Proxy injection format could be compressed

The `<mycelium-context>` block uses XML tags with header sections. For models with strict context windows, every token matters. The format could be compressed:

- Remove `[bracketed headers]` — rely on structure alone
- Combine temporal data into inline format
- Use CSV-like format for known tools

### 9. Cross-session recall doesn't exist yet

The recall pipeline only responds to explicit questions. It doesn't proactively surface memories from other sessions unless you ask. A future "Context Window" feature (Phase 2 in the spec) would enable:

- Automatic recall on session start
- Periodic context refresh
- Proactive memory surfacing

---

## Performance Baseline

| Metric | Current | Target |
|--------|---------|--------|
| Query parser (LLM) | ~300ms | <100ms |
| Graph traversal (SQL) | ~1-40ms | <10ms |
| Context synthesis (LLM) | ~60s (timeout) | Skip — use template |
| End-to-end (fallback) | ~800-1200ms | <500ms |
| Brain atoms | 371K | N/A |
| Brain edges | 1M | N/A |
| Tests | 51 pass | 51 pass |

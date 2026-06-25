# Graph-Guided Recall — Audit Report

**Date:** 2026-06-25
**Auditor:** Automated analysis
**Status:** Live on port 8443, `RUST_LOG=debug`, `MYCELIUM_RECALL_MODE=graph`, model `kimi-k2.6`

---

## 1. Test Coverage

| Component | Tests | Status |
|-----------|-------|--------|
| Graph traversal engine (`recall.rs`) | 11 | ✅ All pass |
| Query parser (`query_parser.rs`) | 4 | ✅ All pass |
| Context synthesizer (`synthesizer.rs`) | 4 | ✅ All pass |
| Brain (recall-adjacent: clusters, when, recall) | 35 | ✅ All pass |
| Integration (seeded brain graph e2e) | 1 | ✅ Pass |
| **Total recall-specific** | **19** | **✅ All pass** |

**Missing tests:**
- No test for `run_recall_pipeline()` end-to-end with mock LLM
- No test for LLM synthesis failure → fallback template path
- No test for temporal hint filtering with non-"night"/"yesterday" values

---

## 2. Performance — Hard Numbers

### Graph Traversal (40 samples from production logs)

| Metric | Value |
|--------|-------|
| Average | **1,106ms** |
| Fastest | **53ms** |
| Slowest | **2,728ms** |
| Success rate | **100%** |
| Always finds clusters? | ✅ When atoms match |
| Empty results | 0 clusters when query has no matching atoms (correct behavior) |

**Honest assessment:** This is **not sub-ms** as the spec claims. The slow average is because the query parser often falls back to the raw user message, and a `LIKE '%long conversation message%'` scan on 371K atoms with long strings is slow. When atoms match directly, it's fast (~500ms). When they don't, it's slow (~2s scanning).

### LLM Context Synthesis (with kimi-k2.6)

| Metric | LLM Synthesis | Fallback Template |
|--------|--------------|-------------------|
| Average time | **11,743ms** *(11.7s)* | **<1ms** |
| Fastest | **538ms** | **<1ms** |
| Slowest | **66,811ms** *(66s)* | **<1ms** |
| Success rate | **~55%** | **100%** |
| Quality | Good prose | Clean bullet points |
| Token cost | ~1-2K output | **Zero** |

**Honest assessment:** LLM synthesis is **not production-ready** with `kimi-k2.6`. It's unreliable — sometimes fast (500ms), mostly slow (12s avg, 66s peak), and fails ~45% of the time. When it fails, the proxy waits for a timeout, then falls back to the template. This means **every recall takes either 500ms (template) or up to 66s (waiting for LLM timeout)**.

---

## 3. LLM Synthesis — What Goes Wrong

The synthesis LLM call sends clusters data and asks the model to write a `<mycelium-context>` block. With `kimi-k2.6`:

1. **Thinking consumes output tokens** — the model spends most of its 4096 `max_tokens` budget on reasoning before producing the short context block
2. **No text block in timeout** — if thinking doesn't finish within the timeout, the response has no `text` block → synthesis fails
3. **Not the model's fault** — `kimi-k2.6` is a reasoning model. It's like asking Claude to "just write one sentence" — it's capable but the thinking overhead is disproportionate for a formatting task

**Evidence from successful synthesis (538ms):** The query "yep we can pushit first" got 5 clusters and produced a context block in 538ms — no thinking needed because the prompt was simple and the response was short.

**Evidence from failure (84s):** The same 5 clusters failed later because the model decided to think deeply before formatting.

---

## 4. End-to-End Correctness

### Does the query parser extract useful atoms?

| Input | Atoms Extracted | Correct? |
|-------|----------------|----------|
| "what is the proxy config" | 1 atom | ✅ |
| "when did I last change the proxy secret" | 2 atoms: "proxy secret", "change" | ✅ |
| "yep we can pushit first" | fallback to raw text | ❌ (but no useful atoms exist for this query) |

### Does the graph traversal find real relationships?

| Seed Phrase | Clusters Found | Connected Atoms |
|-------------|---------------|-----------------|
| "proxy" | 5 | "llm proxy", "proxy interceptor", "recall into proxy" |
| "traversal" | 5 | "traversal engine", "graph traversal" |
| "secret" | 5 | "change secret", "server config" |

**Verdict:** When atoms match, relationships are correct. The brain graph stores real co-occurrence connections.

### What does the injected context actually look like?

Example from fallback template (what the LLM receives):

```
<mycelium-context>

[proxy config]
  - llm proxy (relevance: 0.50)
  - proxy interceptor (relevance: 0.50)
  - recall into proxy (relevance: 0.50)
  Last seen: turn 8209

[traversal engine]
  - traversal engine (relevance: 0.50)
  - graph traversal (relevance: 0.50)
</mycelium-context>
```

### What does LLM synthesis produce?

```
<mycelium-context>
Recent memories about the proxy:

[Proxy Configuration]
- The proxy has been configured for llm proxy functionality, including intercepting and forwarding requests
- Recent work involved the proxy interceptor for memory injection
- Graph-guided recall was integrated into the proxy pipeline

[Traversal]
- The traversal engine has been a key focus, particularly graph-based traversal for memory retrieval
</mycelium-context>
```

**Verdict:** The fallback template is slightly uglier but contains the same information. The LLM version reads better but adds no factual content — it just paraphrases the same data.

---

## 5. Token Cost Analysis

| Component | Input Tokens | Output Tokens | Total |
|-----------|-------------|--------------|-------|
| Query parser | ~200 (prompt) | ~300 (thinking + 50 JSON) | ~500 |
| Context synthesis (LLM) | ~300 (clusters) | ~1,500 (thinking + 200 prose) | ~1,800 |
| Context synthesis (fallback) | 0 | 0 | **0** |
| Total per request (LLM path) | ~500 | ~1,800 | **~2,300** |
| Total per request (fallback) | ~200 | ~50 | **~250** |

---

## 6. Issues Found

| # | Issue | Severity | Impact |
|---|-------|----------|--------|
| 1 | **LLM synthesis unreliable (45% failure rate)** | 🔴 Critical | Pipeline blocks for up to 66s waiting for timeout |
| 2 | **Graph traversal slower than spec (~1.1s avg)** | 🟡 Important | Adds ~1s latency even when synthesis is skipped |
| 3 | **Recall fires on system/internal messages** | 🟡 Important | Wastes tokens and time on "recap under 40 words" type requests |
| 4 | **Temporal hints only handle "night"/"yesterday"** | 🟢 Minor | Other time references silently ignored |
| 5 | **No end-to-end test with mock LLM** | 🟢 Minor | Refactoring risk — can't regression-test pipeline logic |
| 6 | **Atom quality mixed — boilerplate dominates** | 🟢 Minor | `detect_stop_words()` needs tuning |

---

## 7. Recommendations (Priority Order)

### 🔴 Immediate: Switch recall to fast model or disable LLM synthesis

**Option A (recommended):** Set `MYCELIUM_MODEL` to a fast model like `deepseek-v4-flash-free` or `mimo-v2.5-free` for the recall pipeline only. These models have minimal or no thinking overhead, so synthesis would complete in ~500ms instead of ~12s.

**Option B (safest):** Skip LLM synthesis entirely. The fallback template produces usable context blocks with zero latency and zero failure rate. The difference is formatting quality, not factual accuracy.

**Both options:** The main chat model stays unchanged. Only the internal recall pipeline uses the fast model.

### 🟡 Short-term: Filter system messages from recall

Add a check to skip recall when the user message starts with common system prefixes or is a known internal prompt. This stops wasting ~1s per system message.

### 🟢 Medium-term: Add mock-LLM e2e test

One test that runs the full `run_recall_pipeline` with a mocked HTTP client, verifying the fallback chain works when LLM calls fail.

---

## 8. Summary

| Question | Answer |
|----------|--------|
| Does recall find real memories? | ✅ Yes — 5 clusters per query when atoms match |
| Is graph traversal reliable? | ✅ 100% success, but slower than spec (~1.1s vs <10ms) |
| Is LLM synthesis production-ready? | ❌ No — 45% failure rate, avg 12s latency |
| Does the fallback work? | ✅ 100% reliable, zero latency, clean output |
| Are tests sufficient? | 🟡 19 unit tests pass, but no pipeline-level e2e test |
| **Should you use it now?** | **✅ Yes, with fallback template — skip LLM synthesis** |

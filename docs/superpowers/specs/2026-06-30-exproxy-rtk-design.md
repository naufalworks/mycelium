# Exproxy + RTK: Token Compression & Format Translation

**Date:** 2026-06-30
**Status:** Design Draft

## Problem

LLM conversations with coding tools produce massive tool outputs (git diffs, grep results, build logs, directory trees) that get sent to the LLM as `tool_result` content. These can be 10-100KB per message, and Claude Code often sends accumulated context.

9Router's RTK (Real-Time Token compression) saves 20-40% on input tokens by auto-detecting command output types and compressing them losslessly before sending to the LLM.

Additionally, exproxy needs format translation to handle multiple API formats (OpenAI, Anthropic, Gemini) and translate them to Kimchi's OpenAI-compatible format.

## Solution: Standalone RTK Service + Enhanced Exproxy

### Architecture

```
                     ┌─────────────────┐
                     │   Mycelium       │
                     │   Proxy (8443)   │
                     └────────┬────────┘
                              │
                              ▼
┌─────────────────────────────────────────┐
│           RTK Service (9098)             │
│  ↕ auto-detect + compress tool_outputs   │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│    Exproxy (9099)                        │
│  ↕ format translate (Anthropic/Gemini)   │
│  ↕ model fallback (kimi→minimax→kimi2.6) │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│        Kimchi API (llm.kimchi.dev)       │
└─────────────────────────────────────────┘

Optional direct path (bypass RTK when no tool results):
Client → Exproxy (9099) → Kimchi
```

## Component 1: RTK Microservice

### Flow

```
POST /v1/chat/completions
POST /v1/messages
  ↓
Read body
  ↓
Has tool_result content?
  ├── YES → autoDetectFilter()
  │         ├── matches git diff → gitDiff compact
  │         ├── matches git status → gitStatus compact
  │         ├── matches build output → buildOutput compact
  │         ├── matches grep → grep compact
  │         ├── matches find → find compact
  │         ├── matches tree → tree compact
  │         ├── matches ls → ls compact
  │         ├── matches read-numbered → readNumbered compact
  │         ├── matches search-list → searchList compact
  │         ├── lines > threshold → smartTruncate
  │         └── default → dedupLog
  │         ↓
  │   Forward compressed body to upstream
  │
  └── NO → Forward raw body to upstream (zero added latency)
```

### Key design decisions

1. **Detection is regex-based, not LLM-based** — fast (< 1ms), deterministic
2. **Compression is lossless** — all information preserved, just deduplicated and compacted
3. **Pass-through for non-tool messages** — RTK adds no latency to simple chat exchanges
4. **Stateless** — no database, no cache, no state needed

## Component 2: Format Translation (in Exproxy)

### Existing (done)
- Anthropic `/v1/messages` format → OpenAI `/v1/chat/completions` format (anthropic.go)
- Response conversion: OpenAI `choices[0].message.content` → Anthropic `content[0].text`
- Streaming conversion: OpenAI SSE → Anthropic SSE (`content_block_delta`, `message_delta`)

### To add
- Gemini format → OpenAI format
- OpenAI Responses API (`/v1/responses`) → OpenAI Chat format (used by Codex)

## 11 RTK Filters

| # | Filter | Detects | What it does |
|---|--------|---------|-------------|
| 1 | `gitDiff` | `diff --git` | Compact file headers, truncate hunks at 100 lines, count +/-/context lines |
| 2 | `gitStatus` | `On branch` / porcelain | Strip per-file details, keep branch summary and file lists |
| 3 | `buildOutput` | npm/cargo/maven errors | Keep error lines, skip success noise |
| 4 | `grep` | `file:line:content` | Dedup adjacent matches from same file |
| 5 | `find` | `./path` patterns | Compact deep directory prefixes |
| 6 | `tree` | `├──` / `└──` glyphs | Collapse single-child leaf directories |
| 7 | `ls` | `-rwxr-xr-x` patterns | Strip permissions/user/group, keep names only |
| 8 | `smartTruncate` | > 100 lines | Keep first 50 + summary + last 20 |
| 9 | `readNumbered` | `N:line` pattern | Dedup adjacent identical blocks |
| 10 | `searchList` | `N matches` header | Collapse runs into N matches notation |
| 11 | `dedupLog` | repeated lines | Remove duplicate adjacent lines |

## Configuration

```yaml
kimchi:
  api_key: "castai_..."
  base_url: "https://llm.kimchi.dev/openai/v1"

server:
  addr: ":9099"
  timeout: 120

rtk:
  enabled: true
  listen_addr: ":9098"
  upstream_url: "http://localhost:9099"
```

## File Structure

```
exproxy/
├── rtk/
│   ├── main.go               # entry point, HTTP server, proxy handler
│   ├── detector.go           # auto-detect filter from tool output
│   ├── constants.go          # regex patterns, thresholds
│   └── filters/
│       ├── gitdiff.go
│       ├── gitstatus.go
│       ├── buildoutput.go
│       ├── grep.go
│       ├── find.go
│       ├── tree.go
│       ├── ls.go
│       ├── smarttruncate.go
│       ├── readnumbered.go
│       ├── searchlist.go
│       └── deduplog.go
├── internal/
│   ├── config/config.go
│   └── proxy/
│       ├── proxy.go          # shared helpers, model fallback
│       ├── openai.go         # /v1/chat/completions handler
│       ├── anthropic.go      # /v1/messages handler + format conversion
│       └── format.go         # format helper utilities
├── cmd/
│   └── exproxy/main.go
├── config.yaml
├── go.mod
└── go.sum
```

## RTK vs Exproxy Dependencies

| Aspect | RTK Service | Exproxy |
|--------|-------------|---------|
| Depends on | Upstream URL (config) | Kimchi API key + URL |
| State | None | None |
| Scaling | Multiple instances OK | Multiple instances OK |
| Startup | Immediate | Immediate |
| Failure | Pass-through (no compress) | 502 if Kimchi down |

## Success Criteria

| Metric | Target |
|---|---|
| Detection accuracy | > 99% (no misclassification) |
| Compression ratio | 20-50% for tool outputs |
| Zero compression on chat | 100% pass-through for non-tool msgs |
| Additional latency | < 1ms per request (regex detection) |
| Format translation | All → OpenAI correct for all fields |

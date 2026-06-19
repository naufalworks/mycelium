#!/usr/bin/env python3
"""
Mycelium LLM Integration Layer.

OpenAI-compatible API wrapper for fact extraction, query translation,
and entropy scoring. Points to a local LLM server.

Config (from mycelium_lib or env):
  LLM_HOST = "http://127.0.0.1:8443/v1"
  LLM_KEY = "anything"
  LLM_MODEL = "kimi-k2.6"
"""

import json, os, time
from typing import Optional
from pathlib import Path

# ── Config ──────────────────────────────────────────────
LLM_HOST = os.environ.get("MYCELIUM_LLM_HOST", "http://127.0.0.1:8443/v1")
LLM_KEY = os.environ.get("MYCELIUM_LLM_KEY", "anything")
LLM_MODEL = os.environ.get("MYCELIUM_LLM_MODEL", "kimi-k2.6")

# Track usage
_total_tokens = 0
_total_calls = 0


def stats():
    return {"calls": _total_calls, "tokens": _total_tokens}


def _call_llm(prompt: str, system: str = "", temperature: float = 0.1,
              max_tokens: int = 2048) -> Optional[str]:
    """Call the LLM via OpenAI-compatible chat completions endpoint."""
    global _total_tokens, _total_calls

    try:
        import urllib.request
        payload = json.dumps({
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()

        req = urllib.request.Request(
            f"{LLM_HOST}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {LLM_KEY}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode())
            _total_calls += 1
            usage = resp.get("usage", {})
            _total_tokens += usage.get("total_tokens", 0)

            choices = resp.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return None

    except Exception as e:
        return None  # Silent fail — non-blocking


def extract_facts(session_entries: list[str],
                  session_id: str = "unknown") -> list[dict]:
    """Extract structured facts from session entries using LLM.

    Returns list of fact dicts:
      {entity, attribute, value, fact_type, confidence, entropy}
    """
    # Take last ~30 entries (token window)
    sample = session_entries[-30:] if len(session_entries) > 30 else session_entries
    text = "\n".join(sample)

    system = """You are a memory fact extractor. From the conversation text, extract:

1. **Credentials** — API keys, passwords, URLs, tokens (fact_type: "credential")
2. **Decisions** — architectural choices, technology selections (fact_type: "decision")
3. **Ideas** — new concepts, feature suggestions (fact_type: "idea")
4. **Preferences** — user's stated preferences (fact_type: "preference")
5. **Facts** — general knowledge about the system (fact_type: "fact")

For each fact, output JSON lines:
{"entity": "...", "attribute": "...", "value": "...", "fact_type": "...", "confidence": 0.0-1.0, "entropy": 0.0-1.0}

- confidence = how sure you are this fact is correct (1.0 = certain)
- entropy = how surprising/important this fact is (0.0 = mundane, 1.0 = very novel)
- Keep credentials' values masked (show only first/last 4 chars)
- deduplicate: don't repeat the same (entity, attribute, value)

Output ONLY JSON lines, one per fact. No markdown, no explanation."""

    result = _call_llm(text, system=system, temperature=0.05)
    if not result:
        return []

    facts = []
    for line in result.strip().split("\n"):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                f = json.loads(line)
                if all(k in f for k in ("entity", "attribute", "value")):
                    facts.append(f)
            except json.JSONDecodeError:
                continue
    return facts


def query_to_sql(nl_question: str) -> Optional[str]:
    """Translate a natural language recall question to SQL over memory_facts.

    Schema:
      memory_facts(entity, attribute, value, confidence, fact_type, tier, entropy,
                   source_session, created_at, updated_at)

    Returns SQL string or None.
    """
    system = """You translate natural language questions to SQLite queries.

Schema:
  memory_facts(entity TEXT, attribute TEXT, value TEXT, confidence REAL,
               fact_type TEXT, tier INTEGER, entropy REAL,
               source_session TEXT, created_at TEXT, updated_at TEXT)

  context_snapshots(session_id TEXT, summary TEXT, topics TEXT, decisions TEXT,
                    entities TEXT, credentials TEXT, turn_count INTEGER,
                    last_turn_hash TEXT, created_at TEXT)

Rules:
- Use LIKE for fuzzy matching, = for exact
- fact_type values: credential, decision, idea, preference, fact
- tier 0=hot, 1=warm, 2=cool, 3=cold
- Return ONLY the SQL query. No explanation, no markdown.
- SELECT * FROM memory_facts unless aggregation needed
- ORDER BY confidence DESC, updated_at DESC LIMIT 20"""

    result = _call_llm(nl_question, system=system, temperature=0.05, max_tokens=512)
    if not result:
        return None

    # Clean up — remove markdown code fences if present
    result = result.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[-1]
    if result.endswith("```"):
        result = result.rsplit("```", 1)[0]
    result = result.strip()

    # Basic validation: must start with SELECT
    if not result.upper().startswith("SELECT"):
        return None
    return result


def score_entropy(fact_text: str, context: str = "") -> float:
    """Score how surprising/important a fact is (0.0 = mundane, 1.0 = highly novel)."""
    system = """Rate the information entropy of this fact on 0.0-1.0:

0.0-0.3: Routine/common (e.g. "I use Python", "I use VS Code")
0.3-0.6: Notable (e.g. "Database is PostgreSQL", "API key for service X")
0.6-0.8: Surprising (e.g. "Chose MySQL over PostgreSQL for X reason", "Critical security finding")
0.8-1.0: Highly novel (e.g. "Invented new algorithm", "Unique architectural insight")

Output ONLY a float between 0.0 and 1.0, nothing else."""

    result = _call_llm(f"Fact: {fact_text}\nContext: {context}", system=system,
                       temperature=0.1, max_tokens=32)
    if not result:
        return 0.5
    try:
        return max(0.0, min(1.0, float(result.strip())))
    except ValueError:
        return 0.5


def summarize_session(session_entries: list[str],
                      session_id: str = "unknown") -> Optional[dict]:
    """Generate a structured summary of a session for snapshot creation."""
    text = "\n".join(session_entries[-50:]) if session_entries else ""

    system = """Read this conversation and output a JSON summary:

{
  "summary": "One-sentence summary of the session (max 40 words)",
  "topics": ["topic1", "topic2"],
  "decisions": ["decision1", "decision2"],
  "entities": ["entity1", "entity2"],
  "credentials": [{"service": "name", "type": "api_key|password|url|token", "value": "masked"}],
  "turn_count": 123
}

- Keep summary under 40 words
- Topics are general subjects discussed
- Decisions are choices made during the session
- Entities are tools, services, technologies mentioned
- Mask credential values (show first/last 4 chars)
- If session is empty/minimal, return {"summary": "No significant content"}
Output ONLY the JSON. No other text."""

    result = _call_llm(text, system=system, temperature=0.05, max_tokens=1024)
    if not result:
        return None

    # Extract JSON
    result = result.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[-1]
    if result.endswith("```"):
        result = result.rsplit("```", 1)[0]
    result = result.strip()

    try:
        d = json.loads(result)
        d["session_id"] = session_id
        return d
    except json.JSONDecodeError:
        return None

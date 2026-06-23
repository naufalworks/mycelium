#!/usr/bin/env python3
"""
Mycelium Cross-Session Inference Engine.

Reads all context snapshots + memory facts and uses the LLM to:
  1. Discover cross-session patterns (your recurring workflows)
  2. Detect tool/service usage trends
  3. Identify knowledge gaps (things you keep re-learning)
  4. Generate a "user model" — what you care about, your preferences

This is what makes the memory layer *intelligent* — not just storage,
but meta-cognition about what the user does across sessions.
"""

import json, sqlite3
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone


def _conn():
    from mycelium_lib import INDEX
    c = sqlite3.connect(str(INDEX))
    c.row_factory = sqlite3.Row
    return c


def _call_llm(prompt: str, system: str = ""):
    """Call the local LLM (kimi-k2.6)."""
    try:
        from mycelium_llm import _call_llm as llm_call
        return llm_call(prompt, system=system, temperature=0.2, max_tokens=4096)
    except Exception:
        return None


def get_all_snapshots() -> list[dict]:
    """Fetch all context snapshots from DB."""
    db = _conn()
    rows = db.execute("""
        SELECT session_id, summary, topics, decisions, entities, turn_count, created_at
        FROM context_snapshots
        ORDER BY created_at DESC
        LIMIT 50
    """).fetchall()
    db.close()

    snapshots = []
    for r in rows:
        d = dict(r)
        for field in ("topics", "decisions", "entities"):
            if isinstance(d.get(field), str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
        snapshots.append(d)
    return snapshots


def get_top_facts(limit: int = 30) -> list[dict]:
    """Fetch highest-confidence facts."""
    from mycelium_memory import recall_facts
    return recall_facts(limit=limit)


def infer_patterns() -> dict:
    """Run cross-session pattern inference.

    Returns structured insights without requiring the LLM call
    (data-driven analysis first, then optional LLM enrichment).
    """
    snapshots = get_all_snapshots()
    facts = get_top_facts(20)

    # ── Data-driven analysis ────────────────────────────────────

    # Most common topics
    topic_counter = Counter()
    for s in snapshots:
        for t in (s.get("topics") or []):
            topic_counter[t.strip()] += 1

    # Most common entities
    entity_counter = Counter()
    for s in snapshots:
        for e in (s.get("entities") or []):
            entity_counter[e.strip()] += 1

    # Decision history
    all_decisions = []
    for s in snapshots:
        for d in (s.get("decisions") or []):
            all_decisions.append({
                "decision": d.strip(),
                "session": s["session_id"],
                "date": s.get("created_at", "")[:10],
            })

    # Credentials count
    cred_count = sum(1 for f in facts if f.get("fact_type") == "credential")

    # Session timeline
    total_turns = sum(s.get("turn_count", 0) for s in snapshots)
    session_dates = [s.get("created_at", "")[:10] for s in snapshots if s.get("created_at")]
    unique_dates = sorted(set(d for d in session_dates if d))

    # Frequent query topics (from facts that are facts about user patterns)
    pattern_facts = [f for f in facts if f.get("fact_type") == "pattern" or
                     f.get("entity") == "user" and f.get("attribute") == "frequent_topic"]

    insights = {
        "total_sessions_analyzed": len(snapshots),
        "total_turns_across_sessions": total_turns,
        "active_days": len(unique_dates),
        "most_common_topics": topic_counter.most_common(10),
        "most_common_entities": entity_counter.most_common(10),
        "decision_count": len(all_decisions),
        "credential_count": cred_count,
        "decisions": all_decisions[-10:],  # last 10
        "topics_over_time": _topics_over_time(snapshots),
        "session_frequency": _session_frequency(snapshots),
    }

    # ── LLM enrichment ──────────────────────────────────────────
    try:
        llm_insights = _llm_enrich(snapshots, facts)
        if llm_insights:
            insights["llm_insights"] = llm_insights
    except Exception:
        insights["llm_insights"] = None

    return insights


def _topics_over_time(snapshots: list[dict]) -> list[dict]:
    """How topics evolve across sessions."""
    timeline = []
    for s in reversed(snapshots):
        date = s.get("created_at", "")[:10]
        if date and s.get("topics"):
            timeline.append({
                "date": date,
                "session": s["session_id"],
                "topics": (s.get("topics") or [])[:4],
            })
    return timeline[-15:]  # last 15


def _session_frequency(snapshots: list[dict]) -> dict:
    """Aggregate session stats."""
    dates = [s.get("created_at", "")[:10] for s in snapshots if s.get("created_at")]
    unique = sorted(set(dates))
    if len(unique) < 2:
        return {"pattern": "insufficient data"}
    from datetime import datetime
    try:
        first = datetime.fromisoformat(unique[0])
        last = datetime.fromisoformat(unique[-1])
        span = (last - first).days + 1
        return {
            "first_session": unique[0],
            "last_session": unique[-1],
            "active_days": len(unique),
            "total_days_in_period": span,
            "sessions_per_day": round(len(snapshots) / span, 2) if span > 0 else 0,
        }
    except Exception:
        return {"pattern": "date parse error"}


def _llm_enrich(snapshots: list, facts: list):
    """Use LLM to extract meta-insights from all snapshots."""
    if not snapshots:
        return None

    # Build a compact summary for the LLM
    session_summaries = []
    for s in snapshots[:15]:  # last 15
        topics = ", ".join((s.get("topics") or [])[:5])
        decisions = "; ".join((s.get("decisions") or [])[:3])
        summary = s.get("summary", "")[:120]
        session_summaries.append(
            f"Session {s['session_id']} ({s.get('created_at','')[:10]}): "
            f"{summary} | Topics: {topics} | Decisions: {decisions}"
        )

    # Top facts
    top_facts_str = "\n".join(
        f"  [{f.get('fact_type','?')}] {f.get('entity','')}.{f.get('attribute','')} = {str(f.get('value',''))[:80]}"
        for f in facts[:15]
    )

    prompt = f"""Analyze these session summaries and facts to generate user insights:

SESSIONS:
{"".join(f"  {s}" for s in session_summaries)}

TOP FACTS:
{top_facts_str}

Output a JSON object with these fields:
{{
  "your_primary_focus": "What this user mainly works on (1 sentence)",
  "tools_used": ["tool1", "tool2"],
  "work_patterns": ["pattern1", "pattern2"],
  "knowledge_gaps": ["topic user keeps re-learning"],
  "preferences": ["stated preference1"],
  "recommendations": ["what to focus on next"],
  "novel_insight": "Something about their workflow they might not have noticed"
}}

Output ONLY valid JSON. No markdown, no explanation."""

    result = _call_llm(prompt)
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
        return json.loads(result)
    except json.JSONDecodeError:
        return None


def print_insights(insights: dict):
    """Pretty-print inference results."""
    print("🧠 Cross-Session Inference")
    print("=" * 55)
    print(f"  Sessions analyzed: {insights['total_sessions_analyzed']}")
    print(f"  Total turns:       {insights['total_turns_across_sessions']}")
    print(f"  Active days:        {insights['active_days']}")

    print(f"\n📌 Most Common Topics:")
    for topic, count in insights.get("most_common_topics", []):
        bar = "█" * min(count * 3, 20)
        print(f"  {bar} {topic} ({count})")

    print(f"\n⚡ Decisions Made ({insights['decision_count']} total):")
    for d in insights.get("decisions", [])[-5:]:
        print(f"  • {d['decision']}")

    print(f"\n🔑 Credentials stored: {insights['credential_count']}")

    freq = insights.get("session_frequency", {})
    if freq.get("sessions_per_day"):
        print(f"\n📈 Session frequency: {freq['sessions_per_day']} sessions/day")

    llm = insights.get("llm_insights")
    if llm:
        print(f"\n🤖 LLM Meta-Insights:")
        if llm.get("your_primary_focus"):
            print(f"  Focus:      {llm['your_primary_focus']}")
        if llm.get("tools_used"):
            print(f"  Tools:      {', '.join(llm['tools_used'][:6])}")
        if llm.get("work_patterns"):
            print(f"  Patterns:   {'; '.join(llm['work_patterns'][:3])}")
        if llm.get("preferences"):
            print(f"  Preferences: {'; '.join(llm['preferences'][:3])}")
        if llm.get("knowledge_gaps"):
            print(f"  Gaps:       {', '.join(llm['knowledge_gaps'][:3])}")
        if llm.get("novel_insight"):
            print(f"\n  💡 Novel Insight: {llm['novel_insight']}")
        if llm.get("recommendations"):
            print(f"\n  🎯 Recommendations:")
            for r in llm["recommendations"][:3]:
                print(f"    → {r}")

    print()


if __name__ == "__main__":
    insights = infer_patterns()
    print_insights(insights)

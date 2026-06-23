from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any

from .status_service import load_entries

ALIASES: dict[str, list[str]] = {
    "cloakbrowser": [
        "cloakbrowser",
        "cloackbrowser",
        "cloak browser",
        "anti-detect browser",
        "antidetect browser",
        "browser fingerprinting",
        "profile isolation",
        "proxy session",
        "session container",
        "stealth browser",
        "threat model",
    ],
    "mycelium": ["mycelium", "observatory", "memory", "recall", "myceliumd", "memory graph"],
    "companion": ["companion", "desktop companion", "p5.js", "widget", "pywebview"],
    "page radar": ["page radar", "page-radar", "browser extension", "security audit", "page context"],
}

THREAD_ROOT = Path.home() / ".hermes" / "myceliumd" / "threads"
FEEDBACK_LOG = THREAD_ROOT / "feedback.jsonl"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")
    return slug[:80] or "untitled"


def thread_path_for(query: str) -> Path:
    return THREAD_ROOT / f"{slugify(query)}.md"


def read_feedback() -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"boost": 0, "split": 0})
    if not FEEDBACK_LOG.exists():
        return out
    for line in FEEDBACK_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        key = slugify(str(row.get("query") or row.get("thread") or ""))
        action = str(row.get("action") or "")
        if key and action in {"boost", "split"}:
            out[key][action] += 1
    return out


def write_thread_card(query: str, result: dict[str, Any]) -> dict[str, Any]:
    THREAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = thread_path_for(query)
    state = result.get("state") or {}
    sources = result.get("source_sessions") or []
    items = result.get("items") or []
    lines = [
        f"# {query.strip()}",
        "",
        f"updated: {datetime.now(timezone.utc).isoformat()}",
        f"intent: {result.get('intent')}",
        f"confidence: {result.get('confidence')}",
        f"thread: {slugify(query)}",
        "",
        "## Summary",
        str(result.get("summary") or ""),
        "",
        "## Goal",
        str(state.get("goal") or ""),
        "",
        "## Where left off",
        str(state.get("where_left_off") or ""),
        "",
        "## Decisions",
    ]
    for item in state.get("decisions") or []:
        lines.append(f"- {item.get('text','')} (session={item.get('session')} turn={item.get('turn')})")
    lines += ["", "## Next steps"]
    for item in state.get("next_steps") or []:
        lines.append(f"- {item.get('text','')} (session={item.get('session')} turn={item.get('turn')})")
    lines += ["", "## Open questions"]
    for item in state.get("open_questions") or []:
        lines.append(f"- {item.get('text','')} (session={item.get('session')} turn={item.get('turn')})")
    lines += ["", "## Blockers"]
    for item in state.get("blockers") or []:
        lines.append(f"- {item.get('text','')} (session={item.get('session')} turn={item.get('turn')})")
    lines += ["", "## Files touched"]
    for file in state.get("files_touched") or []:
        lines.append(f"- `{file}`")
    lines += ["", "## Sources"]
    for src in sources:
        lines.append(f"- {src.get('session')} turn={src.get('turn')} score={src.get('score')} hash={src.get('hash')}")
    lines += ["", "## Recent items"]
    for item in items[:5]:
        lines.append(f"- {item.get('session')}#{item.get('turn')}: {str(item.get('assistant') or item.get('user') or '')[:220]}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return {"path": str(path), "thread": slugify(query)}


def record_feedback(query: str, action: str, note: str = "") -> dict[str, Any]:
    action = normalize(action)
    if action in {"yes", "yes thats it", "yes that's it", "correct", "good"}:
        action = "boost"
    if action in {"no", "wrong", "not it", "split"}:
        action = "split"
    if action not in {"boost", "split"}:
        return {"ok": False, "message": "action must be boost or split"}
    THREAD_ROOT.mkdir(parents=True, exist_ok=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "query": query, "thread": slugify(query), "action": action, "note": note}
    with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if action == "split":
        split_path = THREAD_ROOT / f"{slugify(query)}-split-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.md"
        split_path.write_text(f"# Split: {query}\n\ncreated: {row['ts']}\nnote: {note}\n", encoding="utf-8")
        row["split_path"] = str(split_path)
    return {"ok": True, **row}


def list_thread_cards() -> dict[str, Any]:
    THREAD_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(THREAD_ROOT.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        text = path.read_text(encoding="utf-8", errors="ignore")
        title = text.splitlines()[0].lstrip("# ") if text.splitlines() else path.stem
        items.append({"name": path.stem, "title": title, "path": str(path), "updated": datetime.fromtimestamp(path.stat().st_mtime).isoformat(), "preview": text[:500]})
    return {"ok": True, "thread_root": str(THREAD_ROOT), "items": items}

RECALL_INTENT_WORDS = {
    "continue",
    "remember",
    "resume",
    "last",
    "context",
    "left",
    "off",
    "idea",
    "thing",
    "what",
    "doing",
}

PATH_RE = re.compile(r"(?:~/|/Users/|[\w.-]+/)+(?:[\w.-]+\.)+(?:py|tsx|ts|css|md|json|yaml|yml|sh)|\bMakefile\b")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9./_~ -]+", " ", (text or "").lower())).strip()


def tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", normalize(text)) if len(t) > 2}


def expand_query(query: str) -> set[str]:
    q = normalize(query)
    expanded = {q} if q else set()
    q_tokens = tokenize(q)
    for canonical, aliases in ALIASES.items():
        alias_tokens = set().union(*(tokenize(alias) for alias in aliases))
        direct = canonical in q or any(alias in q for alias in aliases)
        fuzzy = any(SequenceMatcher(None, token, canonical).ratio() >= 0.82 for token in q_tokens)
        overlap = bool(q_tokens & alias_tokens)
        if direct or fuzzy or overlap:
            expanded.add(canonical)
            expanded.update(aliases)
    return {item for item in expanded if item}


def infer_intent(query: str) -> str:
    q = normalize(query)
    if any(word in q for word in ["continue", "resume", "where left", "left off"]):
        return "continue"
    if any(word in q for word in ["why", "decided", "decision"]):
        return "why_decided"
    if any(word in q for word in ["next", "what now"]):
        return "what_next"
    if any(word in q for word in ["remember", "last context", "what were we"]):
        return "remember"
    return "search"


def entry_blob(entry: dict[str, Any]) -> str:
    return normalize(
        " ".join(
            [
                str(entry.get("session", "")),
                str(entry.get("user", "")),
                str(entry.get("assistant", "")),
                " ".join(str(e) for e in entry.get("entities", [])),
            ]
        )
    )


def parse_ts(entry: dict[str, Any]) -> float:
    raw = str(entry.get("ts") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def score_entry(entry: dict[str, Any], expanded: set[str], query_tokens: set[str], *, recency_rank: float, intent: str) -> float:
    blob = entry_blob(entry)
    entities = {normalize(str(e)) for e in entry.get("entities", [])}
    session = normalize(str(entry.get("session", "")))
    score = 0.0

    for phrase in expanded:
        phrase_n = normalize(phrase)
        if not phrase_n:
            continue
        if phrase_n in blob:
            score += 12 if phrase_n in normalize(" ".join(expanded)) else 10
        if phrase_n in entities:
            score += 8
        if phrase_n in session:
            score += 6

    blob_tokens = tokenize(blob)
    overlap = query_tokens & blob_tokens
    score += len(overlap) * 2.0

    for token in query_tokens:
        if any(SequenceMatcher(None, token, bt).ratio() >= 0.86 for bt in blob_tokens):
            score += 1.5

    if entry.get("tier") == "S":
        score += 2.0
    if entry.get("type") == "decision":
        score += 2.0

    if score <= 0:
        return 0.0

    recency_multiplier = 1.0 + (0.25 if intent == "continue" else 0.12) * recency_rank
    return score * recency_multiplier


def score_entries(query: str, entries: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    expanded = expand_query(query)
    query_tokens = tokenize(" ".join(expanded) + " " + query)
    intent = infer_intent(query)
    timestamps = [parse_ts(e) for e in entries]
    min_ts = min(timestamps) if timestamps else 0
    max_ts = max(timestamps) if timestamps else 0
    span = max(max_ts - min_ts, 1)
    feedback = read_feedback()
    query_thread = slugify(query)
    scored = []
    for idx, entry in enumerate(entries):
        ts = parse_ts(entry)
        recency_rank = ((ts - min_ts) / span) if ts else (idx / max(len(entries) - 1, 1))
        score = score_entry(entry, expanded, query_tokens, recency_rank=recency_rank, intent=intent)
        if score > 0:
            fb = feedback.get(query_thread, {})
            score += float(fb.get("boost", 0)) * 3.0
            score -= float(fb.get("split", 0)) * 2.0
            scored.append((score, entry))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def expand_by_entity(scored: list[tuple[float, dict[str, Any]]], entries: list[dict[str, Any]], limit: int) -> list[tuple[float, dict[str, Any]]]:
    top = scored[: max(3, min(limit, 8))]
    top_entities = Counter()
    seen_ids = {id(entry) for _, entry in scored}
    for _, entry in top:
        for ent in entry.get("entities", []):
            top_entities[normalize(str(ent))] += 1
    if not top_entities:
        return scored
    extras = []
    for entry in entries:
        if id(entry) in seen_ids:
            continue
        ents = {normalize(str(ent)) for ent in entry.get("entities", [])}
        overlap = ents & set(top_entities)
        if overlap:
            extras.append((2.0 + sum(top_entities[e] for e in overlap), entry))
    return sorted(scored + extras, key=lambda x: x[0], reverse=True)


def source_ref(entry: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    out = {
        "session": entry.get("session", "unknown"),
        "turn": entry.get("turn"),
        "ts": entry.get("ts"),
        "type": entry.get("type"),
        "tier": entry.get("tier"),
        "hash": entry.get("hash"),
    }
    if score is not None:
        out["score"] = round(score, 3)
    return out


def sentences(entry: dict[str, Any]) -> list[str]:
    text = " ".join([str(entry.get("user", "")), str(entry.get("assistant", ""))])
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if p.strip()]


def pack_item(text: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {"text": text[:500], **source_ref(entry)}


def extract_state(items: list[dict[str, Any]]) -> dict[str, Any]:
    goal = ""
    decisions = []
    open_questions = []
    next_steps = []
    blockers = []
    files = []
    seen_text = set()

    for entry in items:
        combined = " ".join([str(entry.get("user", "")), str(entry.get("assistant", ""))])
        for match in PATH_RE.findall(combined):
            if match not in files:
                files.append(match)
        for sent in sentences(entry):
            s_low = sent.lower()
            key = normalize(sent)[:120]
            if key in seen_text:
                continue
            seen_text.add(key)
            if not goal and any(k in s_low for k in ["goal:", "we want", "idea", "build", "mvp"]):
                goal = sent[:500]
            if entry.get("type") == "decision" or entry.get("tier") == "S" or any(k in s_low for k in ["decision:", "decided", "we should", "recommended"]):
                decisions.append(pack_item(sent, entry))
            if "?" in sent or any(k in s_low for k in ["open question", "unclear", "whether", "which"]):
                open_questions.append(pack_item(sent, entry))
            if any(k in s_low for k in ["next", "todo", "remaining", "best next", "next step"]):
                next_steps.append(pack_item(sent, entry))
            if any(k in s_low for k in ["blocked", "blocker", "failed", "error", "cannot", "permission", "tcc", "undecided"]):
                blockers.append(pack_item(sent, entry))

    where = ""
    if items:
        last = items[0]
        text = str(last.get("assistant") or last.get("user") or "")
        where = text[:650]

    return {
        "goal": goal,
        "where_left_off": where,
        "decisions": decisions[:6],
        "open_questions": open_questions[:6],
        "files_touched": files[:20],
        "next_steps": next_steps[:6],
        "blockers": blockers[:6],
    }


def related_entities(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter()
    sessions: dict[str, set[str]] = defaultdict(set)
    for entry in items:
        session = str(entry.get("session", "unknown"))
        for ent in entry.get("entities", []):
            name = str(ent)
            counts[name] += 1
            sessions[name].add(session)
    return [
        {"name": name, "count": count, "sessions": sorted(sessions[name])[:5]}
        for name, count in counts.most_common(16)
    ]


def build_summary(query: str, state: dict[str, Any], items: list[dict[str, Any]]) -> str:
    if state.get("goal"):
        return state["goal"]
    if state.get("where_left_off"):
        return state["where_left_off"][:240]
    if items:
        return f"Found {len(items)} related memory item(s) for {query}."
    return f"No strong memory found for {query}."


def confidence_from_scores(scored: list[tuple[float, dict[str, Any]]]) -> float:
    if not scored:
        return 0.0
    top = scored[0][0]
    # Smooth bounded score: 20 ~= 0.63, 40 ~= 0.86, 60 ~= 0.95
    return round(1 - math.exp(-top / 22), 3)


def recall(query: str, limit: int = 12) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"ok": False, "query": query, "message": "query required"}
    entries = load_entries()
    scored = score_entries(q, entries)
    scored = expand_by_entity(scored, entries, limit)
    top = scored[: max(1, limit)]
    items = [entry for _, entry in top]
    state = extract_state(items)
    sources = [source_ref(entry, score) for score, entry in top[:8]]
    result = {
        "ok": True,
        "query": q,
        "intent": infer_intent(q),
        "expanded_query": sorted(expand_query(q)),
        "confidence": confidence_from_scores(scored),
        "summary": build_summary(q, state, items),
        "state": state,
        "related_entities": related_entities(items),
        "source_sessions": sources,
        "items": [
            {
                **source_ref(entry, score),
                "entities": entry.get("entities", []),
                "user": str(entry.get("user", ""))[:300],
                "assistant": str(entry.get("assistant", ""))[:500],
            }
            for score, entry in top
        ],
    }
    if result["confidence"] > 0 and result["source_sessions"]:
        result["thread_card"] = write_thread_card(q, result)
    return result

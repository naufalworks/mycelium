from __future__ import annotations

from web.backend.services import recall_service


def sample_entries():
    return [
        {
            "session": "old-browser",
            "turn": 1,
            "ts": "2026-06-01T10:00:00",
            "type": "talk",
            "tier": "B",
            "entities": ["fingerprinting", "proxy"],
            "user": "we had an anti-detect browser idea",
            "assistant": "Goal: build privacy browser research with profile isolation. Next: define MVP threat model.",
            "hash": "a",
        },
        {
            "session": "new-browser",
            "turn": 2,
            "ts": "2026-06-11T10:00:00",
            "type": "decision",
            "tier": "S",
            "entities": ["cloakbrowser", "session container", "extension"],
            "user": "continue cloak browser",
            "assistant": "Decision: CloakBrowser should be framed as DevSecOps privacy research, not fraud bypass. Open question: extension vs standalone app? Files: web/backend/app.py. Blocker: threat model undecided. Next step: write MVP threat model.",
            "hash": "b",
        },
        {
            "session": "other",
            "turn": 3,
            "ts": "2026-06-11T11:00:00",
            "type": "talk",
            "tier": "B",
            "entities": ["companion"],
            "user": "desktop companion widget",
            "assistant": "cute p5.js widget",
            "hash": "c",
        },
    ]


def test_expand_query_handles_cloakbrowser_aliases():
    expanded = recall_service.expand_query("cloackbrowser")
    assert "cloakbrowser" in expanded
    assert "anti-detect browser" in expanded
    assert "browser fingerprinting" in expanded


def test_score_entries_finds_alias_and_entity_matches(monkeypatch):
    monkeypatch.setattr(recall_service, "load_entries", lambda: sample_entries())
    result = recall_service.recall("cloakbrowser", limit=5)
    assert result["ok"] is True
    assert result["confidence"] > 0
    assert result["source_sessions"][0]["session"] == "new-browser"
    assert any(ent["name"] == "fingerprinting" for ent in result["related_entities"])


def test_continue_intent_boosts_recent_related_entry(monkeypatch):
    monkeypatch.setattr(recall_service, "load_entries", lambda: sample_entries())
    result = recall_service.recall("continue cloakbrowser", limit=5)
    assert result["intent"] == "continue"
    assert result["source_sessions"][0]["session"] == "new-browser"
    assert "where_left_off" in result["state"]


def test_state_extraction_returns_decisions_files_next_blockers(monkeypatch):
    monkeypatch.setattr(recall_service, "load_entries", lambda: sample_entries())
    result = recall_service.recall("cloakbrowser", limit=5)
    state = result["state"]
    assert any("DevSecOps privacy" in item["text"] for item in state["decisions"])
    assert any("extension vs standalone" in item["text"] for item in state["open_questions"])
    assert "web/backend/app.py" in state["files_touched"]
    assert any("MVP threat model" in item["text"] for item in state["next_steps"])
    assert any("threat model undecided" in item["text"] for item in state["blockers"])


def test_empty_query_returns_not_ok(monkeypatch):
    monkeypatch.setattr(recall_service, "load_entries", lambda: sample_entries())
    result = recall_service.recall("   ")
    assert result["ok"] is False
    assert "query required" in result["message"]

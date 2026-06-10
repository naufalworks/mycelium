import importlib.util
import sqlite3
from pathlib import Path


def load_myceliumd_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "myceliumd.py"
    spec = importlib.util.spec_from_file_location("myceliumd_under_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_state_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_content TEXT,
            reasoning_details TEXT,
            codex_reasoning_items TEXT,
            codex_message_items TEXT,
            platform_message_id TEXT,
            observed INTEGER DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.executemany(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) VALUES (?, ?, ?, ?, ?, 1)",
        rows,
    )
    conn.commit()
    conn.close()


def test_fetch_new_pairs_handles_tool_use_session(tmp_path):
    module = load_myceliumd_module()
    db = tmp_path / "state.db"
    build_state_db(
        db,
        [
            (1, "sess-tool", "user", "run full pass", 1.0),
            (2, "sess-tool", "assistant", "", 2.0),
            (3, "sess-tool", "tool", '{"output":"ok"}', 3.0),
            (4, "sess-tool", "assistant", "final answer after tools", 4.0),
        ],
    )
    setattr(module, "STATE_DB", db)

    pairs = module.fetch_new_pairs(0)

    assert len(pairs) == 1
    assert pairs[0]["user_id"] == 1
    assert pairs[0]["assistant_id"] == 4
    assert pairs[0]["user_content"] == "run full pass"
    assert pairs[0]["assistant_content"] == "final answer after tools"


def test_fetch_new_pairs_returns_only_final_nonempty_assistant_per_user(tmp_path):
    module = load_myceliumd_module()
    db = tmp_path / "state.db"
    build_state_db(
        db,
        [
            (10, "sess-multi", "user", "probe", 10.0),
            (11, "sess-multi", "assistant", "intermediate answer", 11.0),
            (12, "sess-multi", "assistant", "final answer", 12.0),
            (13, "sess-multi", "user", "next turn", 13.0),
            (14, "sess-multi", "assistant", "next final", 14.0),
        ],
    )
    setattr(module, "STATE_DB", db)

    pairs = module.fetch_new_pairs(0)

    assert [(p["user_id"], p["assistant_id"], p["assistant_content"]) for p in pairs] == [
        (10, 12, "final answer"),
        (13, 14, "next final"),
    ]

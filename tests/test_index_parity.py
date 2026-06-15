import importlib.util
import sqlite3
from pathlib import Path


def load_mycelium_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mycelium.py"
    spec = importlib.util.spec_from_file_location("mycelium_under_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rebuild_index_indexes_all_type_finding_entries_with_normalized_defaults(tmp_path):
    module = load_mycelium_module()
    index_path = tmp_path / "index.db"
    entries = [
        {"turn": 1, "type": "finding", "finding": {"type": "SQLi", "target": "admin", "severity": "critical", "detail": "auth bypass"}},
        {"turn": 2, "type": "finding", "finding": {"type": "status-check", "target": "grav-shim", "result": "alive"}},
        {"turn": 3, "type": "finding", "finding": {"type": "config-bug", "severity": "low"}},
        {"turn": 4, "type": "finding"},
        {"turn": 5, "type": "talk", "finding": {"type": "noise", "target": "ignored", "severity": "high"}},
    ]

    module.rebuild_index(entries, path=index_path)

    log_finding_count = sum(1 for entry in entries if entry.get("type") == "finding")
    with sqlite3.connect(str(index_path)) as conn:
        db_finding_count = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        rows = conn.execute(
            "SELECT turn, target, ftype, severity FROM findings ORDER BY turn"
        ).fetchall()

    assert db_finding_count == log_finding_count == 4
    assert rows == [
        (1, "admin", "SQLi", "critical"),
        (2, "grav-shim", "status-check", "info"),
        (3, "unknown", "config-bug", "low"),
        (4, "unknown", "unknown", "info"),
    ]

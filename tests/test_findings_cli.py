import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path


def load_findings_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "findings.py"
    spec = importlib.util.spec_from_file_location("findings_under_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def capture_output(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def test_cmd_list_normalizes_partial_finding_records():
    module = load_findings_module()
    entries = [
        {
            "turn": 2,
            "ts": "2026-06-10T14:31:00Z",
            "type": "finding",
            "finding": {
                "type": "status-check",
                "target": "grav-shim",
                "result": "alive, backup pool active",
            },
        },
        {
            "turn": 18,
            "ts": "2026-06-10T07:48:15Z",
            "type": "finding",
            "finding": {
                "type": "config-bug",
                "target": "test",
                "detail": "test merge",
            },
        },
    ]

    output = capture_output(module.cmd_list, entries)

    assert "info" in output.lower()
    assert "alive, backup pool active" in output
    assert "test merge" in output


def test_cmd_stats_counts_unknown_severity_findings_as_info():
    module = load_findings_module()
    entries = [
        {
            "turn": 2,
            "ts": "2026-06-10T14:31:00Z",
            "session": "mycelium-brainstorm",
            "type": "finding",
            "finding": {
                "type": "status-check",
                "target": "grav-shim",
                "result": "alive, backup pool active",
            },
        }
    ]

    output = capture_output(module.cmd_stats, entries)

    assert "info: 1" in output.lower()

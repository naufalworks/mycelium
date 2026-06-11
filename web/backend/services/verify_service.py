from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

VERIFY_SCRIPT = Path.home() / "Documents/mycelium/scripts/mycelium.py"


def run_verify() -> Dict[str, Any]:
    proc = subprocess.run([
        "python3",
        str(VERIFY_SCRIPT),
        "verify",
    ], capture_output=True, text=True)
    output = (proc.stdout or proc.stderr or "").strip()
    return {
        "ok": proc.returncode == 0 or "Integrity chain valid" in output,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output": output,
    }

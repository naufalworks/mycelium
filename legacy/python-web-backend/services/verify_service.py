from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any, Dict

from .paths_service import get_paths


def run_verify() -> Dict[str, Any]:
    paths = get_paths()
    proc = subprocess.run([
        "python3",
        str(paths.scripts / "mycelium.py"),
        "verify",
    ], capture_output=True, text=True)
    output = (proc.stdout or proc.stderr or "").strip()
    return {
        "ok": proc.returncode == 0 or "Integrity chain valid" in output,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "output": output,
    }

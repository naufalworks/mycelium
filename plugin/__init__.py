"""mycelium plugin — mandatory pre-flight check + evolution patches.

Hooks:
  on_session_start — runs precheck + loads evolution patches automatically.
                     No agent memory required. Every session gets mycelium
                     health verification.

Slash command:
  /mycelium status     — brain stats + evolution dashboard
  /mycelium precheck   — run health gate manually
  /mycelium patches    — show active evolution patches
  /mycelium resume     — smart session resume
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MYCELIUM = Path.home() / "Documents" / "mycelium"
SCRIPTS = MYCELIUM / "scripts"


def _run_script(script: str, *args: str, timeout: int = 10) -> tuple:
    """Run a mycelium script, return (stdout, exit_code)."""
    cmd = [sys.executable, str(SCRIPTS / script)] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return f"(timeout after {timeout}s)", 1
    except FileNotFoundError:
        return f"script not found: {script}", 1
    except Exception as e:
        return f"error: {e}", 1


def _run_bash_script(script: str, timeout: int = 15) -> tuple:
    """Run a bash script, return (stdout, exit_code)."""
    cmd = ["bash", str(SCRIPTS / script)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return f"(timeout after {timeout}s)", 1
    except FileNotFoundError:
        return f"script not found: {script}", 1
    except Exception as e:
        return f"error: {e}", 1


# ---------------------------------------------------------------------------
# Hook: on_session_start
# ---------------------------------------------------------------------------

def _on_session_start(
    session_id: str = "",
    **_: Any,
) -> None:
    """Run mycelium precheck + load evolution patches at session start.

    This fires automatically — no agent action required.
    Output is logged (visible in debug mode) and does NOT block the session.
    """
    if not MYCELIUM.exists():
        logger.info("mycelium: project not found at %s, skipping", MYCELIUM)
        return

    # 1. Pre-flight health check
    precheck_out, precheck_rc = _run_script("precheck.py", "--json")
    if precheck_rc == 0:
        logger.info("mycelium precheck: OK")
    else:
        logger.warning("mycelium precheck: FAILED — %s", precheck_out)

    # 2. Load evolution patches
    patches_out, patches_rc = _run_script("evolution.py", "load")
    if patches_rc == 0 and patches_out and "No active patches" not in patches_out:
        logger.info("mycelium patches loaded:\n%s", patches_out)
    else:
        logger.info("mycelium patches: none active")


# ---------------------------------------------------------------------------
# Slash command: /mycelium
# ---------------------------------------------------------------------------

_HELP_TEXT = """\
/mycelium — persistent brain + self-evolution engine

Subcommands:
  status       Brain stats + evolution dashboard
  precheck     Run health gate
  patches      Show active evolution patches
  resume       Smart session resume
  evolution    Evolution engine status
"""


def _handle_slash(raw_args: str) -> Optional[str]:
    argv = raw_args.strip().split()
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return _HELP_TEXT

    sub = argv[0]

    if sub == "status":
        # Brain status
        brain_out, _ = _run_script("mycelium.py", "status")
        # Evolution status
        evo_out, _ = _run_script("evolution.py", "status")
        parts = []
        if brain_out:
            parts.append(brain_out)
        if evo_out:
            parts.append(evo_out)
        return "\n\n".join(parts) if parts else "mycelium: no data"

    if sub == "precheck":
        out, rc = _run_script("precheck.py")
        return out

    if sub == "patches":
        out, rc = _run_script("evolution.py", "load")
        return out or "No active patches."

    if sub == "resume":
        out, rc = _run_script("mycelium.py", "resume")
        return out or "Resume returned no data."

    if sub == "evolution":
        out, rc = _run_script("evolution.py", "status")
        return out or "Evolution engine: no data"

    return f"Unknown subcommand: {sub}\n\n{_HELP_TEXT}"


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_command(
        "mycelium",
        handler=_handle_slash,
        description="Mycelium brain: precheck, resume, evolution patches.",
    )

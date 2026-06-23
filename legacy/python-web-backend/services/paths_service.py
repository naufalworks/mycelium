from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

DATA_NAMES = ("log.jsonl", "index.db", "archive")
INCLUDED_NAMES = ("log.jsonl", "index.db", "archive", "branches", "garden")


def default_source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class MyceliumPaths:
    source_root: Path
    runtime_root: Path
    canonical_root: Path
    log: Path
    index: Path
    archive: Path
    branches: Path
    garden: Path
    scripts: Path
    daemon_dir: Path
    daemon_state: Path
    backup_root: Path
    migration_backup_root: Path


def get_paths() -> MyceliumPaths:
    source = _env_path("MYCELIUM_SOURCE_ROOT", default_source_root())
    runtime = _env_path("MYCELIUM_RUNTIME_ROOT", Path.home() / ".hermes/myceliumd/runtime")
    daemon_dir = runtime.parent
    return MyceliumPaths(
        source_root=source,
        runtime_root=runtime,
        canonical_root=runtime,
        log=runtime / "log.jsonl",
        index=runtime / "index.db",
        archive=runtime / "archive",
        branches=runtime / "branches",
        garden=runtime / "garden",
        scripts=source / "scripts",
        daemon_dir=daemon_dir,
        daemon_state=daemon_dir / "state.json",
        backup_root=daemon_dir / "backups",
        migration_backup_root=daemon_dir / "migration-backups",
    )


def path_info(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {"path": str(path), "exists": path.exists(), "is_symlink": path.is_symlink(), "symlink_target": None}
    if path.is_symlink():
        try:
            info["symlink_target"] = str(path.resolve(strict=False))
        except Exception:
            pass
    return info


def _resolves_to(path: Path, target: Path) -> bool:
    try:
        return path.resolve(strict=False) == target.resolve(strict=False)
    except Exception:
        return False


def detect_split_brain_warnings(paths: MyceliumPaths | None = None) -> List[Dict[str, str]]:
    paths = paths or get_paths()
    warnings: List[Dict[str, str]] = []
    for name in DATA_NAMES:
        source_path = paths.source_root / name
        runtime_path = paths.runtime_root / name
        if not source_path.exists() and not source_path.is_symlink():
            continue
        if source_path.is_symlink() and _resolves_to(source_path, runtime_path):
            continue
        if _resolves_to(source_path, runtime_path):
            continue
        warnings.append({
            "name": name,
            "source_path": str(source_path),
            "runtime_path": str(runtime_path),
            "message": f"split-brain risk: source {source_path} exists and does not resolve to runtime {runtime_path}",
        })
    return warnings


def resolve_canonical_root() -> Path:
    return get_paths().canonical_root

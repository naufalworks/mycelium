from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .status_service import SOURCE_ROOT, get_paths, resolve_canonical_root
from .verify_service import run_verify

BACKUP_ROOT = Path.home() / ".hermes/myceliumd/backups"
INCLUDED_NAMES = ["log.jsonl", "index.db", "archive", "branches", "garden"]
MIGRATION_BACKUP_ROOT = Path.home() / ".hermes/myceliumd/migration-backups"


def nowstamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                yield child


def snapshot_dir() -> Path:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    return BACKUP_ROOT


def migration_backup_dir() -> Path:
    MIGRATION_BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    return MIGRATION_BACKUP_ROOT


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _validate_archive_members(tf: tarfile.TarFile) -> None:
    for member in tf.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"unsafe archive member: {member.name}")
        if member.issym() or member.islnk():
            raise ValueError(f"unsupported archive link member: {member.name}")


def validate_target_root(target_root: str | None, *, allow_runtime_root: bool = False) -> Path:
    target = Path(target_root).expanduser() if target_root else resolve_canonical_root()
    if not target.is_absolute():
        raise ValueError("target_root must be an absolute path")
    canonical_runtime = resolve_canonical_root().resolve()
    source_root = SOURCE_ROOT.resolve()
    resolved_target = target.resolve(strict=False)
    if resolved_target == source_root:
        raise ValueError("target_root cannot equal source root")
    if not allow_runtime_root and resolved_target == canonical_runtime:
        raise ValueError("target_root cannot equal canonical runtime root")
    if allow_runtime_root and resolved_target == canonical_runtime:
        return target
    for protected in INCLUDED_NAMES:
        protected_source = source_root / protected
        protected_runtime = canonical_runtime / protected
        if _is_within(protected_source, resolved_target) or _is_within(protected_runtime, resolved_target):
            raise ValueError(f"target_root overlaps protected path: {protected}")
    return target


def ensure_snapshot_source(path_str: str) -> Tuple[Path, Dict[str, Any]]:
    path = Path(path_str).expanduser()
    if path.is_file() and path.suffixes[-2:] == [".tar", ".gz"]:
        extract_root = snapshot_dir() / f"extract-{path.stem}-{nowstamp()}"
        extract_root.mkdir(parents=True, exist_ok=False)
        with tarfile.open(path, "r:gz") as tf:
            _validate_archive_members(tf)
            tf.extractall(extract_root)
        subdirs = [p for p in extract_root.iterdir() if p.is_dir()]
        if not subdirs:
            raise FileNotFoundError("bundle extracted but no snapshot directory found")
        path = subdirs[0]
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    return path, manifest


def create_snapshot() -> Dict[str, Any]:
    root = resolve_canonical_root()
    stamp = nowstamp()
    snap_name = f"mycelium-backup-{stamp}"
    snap_path = snapshot_dir() / snap_name
    snap_path.mkdir(parents=True, exist_ok=False)
    included_paths: List[str] = []
    file_sizes: Dict[str, int] = {}
    checksums: Dict[str, str] = {}
    total_bytes = 0

    for name in INCLUDED_NAMES:
        src = root / name
        if not src.exists():
            continue
        dest = snap_path / name
        if src.is_dir():
            shutil.copytree(src, dest, symlinks=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        included_paths.append(name)
        for file in iter_files(dest):
            rel = str(file.relative_to(snap_path))
            size = file.stat().st_size
            total_bytes += size
            file_sizes[rel] = size
            checksums[rel] = sha256_file(file)

    manifest = {
        "schema_version": "mycelium-backup-v1",
        "created_at": iso_now(),
        "snapshot_name": snap_name,
        "source_root": str(get_paths()["source_root"]),
        "canonical_runtime_root": str(root),
        "included_paths": included_paths,
        "file_sizes": file_sizes,
        "checksums": checksums,
        "total_bytes": total_bytes,
    }
    manifest_path = snap_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return {
        "snapshot": manifest,
        "path": str(snap_path),
        "manifest_path": str(manifest_path),
    }


def list_backups() -> Dict[str, Any]:
    root = snapshot_dir()
    items = []
    for path in sorted(root.glob("mycelium-backup-*"), reverse=True):
        manifest_path = path / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception:
                manifest = {}
        items.append({
            "name": path.name,
            "path": str(path),
            "created_at": manifest.get("created_at") or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_bytes": manifest.get("total_bytes", 0),
            "verified": None,
            "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        })
    return {"backup_root": str(root), "items": items}


def verify_snapshot(path_str: str) -> Dict[str, Any]:
    try:
        path, manifest = ensure_snapshot_source(path_str)
    except Exception as e:
        return {"ok": False, "message": str(e), "data": {"path": str(path_str)}}

    mismatches = []
    for rel, expected in manifest.get("checksums", {}).items():
        fp = path / rel
        if not fp.exists():
            mismatches.append({"path": rel, "reason": "missing"})
            continue
        actual = sha256_file(fp)
        if actual != expected:
            mismatches.append({"path": rel, "reason": "checksum", "expected": expected, "actual": actual})
    return {
        "ok": len(mismatches) == 0,
        "message": "verified" if len(mismatches) == 0 else "mismatches found",
        "data": {"path": str(path), "mismatches": mismatches, "manifest": manifest},
    }


def export_snapshot(path_str: str) -> Dict[str, Any]:
    path = Path(path_str).expanduser()
    if not path.exists():
        return {"ok": False, "message": "snapshot missing", "data": {"path": str(path)}}
    tar_path = path.with_suffix(".tar.gz")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(path, arcname=path.name)
    return {"ok": True, "message": "exported", "data": {"snapshot_path": str(path), "bundle_path": str(tar_path)}}


def dry_run_import(path_str: str, target_root: str | None = None) -> Dict[str, Any]:
    try:
        path, manifest = ensure_snapshot_source(path_str)
    except Exception as e:
        return {"ok": False, "message": str(e), "data": {"path": str(path_str)}}

    try:
        target = validate_target_root(target_root, allow_runtime_root=target_root is None)
    except Exception as e:
        return {"ok": False, "message": str(e), "data": {"path": str(path_str), "target_root": str(target_root)}}
    actions = []
    conflicts = []
    for name in manifest.get("included_paths", []):
        dest = target / name
        actions.append({"source": str(path / name), "target": str(dest)})
        if dest.exists():
            conflicts.append(str(dest))
    return {
        "ok": True,
        "message": "dry-run ready",
        "data": {
            "snapshot": str(path),
            "target": str(target),
            "actions": actions,
            "conflicts": conflicts,
            "manifest": manifest,
            "need_reindex": True,
        },
    }


def restore_snapshot(path_str: str, target_root: str | None = None, overwrite: bool = False) -> Dict[str, Any]:
    preview = dry_run_import(path_str, target_root)
    if not preview.get("ok"):
        return preview
    if preview["data"]["conflicts"] and not overwrite:
        return {
            "ok": False,
            "message": "target has conflicts; rerun with overwrite=true",
            "data": preview["data"],
        }

    path = Path(preview["data"]["snapshot"])
    target = Path(preview["data"]["target"])
    target.mkdir(parents=True, exist_ok=True)
    restored = []
    for action in preview["data"]["actions"]:
        src = Path(action["source"])
        dest = Path(action["target"])
        if dest.exists() and overwrite:
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src.is_dir():
            shutil.copytree(src, dest, symlinks=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        restored.append(str(dest))

    verify = run_verify()
    return {
        "ok": True,
        "message": "restore complete",
        "data": {
            "snapshot": str(path),
            "target": str(target),
            "restored": restored,
            "verify": verify,
        },
    }


def _compare_paths(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists():
        return False
    if a.is_file() and b.is_file():
        return sha256_file(a) == sha256_file(b)
    if a.is_dir() and b.is_dir():
        a_files = sorted(str(p.relative_to(a)) for p in a.rglob("*") if p.is_file())
        b_files = sorted(str(p.relative_to(b)) for p in b.rglob("*") if p.is_file())
        if a_files != b_files:
            return False
        for rel in a_files:
            if sha256_file(a / rel) != sha256_file(b / rel):
                return False
        return True
    return False


def migrate_dry_run(target_root: str) -> Dict[str, Any]:
    source = resolve_canonical_root()
    try:
        target = validate_target_root(target_root, allow_runtime_root=False)
    except Exception as e:
        return {"ok": False, "message": str(e), "data": {"target_root": str(target_root)}}
    mappings = []
    conflicts = []
    for name in INCLUDED_NAMES:
        src = source / name
        dest = target / name
        mappings.append({"source": str(src), "target": str(dest), "exists": src.exists()})
        if src.exists() and dest.exists() and not _compare_paths(src, dest):
            conflicts.append({"path": str(dest), "reason": "divergent existing target"})
    return {
        "ok": True,
        "message": "migration dry-run ready",
        "data": {
            "source_root": str(source),
            "target_root": str(target),
            "mappings": mappings,
            "conflicts": conflicts,
            "requires_backup": True,
            "source_root_path": str(SOURCE_ROOT),
        },
    }


def migrate_execute(target_root: str, overwrite: bool = False) -> Dict[str, Any]:
    preview = migrate_dry_run(target_root)
    if not preview.get("ok"):
        return preview
    if preview["data"]["conflicts"] and not overwrite:
        return {
            "ok": False,
            "message": "migration conflicts detected; rerun with overwrite=true",
            "data": preview["data"],
        }

    backup = create_snapshot()
    source = Path(preview["data"]["source_root"])
    target = Path(preview["data"]["target_root"])
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for mapping in preview["data"]["mappings"]:
        src = Path(mapping["source"])
        dest = Path(mapping["target"])
        if not src.exists():
            continue
        if dest.exists() and overwrite:
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if src.is_dir():
            shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=overwrite)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        copied.append(str(dest))

    relinked = []
    for name in INCLUDED_NAMES:
        source_path = SOURCE_ROOT / name
        target_path = target / name
        if source_path.exists() or source_path.is_symlink():
            if source_path.is_dir() and not source_path.is_symlink():
                stamp = migration_backup_dir() / f"source-{name}-{nowstamp()}"
                shutil.move(str(source_path), str(stamp))
            else:
                source_path.unlink(missing_ok=True)
        if target_path.exists():
            source_path.symlink_to(target_path)
            relinked.append({"link": str(source_path), "target": str(target_path)})

    verify = run_verify()
    return {
        "ok": True,
        "message": "migration complete",
        "data": {
            "backup": backup,
            "source_root": str(source),
            "target_root": str(target),
            "copied": copied,
            "relinked": relinked,
            "verify": verify,
        },
    }

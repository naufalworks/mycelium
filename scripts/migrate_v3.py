#!/usr/bin/env python3
"""
migrate_v3.py — Migrate flat JSONL log to v3 LSM storage.

Steps:
  1. Backup original log.jsonl + index.db to archive/
  2. Load entries into LSM layers (L0/L1/L2)
  3. Build Bloom filter, entity graph, negation index, causal chain
  4. Initialize attention tracker
  5. Verify hash chain integrity

Usage:
  python3 migrate_v3.py migrate [--backup/--no-backup]
  python3 migrate_v3.py rollback --backup-path archive/pre-v3-migration-XXX.tar.gz
  python3 migrate_v3.py status
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mycelium_lib import (
    MYCELIUM, LOG, INDEX, ARCHIVE,
    load_log, init_index, rebuild_index,
    compute_hash, extract_entities,
)
from mycelium_lsm import MyceliumLSM
from mycelium_bloom import MyceliumBloom
from mycelium_graph import EntityGraph
from mycelium_negation import NegationExtractor
from mycelium_causal import CausalExtractor
from mycelium_attention import AttentionTracker

# ── LSM artifacts to clean on rollback ───────────────────────
LSM_DIRS = ["l1", "l2"]
LSM_FILE_GLOBS = [".bloom_*"]


def _backup_path() -> Path:
    """Generate timestamped backup archive path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ARCHIVE / f"pre-v3-migration-{ts}.tar.gz"


def _create_backup(base: Path, backup_path: Path) -> Path:
    """Backup log.jsonl + index.db into a tar.gz archive."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(backup_path, "w:gz") as tar:
        for name in ["log.jsonl", "index.db"]:
            src = base / name
            if src.exists():
                tar.add(str(src), arcname=name)
    return backup_path


def _restore_backup(backup_path: Path, base: Path) -> dict:
    """Restore log.jsonl + index.db from a backup archive."""
    if not backup_path.exists():
        return {"error": f"Backup not found: {backup_path}"}
    with tarfile.open(backup_path, "r:gz") as tar:
        tar.extractall(path=str(base))
    return {"restored": True, "path": str(backup_path)}


# ── Core Migration ───────────────────────────────────────────

def migrate(log_path: str | Path | None = None,
            base_path: str | Path | None = None,
            backup: bool = True) -> dict:
    """
    Migrate flat JSONL log to v3 LSM storage.

    Returns dict with stats from each build step.
    """
    base = Path(base_path) if base_path else MYCELIUM
    log = Path(log_path) if log_path else LOG
    stats: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}

    # ── 1. Backup ────────────────────────────────────────────
    backup_file = None
    if backup:
        bp = _backup_path()
        if log.exists() or (base / "index.db").exists():
            backup_file = _create_backup(base, bp)
            stats["backup"] = str(backup_file)

    # ── 2. Read all entries ──────────────────────────────────
    entries = load_log(log)
    stats["entries_read"] = len(entries)
    if not entries:
        stats["error"] = "No entries found in log"
        return stats

    # ── 3. Build LSM layers ──────────────────────────────────
    lsm = MyceliumLSM(base)
    lsm_result = lsm.load_from_jsonl(log)
    stats["lsm_load"] = lsm_result
    stats["lsm_stats"] = lsm.stats()

    # ── 4. Build Bloom filter ────────────────────────────────
    bloom = MyceliumBloom(
        capacity=max(len(entries) * 3, 1000),
        error_rate=0.01,
        name="entities",
    )
    bloom_added = bloom.build_from_log(log)
    bloom.save(base / ".bloom_entities")
    bloom.save_to_db(base / "index.db")
    stats["bloom"] = bloom.stats()
    stats["bloom_entities_added"] = bloom_added

    # ── 5. Build entity graph ────────────────────────────────
    graph = EntityGraph(db_path=base / "index.db")
    edge_count = graph.build_from_log(log)
    stats["graph_edges"] = edge_count
    graph.close()

    # ── 6. Build negation index ──────────────────────────────
    ne = NegationExtractor(db_path=base / "index.db")
    negation_count = 0
    for entry in entries:
        user_msg = entry.get("user", "")
        if not user_msg:
            continue
        signals = ne.detect(user_msg)
        for sig in signals:
            sig["user_msg"] = user_msg
            ne.store(sig, session=entry.get("session", ""))
            negation_count += 1
    stats["negations_stored"] = negation_count

    # ── 7. Build causal chain ────────────────────────────────
    ce = CausalExtractor(db_path=base / "index.db")
    causal_count = ce.build_from_log(log)
    stats["causal_edges"] = causal_count
    ce.close()

    # ── 8. Initialize attention tracker ──────────────────────
    at = AttentionTracker(db_path=base / "index.db")
    at.close()
    stats["attention_initialized"] = True

    # ── 9. Verify hash chain integrity ──────────────────────
    integrity = lsm.verify_integrity()
    stats["integrity"] = integrity

    # ── 10. Rebuild turns/entities/findings index ───────────
    rebuild_index(entries, path=base / "index.db")
    stats["index_rebuilt"] = True

    # ── 11. Final summary ───────────────────────────────────
    stats["status"] = "complete" if integrity.get("valid") else "integrity_warning"
    stats["migration_version"] = "v3"

    return stats


# ── Rollback ─────────────────────────────────────────────────

def rollback(backup_path: str | Path,
             base_path: str | Path | None = None) -> dict:
    """Restore pre-migration state from backup archive."""
    base = Path(base_path) if base_path else MYCELIUM
    bp = Path(backup_path)
    result: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}

    # ── Verify backup exists ─────────────────────────────────
    if not bp.exists():
        return {"error": f"Backup not found: {bp}"}

    # ── Remove LSM artifacts ─────────────────────────────────
    for d in LSM_DIRS:
        dpath = base / d
        if dpath.exists():
            shutil.rmtree(dpath)
            result[f"removed_{d}"] = True

    # Remove bloom filter files
    for pattern in LSM_FILE_GLOBS:
        for f in base.glob(pattern):
            f.unlink()
            result[f"removed_{f.name}"] = True

    # Remove content-addressed objects dir if present
    obj_dir = base / "objects"
    if obj_dir.exists():
        shutil.rmtree(obj_dir)
        result["removed_objects"] = True

    # ── Restore original files ───────────────────────────────
    restore_result = _restore_backup(bp, base)
    result["restore"] = restore_result
    if "error" in restore_result:
        return result

    # ── Remove old index.db and rebuild from restored log ────
    index_db = base / "index.db"
    if index_db.exists():
        index_db.unlink()
    entries = load_log(base / "log.jsonl")
    if entries:
        rebuild_index(entries, path=index_db)
        result["index_rebuilt"] = True

    # ── Verify integrity after rollback ──────────────────────
    entries = load_log(base / "log.jsonl")
    if entries:
        # Verify hash chain directly from log entries
        errors = []
        for i, entry in enumerate(entries):
            if i == 0:
                continue
            prev = entries[i - 1]
            if entry.get("prev_hash") and prev.get("hash"):
                if entry["prev_hash"] != prev["hash"]:
                    errors.append(f"Chain break at turn {entry.get('turn')}")
        result["integrity"] = {"valid": len(errors) == 0, "entries": len(entries), "errors": errors}
    else:
        result["integrity"] = {"valid": True, "entries": 0}

    result["status"] = "rolled_back" if result["integrity"].get("valid") else "rollback_integrity_warning"

    return result


# ── Status ───────────────────────────────────────────────────

def status(base_path: str | Path | None = None) -> dict:
    """Show current migration status."""
    base = Path(base_path) if base_path else MYCELIUM
    result: dict = {}

    # Check LSM dirs
    for d in LSM_DIRS:
        dpath = base / d
        if dpath.exists():
            count = sum(1 for _ in dpath.iterdir())
            result[f"{d}_files"] = count
        else:
            result[f"{d}_files"] = 0

    # Check bloom
    bloom_files = list(base.glob(".bloom_*"))
    result["bloom_files"] = len(bloom_files)

    # Check index.db tables
    index_db = base / "index.db"
    if index_db.exists():
        conn = sqlite3.connect(str(index_db))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        result["db_tables"] = tables
        # Row counts
        for t in ["turns", "entity_edges", "negations", "causal_edges", "attention"]:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                result[f"{t}_count"] = count
            except Exception:
                result[f"{t}_count"] = 0
        conn.close()
    else:
        result["db_exists"] = False

    # Check log
    log = base / "log.jsonl"
    if log.exists():
        lines = sum(1 for _ in open(log) if _.strip())
        result["log_entries"] = lines
    else:
        result["log_entries"] = 0

    # Check backups
    if ARCHIVE.exists():
        backups = sorted(ARCHIVE.glob("pre-v3-migration-*.tar.gz"))
        result["backups"] = [b.name for b in backups]
    else:
        result["backups"] = []

    return result


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mycelium v3 Migration Tool")
    sub = parser.add_subparsers(dest="cmd")

    mig = sub.add_parser("migrate", help="Migrate flat JSONL to LSM storage")
    mig.add_argument("--no-backup", action="store_true",
                     help="Skip backup creation")
    mig.add_argument("--log", help="Path to log.jsonl")
    mig.add_argument("--base", help="Base path for mycelium data")

    rol = sub.add_parser("rollback", help="Rollback to pre-migration state")
    rol.add_argument("--backup-path", required=True,
                     help="Path to backup archive")
    rol.add_argument("--base", help="Base path for mycelium data")

    sub.add_parser("status", help="Show migration status")

    args = parser.parse_args()

    if args.cmd == "migrate":
        print("Starting v3 migration...")
        result = migrate(
            log_path=args.log,
            base_path=args.base,
            backup=not args.no_backup,
        )
        print(json.dumps(result, indent=2, default=str))
        if result.get("status") == "complete":
            print("\n✅ Migration complete!")
        else:
            print(f"\n⚠️  Migration finished with status: {result.get('status')}")

    elif args.cmd == "rollback":
        print(f"Rolling back from {args.backup_path}...")
        result = rollback(args.backup_path, base_path=args.base)
        print(json.dumps(result, indent=2, default=str))
        if result.get("status") == "rolled_back":
            print("\n✅ Rollback complete!")
        else:
            print(f"\n⚠️  Rollback finished with status: {result.get('status')}")

    elif args.cmd == "status":
        result = status(base_path=getattr(args, 'base', None))
        print("v3 Migration Status:")
        print(json.dumps(result, indent=2, default=str))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

HOME = Path.home()
SOURCE_ROOT = HOME / "Documents/mycelium"
RUNTIME_ROOT = HOME / ".hermes/myceliumd/runtime"
DAEMON_DIR = HOME / ".hermes/myceliumd"
STATE_PATH = DAEMON_DIR / "state.json"
LOG_FALLBACK = SOURCE_ROOT / "log.jsonl"
INDEX_FALLBACK = SOURCE_ROOT / "index.db"
ARCHIVE_FALLBACK = SOURCE_ROOT / "archive"
BRANCHES_FALLBACK = SOURCE_ROOT / "branches"
GARDEN_FALLBACK = SOURCE_ROOT / "garden"
DAEMON_HEALTH_URL = "http://127.0.0.1:20151/health"


def resolve_canonical_root() -> Path:
    if RUNTIME_ROOT.exists():
        return RUNTIME_ROOT
    return SOURCE_ROOT


def path_info(path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_symlink": path.is_symlink(),
        "symlink_target": None,
    }
    if path.is_symlink():
        try:
            info["symlink_target"] = str(path.resolve())
        except Exception:
            info["symlink_target"] = None
    return info


def get_paths() -> Dict[str, Path]:
    root = resolve_canonical_root()
    return {
        "canonical_root": root,
        "source_root": SOURCE_ROOT,
        "log": root / "log.jsonl" if (root / "log.jsonl").exists() else LOG_FALLBACK,
        "index": root / "index.db" if (root / "index.db").exists() else INDEX_FALLBACK,
        "archive": root / "archive" if (root / "archive").exists() else ARCHIVE_FALLBACK,
        "branches": root / "branches" if (root / "branches").exists() else BRANCHES_FALLBACK,
        "garden": root / "garden" if (root / "garden").exists() else GARDEN_FALLBACK,
        "daemon_state": STATE_PATH,
    }


def load_entries(limit: int | None = None) -> List[Dict[str, Any]]:
    log_path = get_paths()["log"]
    if not log_path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None and limit > 0:
        return items[-limit:]
    return items


def estimate_archived_turns(archive_dir: Path) -> int:
    if not archive_dir.exists():
        return 0
    count = 0
    for path in archive_dir.glob("*.jsonl"):
        try:
            with open(path) as f:
                count += sum(1 for line in f if line.strip())
        except Exception:
            continue
    return count


def recent_sessions(entries: List[Dict[str, Any]], max_sessions: int = 8) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for entry in reversed(entries):
        session = entry.get("session", "unknown")
        if session in seen:
            continue
        seen.add(session)
        out.append(
            {
                "session": session,
                "last_ts": entry.get("ts"),
                "last_type": entry.get("type"),
                "last_tier": entry.get("tier", "B"),
                "entities": entry.get("entities", [])[:5],
            }
        )
        if len(out) >= max_sessions:
            break
    return out


def get_status() -> Dict[str, Any]:
    paths = get_paths()
    entries = load_entries()
    tiers = Counter(entry.get("tier", "B") for entry in entries)
    types = Counter(entry.get("type", "talk") for entry in entries)
    sessions = {entry.get("session", "unknown") for entry in entries}
    archive_dir = paths["archive"]
    archived_files = len(list(archive_dir.glob("*.jsonl"))) if archive_dir.exists() else 0
    archived_turns_estimate = estimate_archived_turns(archive_dir)
    storage_bytes = 0
    for key in ["log", "index"]:
        p = paths[key]
        if p.exists() and p.is_file():
            storage_bytes += p.stat().st_size
    if archive_dir.exists():
        for p in archive_dir.rglob("*"):
            if p.is_file():
                storage_bytes += p.stat().st_size
    return {
        "total_turns": len(entries),
        "total_sessions": len(sessions),
        "tiers": {k: tiers.get(k, 0) for k in ["S", "A", "B", "C"]},
        "types": dict(types),
        "recent_sessions": recent_sessions(entries),
        "last_turn": entries[-1] if entries else None,
        "canonical_runtime": path_info(paths["canonical_root"]),
        "source_root": path_info(paths["source_root"]),
        "log_path": path_info(paths["log"]),
        "index_path": path_info(paths["index"]),
        "archive_path": path_info(paths["archive"]),
        "branches_path": path_info(paths["branches"]),
        "garden_path": path_info(paths["garden"]),
        "daemon_state_path": path_info(paths["daemon_state"]),
        "storage_bytes": storage_bytes,
        "archived_files": archived_files,
        "archived_turns_estimate": archived_turns_estimate,
    }


def probe_daemon_health(timeout: float = 0.4) -> bool:
    with urllib.request.urlopen(DAEMON_HEALTH_URL, timeout=timeout) as response:
        return 200 <= response.status < 300


def get_daemon_state() -> Dict[str, Any]:
    state = {}
    state_exists = STATE_PATH.exists()
    if state_exists:
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            state = {}
    try:
        running = probe_daemon_health()
    except Exception:
        running = False
    if running:
        status_reason = "health_ok"
    elif state_exists:
        status_reason = "state_stale_health_unreachable"
    else:
        status_reason = "no_state"
    return {
        "ok": True,
        "running": running,
        "status_reason": status_reason,
        "state": state,
        "state_path": str(STATE_PATH),
        "log_path": str(DAEMON_DIR / "myceliumd.log"),
        "health_url": DAEMON_HEALTH_URL,
    }


def get_stream(
    *,
    limit: int = 100,
    session: str | None = None,
    tier: str | None = None,
    item_type: str | None = None,
    entity: str | None = None,
    q: str | None = None,
) -> Dict[str, Any]:
    entries = load_entries()
    items = []
    ql = (q or "").lower().strip()
    for entry in entries:
        if session and entry.get("session") != session:
            continue
        if tier and entry.get("tier") != tier:
            continue
        if item_type and entry.get("type") != item_type:
            continue
        if entity and entity not in entry.get("entities", []):
            continue
        if ql:
            blob = f"{entry.get('user','')} {entry.get('assistant','')} {' '.join(entry.get('entities', []))}".lower()
            if ql not in blob:
                continue
        items.append(entry)
    sliced = items[-limit:]
    return {"total": len(items), "items": sliced}


def get_session_detail(session_name: str) -> Dict[str, Any]:
    entries = [e for e in load_entries() if e.get("session") == session_name]
    entity_counts = Counter()
    type_counts = Counter()
    tier_counts = Counter()
    for entry in entries:
        type_counts[entry.get("type", "talk")] += 1
        tier_counts[entry.get("tier", "B")] += 1
        for ent in entry.get("entities", []):
            entity_counts[ent] += 1
    return {
        "session": session_name,
        "total": len(entries),
        "first_ts": entries[0].get("ts") if entries else None,
        "last_ts": entries[-1].get("ts") if entries else None,
        "types": dict(type_counts),
        "tiers": dict(tier_counts),
        "entities": [{"name": k, "count": v} for k, v in entity_counts.most_common(20)],
        "items": entries,
    }


def get_connections(limit: int = 80) -> Dict[str, Any]:
    entries = load_entries(limit=limit)
    session_entities: dict[str, Counter] = defaultdict(Counter)
    entity_sessions: dict[str, set[str]] = defaultdict(set)
    pair_weights: Counter = Counter()
    session_meta: dict[str, dict[str, Any]] = {}

    for entry in entries:
        session = entry.get("session", "unknown")
        entities = list(dict.fromkeys(entry.get("entities", [])[:8]))
        session_meta[session] = {
            "last_ts": entry.get("ts"),
            "last_tier": entry.get("tier", "B"),
            "last_type": entry.get("type", "talk"),
        }
        for ent in entities:
            session_entities[session][ent] += 1
            entity_sessions[ent].add(session)
        for i, left in enumerate(entities):
            for right in entities[i + 1 :]:
                key = tuple(sorted((left, right)))
                pair_weights[key] += 1

    top_sessions = sorted(session_entities.items(), key=lambda kv: sum(kv[1].values()), reverse=True)[:10]
    top_entities = sorted(entity_sessions.items(), key=lambda kv: (len(kv[1]), kv[0]), reverse=True)[:18]

    nodes: List[Dict[str, Any]] = []
    session_names = {name for name, _ in top_sessions}
    entity_names = {name for name, _ in top_entities}

    for session, counter in top_sessions:
        nodes.append(
            {
                "id": f"session:{session}",
                "kind": "session",
                "label": session,
                "weight": sum(counter.values()),
                **session_meta.get(session, {}),
            }
        )
    for entity, sessions in top_entities:
        nodes.append(
            {
                "id": f"entity:{entity}",
                "kind": "entity",
                "label": entity,
                "weight": len(sessions),
            }
        )

    links: List[Dict[str, Any]] = []
    for session, counter in top_sessions:
        for entity, weight in counter.most_common(5):
            if entity in entity_names:
                links.append(
                    {
                        "source": f"session:{session}",
                        "target": f"entity:{entity}",
                        "weight": weight,
                        "kind": "session-entity",
                    }
                )
    for (left, right), weight in pair_weights.most_common(16):
        if left in entity_names and right in entity_names:
            links.append(
                {
                    "source": f"entity:{left}",
                    "target": f"entity:{right}",
                    "weight": weight,
                    "kind": "entity-entity",
                }
            )

    return {
        "ok": True,
        "nodes": nodes,
        "links": links,
        "sessions_considered": len(session_names),
        "entities_considered": len(entity_names),
    }


def get_findings() -> Dict[str, Any]:
    entries = [e for e in load_entries() if e.get("type") == "finding"]
    return {"total": len(entries), "items": entries}

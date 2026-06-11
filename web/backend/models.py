from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

Tier = Literal["S", "A", "B", "C"]


class PathInfo(BaseModel):
    path: str
    exists: bool
    is_symlink: bool = False
    symlink_target: Optional[str] = None


class StatusResponse(BaseModel):
    total_turns: int
    total_sessions: int
    tiers: Dict[str, int]
    types: Dict[str, int]
    recent_sessions: List[Dict[str, Any]]
    last_turn: Optional[Dict[str, Any]]
    canonical_runtime: PathInfo
    source_root: PathInfo
    log_path: PathInfo
    index_path: PathInfo
    archive_path: PathInfo
    branches_path: PathInfo
    garden_path: PathInfo
    daemon_state_path: PathInfo
    storage_bytes: int
    archived_files: int
    archived_turns_estimate: int


class StreamItem(BaseModel):
    turn: int
    ts: str
    session: str
    type: str
    tier: str
    user: str
    assistant: str
    entities: List[str] = Field(default_factory=list)
    finding: Optional[Dict[str, Any]] = None
    prev_hash: Optional[str] = None
    hash: Optional[str] = None


class StreamResponse(BaseModel):
    total: int
    items: List[StreamItem]


class VerifyResponse(BaseModel):
    ok: bool
    checked_at: str
    output: str


class DaemonResponse(BaseModel):
    ok: bool
    running: bool
    state: Dict[str, Any]
    state_path: str
    log_path: str
    health_url: str


class BackupManifest(BaseModel):
    schema_version: str
    created_at: str
    snapshot_name: str
    source_root: str
    canonical_runtime_root: str
    included_paths: List[str]
    file_sizes: Dict[str, int]
    checksums: Dict[str, str]
    total_bytes: int


class BackupSummary(BaseModel):
    name: str
    path: str
    created_at: str
    total_bytes: int
    verified: Optional[bool] = None
    manifest_path: Optional[str] = None


class BackupListResponse(BaseModel):
    backup_root: str
    items: List[BackupSummary]


class OperationResponse(BaseModel):
    ok: bool
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)

# Mycelium REST API Specification

## Base URL: `http://127.0.0.1:8421`

All endpoints return JSON. The server runs on Axum and serves both the REST API
and the Leptos frontend (SSR + static assets).

---

## Health & Status

### `GET /api/health`
Health check for load balancers.

Response:
```json
{ "status": "ok" }
```

### `GET /api/status`
Full brain status with daemon health.

Response:
```json
{
  "total_turns": 1828,
  "total_sessions": 12,
  "tiers": { "core": 1500, "ephemeral": 328 },
  "types": { "conversation": 1800, "system": 28 },
  "storage_bytes": 2097152,
  "last_turn": { "turn": 1828, "ts": "2026-06-23T10:00:00Z", "tier": "core" },
  "daemon": { "running": true, "pid": 12345, "uptime_secs": 3600, "memory_mb": 14.2 }
}
```

---

## Entries (Conversation Log)

### `GET /api/entries?limit=50&offset=0&session=<optional>`
List entries with pagination.

### `GET /api/entries/:turn`
Single entry by turn number.

### `GET /api/stream`
SSE stream of new entries as they're written.

```
data: {"turn": 1829, "session": "cli-abc", "user": "...", "assistant": "..."}
```

### `GET /api/sessions`
List all sessions with entry counts.

### `GET /api/sessions/:name`
Single session details with recent entries.

---

## Memory Facts

### `GET /api/memory/facts?q=<query>&limit=20`
Search memory facts.

### `POST /api/memory/facts`
Add/update a memory fact.

### `DELETE /api/memory/facts/:id`
Delete a memory fact.

### `GET /api/memory/snapshots?session_id=<optional>`
Context timeline snapshots.

### `POST /api/memory/snapshots`
Create a new context snapshot.

### `DELETE /api/memory/snapshots/:id`
Delete a snapshot.

---

## Artifacts

### `GET /api/artifacts?session=<optional>&type=<optional>`
List artifacts.

### `GET /api/artifacts/:id`
Single artifact with content.

### `DELETE /api/artifacts/:id`
Delete an artifact.

### `GET /api/artifacts/:id/download`
Download artifact as file.

---

## Workflows

### `GET /api/workflows`
List all workflow definitions.

### `POST /api/workflows`
Create a new workflow.

### `GET /api/workflows/:name`
Single workflow definition.

### `DELETE /api/workflows/:name`
Delete a workflow.

### `POST /api/workflows/:name/run`
Start a workflow run.

### `GET /api/workflows/runs`
List all runs.

### `GET /api/workflows/runs/:id`
Single run details with step status.

### `POST /api/workflows/runs/:id/cancel`
Cancel a running workflow.

---

## Search

### `GET /api/search?q=<query>&limit=20`
Unified search across entries, facts, and artifacts.

---

## Backup

### `GET /api/backups?dir=<optional>`
List available backups.

### `POST /api/backups`
Create a full backup (tar.gz of mycelium.db + index).

### `POST /api/restore`
Restore from a backup archive.

### `POST /api/export`
Export data to a portable format.

### `POST /api/import`
Import data from export format.

---

## Daemon

### `GET /api/daemon`
Daemon health check.

### `POST /api/daemon/restart`
Restart the daemon process.

---

## Web Frontend

### `GET /`
Serves the Leptos SSR-rendered frontend.

### `GET /static/*`
Static assets (WASM, CSS, images).

### `GET /api/config`
Expose non-sensitive configuration to the frontend.

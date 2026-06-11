# Mycelium Health Fixes Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Fix Mycelium health/daemon/index correctness issues found by audit, then add scalable recall/status optimizations.

**Architecture:** Stabilize correctness first: truthful daemon health, consistent index rebuilds, clean launch lifecycle, backup classification. Then optimize recall/status using SQLite/FTS/caching without changing canonical JSONL hash-chain semantics.

**Tech Stack:** Python 3, FastAPI, pytest, SQLite/FTS5, macOS launchd, Vite/React/TypeScript.

---

## Phase 0 — Baseline / Guardrails

### Task 0.1: Capture baseline health output

**Objective:** Save current observable failures before code edits.

**Files:**
- Create: `docs/audits/2026-06-11-health-baseline.md`

**Commands:**
```bash
cd /Users/azfar.naufal/Documents/mycelium
python3 scripts/mycelium.py verify
python3 scripts/mycelium.py status
bash scripts/mycelium web status
curl -s http://127.0.0.1:8421/api/daemon | python3 -m json.tool
curl -s http://127.0.0.1:20151/health || true
python3 -m pytest web/backend/tests -q
cd web/frontend && npm run build && npm exec tsc -- --noEmit
```

**Expected now:** verify/test/build pass; daemon health mismatch likely visible.

---

## Phase 1 — Truthful Daemon Health

### Task 1.1: Add failing backend test for dead daemon health port

**Objective:** `/api/daemon` must not report `running: true` solely because state file exists.

**Files:**
- Modify: `web/backend/tests/test_status_api.py`
- Modify target later: `web/backend/services/status_service.py`

**Test idea:** monkeypatch daemon state path to valid JSON, monkeypatch HTTP health probe failure, assert `running is False` and `status_reason` explains health unreachable.

**Command:**
```bash
python3 -m pytest web/backend/tests/test_status_api.py -q
```

**Expected first:** FAIL.

### Task 1.2: Implement active daemon health probe

**Objective:** Make daemon running status depend on live process/health, not stale state.

**Files:**
- Modify: `web/backend/services/status_service.py`

**Implementation:**
- Add `probe_daemon_health(timeout=0.4)` using `urllib.request.urlopen("http://127.0.0.1:20151/health")`.
- If health OK: `running=True`, `status_reason="health_ok"`.
- If refused but state exists: `running=False`, `status_reason="state_stale_health_unreachable"`.
- If daemon intentionally `--no-http`, detect from launchd/plist/state mode if available; otherwise report `running_unknown`, not true.

**Verify:**
```bash
python3 -m pytest web/backend/tests/test_status_api.py -q
curl -s http://127.0.0.1:8421/api/daemon | python3 -m json.tool
```

---

## Phase 2 — Fix Findings Index Drift

### Task 2.1: Add regression test for findings count parity

**Objective:** DB/index findings count must match log-derived findings count.

**Files:**
- Create: `tests/test_index_parity.py` or extend existing daemon/index tests
- Modify later: `scripts/mycelium.py` / indexing function

**Test:** build temp log with 4 finding entries, rebuild index, assert SQLite findings table count == 4.

**Expected first:** FAIL if current parser skips partial finding records.

### Task 2.2: Normalize finding indexing

**Objective:** Index all `type == "finding"` entries, even if partial fields missing.

**Files:**
- Modify: `scripts/mycelium.py`
- Maybe modify: `scripts/findings.py`

**Rules:**
- Missing `severity` → `info`
- Missing `target` → `unknown`
- Missing `detail` + present `result` → use `result`
- Never rewrite log; normalize at index/display time.

**Verify:**
```bash
python3 -m pytest tests/test_index_parity.py tests/test_findings_cli.py -q
python3 scripts/mycelium.py status
sqlite3 index.db 'select count(*) from findings;'
```

---

## Phase 3 — Stop myceliumd START Spam / Launch Loop

### Task 3.1: Identify duplicate launcher path

**Objective:** Find why `~/.hermes/myceliumd/myceliumd.log` repeats `START` every ~10s.

**Files:**
- Inspect: `~/Library/LaunchAgents/*mycelium*.plist`
- Inspect: `scripts/install-myceliumd.sh`
- Inspect: `scripts/myceliumd.py`

**Commands:**
```bash
launchctl print gui/$(id -u) | grep -i mycelium -A3 -B3 || true
plutil -p ~/Library/LaunchAgents/*mycelium* 2>/dev/null || true
tail -80 ~/.hermes/myceliumd/myceliumd.log
```

### Task 3.2: Make daemon single-instance safe

**Objective:** Prevent repeated daemon starts/import pressure.

**Files:**
- Modify: `scripts/myceliumd.py`
- Modify: `scripts/install-myceliumd.sh` if needed

**Implementation:**
- Add lockfile under `~/.hermes/myceliumd/myceliumd.lock` using `fcntl.flock`.
- If lock held, log once then exit 0.
- Ensure launchd plist does not restart successful exits every few seconds unless intended.

**Verify:**
```bash
bash scripts/mycelium-start
sleep 12
tail -40 ~/.hermes/myceliumd/myceliumd.log
```

**Expected:** no repeated START loop.

---

## Phase 4 — Fix Web Backend Port Collision

### Task 4.1: Make `mycelium-web` status port-aware and restart-safe

**Objective:** Restart should not leave stale backend attempts or `address already in use` errors.

**Files:**
- Modify: `scripts/mycelium-web`

**Implementation:**
- Before start, check listener on 8421.
- If listener belongs to expected service, reuse/sync PID.
- If stale PID, remove PID file.
- Restart via launchd `kickstart -k` when service installed.
- Avoid spawning second uvicorn if 8421 already live.

**Verify:**
```bash
bash scripts/mycelium web restart
bash scripts/mycelium web status
tail -80 ~/.hermes/myceliumd/web/logs/backend.log | grep -i 'address already in use' || true
```

---

## Phase 5 — Backup Bundle Classification

### Task 5.1: Add test for backup list excluding/exporting bundles separately

**Objective:** `.tar.gz` exports must not appear as zero-byte invalid snapshots.

**Files:**
- Modify: `web/backend/tests/test_backup_service.py`
- Modify later: `web/backend/services/backup_service.py`

**Expected API shape:**
```json
{
  "items": [snapshot_dirs_only],
  "bundles": [tar_gz_exports]
}
```

### Task 5.2: Implement bundle classification

**Objective:** Snapshot dirs and export bundles display separately.

**Files:**
- Modify: `web/backend/services/backup_service.py`
- Modify: `web/frontend/src/App.tsx` if UI should show bundles.

**Verify:**
```bash
python3 -m pytest web/backend/tests/test_backup_service.py -q
curl -s http://127.0.0.1:8421/api/backups | python3 -m json.tool
```

---

## Phase 6 — Split `mycelium-start` Read-only Resume from Install

### Task 6.1: Add `mycelium-resume` read-only wrapper

**Objective:** Session startup must not mutate runtime unless explicitly asked.

**Files:**
- Create: `scripts/mycelium-resume`
- Modify: `scripts/mycelium-start`
- Update skill refs later: `~/.hermes/skills/mycelium/SKILL.md`

**Implementation:**
- `mycelium-resume`: run resume + pattern scan + daemon state read only.
- `mycelium-start`: optional ensure/install + call resume; or rename to `mycelium-ensure-start`.

**Verify:**
```bash
bash scripts/mycelium-resume
git diff --exit-code log.jsonl index.db || true
```

---

## Phase 7 — Path Configuration / Split-brain Guard

### Task 7.1: Centralize paths

**Objective:** Remove hardcoded `~/Documents/mycelium` and runtime ambiguity.

**Files:**
- Create: `scripts/mycelium_paths.py` or `web/backend/services/paths.py`
- Modify: `scripts/mycelium.py`
- Modify: `web/backend/services/status_service.py`
- Modify: `web/backend/services/verify_service.py`

**Rules:**
- `MYCELIUM_SOURCE_ROOT` override allowed.
- `MYCELIUM_RUNTIME_ROOT` override allowed.
- Default source: script parent root.
- Default runtime: `~/.hermes/myceliumd/runtime`.
- Startup guard: if source `log.jsonl/index.db/archive` exist and are not symlinks to runtime, warn loudly.

**Verify:**
```bash
python3 -m pytest -q
python3 scripts/mycelium.py verify
```

---

## Phase 8 — Recall / Status Performance

### Task 8.1: Add FTS5 index for recall/search

**Objective:** Avoid O(n) full JSONL scan for recall at large scale.

**Files:**
- Modify: `scripts/mycelium.py` index rebuild/append path
- Modify: `web/backend/services/recall_service.py`
- Add tests: `web/backend/tests/test_recall_service.py`

**Implementation:**
- SQLite virtual table `turns_fts(session, user, assistant, entities, content='turns', content_rowid='rowid')` or standalone FTS.
- On index rebuild, populate FTS.
- Recall first gets candidate rowids from FTS; then applies current scoring to top N candidates.
- Fallback to JSONL scan if FTS unavailable.

**Verify:**
```bash
python3 -m pytest web/backend/tests/test_recall_service.py -q
python3 scripts/mycelium.py status
bash scripts/mycelium recall "continue mycelium"
```

### Task 8.2: Cache status counters

**Objective:** Dashboard/status should not parse entire JSONL on every request.

**Files:**
- Modify: `web/backend/services/status_service.py`

**Implementation:**
- Use `index.db` for counts/recent sessions when available.
- Use file mtime cache with invalidation.
- Fallback to JSONL parse if DB absent/stale.

**Verify:**
```bash
python3 -m pytest web/backend/tests/test_status_api.py -q
curl -s http://127.0.0.1:8421/api/status | python3 -m json.tool
```

---

## Phase 9 — Security Hardening for Local Web Mutations

### Task 9.1: Add local-origin checks for dangerous POSTs

**Objective:** Reduce localhost CSRF risk for backup/restore/migrate/feedback endpoints.

**Files:**
- Modify: `web/backend/app.py`
- Add tests: `web/backend/tests/test_security_headers.py`

**Implementation:**
- Middleware validates `Origin`/`Referer` for POST endpoints if present.
- Allow only `http://127.0.0.1:8420`, `http://localhost:8420`, `http://127.0.0.1:8421`, `http://localhost:8421`.
- Keep CLI curl usable when no Origin header.

**Verify:**
```bash
python3 -m pytest web/backend/tests/test_security_headers.py -q
```

---

## Final Verification

Run all:

```bash
cd /Users/azfar.naufal/Documents/mycelium
python3 -m pytest -q
python3 scripts/mycelium.py verify
python3 scripts/mycelium.py status
bash scripts/mycelium-start
bash scripts/mycelium recall "continue mycelium"
curl -s http://127.0.0.1:8421/api/status | python3 -m json.tool >/tmp/mycelium_status.json
curl -s http://127.0.0.1:8421/api/daemon | python3 -m json.tool >/tmp/mycelium_daemon.json
curl -s http://127.0.0.1:8421/api/backups | python3 -m json.tool >/tmp/mycelium_backups.json
cd web/frontend && npm run build && npm exec tsc -- --noEmit
```

Success criteria:
- tests pass
- hash chain valid
- daemon status truthful
- findings count parity fixed
- no repeated daemon START spam
- no backend port collision
- backup dirs/bundles classified correctly
- recall still works + thread cards still generated
- Observatory builds clean

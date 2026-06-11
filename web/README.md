# Mycelium Web UI

Local-only web UI for Mycelium.

Important architecture:
- same project / same repo as Mycelium
- separate app surface inside `web/`
- reads the existing Mycelium brain + runtime data
- runs alongside `myceliumd`, not instead of it
- backend can now serve the built frontend directly on `8421`

So:
- `myceliumd` = ingestion / safety-net daemon
- web backend = local API over Mycelium data + static app host
- web frontend = browser UI source during development

User-friendly commands
- `make web` → starts observatory backend
- `make web-status` → shows state
- `make web-open` → opens browser UI
- `make web-stop` → stops backend
- `make web-restart` → restarts backend
- `make web-logs` → tails recent logs
- `make web-build` → production frontend build
- `make web-test` → backend tests
- `make install-cli` → installs `mycelium` command into `~/.local/bin`
- `make web-install-service` → install launchd service
- `make web-service-status` → check launchd + backend state
- `make web-uninstall-service` → remove launchd service

Integrated entrypoint
- `bash scripts/mycelium web start`
- `bash scripts/mycelium web status`
- `bash scripts/mycelium web open`
- `bash scripts/mycelium web stop`
- `bash scripts/mycelium web install-service`

Installable command
- run: `make install-cli`
- ensure PATH contains: `~/.local/bin`
- then use:
  - `mycelium web start`
  - `mycelium web status`
  - `mycelium web open`
  - `mycelium web stop`
  - `mycelium web install-service`
  - `mycelium recall cloakbrowser`
  - `mycelium reflex "continue cloakbrowser"`
  - `mycelium recall cloakbrowser --json`

Recall/reflex
- API: `GET /api/recall?q=continue%20cloakbrowser`
- CLI: `mycelium recall <query>`
- Reflex: `mycelium reflex <user-message>`
- Current ranking: aliases + lexical match + entity graph + recency
- State extraction: goal, where left off, decisions, open questions, files touched, next steps, blockers
- Designed for Hermes auto-recall first; other agents can call CLI/API later

URLs
- unified app: `http://127.0.0.1:8421`
- backend health: `http://127.0.0.1:8421/api/health`
- dev frontend only: `http://127.0.0.1:8420`
- ports stay fixed; if `8421` is occupied by another process, launcher refuses to start instead of silently picking a new port

Launcher runtime files
- pid/log root: `~/.hermes/myceliumd/web`
- backend log: `~/.hermes/myceliumd/web/logs/backend.log`
- service log: `~/.hermes/myceliumd/web/logs/service.log`
- launchd plist: `~/Library/LaunchAgents/com.naufalworks.mycelium-observatory.plist`

Vault features
- create snapshot
- verify snapshot or exported bundle
- export snapshot to `.tar.gz`
- import dry-run
- restore snapshot into target root
- migrate dry-run to a new runtime root
- migrate execute with safety snapshot + relink
- frontend confirmation modal for restore/migrate execute
- Esc / backdrop click cancel support
- typed confirmation required

Observability features
- dashboard
- stream
- prettier session inspector
- branch / connections graph
- findings notebook

This web app reads local Mycelium runtime data only. No outbound sync/cloud.

Little ritual
- start prints:
  - spinning the observatory
  - binding the vault
  - opening the canopy

Novel, but still practical.

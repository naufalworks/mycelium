# Mycelium Web UI

Local-only web UI for Mycelium.

Important architecture:
- same project / same repo as Mycelium
- separate app surface inside `web/`
- reads the existing Mycelium brain + runtime data
- runs alongside `myceliumd`, not instead of it

So:
- `myceliumd` = ingestion / safety-net daemon
- web backend = local API over Mycelium data
- web frontend = browser UI

User-friendly commands
- `make web` → starts backend + frontend together
- `make web-status` → shows both states
- `make web-open` → opens browser UI
- `make web-stop` → stops both
- `make web-restart` → restarts both
- `make web-logs` → tails recent logs
- `make web-build` → production frontend build
- `make web-test` → backend tests
- `make install-cli` → installs `mycelium` command into `~/.local/bin`

Integrated entrypoint
- `bash scripts/mycelium web start`
- `bash scripts/mycelium web status`
- `bash scripts/mycelium web open`
- `bash scripts/mycelium web stop`

Installable command
- run: `make install-cli`
- ensure PATH contains: `~/.local/bin`
- then use:
  - `mycelium web start`
  - `mycelium web status`
  - `mycelium web open`
  - `mycelium web stop`

Direct commands
- launcher: `bash scripts/mycelium-web start`
- backend only: `make web-backend`
- frontend only: `make web-frontend`

URLs
- frontend: `http://127.0.0.1:8420`
- backend: `http://127.0.0.1:8421`
- backend health: `http://127.0.0.1:8421/api/health`

Launcher runtime files
- pid/log root: `~/.hermes/myceliumd/web`
- backend log: `~/.hermes/myceliumd/web/logs/backend.log`
- frontend log: `~/.hermes/myceliumd/web/logs/frontend.log`

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

This web app reads local Mycelium runtime data only. No outbound sync/cloud.

Little ritual
- start prints:
  - spinning the observatory
  - binding the vault
  - opening the canopy

Novel, but still practical.

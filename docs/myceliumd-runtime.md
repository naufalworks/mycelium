# myceliumd runtime install

## Goal

Keep development source in `~/Documents/mycelium`.
Install launchd/runtime copy in `~/.hermes/myceliumd/runtime` so macOS TCC does not block `~/Documents` access.

## Layout

Source of truth:
- `~/Documents/mycelium/scripts/myceliumd.py`
- `~/Documents/mycelium/scripts/append.py`
- `~/Documents/mycelium/scripts/mycelium.py`

Installed runtime:
- `~/.hermes/myceliumd/runtime/scripts/myceliumd.py`
- `~/.hermes/myceliumd/runtime/scripts/append.py`
- `~/.hermes/myceliumd/runtime/scripts/mycelium.py`
- `~/Library/LaunchAgents/com.naufal.myceliumd.plist`

State/logs:
- `~/.hermes/myceliumd/state.json`
- `~/.hermes/myceliumd/myceliumd.log`
- `~/.hermes/myceliumd/launchd.stdout.log`
- `~/.hermes/myceliumd/launchd.stderr.log`

Runtime data snapshot:
- `~/.hermes/myceliumd/runtime/log.jsonl`
- `~/.hermes/myceliumd/runtime/index.db`
- `~/.hermes/myceliumd/runtime/archive/`

## Why runtime lives outside Documents

macOS launchd + Python may hit TCC `Operation not permitted` when reading scripts under `~/Documents`.
Moving the installed runtime to `~/.hermes/myceliumd/runtime` avoids that failure while keeping development in `~/Documents/mycelium`.

## Commands

Install / refresh runtime:

```bash
bash scripts/install-myceliumd.sh
```

Deploy + print verification:

```bash
bash scripts/deploy-myceliumd.sh
```

Status:

```bash
bash scripts/status-myceliumd.sh
```

Make targets:

```bash
make install-daemon
make deploy-daemon
make status-daemon
make verify
make runtime-verify
```

## Workflow

1. Edit source in `~/Documents/mycelium/scripts/...`
2. Run `make deploy-daemon`
3. Check logs/state output
4. If clean, commit + push

## Important caveat

The runtime copy is an installed artifact, not the development source.
Edits in `~/Documents/mycelium` do not go live until `install-myceliumd.sh` or `deploy-myceliumd.sh` runs.

## Verification expectations

Good deploy should show:
- empty `launchd.stderr.log`
- `START poll_interval=5s ...` in `launchd.stdout.log`
- valid `~/.hermes/myceliumd/state.json`
- `verify` returns integrity chain valid

Note: launchd install uses `--no-http`, so health checks should read `state.json` / logs, not `http://127.0.0.1:20151/health`.

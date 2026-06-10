# myceliumd E2E + audit

Status: passed after fixes

## Scope

- install idempotency
- launchd bootstrap / kickstart
- source/runtime canonical data consistency
- daemon import from Hermes `state.db`
- wrapper behavior
- failure visibility via logs/state
- TCC-safe runtime path assumptions

## Findings

### Fixed 1 — split-brain data store

Problem:
- daemon wrote to `~/.hermes/myceliumd/runtime/log.jsonl`
- source tools still read `~/Documents/mycelium/log.jsonl`
- result: source/runtime diverged

Fix:
- `install-myceliumd.sh` now symlinks ignored repo data paths to runtime store:
  - `~/Documents/mycelium/log.jsonl`
  - `~/Documents/mycelium/index.db`
  - `~/Documents/mycelium/archive`
- runtime becomes canonical, source paths remain compatible
- if source/runtime ignored data differ during migration, source copy is backed up under `~/.hermes/myceliumd/migration-backups/` before replacement

### Fixed 2 — daemon missed tool-using Hermes turns

Problem:
- SQL pairing logic assumed no assistant messages existed between user input and final assistant answer
- Hermes tool-use sessions insert empty assistant stubs before tool calls/final answer
- result: `fetch_new_pairs()` skipped legitimate sessions; E2E import stalled

Fix:
- pair final assistant answer to the latest preceding user message with no newer user in between
- ignore assistant/tool rows between them

## Test evidence

Automated E2E script:

```bash
make test-daemon-e2e
```

Latest passing run:
- `last_assistant_id: 8153 -> 8161`
- `imports: 8 -> 9`
- source log lines = runtime log lines = 34
- launchd state = running
- plist path/program/working directory correct
- `mycelium-start` stderr empty
- runtime + source `verify` both pass

Manual audit checks:

```bash
readlink ~/Documents/mycelium/log.jsonl
readlink ~/Documents/mycelium/index.db
readlink ~/Documents/mycelium/archive
launchctl print gui/$(id -u)/com.naufal.myceliumd | sed -n '1,40p'
```

Observed:
- all source data paths symlink to `~/.hermes/myceliumd/runtime/...`
- LaunchAgent runs `/usr/bin/python3` against runtime `scripts/myceliumd.py`
- working directory = runtime dir
- stderr empty

## Follow-up

Recommended next hardening:
1. add uninstall script
2. add fixture-based unit test for `fetch_new_pairs()` against tool-call sessions
3. consider optional `--http` mode only for local debugging, not launchd default

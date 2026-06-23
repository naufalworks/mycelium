#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$HOME/.hermes/myceliumd/runtime"
STATE_FILE="$HOME/.hermes/myceliumd/state.json"
STDOUT_LOG="$HOME/.hermes/myceliumd/launchd.stdout.log"
STDERR_LOG="$HOME/.hermes/myceliumd/launchd.stderr.log"
PLIST="$HOME/Library/LaunchAgents/com.naufal.myceliumd.plist"
LABEL="gui/$(id -u)/com.naufal.myceliumd"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_file() {
  [ -e "$1" ] || fail "missing: $1"
}

assert_symlink_target() {
  local path="$1" expected="$2"
  [ -L "$path" ] || fail "$path not symlink"
  local actual
  actual="$(readlink "$path")"
  [ "$actual" = "$expected" ] || fail "$path -> $actual (expected $expected)"
}

json_field() {
  /usr/bin/python3 - "$1" "$2" <<'PY'
import json, sys
from pathlib import Path
obj = json.loads(Path(sys.argv[1]).read_text())
print(obj[sys.argv[2]])
PY
}

echo "[1] install idempotent"
bash "$ROOT/scripts/install-myceliumd.sh" >/tmp/myceliumd-install-1.out
bash "$ROOT/scripts/install-myceliumd.sh" >/tmp/myceliumd-install-2.out

assert_file "$PLIST"
assert_file "$RUNTIME_DIR/scripts/myceliumd.py"
assert_file "$RUNTIME_DIR/scripts/append.py"
assert_file "$RUNTIME_DIR/scripts/mycelium.py"

assert_symlink_target "$ROOT/log.jsonl" "$RUNTIME_DIR/log.jsonl"
assert_symlink_target "$ROOT/index.db" "$RUNTIME_DIR/index.db"
assert_symlink_target "$ROOT/archive" "$RUNTIME_DIR/archive"

echo "[2] launchd loaded"
launchctl print "$LABEL" >/tmp/myceliumd-launchctl.txt 2>/dev/null || fail "launchd label not loaded"
assert_file "$STATE_FILE"
[ ! -s "$STDERR_LOG" ] || fail "stderr not empty"
grep -q 'START poll_interval=5s' "$STDOUT_LOG" || fail "stdout missing START marker"

before_id="$(json_field "$STATE_FILE" last_assistant_id)"
before_imports="$(json_field "$STATE_FILE" imports)"

src_before="$(wc -l < "$ROOT/log.jsonl")"
rt_before="$(wc -l < "$RUNTIME_DIR/log.jsonl")"
[ "$src_before" = "$rt_before" ] || fail "source/runtime log counts diverged before test: $src_before vs $rt_before"

echo "[3] generate real Hermes turn"
probe="mycelium e2e probe $(date +%s)"
hermes chat -q "$probe" --quiet >/tmp/myceliumd-hermes-probe.out

echo "[4] wait for daemon import"
/usr/bin/python3 - "$before_id" <<'PY'
import json, sys, time
from pathlib import Path
state = Path.home()/'.hermes/myceliumd/state.json'
expected = int(sys.argv[1])
deadline = time.time() + 60
while time.time() < deadline:
    try:
        data = json.loads(state.read_text())
        if int(data.get('last_assistant_id', 0)) > expected:
            raise SystemExit(0)
    except Exception:
        pass
    time.sleep(2)
raise SystemExit(1)
PY

after_id="$(json_field "$STATE_FILE" last_assistant_id)"
after_imports="$(json_field "$STATE_FILE" imports)"
[ "$after_id" -gt "$before_id" ] || fail "last_assistant_id did not advance"
[ "$after_imports" -gt "$before_imports" ] || fail "imports did not advance"

grep -q "$probe" "$RUNTIME_DIR/log.jsonl" || fail "runtime log missing probe"
grep -q "$probe" "$ROOT/log.jsonl" || fail "source log missing probe"

src_after="$(wc -l < "$ROOT/log.jsonl")"
rt_after="$(wc -l < "$RUNTIME_DIR/log.jsonl")"
[ "$src_after" = "$rt_after" ] || fail "source/runtime log counts diverged after test: $src_after vs $rt_after"

echo "[5] runtime verify"
/usr/bin/python3 "$RUNTIME_DIR/scripts/mycelium.py" verify >/tmp/myceliumd-runtime-verify.out
/usr/bin/python3 "$ROOT/scripts/mycelium.py" verify >/tmp/myceliumd-source-verify.out

echo "[6] wrapper sanity"
bash "$ROOT/scripts/mycelium-start" >/tmp/myceliumd-start.out 2>/tmp/myceliumd-start.err
[ ! -s /tmp/myceliumd-start.err ] || fail "mycelium-start stderr not empty"
grep -q '🍄 Daemon state' /tmp/myceliumd-start.out || fail "mycelium-start missing daemon state"

echo "PASS"
echo "  last_assistant_id: $before_id -> $after_id"
echo "  imports          : $before_imports -> $after_imports"
echo "  probe            : $probe"
echo "  source log lines : $src_after"
echo "  runtime log lines: $rt_after"

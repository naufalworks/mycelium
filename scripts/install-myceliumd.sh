#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$HOME/.hermes/myceliumd/runtime"
SCRIPTS_DIR="$RUNTIME_DIR/scripts"
PLIST_PATH="$HOME/Library/LaunchAgents/com.naufal.myceliumd.plist"
PYTHON_BIN="/usr/bin/python3"

mkdir -p "$SCRIPTS_DIR" "$RUNTIME_DIR/archive" "$HOME/.hermes/myceliumd"

install -m 755 "$ROOT/scripts/myceliumd.py" "$SCRIPTS_DIR/myceliumd.py"
install -m 755 "$ROOT/scripts/append.py" "$SCRIPTS_DIR/append.py"
install -m 755 "$ROOT/scripts/mycelium.py" "$SCRIPTS_DIR/mycelium.py"

[ -f "$ROOT/log.jsonl" ] && cp "$ROOT/log.jsonl" "$RUNTIME_DIR/log.jsonl" || true
[ -f "$ROOT/index.db" ] && cp "$ROOT/index.db" "$RUNTIME_DIR/index.db" || true
[ -d "$ROOT/archive" ] && mkdir -p "$RUNTIME_DIR/archive" && cp -R "$ROOT/archive/." "$RUNTIME_DIR/archive/" || true

/usr/bin/python3 - <<'PY'
from pathlib import Path
files = [
    Path.home()/'.hermes/myceliumd/runtime/scripts/myceliumd.py',
    Path.home()/'.hermes/myceliumd/runtime/scripts/append.py',
    Path.home()/'.hermes/myceliumd/runtime/scripts/mycelium.py',
]
replacements = {
    'HOME = Path.home()\nHERMES = HOME / ".hermes"\nSTATE_DB = HERMES / "state.db"\nDAEMON_DIR = HERMES / "myceliumd"\nDAEMON_STATE = DAEMON_DIR / "state.json"\nDAEMON_LOG = DAEMON_DIR / "myceliumd.log"\nMYCELIUM = HOME / "Documents/mycelium"\nAPPEND = MYCELIUM / "scripts/append.py"\nVERIFY = MYCELIUM / "scripts/mycelium.py"\n':
    'HOME = Path.home()\nHERMES = HOME / ".hermes"\nSTATE_DB = HERMES / "state.db"\nDAEMON_DIR = HERMES / "myceliumd"\nDAEMON_STATE = DAEMON_DIR / "state.json"\nDAEMON_LOG = DAEMON_DIR / "myceliumd.log"\nMYCELIUM = HERMES / "myceliumd/runtime"\nAPPEND = MYCELIUM / "scripts/append.py"\nVERIFY = MYCELIUM / "scripts/mycelium.py"\n',
    'MYCELIUM = Path.home() / "Documents/mycelium"\n': 'MYCELIUM = Path.home() / ".hermes/myceliumd/runtime"\n',
}
for path in files:
    text = path.read_text()
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = text.replace('    "mycelium", "memgit", "page-radar", "page radar", "companion",\n', '    "mycelium", "myceliumd", "memgit", "page-radar", "page radar", "companion",\n')
    path.write_text(text)
PY

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.naufal.myceliumd</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>$SCRIPTS_DIR/myceliumd.py</string>
    <string>--once</string>
    <string>--no-http</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>StartInterval</key>
  <integer>5</integer>

  <key>WorkingDirectory</key>
  <string>$RUNTIME_DIR</string>

  <key>StandardOutPath</key>
  <string>$HOME/.hermes/myceliumd/launchd.stdout.log</string>

  <key>StandardErrorPath</key>
  <string>$HOME/.hermes/myceliumd/launchd.stderr.log</string>

  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
: > "$HOME/.hermes/myceliumd/launchd.stderr.log"
: > "$HOME/.hermes/myceliumd/launchd.stdout.log"
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
sleep 2
launchctl kickstart -k "gui/$(id -u)/com.naufal.myceliumd" >/dev/null 2>&1 || true

echo "Installed myceliumd runtime"
echo "  source  : $ROOT"
echo "  runtime : $RUNTIME_DIR"
echo "  plist   : $PLIST_PATH"

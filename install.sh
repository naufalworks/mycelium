#!/usr/bin/env bash
# 🍄 Mycelium — Zero-to-Hero Installer
#
# Installs mycelium from scratch on a fresh machine:
#   1. Checks dependencies (Go)
#   2. Builds all Go binaries
#   3. Installs to /usr/local/bin
#   4. Sets up data directories
#   5. Creates launchd (macOS) or systemd (Linux) service
#   6. Installs Live Echo hooks
#   7. Optionally restores from latest backup
#
# Usage:
#   curl -sfL https://raw.githubusercontent.com/naufalworks/mycelium/main/install.sh | bash
#   # or:
#   bash install.sh [--backup <path>] [--prefix /usr/local]

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────
MYCELIUM_SRC="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"
BACKUP_FILE=""
SKIP_HOOKS=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}🍄${NC} $1"; }
ok()    { echo -e "${GREEN}✅${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠️${NC} $1"; }
err()   { echo -e "${RED}❌${NC} $1"; }

# ── Parse args ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP_FILE="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --skip-hooks) SKIP_HOOKS=1; shift ;;
    --help|-h) echo "Usage: $0 [--backup <file>] [--prefix <dir>] [--skip-hooks]"; exit 0 ;;
    *) err "Unknown option: $1"; exit 1 ;;
  esac
done

BINDIR="$PREFIX/bin"

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  🍄 Mycelium — Zero-to-Hero Install${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Step 1: Check dependencies ──────────────────────────────────────────
info "Checking dependencies..."

if command -v go &>/dev/null; then
  ok "Go $(go version | grep -oP 'go\d+\.\d+' || true) found"
else
  err "Go not found! Install Go 1.25+: https://go.dev/dl/"
  exit 1
fi

if command -v python3 &>/dev/null; then
  ok "Python $(python3 --version 2>&1 | grep -oP '\d+\.\d+\.\d+' || true) found"
else
  warn "Python3 not found (some legacy features may not work)"
fi

# ── Step 2: Build Go binaries ───────────────────────────────────────────
info "Building Go binaries..."
cd "$MYCELIUM_SRC/go"

go build -o "$BINDIR/mycelium"     ./cmd/mycelium/  || { err "Failed to build mycelium CLI"; exit 1; }
go build -o "$BINDIR/myceliumd"    ./cmd/myceliumd/ || { err "Failed to build mycelium daemon"; exit 1; }
go build -o "$BINDIR/mycelium-proxy" ./cmd/proxy/   || warn "Failed to build proxy (optional)"
go build -o "$BINDIR/mycelium-mcp"   ./cmd/mcp/     || warn "Failed to build MCP server (optional)"

ok "Binaries installed to $BINDIR"
echo "    $BINDIR/mycelium        (CLI)"
echo "    $BINDIR/myceliumd       (daemon)"
echo "    $BINDIR/mycelium-proxy  (proxy)"
echo "    $BINDIR/mycelium-mcp    (MCP server)"

# ── Step 3: Create data directories ─────────────────────────────────────
info "Setting up data directories..."
MYCELIUM_DATA="${MYCELIUM_DATA:-$HOME/Documents/mycelium}"
mkdir -p "$MYCELIUM_DATA"
mkdir -p "$MYCELIUM_DATA/l1" "$MYCELIUM_DATA/l2" "$MYCELIUM_DATA/archive"
mkdir -p "$MYCELIUM_DATA/evolution" "$MYCELIUM_DATA/garden"
ok "Data directory: $MYCELIUM_DATA"

# ── Step 4: Restore from backup (if available) ──────────────────────────
if [[ -n "$BACKUP_FILE" ]]; then
  info "Restoring from backup: $BACKUP_FILE..."
  if [[ -f "$BACKUP_FILE" ]]; then
    mycelium restore "$BACKUP_FILE" 2>/dev/null || {
      # Direct extraction fallback
      tar -xzf "$BACKUP_FILE" -C "$MYCELIUM_DATA" 2>/dev/null && ok "Backup restored" || warn "Backup restore failed"
    }
  else
    warn "Backup file not found: $BACKUP_FILE"
  fi
elif [[ -d "$MYCELIUM_DATA" && ! -f "$MYCELIUM_DATA/log.jsonl" ]]; then
  # Look for latest backup in default location
  DEFAULT_BACKUP_DIR="$HOME/.hermes/myceliumd/backups"
  if [[ -d "$DEFAULT_BACKUP_DIR" ]]; then
    LATEST=$(ls -t "$DEFAULT_BACKUP_DIR"/mycelium-backup-*.tar.gz 2>/dev/null | head -1)
    if [[ -n "$LATEST" ]]; then
      info "Found backup: $LATEST"
      echo "  To restore: mycelium restore \"$LATEST\""
    fi
  fi
  info "No existing data found — starting fresh brain"
  touch "$MYCELIUM_DATA/log.jsonl"
fi

# ── Step 5: Install service ─────────────────────────────────────────────
info "Installing daemon service..."
UNAME_S="$(uname -s)"

if [[ "$UNAME_S" == "Darwin" ]]; then
  # macOS: launchd
  PLIST_PATH="$HOME/Library/LaunchAgents/com.naufal.myceliumd.plist"
  cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.naufal.myceliumd</string>
  <key>ProgramArguments</key>
  <array>
    <string>$BINDIR/myceliumd</string>
    <string>--root</string>
    <string>$MYCELIUM_DATA</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <key>WorkingDirectory</key>
  <string>$MYCELIUM_DATA</string>
  <key>StandardOutPath</key>
  <string>$HOME/.hermes/myceliumd/launchd.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/.hermes/myceliumd/launchd.stderr.log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  launchctl load "$PLIST_PATH"
  ok "launchd service installed and started"

elif [[ "$UNAME_S" == "Linux" ]]; then
  # Linux: systemd
  SERVICE_PATH="/etc/systemd/system/myceliumd.service"
  if [[ -d /etc/systemd/system ]]; then
    sudo tee "$SERVICE_PATH" >/dev/null <<UNIT
[Unit]
Description=Mycelium permanent memory daemon
After=network.target

[Service]
Type=simple
ExecStart=$BINDIR/myceliumd --root $MYCELIUM_DATA
Restart=always
RestartSec=5
Environment=MYCELIUM_ROOT=$MYCELIUM_DATA

[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload
    sudo systemctl enable myceliumd
    sudo systemctl start myceliumd
    ok "systemd service installed and started"
  else
    warn "systemd not found. Start manually: myceliumd --root $MYCELIUM_DATA &"
  fi
else
  warn "Unknown OS: $UNAME_S. Start manually: myceliumd --root $MYCELIUM_DATA &"
fi

# ── Step 6: Install hooks ───────────────────────────────────────────────
if [[ $SKIP_HOOKS -eq 0 ]]; then
  info "Installing Live Echo hooks..."
  HOOKS_DIR="$MYCELIUM_SRC/hooks"

  # ZSH hook
  if [[ -f "$HOOKS_DIR/zsh-echo.sh" ]]; then
    cp "$HOOKS_DIR/zsh-echo.sh" "$HOME/.mycelium-echo.zsh"
    ok "ZSH hook installed: $HOME/.mycelium-echo.zsh"
    echo "     Add to ~/.zshrc: source ~/.mycelium-echo.zsh"
  fi

  # Git post-commit
  if [[ -d "$MYCELIUM_DATA/.git/hooks" ]]; then
    cp "$HOOKS_DIR/post-commit" "$MYCELIUM_DATA/.git/hooks/post-commit"
    chmod +x "$MYCELIUM_DATA/.git/hooks/post-commit"
    ok "Git post-commit hook installed"
  fi
fi

# ── Step 7: Verify ──────────────────────────────────────────────────────
info "Running verification..."
if command -v mycelium &>/dev/null; then
  mycelium verify 2>/dev/null || mycelium precheck 2>/dev/null || true
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  🍄 Mycelium installed successfully!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  CLI:       mycelium status"
echo "  Verify:    mycelium verify"
echo "  Search:    mycelium search \"query\""
echo "  Backup:    mycelium backup"
echo "  Daemon:    myceliumd"
echo "  Health:    http://127.0.0.1:20151/health"
echo ""
echo "  To use the proxy:"
echo "    export ANTHROPIC_BASE_URL=http://127.0.0.1:8443"
echo "    mycelium-proxy &"
echo ""
echo "  To install shell hooks:"
echo "    echo 'source ~/.mycelium-echo.zsh' >> ~/.zshrc"
echo ""
echo "  Docs:    $MYCELIUM_SRC/docs"
echo "  Data:    $MYCELIUM_DATA"
echo ""

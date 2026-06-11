#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/Documents/mycelium"
BIN_DIR="$HOME/.local/bin"
TARGET="$BIN_DIR/mycelium"

mkdir -p "$BIN_DIR"
ln -sf "$ROOT/scripts/mycelium" "$TARGET"
chmod +x "$ROOT/scripts/mycelium" "$ROOT/scripts/mycelium-web"

echo "installed → $TARGET"
echo "if needed, add to PATH: export PATH=\"$HOME/.local/bin:$PATH\""
echo "then use: mycelium web start"

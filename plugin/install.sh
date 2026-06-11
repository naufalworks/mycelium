#!/usr/bin/env bash
# Install mycelium as a Hermes plugin via symlink.
# Usage: bash plugin/install.sh
set -euo pipefail

HERMES_PLUGINS="${HOME}/.hermes/plugins"
PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${HERMES_PLUGINS}/mycelium"

mkdir -p "$HERMES_PLUGINS"

if [ -L "$TARGET" ]; then
  echo "Removing existing symlink: $TARGET"
  rm "$TARGET"
elif [ -d "$TARGET" ]; then
  echo "Removing existing directory: $TARGET"
  rm -rf "$TARGET"
fi

ln -s "$PLUGIN_DIR" "$TARGET"
echo "✅ Installed mycelium plugin → $TARGET"
echo "   Restart Hermes or /reload to activate."

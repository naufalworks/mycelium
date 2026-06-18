#!/usr/bin/env bash
# 🧬 Mycelium Live Echo — Install shell + git hooks for auto-logging.
#
# Installs:
#   1. ZSH preexec/precmd hooks (logs every terminal command)
#   2. Git post-commit hook (logs every commit)
#
# Usage:
#   source hooks/install.sh          # install for current shell
#   echo "source ~/.mycelium-echo.zsh" >> ~/.zshrc  # permanent
#
# Requirements:
#   - mycelium append.py or brain CLI accessible from PATH

set -euo pipefail

MYCELIUM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPEND_SCRIPT="$MYCELIUM_DIR/scripts/append.py"
ECHO_SCRIPT="$MYCELIUM_DIR/hooks/zsh-echo.sh"
GIT_HOOK="$MYCELIUM_DIR/hooks/post-commit"
INSTALL_PATH="$HOME/.mycelium-echo.zsh"
GIT_HOOK_INSTALL_DIR="$MYCELIUM_DIR/.git/hooks"

echo "🍄 Mycelium Live Echo — Installing hooks..."
echo "  Mycelium root: $MYCELIUM_DIR"

# ── 1. Install ZSH hook ──────────────────────────────────────────────────
if [ -f "$ECHO_SCRIPT" ]; then
  cp "$ECHO_SCRIPT" "$INSTALL_PATH"
  echo "  ✅ ZSH hook installed: $INSTALL_PATH"
  echo "     Add to ~/.zshrc: source $INSTALL_PATH"
else
  echo "  ⚠️  ZSH hook script not found: $ECHO_SCRIPT"
fi

# ── 2. Install Git post-commit hook ──────────────────────────────────────
if [ -d "$GIT_HOOK_INSTALL_DIR" ]; then
  cp "$GIT_HOOK" "$GIT_HOOK_INSTALL_DIR/post-commit"
  chmod +x "$GIT_HOOK_INSTALL_DIR/post-commit"
  echo "  ✅ Git post-commit hook installed"
else
  echo "  ⚠️  Not a git repository or .git/hooks not found"
fi

# ── 3. Check append.py availability ──────────────────────────────────────
if [ -f "$APPEND_SCRIPT" ]; then
  echo "  ✅ Append script found: $APPEND_SCRIPT"
else
  echo "  ⚠️  Append script not found: $APPEND_SCRIPT"
fi

echo ""
echo "📋 Next steps:"
echo "  1. source $INSTALL_PATH  # activate hooks now"
echo "  2. echo 'source $INSTALL_PATH' >> ~/.zshrc  # permanent"
echo "  3. Done! Every command and commit will be logged to mycelium."

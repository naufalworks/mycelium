# 🧬 Mycelium Live Echo — ZSH hook for auto-logging terminal commands.
#
# Source this file in ~/.zshrc to auto-log every terminal command to mycelium:
#   source ~/.mycelium-echo.zsh
#
# Logs: command, working directory, exit code, duration, timestamp.
# Each command becomes a mycelium entry with type="command" so it's
# searchable alongside conversations and findings.

# ── Configuration ──────────────────────────────────────────────────────
: ${MYCELIUM_ECHO_DIR:="$(cd "$(dirname "${(%):-%x}")/.." &>/dev/null && pwd)"}
: ${MYCELIUM_APPEND:="$MYCELIUM_ECHO_DIR/scripts/append.py"}
: ${MYCELIUM_ECHO_SESSION:="echo-$(hostname -s)"}
: ${MYCELIUM_VERBOSE:=0}

# ── Hooks ──────────────────────────────────────────────────────────────

# preexec: runs RIGHT BEFORE each command executes.
# Captures the command text and start time.
__mycelium_preexec() {
  __MYCELIUM_CMD="$1"
  __MYCELIUM_START=$EPOCHREALTIME
}

# precmd: runs RIGHT AFTER each command completes.
# Captures exit code, duration, and logs to mycelium.
__mycelium_precmd() {
  local exit_code=$?
  local cmd="${__MYCELIUM_CMD:-}"
  __MYCELIUM_CMD=""

  # Skip empty commands and internal commands
  [[ -z "$cmd" ]] && return
  [[ "$cmd" == "__mycelium_"* ]] && return

  # Calculate duration
  local duration=0
  if [[ -n "$__MYCELIUM_START" ]]; then
    duration=$(( EPOCHREALTIME - __MYCELIUM_START ))
    # Convert to integer milliseconds
    duration=$(( ${duration%.*} * 1000 + ${duration#*.} / 1000 ))
  fi
  __MYCELIUM_START=""

  # Build the log entry
  local user_msg="[exit=$exit_code] [${duration}ms] $(pwd) $ ${cmd}"
  local assistant_msg=""

  # Capture last few lines of output (if any)
  if [[ $exit_code -ne 0 && -f /dev/null ]]; then
    assistant_msg="exit_code=$exit_code duration=${duration}ms"
  fi

  # Determine type based on exit code
  local entry_type="talk"
  if [[ $exit_code -ne 0 ]]; then
    entry_type="dead-end"
  fi

  # Log via append.py (async — don't block the prompt)
  if [[ -x "$MYCELIUM_APPEND" ]]; then
    (
      "$MYCELIUM_APPEND" \
        --session "$MYCELIUM_ECHO_SESSION" \
        --type "$entry_type" \
        "$user_msg" \
        "$assistant_msg" \
        2>/dev/null
    ) &!
  fi

  # Verbose: show a small indicator
  if [[ $MYCELIUM_VERBOSE -eq 1 ]]; then
    echo >&2 "🧬 [${duration}ms] $cmd"
  fi
}

# ── Register hooks ─────────────────────────────────────────────────────
# ZSH arrays: preexec_functions and precmd_functions
autoload -Uz add-zsh-hook
add-zsh-hook preexec __mycelium_preexec
add-zsh-hook precmd __mycelium_precmd

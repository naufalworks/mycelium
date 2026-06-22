#!/usr/bin/env bash
# 🧬 Mycelium Antigravity Proxy — intercept Gemini API calls
# Routes Antigravity's Gemini traffic through mycelium for memory logging.
# Usage:
#   ./antigravity-proxy start   — enable proxy + route Gemini → mycelium
#   ./antigravity-proxy stop    — disable proxy, restore normal network
#   ./antigravity-proxy status  — show current state

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PROXY_BIN="$ROOT/mycelium-proxy"
PAC_DIR="$HOME/.hermes/myceliumd"
PAC_FILE="$PAC_DIR/proxy.pac"
PLIST="$HOME/Library/LaunchAgents/com.naufal.mycelium-proxy.plist"
PROXY_PORT="8443"
MESHGATE="http://localhost:8080"

# ── PAC file: only route Gemini domains through proxy ───────
generate_pac() {
  mkdir -p "$PAC_DIR"
  cat > "$PAC_FILE" <<PAC
function FindProxyForURL(url, host) {
  // Route Gemini API calls through mycelium proxy
  if (dnsDomainIs(host, "generativelanguage.googleapis.com") ||
      dnsDomainIs(host, "*.googleapis.com") ||
      dnsDomainIs(host, "ai.googleapis.com")) {
    return "PROXY 127.0.0.1:$PROXY_PORT";
  }
  // Everything else goes direct
  return "DIRECT";
}
PAC
  echo "  → PAC file: $PAC_FILE"
}

# ── Set macOS network proxy via PAC ─────────────────────────
enable_proxy() {
  local service
  service=$(networksetup -listallnetworkservices | grep -v '*' | head -1)
  if [ -z "$service" ]; then
    echo "❌ No network service found"
    return 1
  fi
  echo "  → Network: $service"
  generate_pac
  sudo networksetup -setautoproxyurl "$service" "file://$PAC_FILE"
  sudo networksetup -setautoproxystate "$service" on
  echo "  ✅ PAC proxy enabled for Gemini → :$PROXY_PORT"
}

# ── Clear system proxy ──────────────────────────────────────
disable_proxy() {
  local service
  service=$(networksetup -listallnetworkservices | grep -v '*' | head -1)
  if [ -z "$service" ]; then return 0; fi
  sudo networksetup -setautoproxystate "$service" off
  echo "  ✅ System proxy disabled"
}

# ── Ensure mycelium proxy is running via launchd ─────────────
ensure_proxy() {
  if lsof -i :$PROXY_PORT -sTCP:LISTEN 2>/dev/null | grep -q mycelium; then
    echo "  ✅ Proxy already running on :$PROXY_PORT"
    return 0
  fi
  # Try launchd
  if launchctl print "gui/$(id -u)/com.naufal.mycelium-proxy" 2>/dev/null | grep -q "state = running"; then
    echo "  ✅ Launchd proxy running"
    return 0
  fi
  # Start via launchd
  if [ -f "$PLIST" ]; then
    echo "  → Starting proxy via launchd..."
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    sleep 2
  fi
  # Fallback: direct start
  if ! lsof -i :$PROXY_PORT -sTCP:LISTEN 2>/dev/null | grep -q mycelium; then
    echo "  → Starting proxy directly..."
    nohup "$PROXY_BIN" --port "$PROXY_PORT" --upstream "$MESHGATE" \
      > /tmp/mycelium-proxy.log 2>&1 &
    sleep 2
  fi
  if lsof -i :$PROXY_PORT -sTCP:LISTEN 2>/dev/null | grep -q mycelium; then
    echo "  ✅ Proxy started on :$PROXY_PORT"
  else
    echo "  ❌ Proxy failed to start"
    return 1
  fi
}

# ── Status ──────────────────────────────────────────────────
show_status() {
  echo "🧬 Mycelium Antigravity Proxy"
  echo ""
  if lsof -i :$PROXY_PORT -sTCP:LISTEN 2>/dev/null | grep -q mycelium; then
    local cpu pid
    pid=$(lsof -i :$PROXY_PORT -sTCP:LISTEN -t 2>/dev/null)
    cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null || echo "?")
    echo "  Proxy:    🟢 :$PROXY_PORT (PID $pid, CPU ${cpu}%)"
  else
    echo "  Proxy:    🔴 offline"
  fi

  local service
  service=$(networksetup -listallnetworkservices | grep -v '*' | head -1 2>/dev/null || echo "")
  if [ -n "$service" ]; then
    local proxy_state
    proxy_state=$(networksetup -getautoproxyurl "$service" 2>/dev/null | head -3 || echo "")
    if echo "$proxy_state" | grep -q "Enabled: Yes"; then
      echo "  PAC:      🟢 active"
      echo "$proxy_state" | head -3
    else
      echo "  PAC:      🔴 disabled"
    fi
  fi

  echo ""
  echo "  Brain:    $(python3 "$ROOT/scripts/mycelium.py" status 2>&1 | grep 'Turns' | head -1)"
}

# ── Main ────────────────────────────────────────────────────
case "${1:-help}" in
  start)
    echo "🧬 Starting Antigravity proxy..."
    ensure_proxy
    enable_proxy
    echo ""
    show_status
    ;;
  stop)
    echo "🧬 Stopping Antigravity proxy..."
    disable_proxy
    echo "  Proxy can be stopped with: launchctl bootout gui/$(id -u)/com.naufal.mycelium-proxy"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    show_status
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}"
    echo ""
    echo "  start    — enable Gemini proxy + PAC + mycelium proxy"
    echo "  stop     — disable PAC, restore direct network"
    echo "  status   — show proxy/PAC/brain status"
    ;;
esac

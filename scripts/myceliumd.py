#!/usr/bin/env python3
"""
🍄 myceliumd.py — Mycelium safety-net daemon.

Polls Hermes ~/.hermes/state.db for completed user→assistant pairs.
Imports missed turns into ~/Documents/mycelium/log.jsonl.
Provides health endpoint on localhost:20151.

Why:
- Survives /new
- Survives agent forgetfulness
- Survives crashes (once message pair lands in state.db)

State:
- ~/.hermes/myceliumd/state.json — last imported assistant message id
- ~/.hermes/myceliumd/myceliumd.log — daemon log
"""
import argparse, fcntl, json, os, sqlite3, sys, time, subprocess, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HOME = Path.home()
HERMES = HOME / ".hermes"
STATE_DB = HERMES / "state.db"
DAEMON_DIR = HERMES / "myceliumd"
DAEMON_STATE = DAEMON_DIR / "state.json"
DAEMON_LOG = DAEMON_DIR / "myceliumd.log"
DAEMON_LOCK = DAEMON_DIR / "myceliumd.lock"
MYCELIUM = Path(__file__).resolve().parent.parent  # runtime dir
APPEND = MYCELIUM / "scripts/append.py"
VERIFY = MYCELIUM / "scripts/mycelium.py"
POLL_INTERVAL = 15
PORT = 20151


def log(msg):
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(DAEMON_LOG, "a") as f:
        f.write(line + "\n")


def acquire_lock():
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(DAEMON_LOCK, "a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("LOCK_HELD another myceliumd instance is running; exiting")
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"pid={os.getpid()}\n")
    lock_file.flush()
    return lock_file


def get_latest_assistant_id():
    if not STATE_DB.exists():
        return 0
    conn = sqlite3.connect(str(STATE_DB))
    cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE role='assistant'")
    value = int(cur.fetchone()[0] or 0)
    conn.close()
    return value


def load_state():
    if DAEMON_STATE.exists():
        try:
            return json.loads(DAEMON_STATE.read_text())
        except Exception:
            pass
    # First run: bootstrap to current latest assistant msg.
    # Prevents mass backfill / duplicate import of historical sessions.
    return {"last_assistant_id": get_latest_assistant_id(), "last_verify_hour": None, "imports": 0}


def save_state(state):
    DAEMON_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_STATE.write_text(json.dumps(state, indent=2))


def ensure_ready():
    if not STATE_DB.exists():
        raise FileNotFoundError(f"Hermes state DB missing: {STATE_DB}")
    if not APPEND.exists():
        raise FileNotFoundError(f"append.py missing: {APPEND}")


def classify_pair(user_text, assistant_text):
    combined = f"{user_text} {assistant_text}".lower()
    if any(k in combined for k in ["decide", "decision", "lets build", "let's build", "plan it", "overhaul"]):
        return "decision"
    if any(k in combined for k in ["idea", "novel way", "brainstorm"]):
        return "idea"
    return "talk"


def session_name_from_messages(session_id, first_user, assistant):
    text = f"{first_user} {assistant}".lower()
    # lightweight heuristic; daemon is safety-net, not perfect semantics
    if "mycelium" in text:
        return "mycelium-auto"
    if "grav" in text:
        return "grav-auto"
    if "page radar" in text or "page-radar" in text:
        return "page-radar-auto"
    return f"session-{session_id[:8]}"


def fetch_new_pairs(after_assistant_id):
    conn = sqlite3.connect(str(STATE_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT a.id AS assistant_id, a.session_id, a.timestamp AS assistant_ts,
               a.content AS assistant_content,
               (
                 SELECT u.id FROM messages u
                 WHERE u.session_id = a.session_id
                   AND u.role = 'user'
                   AND u.id < a.id
                   AND NOT EXISTS (
                     SELECT 1 FROM messages mid
                     WHERE mid.session_id = a.session_id
                       AND mid.id > u.id AND mid.id < a.id
                       AND mid.role = 'user'
                   )
                 ORDER BY u.id DESC LIMIT 1
               ) AS user_id,
               (
                 SELECT u.content FROM messages u
                 WHERE u.id = (
                   SELECT u2.id FROM messages u2
                   WHERE u2.session_id = a.session_id
                     AND u2.role = 'user'
                     AND u2.id < a.id
                     AND NOT EXISTS (
                       SELECT 1 FROM messages mid
                       WHERE mid.session_id = a.session_id
                         AND mid.id > u2.id AND mid.id < a.id
                         AND mid.role = 'user'
                     )
                   ORDER BY u2.id DESC LIMIT 1
                 )
               ) AS user_content
        FROM messages a
        WHERE a.role = 'assistant'
          AND a.id > ?
          AND a.content IS NOT NULL
          AND trim(a.content) != ''
          AND NOT EXISTS (
            SELECT 1 FROM messages a2
            WHERE a2.session_id = a.session_id
              AND a2.role = 'assistant'
              AND a2.id > a.id
              AND a2.content IS NOT NULL
              AND trim(a2.content) != ''
              AND NOT EXISTS (
                SELECT 1 FROM messages u3
                WHERE u3.session_id = a.session_id
                  AND u3.role = 'user'
                  AND u3.id > a.id
                  AND u3.id < a2.id
              )
          )
        ORDER BY a.id ASC
        """,
        (after_assistant_id,),
    )
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        if not r["user_id"] or not r["user_content"]:
            continue
        out.append({
            "assistant_id": r["assistant_id"],
            "session_id": r["session_id"],
            "assistant_ts": r["assistant_ts"],
            "assistant_content": r["assistant_content"],
            "user_id": r["user_id"],
            "user_content": r["user_content"],
        })
    return out


def condense(text, limit=240):
    text = (text or "").strip().replace("\n", " ")
    text = " ".join(text.split())
    return text[:limit]


def import_pair(pair, state):
    user_text = condense(pair["user_content"])
    assistant_text = condense(pair["assistant_content"])
    turn_type = classify_pair(user_text, assistant_text)
    session = session_name_from_messages(pair["session_id"], user_text, assistant_text)

    cmd = [
        sys.executable,
        str(APPEND),
        "--session", session,
        "--type", turn_type,
        user_text,
        assistant_text,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"IMPORT_FAIL assistant_id={pair['assistant_id']} rc={proc.returncode} err={proc.stderr.strip()}")
        return False
    state["last_assistant_id"] = pair["assistant_id"]
    state["imports"] = state.get("imports", 0) + 1
    save_state(state)
    log(f"IMPORTED assistant_id={pair['assistant_id']} session={session} type={turn_type}")
    return True


def verify_if_due(state):
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    if state.get("last_verify_hour") == hour:
        return
    proc = subprocess.run([sys.executable, str(VERIFY), "verify"], capture_output=True, text=True)
    state["last_verify_hour"] = hour
    save_state(state)
    out = (proc.stdout or proc.stderr or "").strip()
    log(f"VERIFY rc={proc.returncode} {out}")


class HealthHandler(BaseHTTPRequestHandler):
    daemon_state = {}

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        state = type(self).daemon_state or {}
        body = json.dumps({
            "ok": True,
            "last_assistant_id": state.get("last_assistant_id", 0),
            "imports": state.get("imports", 0),
            "last_verify_hour": state.get("last_verify_hour"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def start_http(state):
    HealthHandler.daemon_state = state
    server = HTTPServer(("127.0.0.1", PORT), HealthHandler)
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    return server


def run_once(state):
    pairs = fetch_new_pairs(state["last_assistant_id"])
    for pair in pairs:
        import_pair(pair, state)
    verify_if_due(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run one poll/import cycle, then exit")
    ap.add_argument("--no-http", action="store_true", help="Do not start health HTTP server")
    args = ap.parse_args()

    lock_file = acquire_lock()
    if lock_file is None:
        return 0

    ensure_ready()
    state = load_state()
    log(f"START poll_interval={POLL_INTERVAL}s port={PORT} last_assistant_id={state['last_assistant_id']}")
    server = None if args.no_http else start_http(state)
    try:
        if args.once:
            run_once(state)
            return 0
        while True:
            try:
                run_once(state)
                time.sleep(POLL_INTERVAL)
            except Exception as e:
                log(f"LOOP_ERR {type(e).__name__}: {e}")
                time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log("STOP keyboard_interrupt")
    finally:
        if server is not None:
            server.shutdown()


if __name__ == "__main__":
    sys.exit(main())

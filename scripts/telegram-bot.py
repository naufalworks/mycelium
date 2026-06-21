#!/usr/bin/env python3
"""Telegram bot that bridges to Claude Code in mycelium context."""

import json, os, subprocess, sys, time, urllib.request, urllib.parse, re
from pathlib import Path

BOT_TOKEN = "8848199927:AAGnltJd_eVxkMCHC3LyIerMsWSAo7mXZv4"
ALLOWED_USER = 6700307279
MYCELIUM_ROOT = str(Path(__file__).resolve().parent.parent)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
POLL_INTERVAL = 2
CLAUDE_TIMEOUT = 300  # 5 minutes
MAX_CHUNK = 3800       # keep under 4096 so parse_mode overhead fits
last_offset = 0


def tg(method, data=None):
    url = f"{API}/{method}"
    if data:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"ok": False, "error": str(e)}


def escape_markdown(text):
    """Escape special chars for MarkdownV2, but preserve code blocks."""
    placeholders = {}

    def _save(m):
        idx = len(placeholders)
        placeholders[idx] = m.group(0)
        return f"\x00MC{idx}\x00"

    # Fence + inline code — do not escape inside these
    text = re.sub(r'```[\s\S]*?```', _save, text)
    text = re.sub(r'(?<!`)`[^`\n]+`(?!`)', _save, text)

    # Escape MarkdownV2 special chars: _ * [ ] ( ) ~ ` > # + - = | { } . !
    text = re.sub(r'([_*\[\]()~`>#+\-=\|{}!])', r'\\\1', text)

    for idx, code in placeholders.items():
        text = text.replace(f"\x00MC{idx}\x00", code)

    return text


def split_message(text):
    """Split long messages at paragraph boundaries so formatting isn't broken."""
    if len(text) <= MAX_CHUNK:
        return [text]

    chunks = []
    for para in text.split("\n\n"):
        if not chunks or len(chunks[-1]) + len(para) + 2 > MAX_CHUNK:
            chunks.append(para)
        else:
            chunks[-1] += "\n\n" + para
    # If one paragraph alone exceeds limit, hard-split it
    return [c[:MAX_CHUNK] for c in chunks] if len(chunks) == 1 and len(chunks[0]) > MAX_CHUNK else chunks


def send(chat_id, text, parse_mode="MarkdownV2"):
    escaped = escape_markdown(text)
    for chunk in split_message(escaped):
        data = {"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode}
        result = tg("sendMessage", data)
        if not result.get("ok"):
            # Fall back to plain text if parse_mode fails
            tg("sendMessage", {"chat_id": chat_id, "text": chunk})


def send_plain(chat_id, text):
    """Send without any parse_mode (e.g. status messages with underscores)."""
    for chunk in split_message(text):
        tg("sendMessage", {"chat_id": chat_id, "text": chunk})


def call_claude(prompt):
    """Run Claude Code with the prompt in mycelium directory. Returns output."""
    try:
        result = subprocess.run(
            ["claude", "--dangerously-skip-permissions", "--print", prompt],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
            cwd=MYCELIUM_ROOT,
            env={**os.environ, "CLAUDE_HOME": str(Path.home() / ".claude")},
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        return output[:8000]
    except subprocess.TimeoutExpired:
        return "Error: Request timed out after 300s"
    except Exception as e:
        return f"Error: {e}"


def main():
    global last_offset
    print("🍄 Mycelium Telegram Bot running...")
    send_plain(ALLOWED_USER, "🍄 Mycelium Telegram Bot connected. Send me a message.")

    while True:
        try:
            updates = tg("getUpdates", {
                "offset": last_offset + 1,
                "timeout": 30,
            })
            for u in updates.get("result", []):
                last_offset = u["update_id"]
                msg = u.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                user_id = msg.get("from", {}).get("id")
                text = msg.get("text", "")

                if not text or user_id != ALLOWED_USER:
                    continue

                # Acknowledge receipt (plain — no markdown parsing needed)
                send_plain(chat_id, "...thinking")

                # Call Claude
                response = call_claude(text)
                send(chat_id, response)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Poll error: {e}")
            time.sleep(5)

    send_plain(ALLOWED_USER, "🍄 Bot shutting down.")


if __name__ == "__main__":
    main()

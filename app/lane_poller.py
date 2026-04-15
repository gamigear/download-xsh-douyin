#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import ensure_dir, env_int, extract_all_urls, log_append, telegram_api

LANE = os.environ.get("BOT_LANE", "").strip() or (sys.argv[1] if len(sys.argv) > 1 else "")
if LANE not in {"douyin", "xiaohongshu"}:
    raise SystemExit("usage: lane_poller.py <douyin|xiaohongshu>")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("missing TELEGRAM_BOT_TOKEN")

BOT_TARGET = str(os.environ.get("BOT_TARGET", "865660575")).strip()
TIMEOUT = env_int("TELEGRAM_POLL_TIMEOUT", 45)
SLEEP_ON_ERROR = float(os.environ.get("TELEGRAM_POLL_ERROR_SLEEP", "3"))
RUNTIME = ensure_dir(Path(f"/data/{LANE}-runtime"))
OFFSET_FILE = RUNTIME / "offset.txt"
LOG_FILE = RUNTIME / "poller.log"
LOCK_FILE = RUNTIME / "poller.lock"
HINTS = ("douyin.com", "v.douyin.com") if LANE == "douyin" else ("xiaohongshu.com", "xhslink.com")
RUNNER = "/app/app/douyin_bot.py" if LANE == "douyin" else "/app/app/xhs_bot.py"


def read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0


def write_offset(value: int):
    OFFSET_FILE.write_text(str(int(value)), encoding="utf-8")


def extract_text(update: dict) -> tuple[str, str | None, str | None]:
    msg = update.get("message") or update.get("edited_message") or {}
    chat_id = str(((msg.get("chat") or {}).get("id")) or "")
    message_id = str(msg.get("message_id")) if msg.get("message_id") is not None else None
    text = msg.get("text") or msg.get("caption") or ""
    return text, chat_id, message_id


def process_text(text: str, message_id: str | None):
    proc = subprocess.run(
        ["python", RUNNER, text, BOT_TOKEN, BOT_TARGET, message_id or ""],
        text=True,
        capture_output=True,
    )
    log_append(LOG_FILE, f"processed lane={LANE} code={proc.returncode} text={json.dumps(text)[:240]}")
    if proc.stdout.strip():
        log_append(LOG_FILE, f"stdout={proc.stdout.strip()[:1000]}")
    if proc.stderr.strip():
        log_append(LOG_FILE, f"stderr={proc.stderr.strip()[:1000]}")


def main():
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        raise SystemExit(f"poller already running for lane={LANE}")

    offset = read_offset()
    log_append(LOG_FILE, f"start lane={LANE} target={BOT_TARGET} offset={offset}")
    while True:
        try:
            updates = telegram_api(
                BOT_TOKEN,
                "getUpdates",
                {"offset": offset, "timeout": TIMEOUT, "allowed_updates": ["message", "edited_message"]},
                timeout=TIMEOUT + 15,
            )
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                text, chat_id, message_id = extract_text(update)
                if not text or chat_id != BOT_TARGET:
                    continue
                if not extract_all_urls(text, HINTS):
                    continue
                process_text(text, message_id)
            write_offset(offset)
        except KeyboardInterrupt:
            log_append(LOG_FILE, f"stop lane={LANE}")
            break
        except Exception as exc:
            log_append(LOG_FILE, f"error lane={LANE} err={exc}")
            time.sleep(SLEEP_ON_ERROR)


if __name__ == "__main__":
    main()

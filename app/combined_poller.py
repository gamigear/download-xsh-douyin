#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import ensure_dir, env_int, extract_all_urls, log_append, telegram_api
from telegram_bot_api import TelegramBotApi

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise SystemExit("missing TELEGRAM_BOT_TOKEN")

BOT_TARGET = str(os.environ.get("BOT_TARGET", "865660575")).strip()
TIMEOUT = env_int("TELEGRAM_POLL_TIMEOUT", 45)
SLEEP_ON_ERROR = float(os.environ.get("TELEGRAM_POLL_ERROR_SLEEP", "3"))
RUNTIME = ensure_dir(Path(os.environ.get("BOT_RUNTIME_DIR", "/data/shared-runtime")).expanduser())
OFFSET_FILE = RUNTIME / "offset.txt"
LOG_FILE = RUNTIME / "poller.log"
LOCK_FILE = RUNTIME / "poller.lock"
LANES = {
    "douyin": {
        "hints": ("douyin.com", "v.douyin.com"),
        "runner": "/app/app/douyin_bot.py",
    },
    "xiaohongshu": {
        "hints": ("xiaohongshu.com", "xhslink.com"),
        "runner": "/app/app/xhs_bot.py",
    },
}


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


def detect_lane(text: str) -> str | None:
    lowered = (text or "").lower()
    for lane, config in LANES.items():
        if any(hint in lowered for hint in config["hints"]):
            return lane
    return None


def process_text(lane: str, text: str, message_id: str | None):
    runner = LANES[lane]["runner"]
    proc = subprocess.run(
        ["python", runner, text, BOT_TOKEN, BOT_TARGET, message_id or ""],
        text=True,
        capture_output=True,
    )
    log_append(LOG_FILE, f"processed lane={lane} code={proc.returncode} text={json.dumps(text)[:240]}")
    if proc.stdout.strip():
        log_append(LOG_FILE, f"stdout={proc.stdout.strip()[:1000]}")
    if proc.stderr.strip():
        log_append(LOG_FILE, f"stderr={proc.stderr.strip()[:1000]}")
    if proc.returncode != 0:
        error_hint = "Tải thất bại."
        combined = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
        if "douyin_browser_no_media_found" in combined:
            error_hint = "Lane Douyin browser chưa bắt được media URL hoặc DOM media từ link này."
        elif "douyin_browser_video_download_failed" in combined:
            error_hint = "Lane Douyin browser đã thấy candidate video nhưng tải trực tiếp chưa thành công."
        elif "douyin_cookie_or_risk_control" in combined:
            error_hint = "Lane Douyin cũ kiểu API đang bị cookie/risk-control, nhưng bundle này đã chuyển sang browser lane."
        elif "detail_fetch_failed" in combined:
            error_hint = "Lane Douyin chưa lấy được metadata từ backend."
        try:
            TelegramBotApi(BOT_TOKEN, BOT_TARGET).send_text(error_hint, reply_to_message_id=message_id)
        except Exception as send_exc:
            log_append(LOG_FILE, f"notify_error_failed={send_exc}")


def main():
    lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        raise SystemExit("poller already running for shared bot")

    offset = read_offset()
    log_append(LOG_FILE, f"start target={BOT_TARGET} offset={offset}")
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
                lane = detect_lane(text)
                if not lane:
                    continue
                if not extract_all_urls(text, LANES[lane]["hints"]):
                    continue
                process_text(lane, text, message_id)
            write_offset(offset)
        except KeyboardInterrupt:
            log_append(LOG_FILE, "stop shared bot")
            break
        except Exception as exc:
            log_append(LOG_FILE, f"error err={exc}")
            time.sleep(SLEEP_ON_ERROR)


if __name__ == "__main__":
    main()

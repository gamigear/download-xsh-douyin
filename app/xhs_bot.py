#!/usr/bin/env python3
import json
import subprocess
import sys

from telegram_bot_api import TelegramBotApi

BOT_TARGET = "865660575"
MAX_BYTES = 49 * 1024 * 1024


def run_fetch(url: str) -> dict:
    proc = subprocess.run(["python", "/app/app/xhs_fetch.py", url], text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def handle_message(text: str, token: str, target: str, reply_to_message_id: str | None = None):
    tg = TelegramBotApi(token, target or BOT_TARGET, max_bytes=MAX_BYTES)
    data = run_fetch(text)
    caption = f"Link gốc: {data.get('original_link') or text}"
    if data.get("content_type") == "image":
        tg.send_media_group(data["files"], caption=caption, reply_to_message_id=reply_to_message_id)
    else:
        tg.send_document(data["files"][0], caption=caption, reply_to_message_id=reply_to_message_id)
    return data


def main() -> int:
    if len(sys.argv) < 4:
        return 2
    text, token, target = sys.argv[1], sys.argv[2], sys.argv[3]
    reply_to = sys.argv[4] if len(sys.argv) > 4 else None
    result = handle_message(text, token, target, reply_to)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

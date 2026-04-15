#!/usr/bin/env python3
import json
import mimetypes
import subprocess
from pathlib import Path


class TelegramBotApi:
    def __init__(self, token: str, chat_id: str, max_bytes: int = 49 * 1024 * 1024):
        if not token:
            raise RuntimeError("missing_TELEGRAM_BOT_TOKEN")
        if not str(chat_id or "").strip():
            raise RuntimeError("missing_TELEGRAM_CHAT_ID")
        self.token = token
        self.chat_id = str(chat_id)
        self.max_bytes = int(max_bytes)

    def _capture(self, cmd: list[str]) -> str:
        return subprocess.check_output(cmd, text=True).strip()

    def _call(self, method: str, form_parts: list[str]):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        cmd = ["curl", "-fsS", url, "-F", f"chat_id={self.chat_id}", *form_parts]
        out = self._capture(cmd)
        parsed = json.loads(out)
        if not parsed.get("ok"):
            raise RuntimeError(f"{method} failed: {out}")
        return parsed

    def send_text(self, text: str, reply_to_message_id: str | None = None):
        parts = ["-F", f"text={text}"]
        if reply_to_message_id:
            parts += ["-F", f"reply_to_message_id={reply_to_message_id}"]
        return self._call("sendMessage", parts)

    def send_document(self, path: str, caption: str | None = None, reply_to_message_id: str | None = None):
        file_path = Path(path)
        size = file_path.stat().st_size
        if size > self.max_bytes:
            note = [
                "File quá lớn nên bot không gửi trực tiếp được.",
                f"Kích thước: {round(size / (1024 * 1024), 1)} MB",
            ]
            if caption:
                note.append(caption)
            note.append(f"File local: {file_path}")
            return self.send_text("\n".join(note), reply_to_message_id=reply_to_message_id)
        parts = ["-F", f"document=@{file_path}"]
        if caption:
            parts += ["-F", f"caption={caption}"]
        if reply_to_message_id:
            parts += ["-F", f"reply_to_message_id={reply_to_message_id}"]
        return self._call("sendDocument", parts)

    def send_photo(self, path: str, caption: str | None = None, reply_to_message_id: str | None = None):
        file_path = Path(path)
        parts = ["-F", f"photo=@{file_path}"]
        if caption:
            parts += ["-F", f"caption={caption}"]
        if reply_to_message_id:
            parts += ["-F", f"reply_to_message_id={reply_to_message_id}"]
        return self._call("sendPhoto", parts)

    def send_media_group(self, paths: list[str], caption: str | None = None, reply_to_message_id: str | None = None):
        media = []
        parts = []
        for idx, path in enumerate(paths):
            file_path = Path(path)
            attach_name = f"file{idx}"
            mime, _ = mimetypes.guess_type(str(file_path))
            media_type = "photo" if (mime or "").startswith("image/") else "document"
            item = {"type": media_type, "media": f"attach://{attach_name}"}
            if idx == 0 and caption:
                item["caption"] = caption
            media.append(item)
            parts += ["-F", f"{attach_name}=@{file_path}"]
        parts += ["-F", f"media={json.dumps(media, ensure_ascii=False)}"]
        if reply_to_message_id:
            parts += ["-F", f"reply_to_message_id={reply_to_message_id}"]
        return self._call("sendMediaGroup", parts)

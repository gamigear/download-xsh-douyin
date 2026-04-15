#!/usr/bin/env python3
import json
import os
import re
import urllib.request
from pathlib import Path

URL_RE = re.compile(r"https?://\S+")


def normalize_url(url: str) -> str:
    return url.rstrip(").,>'\"\n")


def extract_all_urls(text: str, hints: tuple[str, ...]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text or ""):
        url = normalize_url(match.group(0))
        lowered = url.lower()
        if not any(hint in lowered for hint in hints):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_append(log_file: Path, line: str):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def telegram_api(token: str, method: str, payload: dict | None = None, timeout: int = 60):
    url = f"https://api.telegram.org/bot{token}/{method}"
    headers = {}
    data = None
    if payload is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8", "ignore"))
    if not body.get("ok"):
        raise RuntimeError(json.dumps(body, ensure_ascii=False))
    return body["result"]


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))

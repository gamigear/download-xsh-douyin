#!/usr/bin/env python3
import json
import mimetypes
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir, write_caption_artifacts

API_URL = "http://xhs-runtime:5556/xhs/detail"
OUT_ROOT = Path("/data/xhs-fetch-jobs")
USER_AGENT = "Mozilla/5.0 (compatible; XHSStandaloneBot/1.0)"


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def api_detail(raw_url: str) -> dict:
    payload = {"url": raw_url, "download": False, "index": [], "skip": False}
    # XHS yêu cầu cookie đăng nhập để lấy dữ liệu note. Lấy từ env (đặt trong gamigear.env).
    cookie = os.environ.get("XHS_COOKIE", "").strip()
    if cookie:
        payload["cookie"] = cookie
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json", "user-agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def download_file(url: str, dest: Path):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response:
        dest.write_bytes(response.read())


def guess_ext(url: str, default_ext: str) -> str:
    mime, _ = mimetypes.guess_type(url)
    if mime:
        if mime == "video/mp4":
            return ".mp4"
        if mime.startswith("image/"):
            return f".{mime.split('/', 1)[1].replace('jpeg', 'jpg')}"
    return default_ext


def fetch(raw_url: str) -> dict:
    out_root = ensure_dir(OUT_ROOT)
    job_id = f"xiaohongshu-{now_ts()}"
    job_dir = ensure_dir(out_root / job_id)

    detail_resp = api_detail(raw_url)
    data = detail_resp.get("data") or {}
    urls = data.get("下载地址") or []
    if not urls:
        raise RuntimeError(f"xhs_detail_failed: {json.dumps(detail_resp, ensure_ascii=False)}")

    title = str(data.get("作品标题") or "").strip()
    author_name = str(data.get("作者昵称") or "").strip()
    media_type = "video" if data.get("作品类型") == "视频" else "image"

    files: list[Path] = []
    for idx, url in enumerate(urls, start=1):
        ext = guess_ext(url, ".mp4" if media_type == "video" else ".jpg")
        dest = job_dir / (f"video_{idx:02d}{ext}" if media_type == "video" else f"image_{idx:02d}{ext}")
        download_file(url, dest)
        files.append(dest)

    result = {
        "ok": True,
        "job_id": job_id,
        "platform": "xiaohongshu",
        "content_type": media_type,
        "original_link": raw_url,
        "media_count": len(files),
        "files": [str(path) for path in files],
        "job_dir": str(job_dir),
        "normalized": {
            "source_platform": "xiaohongshu",
            "source_url": raw_url,
            "source_post_id": data.get("作品ID"),
            "source_author_name": author_name,
            "source_author_handle": data.get("作者ID"),
            "caption_raw": data.get("作品描述"),
            "title": title,
            "media_type": media_type,
        },
    }
    write_caption_artifacts(job_dir, result, files)
    (job_dir / "detail_response.json").write_text(json.dumps(detail_resp, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "missing_url_arg"}, ensure_ascii=False))
        return 2
    try:
        print(json.dumps(fetch(sys.argv[1].strip()), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

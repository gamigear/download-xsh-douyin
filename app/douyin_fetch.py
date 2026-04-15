#!/usr/bin/env python3
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir

OUT_ROOT = Path("/data/douyin-fetch-jobs")
PROBE = Path("/app/app/douyin_browser_probe.js")
PROBE_OUT = Path("/tmp/douyin-browser-out.json")
CHROMIUM = Path("/usr/bin/chromium")
USER_AGENT = "Mozilla/5.0 (compatible; DouyinStandaloneBot/1.0)"


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def normalize_url(url: str) -> str:
    return url.rstrip(").,>'\"\n")


def extract_first_url(text: str) -> str | None:
    match = re.search(r"https?://\S+", text or "")
    if not match:
        return None
    return normalize_url(match.group(0))


def run_probe(url: str) -> dict:
    subprocess.run(["node", str(PROBE), url], text=True, capture_output=True, check=True)
    return json.loads(PROBE_OUT.read_text(encoding="utf-8"))


def run_chromium_capture(url: str) -> dict:
    if not CHROMIUM.exists():
        raise RuntimeError(f"missing_chromium_binary: {CHROMIUM}")

    with tempfile.TemporaryDirectory(prefix="douyin-chromium-") as tmpdir:
        dom_path = Path(tmpdir) / "dom.html"
        netlog_path = Path(tmpdir) / "netlog.json"
        stderr_path = Path(tmpdir) / "stderr.txt"
        with dom_path.open("w", encoding="utf-8") as dom_fp, stderr_path.open("w", encoding="utf-8") as err_fp:
            subprocess.run(
                [
                    str(CHROMIUM),
                    "--headless",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--virtual-time-budget=15000",
                    f"--log-net-log={netlog_path}",
                    "--net-log-capture-mode=Everything",
                    "--dump-dom",
                    url,
                ],
                text=True,
                stdout=dom_fp,
                stderr=err_fp,
                check=False,
                timeout=120,
            )
        return {
            "html": dom_path.read_text(encoding="utf-8", errors="ignore") if dom_path.exists() else "",
            "netlog": netlog_path.read_text(encoding="utf-8", errors="ignore") if netlog_path.exists() else "",
            "stderr": stderr_path.read_text(encoding="utf-8", errors="ignore") if stderr_path.exists() else "",
        }


def download_file(url: str, dest: Path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.douyin.com/"})
    with urllib.request.urlopen(req, timeout=60) as response:
        with dest.open("wb") as fp:
            shutil.copyfileobj(response, fp, 1024 * 256)


def parse_dom(html_text: str) -> dict:
    out = {
        "title": None,
        "description": None,
        "author_name": None,
        "user_url": None,
    }
    match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', html_text, re.I | re.S)
    if match:
        out["description"] = html.unescape(match.group(1))
    match = re.search(r"<title>(.*?)</title>", html_text, re.I | re.S)
    if match:
        out["title"] = html.unescape(match.group(1))
    match = re.search(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.I | re.S)
    if match:
        try:
            obj = json.loads(match.group(1))
            items = obj.get("itemListElement") or []
            if len(items) >= 2:
                out["author_name"] = items[1].get("name")
                out["user_url"] = items[1].get("item")
        except Exception:
            pass
    return out


def collect_video_urls(seen: list[dict]) -> list[str]:
    urls: list[str] = []
    seen_urls: set[str] = set()
    for row in seen or []:
        url = str(row.get("url") or "")
        if not url:
            continue
        if ("zjcdn.com" not in url and "douyinvod.com" not in url and "/video/tos/" not in url):
            continue
        if ("mime_type=video_mp4" not in url and ".mp4" not in url and "/video/tos/" not in url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        urls.append(url)
    return urls


def collect_video_urls_from_netlog(netlog_text: str) -> list[str]:
    urls: list[str] = []
    seen_urls: set[str] = set()
    for url in re.findall(r'https?://[^"\\\s]+', netlog_text or ""):
        if ("zjcdn.com" not in url and "douyinvod.com" not in url and "/video/tos/" not in url):
            continue
        if ("mime_type=video_mp4" not in url and ".mp4" not in url and "/video/tos/" not in url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        urls.append(url)
    return urls


def collect_image_urls(dom_images: list[dict]) -> list[str]:
    urls: list[str] = []
    seen_urls: set[str] = set()
    for row in dom_images or []:
        src = html.unescape(str(row.get("src") or row.get("dataSrc") or ""))
        width = int(row.get("w") or 0)
        height = int(row.get("h") or 0)
        if "douyinpic.com" not in src:
            continue
        if "PackSourceEnum_AWEME_DETAIL" not in src:
            continue
        if width < 1000 and height < 1000:
            continue
        base = src.split("&")[0]
        if base in seen_urls:
            continue
        seen_urls.add(base)
        urls.append(src)
    return urls


def score_video_url(url: str) -> tuple[int, int, int]:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    def read_int(name: str) -> int:
        try:
            return int((query.get(name) or ["0"])[0])
        except Exception:
            return 0

    has_mp4 = 1 if "mime_type=video_mp4" in url or ".mp4" in url else 0
    return (has_mp4, read_int("bt"), read_int("br"))


def inspect_media_file(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,bit_rate",
            "-show_entries",
            "format=duration,size,bit_rate",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout or "{}")
    stream = ((data.get("streams") or [{}])[0]) if isinstance(data, dict) else {}
    fmt = data.get("format") or {}
    return {
        "path": str(path),
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "stream_bit_rate": stream.get("bit_rate"),
        "format_bit_rate": fmt.get("bit_rate"),
        "duration": fmt.get("duration"),
        "size": fmt.get("size"),
    }


def try_ranked_video_candidates(video_urls: list[str], candidates_dir: Path) -> tuple[list[dict], dict | None]:
    candidate_results = []
    ranked = sorted(video_urls, key=score_video_url, reverse=True)
    for idx, url in enumerate(ranked, start=1):
        path = candidates_dir / f"video_{idx:02d}.mp4"
        row = {"url": url, "path": str(path), "ok": False, "size": 0}
        try:
            download_file(url, path)
            row["size"] = path.stat().st_size
            row["ok"] = True
            row["media_info"] = inspect_media_file(path)
        except Exception as exc:
            row["error"] = str(exc)
        candidate_results.append(row)
        # The old browser lane only needed one direct media URL to succeed.
        if row["ok"] and row["size"] > 0:
            return candidate_results, row
    return candidate_results, None


def fetch(raw_input: str) -> dict:
    source_url = extract_first_url(raw_input) or raw_input.strip()
    out_root = ensure_dir(OUT_ROOT)
    job_id = f"douyin-browser-{now_ts()}"
    job_dir = ensure_dir(out_root / job_id)
    candidates_dir = ensure_dir(job_dir / "candidates")

    chromium_capture = run_chromium_capture(source_url)
    probe = run_probe(source_url)
    final_url = probe.get("finalUrl") or source_url
    parsed = parse_dom(chromium_capture.get("html") or probe.get("html") or "")
    detail_id_match = re.search(r"/(?:video|note)/(\d+)", final_url)
    detail_id = detail_id_match.group(1) if detail_id_match else None

    # Prefer Chromium netlog, which matches the old browser lane more reliably than
    # Playwright response listeners on Douyin's current web app.
    video_urls = collect_video_urls_from_netlog(chromium_capture.get("netlog") or "")
    if not video_urls:
        video_urls = collect_video_urls(probe.get("mediaResponses") or [])
    image_urls = collect_image_urls(probe.get("domImages") or [])

    files: list[Path] = []
    content_type = "video"
    downloaded_media: str | None = None
    (job_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "source_url": source_url,
                "final_url": final_url,
                "video_url_count": len(video_urls),
                "image_url_count": len(image_urls),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if video_urls:
        candidate_results, best = try_ranked_video_candidates(video_urls, candidates_dir)
        if not best:
            raise RuntimeError(f"douyin_browser_video_download_failed: {json.dumps(candidate_results, ensure_ascii=False)}")
        downloaded_media = best["path"]
        files = [Path(downloaded_media)]
    elif image_urls:
        content_type = "image"
        for idx, url in enumerate(image_urls, start=1):
            suffix = ".jpg" if (".jpeg" in url or ".jpg" in url) else ".webp"
            path = candidates_dir / f"image_{idx:02d}{suffix}"
            download_file(url, path)
            files.append(path)
    else:
        raise RuntimeError("douyin_browser_no_media_found")

    result = {
        "ok": True,
        "job_id": job_id,
        "platform": "douyin",
        "content_type": content_type,
        "original_link": final_url,
        "files": [str(path) for path in files],
        "media_count": len(files),
        "job_dir": str(job_dir),
        "normalized": {
            "source_platform": "douyin",
            "source_url": source_url,
            "resolved_url": final_url,
            "source_post_id": detail_id,
            "source_author_name": parsed.get("author_name"),
            "source_author_handle": None,
            "caption_raw": parsed.get("description") or parsed.get("title"),
            "title": parsed.get("title"),
            "media_type": content_type,
            "method": "chromium_netlog_plus_playwright_dom",
            "user_url": parsed.get("user_url"),
            "video_url_count": len(video_urls),
            "image_url_count": len(image_urls),
            "chromium_netlog_media_url_count": len(collect_video_urls_from_netlog(chromium_capture.get("netlog") or "")),
        },
    }
    if content_type == "video" and downloaded_media:
        result["best_media"] = inspect_media_file(Path(downloaded_media))
        result["downloaded_media"] = downloaded_media

    (job_dir / "chromium_dom.html").write_text(chromium_capture.get("html") or "", encoding="utf-8")
    (job_dir / "chromium_netlog.json").write_text(chromium_capture.get("netlog") or "", encoding="utf-8")
    (job_dir / "chromium_stderr.txt").write_text(chromium_capture.get("stderr") or "", encoding="utf-8")
    (job_dir / "probe.json").write_text(json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8")
    (job_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "missing_url_arg"}, ensure_ascii=False))
        return 2
    try:
        print(json.dumps(fetch(sys.argv[1]), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
video_concat.py — nối nhiều video (XHS/Douyin) thành MỘT file video dài.

Các video nguồn thường khác độ phân giải/codec/fps -> concat demuxer trực tiếp dễ
lỗi. Cách làm: chuẩn hoá từng input (scale+pad về khung chuẩn, 30fps, h264+aac, đảm
bảo có audio) -> nối bằng concat demuxer.

Dùng:
  python video_concat.py <output.mp4> <in1.mp4> <in2.mp4> [...]

Env (tuỳ chọn):
  CONCAT_TARGET_W (mặc định 1080), CONCAT_TARGET_H (mặc định 1920)

Output JSON dòng cuối (Gami parse):
  { "ok": true, "output": "/path/out.mp4", "count": N, "duration": 12.3 }
  Lỗi: { "ok": false, "error": "..." }
"""
import json
import os
import subprocess
import sys
import tempfile


def fail(msg):
    print(json.dumps({"ok": False, "error": str(msg)}, ensure_ascii=False))
    sys.exit(0)


def log(msg):
    print(f"[concat] {msg}", file=sys.stderr, flush=True)


def ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def has_audio(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60,
        )
        return bool((out.stdout or "").strip())
    except Exception:
        return False


def normalize(src, dst, w, h):
    # scale giữ tỉ lệ + pad về đúng khung w×h, 30fps, h264+aac. Thiếu audio -> chèn silent.
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p"
    )
    common_v = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-video_track_timescale", "30000"]
    common_a = ["-c:a", "aac", "-ar", "44100", "-ac", "2"]

    if has_audio(src):
        cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf,
               "-map", "0:v:0", "-map", "0:a:0",
               *common_v, *common_a, dst]
    else:
        # Không có audio -> ghép luồng silent dài bằng video.
        cmd = ["ffmpeg", "-y", "-i", src,
               "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
               "-vf", vf,
               "-map", "0:v:0", "-map", "1:a:0", "-shortest",
               *common_v, *common_a, dst]

    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        fail("usage: video_concat.py <output> <input1> <input2> [...]")

    out_path = args[0]
    inputs = args[1:]

    w = int(os.environ.get("CONCAT_TARGET_W", "1080"))
    h = int(os.environ.get("CONCAT_TARGET_H", "1920"))

    for p in inputs:
        if not os.path.exists(p):
            fail(f"input_not_found: {p}")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    tmpdir = tempfile.mkdtemp(prefix="concat_")
    normalized = []
    try:
        for idx, src in enumerate(inputs):
            dst = os.path.join(tmpdir, f"norm_{idx:03d}.mp4")
            log(f"Chuẩn hoá {idx + 1}/{len(inputs)}: {os.path.basename(src)}")
            normalize(src, dst, w, h)
            normalized.append(dst)

        # File danh sách cho concat demuxer.
        list_path = os.path.join(tmpdir, "list.txt")
        with open(list_path, "w", encoding="utf-8") as fp:
            for p in normalized:
                safe = p.replace("'", "'\\''")
                fp.write(f"file '{safe}'\n")

        log(f"Nối {len(normalized)} video…")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
             "-c", "copy", out_path],
            check=True, capture_output=True, timeout=1800,
        )

        log("Hoàn tất.")
        print(json.dumps({
            "ok": True, "output": out_path,
            "count": len(inputs), "duration": ffprobe_duration(out_path),
        }, ensure_ascii=False))
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "ignore")[-500:] if isinstance(exc.stderr, (bytes, bytearray)) else str(exc.stderr)
        fail(f"ffmpeg_error: {tail}")
    except Exception as exc:
        fail(str(exc))
    finally:
        for p in normalized:
            try:
                os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    main()

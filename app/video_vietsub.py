#!/usr/bin/env python3
"""
video_vietsub.py — burn phụ đề tiếng Việt vào video Trung (XSH/Douyin).

Luồng: faster-whisper (ASR tiếng Trung + timestamp) -> dịch ZH->VI qua LLM endpoint
(OpenAI-compatible) -> sinh .ass -> ffmpeg burn-in -> mp4 mới.

Dùng:
  python video_vietsub.py <input.mp4> [--out <output.mp4>] [--model medium]

Env (truyền từ Gami qua docker exec -e):
  VIETSUB_TRANSLATE_BASE_URL, VIETSUB_TRANSLATE_API_KEY, VIETSUB_TRANSLATE_MODEL
  VIETSUB_WHISPER_MODEL (mặc định medium)

Output JSON dòng cuối (Gami parse):
  { "ok": true, "output": "/path/out.mp4", "srt": "/path/out.srt", "segments": N, "duration": 12.3 }
  Lỗi: { "ok": false, "error": "..." }
"""
import json
import os
import subprocess
import sys
import urllib.request


def fail(msg):
    print(json.dumps({"ok": False, "error": str(msg)}, ensure_ascii=False))
    sys.exit(0)


def log(msg):
    # stderr = log tiến trình (Gami stream), stdout dòng cuối = JSON kết quả.
    print(f"[vietsub] {msg}", file=sys.stderr, flush=True)


def ass_time(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\n", "\\N").replace("{", "(").replace("}", ")").strip()


def transcribe(input_path: str, model_name: str):
    from faster_whisper import WhisperModel

    log(f"Load whisper model={model_name} (CPU int8)…")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    def run(vad: bool):
        segments, info = model.transcribe(input_path, language="zh", vad_filter=vad, beam_size=1)
        rows = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                rows.append({"start": seg.start, "end": seg.end, "zh": text})
                log(f"  [{seg.start:.1f}s] {text[:40]}")
        return rows, float(getattr(info, "duration", 0) or 0)

    log("Đang nhận dạng giọng nói (tiếng Trung)… [VAD on]")
    out, duration = run(True)
    if not out:
        # VAD lọc quá gắt với nhiều video Douyin -> thử lại không VAD.
        log("Không thấy thoại với VAD — thử lại không VAD…")
        out, duration = run(False)
    return out, duration


def translate_batch(texts, base_url, api_key, model, context_hint=""):
    if not texts:
        return []
    if not api_key:
        # Không có key dịch -> giữ nguyên tiếng Trung (vẫn burn để có phụ đề).
        log("CẢNH BÁO: thiếu API key dịch — giữ nguyên text gốc.")
        return texts

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    hint = context_hint.strip() if context_hint else ""
    hint_block = (
        f"\n\nBỐI CẢNH do người dùng cung cấp (ưu tiên dùng để chọn xưng hô cho đúng):\n{hint}"
        if hint
        else ""
    )
    system = (
        "Bạn là dịch giả phụ đề phim Trung–Việt chuyên nghiệp. "
        "Dịch sang tiếng Việt tự nhiên, đúng văn nói, mượt như phụ đề phim chiếu rạp."
    )
    prompt = (
        "Dưới đây là TOÀN BỘ lời thoại của một video, theo thứ tự thời gian. "
        "Hãy đọc hết trước để hiểu ngữ cảnh, rồi mới dịch sang tiếng Việt.\n\n"
        "YÊU CẦU QUAN TRỌNG:\n"
        "- Tự suy luận quan hệ, GIỚI TÍNH và VAI VẾ (tuổi tác, cấp bậc, thân–sơ) của các nhân vật "
        "từ toàn bộ hội thoại, rồi chọn ĐẠI TỪ XƯNG HÔ tiếng Việt phù hợp và NHẤT QUÁN xuyên suốt "
        "(anh/em/chị/ông/bà/cô/chú/cháu/con/tôi/ta/mày/tao/ngài…). "
        "TUYỆT ĐỐI không dịch máy móc kiểu 你→bạn, 我→tôi cho mọi trường hợp.\n"
        "- Giữ đúng giọng điệu và sắc thái cảm xúc (trang trọng / suồng sã / mỉa mai / giận dữ…).\n"
        "- Dịch thoát ý, tự nhiên như người Việt nói, KHÔNG bám từng chữ; "
        "nhưng phải giữ ĐÚNG số dòng và đúng thứ tự.\n"
        "- CHỈ trả về mỗi dòng dạng '<số>. <bản dịch>', không thêm bất kỳ giải thích nào."
        f"{hint_block}\n\nLỜI THOẠI:\n{numbered}"
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    log(f"Dịch {len(texts)} câu sang tiếng Việt…")
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8", "ignore")

    # Provider (9router kr/*) có thể trả SSE 'data: {...}' dù stream:false -> ghép delta.
    if raw.lstrip().startswith("data:"):
        content = ""
        for line in raw.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
                ch = (chunk.get("choices") or [{}])[0]
                content += (ch.get("delta", {}).get("content") or ch.get("message", {}).get("content") or "")
            except Exception:
                pass
    else:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]

    # Parse "<n>. text" về đúng index; fallback theo dòng.
    result = list(texts)
    for line in content.splitlines():
        line = line.strip()
        m = line.split(".", 1)
        if len(m) == 2 and m[0].strip().isdigit():
            idx = int(m[0].strip()) - 1
            if 0 <= idx < len(result):
                result[idx] = m[1].strip()
    return result


def build_ass(segments, ass_path):
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: VI,Arial,46,&H00FFFFFF,&H00000000,&H80000000,1,0,1,3,1,2,40,40,60,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for seg in segments:
        lines.append(
            f"Dialogue: 0,{ass_time(seg['start'])},{ass_time(seg['end'])},VI,,0,0,0,,{ass_escape(seg['vi'])}"
        )
    with open(ass_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))


def burn(input_path, ass_path, out_path):
    log("Burn phụ đề vào video (ffmpeg)…")
    # Escape path cho filter subtitles.
    safe = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vf", f"subtitles='{safe}'",
         "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", out_path],
        check=True, capture_output=True, timeout=1800,
    )


def main():
    args = sys.argv[1:]
    if not args:
        fail("missing_input")
    input_path = args[0]
    out_path = None
    model_name = os.environ.get("VIETSUB_WHISPER_MODEL", "medium")
    i = 1
    while i < len(args):
        if args[i] == "--out" and i + 1 < len(args):
            out_path = args[i + 1]; i += 2; continue
        if args[i] == "--model" and i + 1 < len(args):
            model_name = args[i + 1]; i += 2; continue
        i += 1

    if not os.path.exists(input_path):
        fail(f"input_not_found: {input_path}")
    base, _ = os.path.splitext(input_path)
    if not out_path:
        out_path = f"{base}.vietsub.mp4"
    srt_path = f"{base}.vietsub.srt"
    ass_path = f"{base}.vietsub.ass"

    try:
        segments, duration = transcribe(input_path, model_name)
        if not segments:
            fail("no_speech_detected")

        vi = translate_batch(
            [s["zh"] for s in segments],
            os.environ.get("VIETSUB_TRANSLATE_BASE_URL", ""),
            os.environ.get("VIETSUB_TRANSLATE_API_KEY", ""),
            os.environ.get("VIETSUB_TRANSLATE_MODEL", "gpt-4o-mini"),
            os.environ.get("VIETSUB_CONTEXT_HINT", ""),
        )
        for s, t in zip(segments, vi):
            s["vi"] = t

        # .srt (tham khảo) + .ass (burn).
        with open(srt_path, "w", encoding="utf-8") as fp:
            for idx, s in enumerate(segments, 1):
                def srt_t(x):
                    h = int(x // 3600); m = int((x % 3600) // 60); sec = int(x % 60); ms = int((x - int(x)) * 1000)
                    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
                fp.write(f"{idx}\n{srt_t(s['start'])} --> {srt_t(s['end'])}\n{s['vi']}\n\n")

        build_ass(segments, ass_path)
        burn(input_path, ass_path, out_path)

        log("Hoàn tất.")
        print(json.dumps({
            "ok": True, "output": out_path, "srt": srt_path,
            "segments": len(segments), "duration": duration,
        }, ensure_ascii=False))
    except subprocess.CalledProcessError as e:
        fail(f"ffmpeg_failed: {e.stderr.decode('utf-8','ignore')[-400:] if e.stderr else e}")
    except Exception as e:  # noqa
        fail(str(e))


if __name__ == "__main__":
    main()

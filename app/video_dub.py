#!/usr/bin/env python3
"""
video_dub.py — lồng tiếng (voiceover) tiếng Việt cho video Trung (XSH/Douyin).

Luồng: faster-whisper (ASR tiếng Trung + timestamp) -> dịch ZH->VI (LLM) ->
edge-tts sinh giọng Việt từng câu -> atempo khít khung giờ -> dựng track voiceover
-> ffmpeg mux + ducking (hạ nhạc nền khi có giọng đọc). Tuỳ chọn burn phụ đề kèm.

Tái dụng transcribe/translate_batch/build_ass từ video_vietsub.py.

Dùng:
  python video_dub.py <input.mp4> [--out <output.mp4>] [--model medium]

Env (truyền từ Gami qua docker exec -e):
  VIETSUB_TRANSLATE_BASE_URL, VIETSUB_TRANSLATE_API_KEY, VIETSUB_TRANSLATE_MODEL
  VIETSUB_CONTEXT_HINT, VIETSUB_WHISPER_MODEL
  DUB_VOICE (mặc định vi-VN-HoaiMyNeural), DUB_BURN_SUB (1/0)

Output JSON dòng cuối: { "ok": true, "output": "...", "segments": N, "duration": 12.3 }
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from video_vietsub import transcribe, translate_batch, build_ass

DEFAULT_VOICE = "vi-VN-HoaiMyNeural"
MAX_SPEEDUP = 1.3  # chặn atempo để giữ dễ nghe


def fail(msg):
    print(json.dumps({"ok": False, "error": str(msg)}, ensure_ascii=False))
    sys.exit(0)


def log(msg):
    print(f"[dub] {msg}", file=sys.stderr, flush=True)


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


async def _tts(text, voice, out_path):
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)


# Câu có chữ để đọc không? (edge-tts trả rỗng nếu chỉ có dấu câu/ký hiệu -> bỏ qua, coi là im lặng.)
def has_speakable(text: str) -> bool:
    return bool(re.search(r"[^\W_]", text or "", flags=re.UNICODE))


def synth_segment(text, voice, out_path):
    # edge-tts (free) hay chập chờn "No audio was received" -> thử lại nhiều lần, có nghỉ giữa các lần.
    last = None
    for attempt in range(4):
        try:
            asyncio.run(_tts(text, voice, out_path))
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1.5 * (attempt + 1))  # backoff 1.5s, 3s, 4.5s
    if last:
        raise last
    raise RuntimeError("No audio was received")


def atempo(src, dst, factor):
    # factor > 1 => tăng tốc (audio ngắn lại). Chuỗi atempo (mỗi cái 0.5..2.0); ở đây factor <= 1.3.
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-filter:a", f"atempo={factor:.4f}", dst],
        check=True, capture_output=True, timeout=300,
    )


def build_voice_track(segments, voice, total_ms, tmpdir):
    """Dựng track voiceover (AudioSegment) dài total_ms, đặt từng câu đúng vị trí, đẩy trễ nếu tràn."""
    from pydub import AudioSegment

    canvas = AudioSegment.silent(duration=total_ms)
    cursor_ms = 0

    for idx, seg in enumerate(segments):
        text = (seg.get("vi") or "").strip()
        # Bỏ qua câu rỗng hoặc chỉ có dấu câu/ký hiệu (edge-tts không đọc được -> để im lặng).
        if not has_speakable(text):
            continue

        raw = os.path.join(tmpdir, f"tts_{idx:03d}.mp3")
        try:
            synth_segment(text, voice, raw)
        except Exception as exc:
            log(f"TTS lỗi câu {idx + 1}: {exc}")
            continue
        if not os.path.exists(raw) or os.path.getsize(raw) == 0:
            continue

        clip = AudioSegment.from_file(raw)
        target_ms = max(0, int((seg["end"] - seg["start"]) * 1000))

        # Quá dài so với khung -> tăng tốc (chặn 1.3x).
        if target_ms > 0 and len(clip) > target_ms:
            factor = min(len(clip) / target_ms, MAX_SPEEDUP)
            if factor > 1.01:
                sped = os.path.join(tmpdir, f"tts_{idx:03d}_fast.wav")
                try:
                    atempo(raw, sped, factor)
                    clip = AudioSegment.from_file(sped)
                except Exception:
                    pass

        start_ms = max(int(seg["start"] * 1000), cursor_ms)  # không chồng câu trước
        if start_ms >= len(canvas):
            canvas += AudioSegment.silent(duration=start_ms - len(canvas) + len(clip) + 10)
        elif start_ms + len(clip) > len(canvas):
            canvas += AudioSegment.silent(duration=start_ms + len(clip) - len(canvas) + 10)

        canvas = canvas.overlay(clip, position=start_ms)
        cursor_ms = start_ms + len(clip)

    return canvas


def mux(input_path, voice_wav, out_path, ass_path):
    """Ghép voiceover vào video. Ducking nếu có audio gốc; burn sub nếu ass_path."""
    burn = bool(ass_path)
    base_v = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"] if burn else ["-c:v", "copy"]

    sub_chain = ""
    if burn:
        safe = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        sub_chain = f"[0:v]subtitles='{safe}'[v];"
    v_map = "[v]" if burn else "0:v"

    if has_audio(input_path):
        # Ducking: nén audio gốc khi voiceover có tiếng, rồi trộn. Video chain (nếu có) đặt trước.
        audio_chain = (
            "[1:a]asplit=2[sc][vmix];"
            "[0:a][sc]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=300[bg];"
            "[bg][vmix]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
        )
        filtic = f"{sub_chain}{audio_chain}".rstrip(";")
        a_map = "[aout]"
        cmd = [
            "ffmpeg", "-y", "-i", input_path, "-i", voice_wav,
            "-filter_complex", filtic,
            "-map", v_map, "-map", a_map,
            *base_v, "-c:a", "aac", "-ar", "44100", "-ac", "2",
            "-shortest", out_path,
        ]
    else:
        # Không có audio gốc -> dùng thẳng voiceover làm audio.
        cmd = ["ffmpeg", "-y", "-i", input_path, "-i", voice_wav]
        if burn:
            safe = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            cmd += ["-filter_complex", f"[0:v]subtitles='{safe}'[v]", "-map", "[v]"]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-map", "1:a", *base_v, "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", out_path]

    subprocess.run(cmd, check=True, capture_output=True, timeout=1800)


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
        out_path = f"{base}.dub.mp4"
    ass_path = f"{base}.dub.ass"

    voice = os.environ.get("DUB_VOICE") or DEFAULT_VOICE
    burn_sub = os.environ.get("DUB_BURN_SUB", "0") == "1"

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

        tmpdir = tempfile.mkdtemp(prefix="dub_")
        last_end_ms = int(max((s["end"] for s in segments), default=0) * 1000)
        total_ms = max(int((duration or 0) * 1000), last_end_ms) + 200

        log(f"Tổng hợp giọng tiếng Việt ({voice})…")
        voice_track = build_voice_track(segments, voice, total_ms, tmpdir)
        voice_wav = os.path.join(tmpdir, "voice.wav")
        voice_track.export(voice_wav, format="wav")

        if burn_sub:
            build_ass(segments, ass_path)

        log("Ghép giọng vào video (ffmpeg)…")
        mux(input_path, voice_wav, out_path, ass_path if burn_sub else None)

        log("Hoàn tất.")
        print(json.dumps({
            "ok": True, "output": out_path,
            "segments": len(segments), "duration": duration,
        }, ensure_ascii=False))
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"")
        tail = tail.decode("utf-8", "ignore")[-500:] if isinstance(tail, (bytes, bytearray)) else str(tail)
        fail(f"ffmpeg_error: {tail}")
    except Exception as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()

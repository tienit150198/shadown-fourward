"""Bounded video decoding and transcription; reuse the existing skill compiler."""
import asyncio
import base64
import json
import math
from pathlib import Path

from skill_compiler import CompileRequest, compile_skill
from providers import compiler_model

MAX_SECONDS = 180
MAX_BYTES = 32 * 1024 * 1024


async def command(*args, timeout=60):
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise ValueError("Không đọc được video. Cần video MP4/MOV có hình ảnh hợp lệ.")
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def inspect(path):
    raw = await command("ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams", "-of", "json", str(path), timeout=20)
    info = json.loads(raw)
    duration = float(info.get("format", {}).get("duration", 0))
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video or not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS:
        raise ValueError("Video cần dài từ trên 0 đến 180 giây và có hình ảnh.")
    if max(video.get("width", 0), video.get("height", 0)) > 4096:
        raise ValueError("Video vượt giới hạn độ phân giải 4096px.")
    return duration, any(s.get("codec_type") == "audio" for s in info["streams"])


async def process(job, store, provider, voice_client, skills_root):
    ident = job["job_id"]
    folder = store.directory(ident)
    source = folder / "source.video"
    store.update(ident, status="extracting")
    duration, has_audio = await inspect(source)
    interval = max(duration / 23, 0.25)
    frames_dir = folder / "frames"
    frames_dir.mkdir(exist_ok=True)
    await command("ffmpeg", "-nostdin", "-y", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", str(source),
                  "-vf", f"fps=1/{interval},scale=448:448:force_original_aspect_ratio=decrease", "-frames:v", "24", "-q:v", "4", str(frames_dir / "%03d.jpg"))
    frames = [{"t": min(index * interval, duration), "image": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode(), "reason": "video"}
              for index, p in enumerate(sorted(frames_dir.glob("*.jpg"))[:24])]
    if not frames:
        raise ValueError("Không lấy được ảnh từ video.")
    lines, warnings = [], []
    if has_audio and voice_client is not None:
        store.update(ident, status="transcribing")
        # Fixed segments give explicit time bounds even if the speech provider returns text only.
        for start in range(0, math.ceil(duration), 15):
            audio = await command("ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(source),
                                  "-t", "15", "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1")
            response = await voice_client.post("/audio/transcriptions", files={"file": ("segment.wav", audio, "audio/wav")},
                                               data={"model": "large-v3-turbo", "language": "vi"})
            response.raise_for_status()
            text = response.json().get("text", "")
            if not isinstance(text, str):
                raise ValueError("Dịch vụ giọng nói trả dữ liệu không hợp lệ.")
            if text.strip():
                lines.append({"t0": start, "t1": min(start + 15, duration), "text": text.strip()[:2000]})
    else:
        warnings.append("Không có lời nói được nhận dạng; bài học dựa trên ảnh và ghi chú.")
    if not lines and not job.get("notes", "").strip():
        raise ValueError("Cần ghi chú mô tả thao tác khi video không có transcript. Thêm ghi chú rồi thử lại.")
    request = CompileRequest(title=job["title"], teacher_name=job.get("teacher_name", ""), notes=job.get("notes", ""), frames=frames, transcript=lines)
    result = await compile_skill(request, provider=provider, model=compiler_model(), skills_root=skills_root,
                                 on_phase=lambda phase, **info: store.update(ident, status=phase if phase != "done" else "verifying", progress=info))
    result["video"] = {"duration_seconds": duration, "warnings": warnings, "transcript": lines,
                       "timestamp_precision": "15-second audio segments; frame sampling interval " + str(round(interval, 2))}
    result_file = folder / "result.json"
    temporary = folder / "result.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    temporary.replace(result_file)
    store.update(ident, status="ready", error=None, duration_seconds=duration, warnings=warnings)

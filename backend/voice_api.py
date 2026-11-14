"""Optional Hypertek audio for clients without Vietnamese browser speech."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from intent_router import IntentRequest
from providers import llm_base_url

router = APIRouter(prefix="/api/voice")
logger = logging.getLogger(__name__)
HYPERTEK = "https://llm.hypertek.vn/v1"
MAX_AUDIO_BYTES = 4 * 1024 * 1024
FORMATS = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
}


def openai_voice() -> bool:
    provider = os.getenv("SHADOW_VOICE_PROVIDER", "").strip().lower()
    return provider == "openai" or (not provider and bool(os.getenv("OPENAI_API_KEY", "").strip()))


def voice_client_from_env() -> httpx.AsyncClient | None:
    if openai_voice():
        key = os.getenv("OPENAI_API_KEY", "").strip()
        return httpx.AsyncClient(base_url="https://api.openai.com/v1", headers={"Authorization": f"Bearer {key}"}, timeout=18) if key else None
    if os.getenv("SHADOW_VOICE_PROVIDER", "").strip().lower() != "hypertek":
        return None
    key = os.getenv("HYPERTEK_API_KEY", "").strip()
    if not key and llm_base_url() == HYPERTEK:
        key = os.getenv("LLM_API_KEY", "").strip()
    if not key:
        return None
    return httpx.AsyncClient(base_url=HYPERTEK, headers={"Authorization": f"Bearer {key}"}, timeout=18)


@router.get("/config")
def config(request: Request) -> dict[str, bool]:
    return {"available": request.app.state.voice_client is not None}


async def send_audio(request: Request, endpoint: str, **body) -> httpx.Response:
    client = request.app.state.voice_client
    if client is None:
        raise HTTPException(503, "Máy chủ chưa cấu hình dịch vụ giọng nói.")
    try:
        response = await client.post(endpoint, **body)
        response.raise_for_status()
        return response
    except httpx.TimeoutException as error:
        raise HTTPException(504, "Dịch vụ giọng nói phản hồi quá lâu.") from error
    except httpx.HTTPStatusError as error:
        logger.info("Voice upstream %s returned HTTP %d", endpoint, error.response.status_code)
        raise HTTPException(502, "Dịch vụ giọng nói đang lỗi. Thử lại nhé.") from error
    except httpx.HTTPError as error:
        raise HTTPException(502, "Chưa kết nối được dịch vụ giọng nói.") from error


@router.post("/transcribe")
async def transcribe(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    ext = FORMATS.get(content_type)
    if ext is None:
        raise HTTPException(415, "Định dạng ghi âm chưa được hỗ trợ.")
    audio = bytearray()
    async for chunk in request.stream():
        if len(audio) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Đoạn ghi âm vượt quá 4 MB.")
        audio.extend(chunk)
    if not audio:
        raise HTTPException(422, "Đoạn ghi âm đang trống.")
    response = await send_audio(
        request, "/audio/transcriptions",
        files={"file": (f"recording.{ext}", bytes(audio), content_type)},
        data={"model": "gpt-4o-mini-transcribe" if openai_voice() else "large-v3-turbo", "language": "vi"},
    )
    try:
        text = response.json()["text"]
        if not isinstance(text, str):
            raise TypeError("Invalid transcript")
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(502, "Dịch vụ giọng nói trả dữ liệu không hợp lệ.") from error
    return {"text": text.strip()[:500]}


@router.post("/speech")
async def speech(payload: IntentRequest, request: Request) -> Response:
    response = await send_audio(request, "/audio/speech", json={
        "model": "gpt-4o-mini-tts" if openai_voice() else "vieneu-v3-turbo", "voice": "marin" if openai_voice() else "Mai Anh", "input": payload.text, "response_format": "wav",
    })
    if not response.content.startswith(b"RIFF"):
        raise HTTPException(502, "Dịch vụ giọng nói trả audio không hợp lệ.")
    return Response(response.content, media_type="audio/wav", headers={"Cache-Control": "no-store"})

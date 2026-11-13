"""Optional Hypertek audio for clients without Vietnamese browser speech."""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from intent_router import IntentRequest
from providers import llm_base_url
from service_status import record_success

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


def voice_client_from_env() -> httpx.AsyncClient | None:
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


def voice_error(status: int, message: str, code: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(status, message, headers={
        "X-Shadow-Error-Code": code, "X-Shadow-Service": "voice",
        "X-Shadow-Retryable": str(retryable).lower(),
    })


async def send_audio(request: Request, endpoint: str, **body) -> httpx.Response:
    client = request.app.state.voice_client
    if client is None:
        disabled = os.getenv("SHADOW_VOICE_PROVIDER", "").strip().lower() != "hypertek"
        raise voice_error(503, "Dịch vụ giọng nói đang tắt." if disabled else "Máy chủ chưa cấu hình khóa API giọng nói Hypertek.", "service_disabled" if disabled else "missing_api_key")
    try:
        response = await client.post(endpoint, **body)
        response.raise_for_status()
        return response
    except httpx.TimeoutException as error:
        raise voice_error(504, "Dịch vụ giọng nói phản hồi quá lâu.", "provider_timeout", retryable=True) from error
    except httpx.HTTPStatusError as error:
        logger.info("Voice upstream %s returned HTTP %d", endpoint, error.response.status_code)
        status = error.response.status_code
        if status in (401, 403):
            raise voice_error(503, "Dịch vụ giọng nói từ chối khóa API. Kiểm tra cấu hình máy chủ.", "provider_auth_failed") from error
        if status == 402:
            raise voice_error(503, "Tài khoản dịch vụ giọng nói không đủ credit.", "insufficient_credits") from error
        if status == 429:
            raise voice_error(429, "Dịch vụ giọng nói đang giới hạn lượt gọi. Thử lại sau.", "rate_limited", retryable=True) from error
        raise voice_error(502, "Dịch vụ giọng nói đang lỗi. Thử lại nhé.", "provider_unavailable", retryable=True) from error
    except httpx.HTTPError as error:
        raise voice_error(502, "Chưa kết nối được dịch vụ giọng nói.", "provider_unavailable", retryable=True) from error


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
        data={"model": "large-v3-turbo", "language": "vi"},
    )
    try:
        text = response.json()["text"]
        if not isinstance(text, str):
            raise TypeError("Invalid transcript")
    except (ValueError, KeyError, TypeError) as error:
        raise voice_error(502, "Dịch vụ giọng nói trả dữ liệu không hợp lệ.", "invalid_provider_response") from error
    record_success("voice")
    return {"text": text.strip()[:500]}


@router.post("/speech")
async def speech(payload: IntentRequest, request: Request) -> Response:
    response = await send_audio(request, "/audio/speech", json={
        "model": "vieneu-v3-turbo", "voice": "Mai Anh", "input": payload.text, "response_format": "wav",
    })
    if not response.content.startswith(b"RIFF"):
        raise voice_error(502, "Dịch vụ giọng nói trả audio không hợp lệ.", "invalid_provider_response")
    record_success("voice")
    return Response(response.content, media_type="audio/wav", headers={"Cache-Control": "no-store"})

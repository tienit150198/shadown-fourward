"""Server-side session creation for Vidu S2-Editing and its managed Agora channel."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from service_status import record_success
from wardrobe_store import WardrobeError, decode_image_data_url, jpeg_data_url, to_jpeg

router = APIRouter()

LIVE_MODEL = "vidu-s2-editing"
DEFAULT_BASE_URL = "https://api.vidu.com"
BASE_URLS = {DEFAULT_BASE_URL, "https://api.vidu.cn"}
RTC_PROVIDER = "agora"
MAX_REFERENCE_BYTES = 2 * 1024 * 1024


class ViduLiveError(RuntimeError):
    def __init__(self, message: str, *, status: int, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _api_key() -> str:
    return os.environ.get("VIDU_API_KEY", "").strip()


def _base_url() -> str:
    return os.environ.get("VIDU_API_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL


def _bounded_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return max(low, min(value, high))


def _max_session_seconds() -> int:
    return _bounded_env("VIDU_MAX_SESSION_SECONDS", 120, 1, 1800)


def live_config() -> dict[str, Any]:
    config = {
        "available": bool(_api_key()) and _base_url() in BASE_URLS,
        "provider": "vidu",
        "model": LIVE_MODEL,
        "credits_per_second": 1,
        "max_session_seconds": _max_session_seconds(),
        "idle_seconds": _bounded_env("VIDU_IDLE_SECONDS", 60, 5, 300),
    }
    if not config["available"]:
        config["detail"] = (
            "Máy chủ SHADOW chưa được cấu hình VIDU_API_KEY."
            if not _api_key() else "VIDU_API_BASE_URL phải là địa chỉ Vidu đúng khu vực."
        )
    return config


def _normalize_reference(reference_image: str) -> str:
    raw = decode_image_data_url(reference_image, max_bytes=MAX_REFERENCE_BYTES)
    return jpeg_data_url(to_jpeg(raw, max_side=1536))


def _upstream_error(status: int, body: dict[str, Any]) -> ViduLiveError:
    if status in (401, 403):
        return ViduLiveError("Vidu từ chối khóa API. Kiểm tra VIDU_API_KEY và khu vực của tài khoản.", status=503, code="VIDU_AUTH")
    if status == 402 or body.get("reason") == "CreditInsufficient":
        return ViduLiveError("Tài khoản Vidu không đủ credit để mở phiên.", status=503, code="VIDU_CREDITS")
    if status == 429:
        return ViduLiveError("Vidu đang có quá nhiều phiên mặc thử. Đợi một chút rồi thử lại.", status=429, code="VIDU_BUSY")
    if status == 503:
        return ViduLiveError("Kênh video của Vidu tạm thời không khả dụng. Thử lại sau.", status=503, code="VIDU_UNAVAILABLE")
    return ViduLiveError("Vidu không mở được phiên mặc thử. Thử lại sau.", status=502, code="VIDU_CREATE_FAILED")


def _session_details(body: dict[str, Any], base_url: str) -> dict[str, Any]:
    live = body.get("live")
    rtc = body.get("rtc")
    render_uid = body.get("render_uid")
    secret = body.get("client_secret")
    if (
        not isinstance(live, dict)
        or not isinstance(rtc, dict)
        or rtc.get("provider") != RTC_PROVIDER
        or not isinstance(secret, str)
        or not secret
        or not all(isinstance(rtc.get(k), str) and rtc[k] for k in ("app_id", "channel_id", "token"))
        or not all(type(v) in (str, int) and v for v in (live.get("id"), render_uid, rtc.get("user_id")))
    ):
        raise ViduLiveError("Vidu trả thông tin phiên không đầy đủ.", status=502, code="VIDU_INVALID_SESSION")

    max_seconds = _max_session_seconds()
    provider_duration = live.get("live_duration")
    if type(provider_duration) is int and provider_duration > 0:
        max_seconds = min(max_seconds, provider_duration)

    credentials = {name: rtc[name] for name in ("app_id", "channel_id", "user_id", "token", "provider")}
    if type(rtc.get("agora_int_uid")) is int and rtc["agora_int_uid"] > 0:
        credentials["agora_int_uid"] = rtc["agora_int_uid"]
    return {
        "live_id": str(live["id"]),
        "rtc": credentials,
        "render_uid": str(render_uid),
        "client_secret": secret,
        "ws_url": f"wss://{base_url.removeprefix('https://')}/live/ws/live/connect",
        "max_session_seconds": max_seconds,
    }


async def create_live_session(reference_image: str, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    api_key = _api_key()
    base_url = _base_url()
    if not api_key:
        raise ViduLiveError("Máy chủ SHADOW chưa được cấu hình VIDU_API_KEY.", status=503, code="NO_VIDU_KEY")
    if base_url not in BASE_URLS:
        raise ViduLiveError("VIDU_API_BASE_URL phải là địa chỉ Vidu đúng khu vực.", status=503, code="VIDU_INVALID_BASE_URL")
    try:
        reference = await asyncio.to_thread(_normalize_reference, reference_image)
    except WardrobeError:
        raise ViduLiveError("Ảnh tham chiếu phải là ảnh JPEG hoặc PNG hợp lệ, tối đa 2 MB.", status=422, code="VIDU_INVALID_REFERENCE") from None

    headers = {
        "Authorization": api_key if api_key.startswith("vda_") else f"vda_{api_key}",
        "X-Request-ID": str(uuid4()),
    }
    try:
        async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
            response = await client.post(
                f"{base_url}/live/s_editing/realtime",
                headers=headers,
                json={
                    "image_url": reference,
                    "editing_type": "virtual_tryon",
                    "managed_rtc": {"provider": RTC_PROVIDER, "enable_recording": False},
                },
            )
    except httpx.TimeoutException:
        raise ViduLiveError("Vidu phản hồi quá lâu. Thử lại nhé.", status=504, code="VIDU_TIMEOUT") from None
    except httpx.RequestError:
        raise ViduLiveError("Máy chủ SHADOW không kết nối được với Vidu.", status=502, code="VIDU_CONNECTION") from None

    try:
        body = response.json()
    except ValueError:
        body = None
    body = body if isinstance(body, dict) else {}
    if not response.is_success:
        raise _upstream_error(response.status_code, body)
    return _session_details(body, base_url)


class LiveSessionRequest(BaseModel):
    reference_image: str


@router.post("/api/tryon/live-session")
async def tryon_live_session(payload: LiveSessionRequest, request: Request) -> JSONResponse:
    task = asyncio.create_task(create_live_session(payload.reference_image))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.25)
            if not task.done() and await request.is_disconnected():
                raise HTTPException(status_code=499, detail="Đã hủy mở phiên mặc thử.")
        session = task.result()
    except ViduLiveError as error:
        code = {"VIDU_AUTH": "provider_auth_failed", "VIDU_CREDITS": "insufficient_credits", "VIDU_BUSY": "rate_limited", "VIDU_UNAVAILABLE": "provider_unavailable", "VIDU_NOT_CONFIGURED": "missing_api_key", "VIDU_TIMEOUT": "provider_timeout", "VIDU_INVALID_SESSION": "invalid_provider_response", "VIDU_INVALID_REFERENCE": "invalid_request"}.get(error.code, "provider_unavailable")
        if not _api_key():
            code = "missing_api_key"
        elif _base_url() not in BASE_URLS:
            code = "invalid_configuration"
        raise HTTPException(status_code=error.status, detail={"code": error.code, "message": str(error)}, headers={"X-Shadow-Error-Code": code, "X-Shadow-Service": "live_tryon", "X-Shadow-Retryable": str(code in ("rate_limited", "provider_timeout", "provider_unavailable")).lower()}) from None
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    record_success("live_tryon")
    return JSONResponse(session, headers={"Cache-Control": "no-store"})

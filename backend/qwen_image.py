"""Server-side Qwen Image generation through Hypertek."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
from contextlib import suppress
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StringConstraints, field_validator

from wardrobe_store import MAX_IMAGE_BYTES, WardrobeError, jpeg_data_url, to_jpeg

DEFAULT_BASE_URL = "https://llm.hypertek.vn/v1"
DEFAULT_MODEL = "Qwen/Qwen-Image-2.1"
DEFAULT_SIZE = "768x768"
DEFAULT_TIMEOUT_S = 180
DISCONNECT_POLL_S = 1.0

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/images")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


class QwenImageError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


class QwenImage:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @property
    def base_url(self) -> str:
        return _env("QWEN_IMAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    @property
    def model(self) -> str:
        return _env("QWEN_IMAGE_MODEL", DEFAULT_MODEL)

    def _key(self) -> str:
        key = _env("HYPERTEK_API_KEY")
        if not key and _env("LLM_BASE_URL").rstrip("/") == self.base_url:
            return _env("LLM_API_KEY")
        return key

    def available(self) -> bool:
        return bool(self._key())

    async def generate(self, prompt: str, size: str = DEFAULT_SIZE) -> bytes:
        key = self._key()
        if not key:
            raise QwenImageError("Chưa cấu hình khóa API cho Qwen Image.", 503)
        try:
            timeout = int(_env("QWEN_IMAGE_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        timeout = timeout if 1 <= timeout <= 270 else DEFAULT_TIMEOUT_S
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=min(15, timeout)), transport=self._transport,
            ) as client:
                response = await asyncio.wait_for(client.post(
                    f"{self.base_url}/images/generations",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": self.model, "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"},
                ), timeout)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise QwenImageError("Qwen Image phản hồi quá chậm. Vui lòng thử lại.", 504) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise QwenImageError("Không kết nối được với Qwen Image.") from None
        if not response.is_success:
            logger.warning("Qwen Image generation HTTP %s", response.status_code)
            if response.status_code == 429:
                raise QwenImageError("Qwen Image đã đạt giới hạn lượt gọi. Vui lòng thử lại sau.", 429)
            raise QwenImageError(f"Qwen Image xử lý ảnh thất bại (HTTP {response.status_code}).")
        try:
            encoded = response.json()["data"][0]["b64_json"]
            if not isinstance(encoded, str) or not 0 < len(encoded) <= ((MAX_IMAGE_BYTES + 2) // 3) * 4:
                raise ValueError("invalid base64 image size")
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError("image too large")
            return await asyncio.to_thread(to_jpeg, raw, max_side=1024, quality=90)
        except (KeyError, IndexError, TypeError, ValueError, WardrobeError):
            raise QwenImageError("Qwen Image trả về ảnh không hợp lệ.") from None


class ImageRequest(BaseModel):
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    size: str = DEFAULT_SIZE

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        match = re.fullmatch(r"([0-9]{3,4})x([0-9]{3,4})", value)
        if match and all(512 <= int(side) <= 1024 and int(side) % 16 == 0 for side in match.groups()):
            return value
        raise ValueError("Kích thước ảnh phải là RỘNGxCAO, mỗi cạnh từ 512 đến 1024 và chia hết cho 16.")


def _service(request: Request) -> QwenImage:
    service = getattr(request.app.state, "qwen_image", None)
    return QwenImage() if service is None else service


@router.get("/config")
async def image_config(request: Request) -> dict[str, bool | str]:
    service = _service(request)
    return {"available": service.available(), "model": service.model}


@router.post("/generate")
async def generate_image(payload: ImageRequest, request: Request) -> dict[str, str | float]:
    service = _service(request)
    started = time.monotonic()
    task = asyncio.create_task(service.generate(payload.prompt, payload.size))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=DISCONNECT_POLL_S)
            if not task.done() and await request.is_disconnected():
                raise HTTPException(status_code=499, detail="Đã huỷ tạo ảnh.")
        jpeg = task.result()
    except QwenImageError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from None
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return {
        "image": jpeg_data_url(jpeg),
        "provider": "qwen",
        "model": service.model,
        "seconds": round(time.monotonic() - started, 1),
    }

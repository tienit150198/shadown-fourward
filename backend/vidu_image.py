"""Vidu Q2 reference-to-image for photo try-on."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
from typing import Any

import httpx
from PIL import Image

from wardrobe_store import MAX_IMAGE_BYTES, WardrobeError, jpeg_data_url, to_jpeg

DEFAULT_BASE_URL = "https://api.vidu.com"
BASE_URLS = {DEFAULT_BASE_URL, "https://api.vidu.cn"}
MODEL = "viduq2"
MAX_REFERENCES = 7
DEFAULT_TIMEOUT_S = 180
POLL_INTERVAL_S = 2.0
_PENDING_STATES = {"created", "queueing", "processing"}

logger = logging.getLogger("uvicorn.error")


class ViduImageError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _api_key() -> str:
    return os.getenv("VIDU_API_KEY", "").strip()


def _base_url() -> str:
    return os.getenv("VIDU_API_BASE_URL", "").strip().rstrip("/") or DEFAULT_BASE_URL


def _reference_data_urls(images: list[bytes]) -> list[str]:
    references = []
    for raw in images:
        try:
            with Image.open(io.BytesIO(raw)) as source:
                width, height = source.size
                if max(width, height) >= 4 * min(width, height):
                    raise ViduImageError("Ảnh mặc thử cần có tỷ lệ khung hình nằm trong khoảng 1:4 đến 4:1.", 422)
                if min(width, height) < 128:
                    scale = 128 / min(width, height)
                    resized = source.convert("RGB").resize(
                        (math.ceil(width * scale), math.ceil(height * scale)), Image.Resampling.LANCZOS,
                    )
                    output = io.BytesIO()
                    resized.save(output, format="JPEG", quality=90)
                    raw = output.getvalue()
        except (OSError, ValueError):
            raise ViduImageError("Không đọc được ảnh tham chiếu để mặc thử.", 422) from None
        references.append(jpeg_data_url(raw))
    return references


class ViduImage:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def available(self) -> bool:
        return bool(_api_key()) and _base_url() in BASE_URLS

    async def edit(self, prompt: str, images: list[bytes]) -> bytes:
        key = _api_key()
        if not key:
            raise ViduImageError("Chưa cấu hình khóa API Vidu để mặc thử.", 503)
        base_url = _base_url()
        if base_url not in BASE_URLS:
            raise ViduImageError("VIDU_API_BASE_URL phải là địa chỉ Vidu đúng khu vực.", 503)
        if not 2 <= len(images) <= MAX_REFERENCES:
            raise ViduImageError("Cần ảnh người và từ một đến sáu ảnh đồ để mặc thử.", 422)
        try:
            timeout = int(os.getenv("VIDU_IMAGE_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        timeout = timeout if 1 <= timeout <= 270 else DEFAULT_TIMEOUT_S
        references = await asyncio.to_thread(_reference_data_urls, images)
        payload = {"model": MODEL, "images": references, "prompt": prompt, "aspect_ratio": "auto", "resolution": "1080p"}
        headers = {"Authorization": f"Token {key}"}
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=self._transport) as client:
                return await asyncio.wait_for(self._run(client, base_url, headers, payload), timeout)
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise ViduImageError("Vidu xử lý ảnh quá lâu. Vui lòng thử lại.", 504) from None
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ViduImageError("Không kết nối được với Vidu để mặc thử.") from None

    async def _run(
        self, client: httpx.AsyncClient, base_url: str, headers: dict[str, str], payload: dict[str, Any],
    ) -> bytes:
        response = await client.post(f"{base_url}/ent/v2/reference2image", headers=headers, json=payload)
        created = _task_json(response)
        task_id = created.get("task_id")
        if created.get("state") == "failed":
            raise ViduImageError("Vidu không xử lý được ảnh mặc thử.")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise ViduImageError("Vidu không trả về mã tác vụ mặc thử hợp lệ.")
        url = f"{base_url}/ent/v2/tasks/{task_id}/creations"
        while True:
            task = _task_json(await client.get(url, headers=headers))
            state = task.get("state")
            if state == "success":
                return await _download_image(client, task)
            if state == "failed":
                raise ViduImageError("Vidu không xử lý được ảnh mặc thử.")
            if not isinstance(state, str) or state not in _PENDING_STATES:
                raise ViduImageError("Vidu trả về trạng thái mặc thử không hợp lệ.")
            await asyncio.sleep(POLL_INTERVAL_S)


def _task_json(response: httpx.Response) -> dict[str, Any]:
    if not response.is_success:
        logger.warning("Vidu Image returned HTTP %s", response.status_code)
        if response.status_code == 429:
            raise ViduImageError("Vidu đã đạt giới hạn lượt gọi. Vui lòng thử lại sau.", 429)
        if response.status_code in (401, 403):
            raise ViduImageError("Vidu từ chối quyền tạo ảnh. Kiểm tra khóa API và quyền tài khoản.", 503)
        raise ViduImageError(f"Vidu xử lý ảnh thất bại (HTTP {response.status_code}).")
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise ViduImageError("Vidu trả về dữ liệu tác vụ không hợp lệ.")


async def _download_image(client: httpx.AsyncClient, task: dict[str, Any]) -> bytes:
    try:
        url = httpx.URL(task["creations"][0]["url"])
        if url.scheme != "https" or not url.host or url.userinfo:
            raise ValueError("invalid image URL")
    except (KeyError, IndexError, TypeError, ValueError, httpx.InvalidURL):
        raise ViduImageError("Vidu không trả về địa chỉ ảnh mặc thử hợp lệ.") from None

    # Result URLs are signed by Vidu's CDN and need no API credentials.
    data = bytearray()
    async with client.stream("GET", url) as response:
        if not response.is_success:
            raise ViduImageError("Không tải được ảnh mặc thử từ Vidu.")
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ViduImageError("Ảnh mặc thử Vidu trả về quá lớn.")
    try:
        return await asyncio.to_thread(to_jpeg, bytes(data), max_side=1024, quality=90)
    except WardrobeError:
        raise ViduImageError("Vidu trả về ảnh mặc thử không hợp lệ.") from None

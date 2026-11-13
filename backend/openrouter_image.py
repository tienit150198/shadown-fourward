"""OpenRouter's unified image editing API for Nano Banana photo try-on."""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os

import httpx
from PIL import Image

from wardrobe_store import MAX_IMAGE_BYTES, WardrobeError, jpeg_data_url, to_jpeg

MODEL = "google/gemini-nano-banana-2.1"
MAX_REFERENCES = 14


class OpenRouterImageError(RuntimeError):
    def __init__(self, message: str, status: int = 502, *, code: str | None = None,
                 service: str = "photo_tryon", retryable: bool | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code or {422: "invalid_request", 429: "rate_limited", 504: "provider_timeout"}.get(status, "provider_unavailable")
        self.service = service
        self.retryable = (self.code in {"rate_limited", "provider_timeout", "provider_unavailable"}) if retryable is None else retryable


class OpenRouterImage:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @staticmethod
    def available() -> bool:
        return bool(os.getenv("OPENROUTER_API_KEY", "").strip())

    async def edit(self, prompt: str, images: list[bytes]) -> bytes:
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise OpenRouterImageError("Chưa cấu hình OPENROUTER_API_KEY để mặc thử bằng Nano Banana.", 503, code="missing_api_key")
        if not 2 <= len(images) <= MAX_REFERENCES:
            raise OpenRouterImageError("Nano Banana cần ảnh người và từ một đến 13 ảnh đồ.", 422)
        with Image.open(io.BytesIO(images[0])) as person:
            ratio = person.width / person.height
        ratios = {"1:1": 1, "2:3": 2/3, "3:2": 3/2, "3:4": 3/4, "4:3": 4/3, "4:5": 4/5, "5:4": 5/4, "9:16": 9/16, "16:9": 16/9}
        payload = {
            "model": MODEL, "prompt": prompt, "n": 1, "resolution": "1K",
            "aspect_ratio": min(ratios, key=lambda key: abs(ratios[key] - ratio)),
            "input_references": [{"type": "image_url", "image_url": {"url": jpeg_data_url(image)}} for image in images],
        }
        try:
            # No automatic retries: an ambiguous timeout may already have incurred a generation charge.
            async with httpx.AsyncClient(timeout=180, transport=self._transport) as client:
                async with client.stream(
                    "POST", "https://openrouter.ai/api/v1/images",
                    headers={"Authorization": f"Bearer {key}"}, json=payload,
                ) as response:
                    if not response.is_success:
                        status = response.status_code
                        if status == 402:
                            raise OpenRouterImageError("OpenRouter không đủ credit. Hãy nạp credit rồi thử lại.", 503, code="insufficient_credits")
                        if status in (401, 403):
                            raise OpenRouterImageError("OpenRouter từ chối quyền tạo ảnh. Kiểm tra khóa API và quyền mô hình.", 503, code="provider_auth_failed")
                        if status == 429:
                            raise OpenRouterImageError("OpenRouter đang giới hạn lượt gọi. Vui lòng thử lại sau.", 429)
                        raise OpenRouterImageError(f"Nano Banana xử lý ảnh thất bại (HTTP {status}).")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 256 * 1024:
                            raise OpenRouterImageError("Ảnh Nano Banana trả về quá lớn.", code="invalid_provider_response")
            data = json.loads(raw)
            encoded = data["data"][0]["b64_json"]
            if not isinstance(encoded, str):
                raise ValueError("invalid image")
            image = base64.b64decode(encoded, validate=True)
            if len(image) > MAX_IMAGE_BYTES:
                raise ValueError("oversized image")
            return await asyncio.to_thread(to_jpeg, image, max_side=1024, quality=90)
        except httpx.TimeoutException:
            raise OpenRouterImageError("Nano Banana xử lý ảnh quá lâu. Vui lòng thử lại.", 504) from None
        except httpx.HTTPError:
            raise OpenRouterImageError("Không kết nối được OpenRouter để mặc thử.") from None
        except (ValueError, KeyError, IndexError, TypeError, binascii.Error, WardrobeError):
            raise OpenRouterImageError("Nano Banana trả về ảnh mặc thử không hợp lệ.", code="invalid_provider_response") from None

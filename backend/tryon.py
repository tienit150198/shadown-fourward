"""Virtual try-on for "Trợ lý tủ đồ" (new in SHADOW).

Live (Decart, WebRTC in the browser):
    The backend keeps DECART_API_KEY and only mints short-lived *client tokens*, mirroring
    `client.tokens.create()` of @decartai/sdk (DecartAI/sdk packages/sdk/src/tokens/client.ts):
        POST {DECART_BASE_URL or https://api.decart.ai}/v1/client/tokens
        headers: X-API-KEY: <key>, content-type: application/json
        body:    {"expiresIn": 1..3600, "constraints"?: {"realtime": {"maxSessionDuration": s}}}
        200 →    {"apiKey": "ek_...", "token": "<JWT>", "expiresAt": "...", "permissions"?, "constraints"?}
    The browser passes `apiKey` to `createDecartClient({apiKey})` (or returns it from `apiKeyProvider`)
    and connects to model "lucy-vton-latest". Pattern from DecartAI/tryon-examples (MIT).

Photo (server side), TRYON_PHOTO_PROVIDER = auto (default) | fashn | worker | hf_space | mock:
    fashn    — FASHN tryon-v1.6 commercial API; requires FASHN_API_KEY
    worker   — Mac mini CatVTON worker: POST {TRYON_WORKER_URL}/tryon, GET /health (cached 30 s)
    hf_space — Hugging Face Space (default yisol/IDM-VTON, CC BY-NC-SA 4.0) via gradio_client;
               upper-body only (bottoms are not rendered)
    mock     — offline composite (garment pasted on the photo + "DEMO" label)
    auto     — worker (if configured and healthy) → hf_space → mock
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from wardrobe_store import WardrobeError, decode_image_data_url, jpeg_data_url, to_jpeg

logger = logging.getLogger("uvicorn.error")

LIVE_MODEL = "lucy-vton-latest"
DEFAULT_DECART_BASE_URL = "https://api.decart.ai"
DEFAULT_HF_SPACE = "yisol/IDM-VTON"
PHOTO_PROVIDERS = ("fashn", "worker", "hf_space", "mock")
DEFAULT_FASHN_BASE_URL = "https://api.fashn.ai"
FASHN_TIMEOUT_S = 90.0
FASHN_POLL_INTERVAL_S = 0.75
HF_TIMEOUT_S = 120.0
WORKER_TIMEOUT_S = 180.0
# Decart cost guard, enforced by Decart itself through the client-token constraints (the browser's
# own idle/cap timers are only a convenience and can be changed by anyone).
DEFAULT_DECART_TOKEN_TTL_S = 60
DEFAULT_DECART_MAX_SESSION_S = 180
MAX_DECART_SESSION_S = 1800


def worker_headers() -> dict[str, str]:
    """Shared secret for tryon-worker (checked there via X-Tryon-Token when TRYON_TOKEN is set)."""
    token = os.getenv("TRYON_TOKEN", "").strip()
    return {"X-Tryon-Token": token} if token else {}
WORKER_HEALTH_TTL_S = 30.0


HF_QUOTA_BACKOFF_S = 15 * 60


def hf_quota_message(error_text: str) -> str:
    """Vietnamese message for a ZeroGPU quota error from the Space, else ""."""
    if "quota" not in error_text.lower():
        return ""
    wait = re.search(r"try again in (\d+):(\d+):(\d+)", error_text, flags=re.IGNORECASE)
    when = ""
    if wait:
        hours, minutes = int(wait.group(1)), int(wait.group(2))
        when = f" (thử lại sau khoảng {hours} giờ {minutes} phút)" if hours else f" (thử lại sau khoảng {max(1, minutes)} phút)"
    return (
        f"Hugging Face đã hết lượt GPU miễn phí{when}. "
        "Thêm HF_TOKEN vào .env để có thêm lượt, hoặc dùng máy mặc thử Mac mini."
    )


class TryOnError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ---- live: Decart client token -----------------------------------------------


async def create_live_token(*, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    api_key = _env("DECART_API_KEY")
    if not api_key:
        raise TryOnError("Chưa cấu hình DECART_API_KEY nên chưa mặc thử trực tiếp được.", status=503)
    base_url = _env("DECART_BASE_URL", DEFAULT_DECART_BASE_URL).rstrip("/")
    body: dict[str, Any] = {
        "expiresIn": max(1, min(3600, _env_int("DECART_TOKEN_TTL_S", DEFAULT_DECART_TOKEN_TTL_S))),
        "constraints": {"realtime": {"maxSessionDuration": live_max_session_s()}},
    }
    headers = {
        "X-API-KEY": api_key,
        "content-type": "application/json",
        "User-Agent": "shadow-wardrobe/1.0",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, transport=transport) as client:
            response = await client.post(f"{base_url}/v1/client/tokens", json=body, headers=headers)
    except httpx.HTTPError as error:
        logger.warning("decart token request failed: %r", error)
        raise TryOnError("Không kết nối được Decart để lấy token.", status=502) from None
    if not response.is_success:
        logger.warning("decart token HTTP %s: %s", response.status_code, response.text[:200])
        raise TryOnError(f"Decart từ chối tạo token (HTTP {response.status_code}).", status=502)
    try:
        data = response.json()
    except ValueError:
        raise TryOnError("Decart trả về dữ liệu token không hợp lệ.", status=502) from None
    if not isinstance(data, dict) or not isinstance(data.get("apiKey"), str):
        raise TryOnError("Decart trả về dữ liệu token không hợp lệ.", status=502)
    return {
        "apiKey": data["apiKey"],
        "token": data.get("token"),
        "expiresAt": data.get("expiresAt"),
        "permissions": data.get("permissions"),
        "constraints": data.get("constraints"),
        "model": LIVE_MODEL,
        "max_session_seconds": live_max_session_s(),
    }


def live_available() -> bool:
    return bool(_env("DECART_API_KEY"))


def live_max_session_s() -> int:
    """Hard per-session cap sent to Decart (DECART_MAX_SESSION_S, default 180 s, max 1800 s)."""
    value = _env_int("DECART_MAX_SESSION_S", DEFAULT_DECART_MAX_SESSION_S)
    if value <= 0:
        value = DEFAULT_DECART_MAX_SESSION_S
    return min(value, MAX_DECART_SESSION_S)


# ---- garment selection ---------------------------------------------------------


def plan_garments(items: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    """Order the try-on passes: [(item, "upper"|"lower"|"overall")].

    Dress → overall. Otherwise the top (or outerwear if there is no top) as upper, then the bottom.
    """
    by_cat: dict[str, dict[str, Any]] = {}
    for item in items:
        by_cat.setdefault(item.get("category") or "", item)
    if "dress" in by_cat:
        return [(by_cat["dress"], "overall")]
    passes: list[tuple[dict[str, Any], str]] = []
    upper = by_cat.get("top") or by_cat.get("outerwear")
    if upper:
        passes.append((upper, "upper"))
    if "bottom" in by_cat:
        passes.append((by_cat["bottom"], "lower"))
    return passes


def garment_description(item: dict[str, Any]) -> str:
    descriptor = item.get("tryon_descriptor")
    if isinstance(descriptor, dict):
        text = str(descriptor.get("description") or "").strip()
        if text:
            return text[:180]
    text = str(item.get("description_en") or "").strip()
    if text:
        return text[:120]
    from stylist import fallback_description_en

    return fallback_description_en(item)


def live_prompt(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Decart substitution prompt for an outfit (format from tryon-examples/outfit-builder, MIT)."""
    by_cat: dict[str, dict[str, Any]] = {}
    for item in items:
        by_cat.setdefault(item.get("category") or "", item)
    reference = None
    if "dress" in by_cat:
        reference = by_cat["dress"]
        prompt = f"Substitute the current outfit with {garment_description(reference)}"
    else:
        top, bottom, outer = by_cat.get("top"), by_cat.get("bottom"), by_cat.get("outerwear")
        upper = None
        if top and outer:
            upper = f"{garment_description(outer)} over {garment_description(top)}"
        elif top or outer:
            upper = garment_description(top or outer)
        reference = top or outer or bottom
        if upper and bottom:
            prompt = (f"Substitute the current outfit with {upper} on top "
                      f"and {garment_description(bottom)} on the bottom")
        elif upper:
            prompt = f"Substitute the current top with {upper}"
        elif bottom:
            prompt = f"Substitute the current pants with {garment_description(bottom)}"
        else:
            prompt = ""
    return {"prompt": prompt, "reference_item_id": reference["id"] if reference else None}


# ---- photo providers -------------------------------------------------------------


def _result_to_jpeg(path_or_bytes: Any) -> bytes:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    else:
        path = Path(str(path_or_bytes))
        raw = path.read_bytes()
        # gradio_client downloads every result into the system temp dir and never deletes it.
        try:
            path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        except (OSError, ValueError):
            pass
        else:
            path.unlink(missing_ok=True)
    return to_jpeg(raw, max_side=1024, quality=90)


_MOCK_PLACEMENT = {  # kind → (max width, max height, top) as fractions of the photo
    "upper": (0.45, 0.36, 0.24),
    "overall": (0.5, 0.6, 0.24),
    "lower": (0.38, 0.4, 0.55),
}


_LABEL_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _label_font(size: int) -> Any:
    from PIL import ImageFont

    for path in _LABEL_FONTS:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def mock_composite(
    person_jpeg: bytes, garment_jpeg: bytes, label: str = "DEMO · ảnh minh hoạ, không phải AI", kind: str = "upper"
) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(io.BytesIO(person_jpeg)) as p, Image.open(io.BytesIO(garment_jpeg)) as g:
        person = p.convert("RGB")
        garment = g.convert("RGB")
        w, h = person.size
        max_w, max_h, top = _MOCK_PLACEMENT.get(kind, _MOCK_PLACEMENT["upper"])
        garment.thumbnail((int(w * max_w), int(h * max_h)))
        gw, gh = garment.size
        # Treat near-white garment background as transparent.
        mask = Image.eval(garment.convert("L"), lambda v: 0 if v > 238 else 215)
        person.paste(garment, ((w - gw) // 2, int(h * top)), mask)
        draw = ImageDraw.Draw(person)
        band = max(18, h // 22)
        draw.rectangle((0, h - band, w, h), fill=(20, 20, 20))
        font, text = _label_font(max(10, int(band * 0.55))), label
        if font is None:  # no font with Vietnamese glyphs: Pillow's default only has ASCII
            text = "DEMO - anh minh hoa, khong phai AI"
            try:
                font = ImageFont.load_default(size=max(10, int(band * 0.55)))
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
        draw.text((8, h - band + band // 5), text, fill=(255, 255, 255), font=font)
        out = io.BytesIO()
        person.save(out, format="JPEG", quality=88)
        return out.getvalue()


class TryOnService:
    """Photo try-on with a provider chain. HTTP transports and the HF runner are injectable in tests."""

    def __init__(
        self,
        *,
        fashn_transport: httpx.AsyncBaseTransport | None = None,
        worker_transport: httpx.AsyncBaseTransport | None = None,
        hf_runner: Any = None,
    ) -> None:
        self._fashn_transport = fashn_transport
        self._worker_transport = worker_transport
        self._hf_runner = hf_runner  # callable(person_path, garment_path, garment_des) -> output path
        self._health: tuple[float, bool] | None = None
        self._hf_client: Any = None
        self._hf_lock = asyncio.Lock()
        # Public ZeroGPU Spaces have a daily per-IP/user GPU quota (~2-3 IDM-VTON runs anonymously).
        # Once exhausted, skip the Space for a while instead of failing every request.
        self._hf_blocked_until = 0.0
        self._hf_blocked_reason = ""

    # ---- config ------------------------------------------------------------
    @staticmethod
    def configured_provider() -> str:
        value = _env("TRYON_PHOTO_PROVIDER", "auto").lower()
        return value if value in PHOTO_PROVIDERS + ("auto",) else "auto"

    @staticmethod
    def worker_url() -> str:
        return _env("TRYON_WORKER_URL").rstrip("/")

    @staticmethod
    def hf_space() -> str:
        return _env("HF_TRYON_SPACE", DEFAULT_HF_SPACE)

    def hf_blocked(self) -> str:
        """Vietnamese reason while the Space is known to be out of quota, else ""."""
        if self._hf_blocked_until and time.monotonic() < self._hf_blocked_until:
            return self._hf_blocked_reason
        return ""

    def hf_available(self) -> bool:
        if self.hf_blocked():
            return False
        if self._hf_runner is not None:
            return True
        try:
            import gradio_client  # noqa: F401
        except ImportError:
            return False
        return True

    async def worker_healthy(self) -> bool:
        url = self.worker_url()
        if not url:
            return False
        now = time.monotonic()
        if self._health and now - self._health[0] < WORKER_HEALTH_TTL_S:
            return self._health[1]
        ok = False
        try:
            async with httpx.AsyncClient(timeout=3.0, transport=self._worker_transport, headers=worker_headers()) as client:
                response = await client.get(f"{url}/health")
                ok = response.is_success
        except httpx.HTTPError:
            ok = False
        self._health = (now, ok)
        return ok

    async def provider_chain(self) -> list[str]:
        configured = self.configured_provider()
        if configured != "auto":
            return [configured]
        chain: list[str] = []
        if await self.worker_healthy():
            chain.append("worker")
        if self.hf_available():
            chain.append("hf_space")
        chain.append("mock")
        return chain

    async def config(self) -> dict[str, Any]:
        chain = await self.provider_chain()
        provider = chain[0]
        fashn_missing_key = provider == "fashn" and not _env("FASHN_API_KEY")
        return {
            "live": {
                "available": live_available(),
                "model": LIVE_MODEL,
                "max_session_seconds": live_max_session_s(),
            },
            "photo": {
                "provider": provider,
                "configured": self.configured_provider(),
                "chain": chain,
                "available": not fashn_missing_key,
                "renders_bottoms": provider in ("fashn", "worker", "mock"),
                "expected_seconds": {"fashn": 10, "worker": 40, "hf_space": 30, "mock": 1}.get(provider),
                "notice": (
                    "Chưa cấu hình FASHN_API_KEY nên chưa mặc thử ảnh thật được."
                    if fashn_missing_key else self.hf_blocked() or None
                ),
            },
        }

    # ---- run ---------------------------------------------------------------
    async def run(
        self,
        person_image: str,
        items: list[dict[str, Any]],
        garment_jpegs: dict[str, bytes],
    ) -> dict[str, Any]:
        """→ {"jpeg": bytes, "provider", "seconds", "rendered_item_ids", "skipped_item_ids", "note"}."""
        person_jpeg = await asyncio.to_thread(
            lambda: to_jpeg(decode_image_data_url(person_image), max_side=1024, quality=90)
        )
        from PIL import Image

        with Image.open(io.BytesIO(person_jpeg)) as person_source:
            person_width, person_height = person_source.size
        passes = plan_garments(items)
        if not passes:
            raise TryOnError("Hãy chọn ít nhất một áo, quần hoặc váy để mặc thử.", status=422)
        chain = await self.provider_chain()
        explicit = self.configured_provider() != "auto"
        errors: list[str] = []
        if not explicit and self.hf_blocked() and "hf_space" not in chain:
            errors.append(f"hf_space: {self.hf_blocked()}")  # skipped (quota): still tell the user why
        started = time.monotonic()
        for provider in chain:
            try:
                result = await self._run_provider(provider, person_jpeg, passes, garment_jpegs)
                from image_quality import ImageQualityError, validate_generated_image

                try:
                    validate_generated_image(
                        result.get("jpeg", b""),
                        min_width=min(384, max(64, person_width // 2)),
                        min_height=min(512, max(80, person_height // 2)),
                    )
                except ImageQualityError as error:
                    raise TryOnError(f"Ảnh từ {provider} không đạt kiểm tra kỹ thuật ({error.code}).") from error
            except TryOnError as error:
                if explicit or error.status == 422:
                    raise  # unsupported input for the best available provider: say so, don't fake it
                errors.append(f"{provider}: {error}")
                logger.warning("try-on provider %s failed: %s", provider, error)
                continue
            except Exception as error:  # noqa: BLE001 - provider bugs must not kill the chain
                # Exception text may hold URLs/paths: log it, tell the client only the provider.
                logger.exception("try-on provider %s crashed", provider)
                if explicit:
                    raise TryOnError(f"Mặc thử ({provider}) thất bại.") from error
                errors.append(f"{provider}: lỗi nội bộ")
                continue
            result["provider"] = provider
            result["seconds"] = round(time.monotonic() - started, 1)
            if errors:
                result["fallback_reason"] = "; ".join(errors)[:300]
            return result
        raise TryOnError("Không nhà cung cấp mặc thử nào chạy được: " + "; ".join(errors)[:300])

    async def _run_provider(
        self,
        provider: str,
        person_jpeg: bytes,
        passes: list[tuple[dict[str, Any], str]],
        garment_jpegs: dict[str, bytes],
    ) -> dict[str, Any]:
        if provider == "fashn":
            return await self._run_fashn(person_jpeg, passes, garment_jpegs)
        if provider == "worker":
            return await self._run_worker(person_jpeg, passes, garment_jpegs)
        if provider == "hf_space":
            return await self._run_hf(person_jpeg, passes, garment_jpegs)
        return await asyncio.to_thread(self._run_mock, person_jpeg, passes, garment_jpegs)

    def _run_mock(self, person_jpeg, passes, garment_jpegs) -> dict[str, Any]:
        image = person_jpeg
        for item, _kind in passes:
            image = mock_composite(image, garment_jpegs[item["id"]], kind=_kind)
        return {
            "jpeg": image,
            "rendered_item_ids": [item["id"] for item, _ in passes],
            "skipped_item_ids": [],
            "note": "Ảnh giả lập (chế độ demo) — chưa dùng AI mặc thử.",
        }

    async def _run_fashn(self, person_jpeg, passes, garment_jpegs) -> dict[str, Any]:
        api_key = _env("FASHN_API_KEY")
        if not api_key:
            raise TryOnError("Chưa cấu hình FASHN_API_KEY nên chưa mặc thử ảnh thật được.", status=503)
        base_url = _env("FASHN_BASE_URL", DEFAULT_FASHN_BASE_URL).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}", "User-Agent": "shadow-wardrobe/1.0"}
        category = {"upper": "tops", "lower": "bottoms", "overall": "one-pieces"}
        current = jpeg_data_url(person_jpeg)
        rendered: list[str] = []
        async with httpx.AsyncClient(
            timeout=30.0, transport=self._fashn_transport, headers=headers
        ) as client:
            for item, kind in passes:
                try:
                    response = await client.post(
                        f"{base_url}/v1/run",
                        json={
                            "model_name": "tryon-v1.6",
                            "inputs": {
                                "model_image": current,
                                "garment_image": jpeg_data_url(garment_jpegs[item["id"]]),
                                "category": category[kind],
                                "mode": "balanced",
                                "num_samples": 1,
                                "output_format": "jpeg",
                                "return_base64": True,
                            },
                        },
                    )
                except httpx.HTTPError as error:
                    logger.warning("FASHN submit failed: %r", error)
                    raise TryOnError("Không kết nối được FASHN.") from None
                if not response.is_success:
                    logger.warning("FASHN submit HTTP %s: %s", response.status_code, response.text[:200])
                    raise TryOnError(f"FASHN từ chối yêu cầu (HTTP {response.status_code}).")
                try:
                    prediction_id = response.json()["id"]
                except (ValueError, KeyError, TypeError):
                    raise TryOnError("FASHN trả về mã dự đoán không hợp lệ.") from None

                deadline = time.monotonic() + FASHN_TIMEOUT_S
                while True:
                    try:
                        status_response = await client.get(f"{base_url}/v1/status/{prediction_id}")
                    except httpx.HTTPError as error:
                        logger.warning("FASHN status failed: %r", error)
                        raise TryOnError("Không lấy được kết quả từ FASHN.") from None
                    if not status_response.is_success:
                        raise TryOnError(f"FASHN báo lỗi trạng thái HTTP {status_response.status_code}.")
                    try:
                        status = status_response.json()
                    except ValueError:
                        raise TryOnError("FASHN trả về trạng thái không hợp lệ.") from None
                    if status.get("status") == "completed":
                        output = status.get("output")
                        if not isinstance(output, list) or not output:
                            raise TryOnError("FASHN hoàn tất nhưng không trả ảnh.")
                        try:
                            raw = decode_image_data_url(output[0])
                        except (TypeError, WardrobeError):
                            raise TryOnError("FASHN trả về ảnh không hợp lệ.") from None
                        current = jpeg_data_url(to_jpeg(raw, max_side=1024, quality=90))
                        rendered.append(item["id"])
                        break
                    if status.get("status") == "failed":
                        logger.warning("FASHN prediction %s failed: %s", prediction_id, status.get("error"))
                        raise TryOnError("FASHN không thể tạo ảnh mặc thử từ ảnh đã gửi.", status=422)
                    if time.monotonic() >= deadline:
                        raise TryOnError(f"FASHN quá {FASHN_TIMEOUT_S:.0f}s chưa trả kết quả.")
                    await asyncio.sleep(FASHN_POLL_INTERVAL_S)

        final = base64.b64decode(current.split(",", 1)[1])
        return {"jpeg": final, "rendered_item_ids": rendered, "skipped_item_ids": [], "note": ""}

    async def _run_worker(self, person_jpeg, passes, garment_jpegs) -> dict[str, Any]:
        url = self.worker_url()
        if not url:
            raise TryOnError("Chưa cấu hình TRYON_WORKER_URL.", status=503)
        current = jpeg_data_url(person_jpeg)
        rendered = []
        async with httpx.AsyncClient(timeout=WORKER_TIMEOUT_S, transport=self._worker_transport, headers=worker_headers()) as client:
            for item, kind in passes:
                try:
                    response = await client.post(
                        f"{url}/tryon",
                        json={
                            "person_image": current,
                            "garment_image": jpeg_data_url(garment_jpegs[item["id"]]),
                            "category": kind,
                            "garment_des": garment_description(item),
                        },
                    )
                except httpx.HTTPError as error:
                    self._health = None
                    logger.warning("try-on worker request failed: %r", error)
                    raise TryOnError("Máy mặc thử không phản hồi.") from None
                if not response.is_success:
                    raise TryOnError(f"Máy mặc thử lỗi HTTP {response.status_code}.")
                try:
                    image = response.json().get("image")
                    raw = decode_image_data_url(image)
                except (ValueError, AttributeError, WardrobeError) as error:
                    logger.warning("try-on worker returned a bad image: %s", error)
                    raise TryOnError("Máy mặc thử trả ảnh không hợp lệ.") from None
                current = jpeg_data_url(to_jpeg(raw, max_side=1024, quality=90))
                rendered.append(item["id"])
        final = base64.b64decode(current.split(",", 1)[1])
        return {"jpeg": final, "rendered_item_ids": rendered, "skipped_item_ids": [], "note": ""}

    def _hf_predict(self, person_path: str, garment_path: str, garment_des: str, cancel: Any = None) -> Any:
        """Blocking call to the IDM-VTON Space (runs in a worker thread).

        `cancel` (threading.Event) is set by the async side when the request is abandoned
        (client went away / timeout): the queued Space job is then cancelled instead of running on.
        """
        if self._hf_runner is not None:
            return self._hf_runner(person_path, garment_path, garment_des)
        from gradio_client import Client, handle_file

        if self._hf_client is None:
            token = _env("HF_TOKEN") or None
            self._hf_client = Client(self.hf_space(), token=token, verbose=False)
        job = self._hf_client.submit(
            dict={"background": handle_file(person_path), "layers": [], "composite": None},
            garm_img=handle_file(garment_path),
            garment_des=garment_des,
            is_checked=True,
            is_checked_crop=False,
            denoise_steps=30,
            seed=42,
            api_name="/tryon",
        )
        while not job.done():
            if cancel is not None and cancel.is_set():
                job.cancel()
                logger.info("HF Space try-on job cancelled (request abandoned)")
                return None
            time.sleep(0.25)
        result = job.result()
        return result[0] if isinstance(result, (list, tuple)) else result

    async def _run_hf(self, person_jpeg, passes, garment_jpegs) -> dict[str, Any]:
        upper = [(item, kind) for item, kind in passes if kind in ("upper", "overall")]
        skipped = [item["id"] for item, kind in passes if kind == "lower"]
        if not upper:
            raise TryOnError(
                "Nhà cung cấp ảnh hiện tại (IDM-VTON) chỉ mặc thử được áo/váy, chưa làm được quần/chân váy.",
                status=422,
            )
        item, _kind = upper[0]
        async with self._hf_lock:  # the public Space queues anyway; avoid piling up requests
            with tempfile.TemporaryDirectory(prefix="shadow-tryon-") as tmp:
                person_path = os.path.join(tmp, "person.jpg")
                garment_path = os.path.join(tmp, "garment.jpg")
                Path(person_path).write_bytes(person_jpeg)
                Path(garment_path).write_bytes(garment_jpegs[item["id"]])
                cancel = threading.Event()
                try:
                    output = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._hf_predict, person_path, garment_path, garment_description(item), cancel
                        ),
                        timeout=HF_TIMEOUT_S,
                    )
                except asyncio.CancelledError:
                    cancel.set()  # client cancelled: drop the queued Space job too
                    raise
                except asyncio.TimeoutError:
                    cancel.set()
                    raise TryOnError(f"Hugging Face Space quá {HF_TIMEOUT_S:.0f}s chưa trả kết quả.") from None
                except TryOnError:
                    raise
                except Exception as error:
                    self._hf_client = None  # reconnect next time (Space may have restarted)
                    logger.warning("HF Space try-on failed: %s", f"{type(error).__name__}: {error}"[:300])
                    quota = hf_quota_message(str(error))
                    if quota:
                        self._hf_blocked_until = time.monotonic() + HF_QUOTA_BACKOFF_S
                        self._hf_blocked_reason = quota
                        raise TryOnError(quota, status=503) from error
                    raise TryOnError("Hugging Face Space đang lỗi hoặc quá tải.") from error
                jpeg = await asyncio.to_thread(_result_to_jpeg, output)
        note = ""
        if skipped:
            note = "IDM-VTON chỉ vẽ phần thân trên — quần/chân váy chưa được thay trong ảnh."
        return {"jpeg": jpeg, "rendered_item_ids": [item["id"]], "skipped_item_ids": skipped, "note": note}

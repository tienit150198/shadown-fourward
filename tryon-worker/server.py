"""SHADOW try-on worker: FastAPI server for the Mac mini (CatVTON on PyTorch MPS).

Contract (see ../PLAN.md, "Mac mini worker"):
  GET  /health -> {"ok": true, "model": "catvton", "device": "mps|cpu|cuda", "loaded": bool, "warm": bool, ...}
  POST /tryon  {person_image, garment_image, category: upper|lower|overall, garment_des?, steps?, guidance?, seed?}
            -> {"image": "data:image/jpeg;base64,...", "seconds": float, "width": int, "height": int, ...}

One inference at a time (asyncio.Lock); the model is loaded lazily on first use and kept warm.
Environment variables are documented in README.md and .env.example.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFilter
from pydantic import BaseModel, Field

from imaging import (ImageDecodeError, box_mask, decode_image, encode_jpeg_data_url, resize_and_crop,
                     resize_and_padding)

log = logging.getLogger("tryon.server")

MODEL_NAME = "catvton"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _parse_resolution(raw: str) -> tuple[int, int]:
    """'768x1024' -> (768, 1024). Rounded down to multiples of 64 (UNet needs latents divisible by 8)."""
    try:
        w_s, h_s = raw.lower().replace(" ", "").split("x")
        w, h = int(w_s), int(h_s)
    except ValueError as exc:
        raise ValueError(f"TRYON_RESOLUTION must look like 768x1024, got {raw!r}") from exc
    w, h = max(256, w - w % 64), max(256, h - h % 64)
    return w, h


@dataclass
class Settings:
    model: str = "catvton"
    mock: bool = False
    device: str = "auto"
    dtype: str = "auto"
    steps: int = 30
    guidance: float = 2.5
    size: tuple[int, int] = (768, 1024)
    token: str = ""
    port: int = 8188
    warmup: bool = False
    max_queue: int = 4
    max_image_mb: int = 15
    attn_chunk: Optional[int] = None
    masker: str = "schp"
    masker_device: str = "cpu"
    safety_check: bool = True
    repaint: bool = True
    offline: bool = False
    mock_delay: float = 0.0
    jpeg_quality: int = 92

    @classmethod
    def from_env(cls) -> "Settings":
        chunk_raw = os.environ.get("TRYON_ATTN_CHUNK", "").strip()
        return cls(
            model=os.environ.get("TRYON_MODEL", "catvton").strip().lower() or "catvton",
            mock=_env_bool("TRYON_MOCK", False),
            device=os.environ.get("TRYON_DEVICE", "auto").strip().lower() or "auto",
            dtype=os.environ.get("TRYON_DTYPE", "auto").strip().lower() or "auto",
            steps=_env_int("TRYON_STEPS", 30),
            guidance=float(os.environ.get("TRYON_GUIDANCE", "").strip() or 2.5),
            size=_parse_resolution(os.environ.get("TRYON_RESOLUTION", "").strip() or "576x768"),
            token=os.environ.get("TRYON_TOKEN", "").strip(),
            port=_env_int("PORT", 8188),
            warmup=_env_bool("TRYON_WARMUP", False),
            max_queue=_env_int("TRYON_MAX_QUEUE", 4),
            max_image_mb=_env_int("TRYON_MAX_IMAGE_MB", 15),
            attn_chunk=int(chunk_raw) if chunk_raw else None,
            masker=(os.environ.get("TRYON_MASKER", "schp").strip().lower() or "schp"),
            masker_device=os.environ.get("TRYON_MASKER_DEVICE", "cpu").strip().lower() or "cpu",
            safety_check=_env_bool("TRYON_SAFETY_CHECK", True),
            repaint=_env_bool("TRYON_REPAINT", True),
            offline=_env_bool("TRYON_OFFLINE", False),
            mock_delay=float(os.environ.get("TRYON_MOCK_DELAY", "").strip() or 0),
            jpeg_quality=_env_int("TRYON_JPEG_QUALITY", 92),
        )


# ----------------------------------------------------------------------------- mock backend

class MockBackend:
    """No model: pastes the garment onto the person so the SHADOW backend can be tested end-to-end."""

    name = "mock"
    device = "cpu"
    dtype_name = "none"

    def __init__(self, size: tuple[int, int], delay: float = 0.0):
        self.size = size
        self.delay = delay
        self.loaded = True  # nothing to load

    def load(self) -> None:
        self.loaded = True

    def warmup(self) -> None:
        self.load()

    def run(self, person: Image.Image, garment: Image.Image, category: str, *, steps: int, guidance: float,
            seed: Optional[int], mask: Optional[Image.Image] = None, garment_des: Optional[str] = None
            ) -> tuple[Image.Image, dict]:
        self.load()
        if self.delay:
            time.sleep(self.delay)
        w, h = self.size
        out = resize_and_crop(person, (w, h)).convert("RGB")
        region = mask.convert("L").resize((w, h)) if mask is not None else box_mask((w, h), category)
        bbox = region.getbbox() or (0, 0, w, h)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cloth = resize_and_padding(garment, (bw, bh))
        alpha = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(alpha).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=max(4, min(bw, bh) // 6), fill=220)
        alpha = alpha.filter(ImageFilter.GaussianBlur(max(2, min(bw, bh) // 40)))
        out.paste(cloth, bbox[:2], alpha)
        draw = ImageDraw.Draw(out)
        draw.rectangle((0, 0, 92, 22), fill=(0, 0, 0))
        draw.text((6, 5), f"MOCK {category}", fill=(255, 255, 0))
        info = {"seed": seed if seed is not None else 0, "steps": steps, "guidance": guidance,
                "mask_mode": "client" if mask is not None else "box", "mask_seconds": 0.0,
                "dtype": "none", "mask_image": region}
        return out, info


# ----------------------------------------------------------------------------- API models

class TryOnRequest(BaseModel):
    person_image: str = Field(..., description="data:image/...;base64,... of the person (full body works best)")
    garment_image: str = Field(..., description="data:image/...;base64,... of the garment (flat lay / product photo)")
    category: Literal["upper", "lower", "overall"] = "upper"
    garment_des: Optional[str] = Field(None, max_length=500, description="Ignored by CatVTON (no text encoder)")
    steps: Optional[int] = Field(None, ge=1, le=100)
    guidance: Optional[float] = Field(None, ge=0.0, le=20.0)
    seed: Optional[int] = Field(None, description="-1 or omitted = random; the used seed is returned")
    mask_image: Optional[str] = Field(None, description="Optional: white = area to repaint (skips auto mask)")
    return_mask: bool = Field(False, description="Include the mask used in the response (debugging)")


class TryOnResponse(BaseModel):
    image: str
    seconds: float
    width: int
    height: int
    queue_seconds: float = 0.0
    model: str = MODEL_NAME
    device: str = "cpu"
    seed: Optional[int] = None
    steps: Optional[int] = None
    mask_mode: Optional[str] = None
    mock: bool = False
    mask: Optional[str] = None


# ----------------------------------------------------------------------------- app

class WorkerState:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backend = self._make_backend(settings)
        self.lock = asyncio.Lock()
        self.waiting = 0
        self.busy = False
        self.warm = isinstance(self.backend, MockBackend)
        self.last_error: Optional[str] = None
        self.served = 0
        self.warmup_task: Optional[asyncio.Task] = None

    @staticmethod
    def _make_backend(s: Settings):
        if s.mock:
            return MockBackend(s.size, s.mock_delay)
        if s.model == "idm":
            raise RuntimeError(
                "TRYON_MODEL=idm (IDM-VTON) is not supported on this worker: it needs detectron2 DensePose as a "
                "UNet input, two SDXL UNets (~14 GB fp16) and would take many minutes per image on an M4. "
                "See README.md section 'IDM-VTON'. Use TRYON_MODEL=catvton."
            )
        if s.model != "catvton":
            raise RuntimeError(f"Unknown TRYON_MODEL={s.model!r} (supported: catvton)")
        if s.masker not in ("schp", "box"):
            raise RuntimeError(f"Unknown TRYON_MASKER={s.masker!r} (supported: schp, box)")
        from pipeline_catvton import CatVTONBackend  # imports torch

        return CatVTONBackend(device_pref=s.device, dtype_pref=s.dtype, size=s.size, attn_chunk=s.attn_chunk,
                              masker=s.masker, masker_device=s.masker_device, safety_check=s.safety_check,
                              repaint=s.repaint, offline=s.offline)

    def health(self) -> dict:
        b = self.backend
        return {
            "ok": True,
            "model": MODEL_NAME,
            "device": b.device,
            "loaded": bool(b.loaded),
            "warm": self.warm,
            "busy": self.busy,
            "queue": self.waiting,
            "mock": isinstance(b, MockBackend),
            "dtype": b.dtype_name,
            "resolution": f"{self.settings.size[0]}x{self.settings.size[1]}",
            "default_steps": self.settings.steps,
            "auth": bool(self.settings.token),
            "served": self.served,
            "error": self.last_error,
        }

    async def warmup(self) -> None:
        async with self.lock:
            self.busy = True
            try:
                t0 = time.perf_counter()
                await asyncio.to_thread(self.backend.warmup)
                self.warm = True
                self.last_error = None
                log.info("Warm-up finished in %.1fs", time.perf_counter() - t0)
            except Exception as exc:  # keep serving; /tryon will retry the load
                self.last_error = f"warmup failed: {exc}"
                log.exception("Warm-up failed")
            finally:
                self.busy = False


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = WorkerState(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Try-on worker: model=%s mock=%s device=%s resolution=%dx%d steps=%d auth=%s",
                 MODEL_NAME, settings.mock, state.backend.device, settings.size[0], settings.size[1],
                 settings.steps, bool(settings.token))
        if settings.warmup:
            state.warmup_task = asyncio.create_task(state.warmup())
        yield
        if state.warmup_task and not state.warmup_task.done():
            state.warmup_task.cancel()

    app = FastAPI(title="SHADOW try-on worker", version="1.0", lifespan=lifespan)
    app.state.worker = state
    # Whole-body cap (person + garment + optional mask, base64) checked before the JSON is parsed.
    max_body = 3 * (settings.max_image_mb * 1024 * 1024 * 4 // 3) + 64 * 1024

    @app.middleware("http")
    async def limit_body(request, call_next):
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if declared > max_body:
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    def check_token(x_tryon_token: Optional[str]) -> None:
        if settings.token and not hmac.compare_digest((x_tryon_token or "").encode(), settings.token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing X-Tryon-Token")

    @app.get("/health")
    async def health() -> dict:
        return state.health()

    @app.post("/tryon", response_model=TryOnResponse)
    async def tryon(req: TryOnRequest, x_tryon_token: Optional[str] = Header(None)) -> TryOnResponse:
        check_token(x_tryon_token)
        max_bytes = settings.max_image_mb * 1024 * 1024
        try:
            person = decode_image(req.person_image, max_bytes=max_bytes, field="person_image")
            garment = decode_image(req.garment_image, max_bytes=max_bytes, field="garment_image")
            mask = (decode_image(req.mask_image, max_bytes=max_bytes, field="mask_image")
                    if req.mask_image else None)
        except ImageDecodeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for name, img in (("person_image", person), ("garment_image", garment)):
            if min(img.size) < 64:
                raise HTTPException(status_code=400, detail=f"{name}: too small ({img.size[0]}x{img.size[1]})")

        if state.waiting >= settings.max_queue:
            raise HTTPException(status_code=503, detail="worker busy, queue full - try again later")
        steps = req.steps or settings.steps
        guidance = settings.guidance if req.guidance is None else req.guidance
        t_queue = time.perf_counter()
        state.waiting += 1
        try:
            await state.lock.acquire()
        finally:
            state.waiting -= 1
        try:
            queue_seconds = time.perf_counter() - t_queue
            state.busy = True
            t0 = time.perf_counter()
            try:
                result, info = await asyncio.to_thread(
                    state.backend.run, person, garment, req.category, steps=steps, guidance=guidance,
                    seed=req.seed, mask=mask, garment_des=req.garment_des)
            except Exception as exc:
                kind = type(exc).__name__
                if kind == "UnsafeOutput":
                    raise HTTPException(status_code=422, detail="result blocked by safety checker") from exc
                state.last_error = f"{kind}: {exc}"
                log.exception("Try-on failed")
                status = 503 if not state.backend.loaded else 500
                raise HTTPException(status_code=status, detail=f"try-on failed: {kind}: {exc}") from exc
            seconds = time.perf_counter() - t0
            state.warm = True
            state.last_error = None
            state.served += 1
        finally:
            state.busy = False
            state.lock.release()
        log.info("tryon %s %dx%d steps=%d seed=%s in %.1fs (queued %.1fs)", req.category, result.size[0],
                 result.size[1], steps, info.get("seed"), seconds, queue_seconds)
        mask_img = info.get("mask_image")
        return TryOnResponse(
            image=encode_jpeg_data_url(result, settings.jpeg_quality),
            seconds=round(seconds, 2),
            width=result.size[0],
            height=result.size[1],
            queue_seconds=round(queue_seconds, 2),
            device=state.backend.device,
            seed=info.get("seed"),
            steps=steps,
            mask_mode=info.get("mask_mode"),
            mock=isinstance(state.backend, MockBackend),
            mask=encode_jpeg_data_url(mask_img.convert("RGB"), 85) if (req.return_mask and mask_img) else None,
        )

    return app


def _lan_ips() -> list[str]:
    import socket

    ips = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # TEST-NET address; no packet is sent for UDP connect
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def main() -> None:
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    host = os.environ.get("HOST", "0.0.0.0")
    if not settings.token and host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("TRYON_TOKEN is not set: anyone on this network can use the worker. "
                    "Set TRYON_TOKEN here and the same value in SHADOW's .env.")
    for ip in _lan_ips():
        log.info("SHADOW .env on the laptop: TRYON_WORKER_URL=http://%s:%d", ip, settings.port)
    uvicorn.run(create_app(settings), host=host, port=settings.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()

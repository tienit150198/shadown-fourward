"""SHADOW backend entrypoint (FastAPI).

Forked from GlassKit `main.py` (MIT). SHADOW: WebRTC/SDP routes removed; adds REST API for
skills, teach-mode compilation and "Hỏi Shadow", plus static hosting of the web app.
Runs without any API key (mock mode).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
WEB_DIR = PROJECT_DIR / "web"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (PROJECT_DIR / ".env", BACKEND_DIR / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


_load_dotenv()

from providers import (  # noqa: E402  (env must be loaded first)
    LLMProvider,
    build_provider_from_env,
    compiler_model,
    vision_model,
)
from session_manager import ShadowSessionManager  # noqa: E402
from skill_store import SkillNotFound, SkillStore, SkillValidationError  # noqa: E402
from eval_store import EvalError, EvalStore, default_evals_root  # noqa: E402
from session_constants import MAX_FRAME_DATA_URL_CHARS  # noqa: E402
from intent_router import IntentRouter, router as intent_api  # noqa: E402
from conversation import router as conversation_api, ConversationStore  # noqa: E402
from voice_api import router as voice_api, voice_client_from_env  # noqa: E402
from qwen_image import router as image_api  # noqa: E402
from vidu_live import router as vidu_api  # noqa: E402
import security  # noqa: E402

security.configure_pillow()

logger = logging.getLogger("uvicorn.error")


def skills_root() -> Path:
    override = (os.getenv("SHADOW_SKILLS_DIR") or "").strip()
    return Path(override) if override else BACKEND_DIR / "skills"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    security.GUARD.reset()  # re-read limits from env; fresh buckets per app start
    if security.access_code():
        logger.info("SHADOW access gate ON (SHADOW_ACCESS_CODE set)")
    provider = build_provider_from_env()
    root = skills_root()
    root.mkdir(parents=True, exist_ok=True)
    store = SkillStore(root)
    manager = ShadowSessionManager(provider=provider, skill_store=store)
    app.state.conversations = ConversationStore()
    app.state.provider = provider
    app.state.intent_router = IntentRouter.from_env()
    app.state.voice_client = voice_client_from_env()
    app.state.skill_store = store
    app.state.manager = manager
    logger.info(
        "SHADOW ready mode=%s skills_dir=%s vision_model=%s",
        provider.mode,
        root,
        vision_model(),
    )
    try:
        yield
    finally:
        await manager.close()
        await provider.aclose()
        await app.state.intent_router.aclose()
        if app.state.voice_client is not None:
            await app.state.voice_client.aclose()


app = FastAPI(title="SHADOW", lifespan=lifespan)
# Access gate, body-size cap, per-IP rate limits, try-on concurrency, WS limits (see security.py).
app.add_middleware(security.SecurityMiddleware)


def _provider(request: Request) -> LLMProvider:
    return request.app.state.provider


def _store(request: Request) -> SkillStore:
    return request.app.state.skill_store


def _manager(request: Request) -> ShadowSessionManager:
    return request.app.state.manager


# ---- REST: health / skills ---------------------------------------------------


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": _provider(request).mode,
        "vision_model": vision_model(),
        "compiler_model": compiler_model(),
        "access_required": bool(security.access_code()),
    }


class AccessRequest(BaseModel):
    code: str = Field("", max_length=200)


@app.post("/api/access")
async def access(payload: AccessRequest, request: Request) -> JSONResponse:
    """Exchange the demo access code (SHADOW_ACCESS_CODE) for an HttpOnly cookie."""
    code = security.access_code()
    if code and not security.check_access_code(payload.code.strip()):
        raise HTTPException(status_code=401, detail="Mã truy cập không đúng.")
    response = JSONResponse({"ok": True, "access_required": bool(code)})
    if code:
        cookie = security.access_cookie_header(code, secure=request.url.scheme == "https")
        response.headers.append("set-cookie", cookie.decode("latin-1"))
    return response


@app.get("/api/skills")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    return _store(request).list()


@app.get("/api/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store(request).get_dict(skill_id)
    except SkillNotFound:
        raise HTTPException(status_code=404, detail="Không tìm thấy kỹ năng.")
    except SkillValidationError as error:
        raise HTTPException(status_code=500, detail=f"Dữ liệu kỹ năng bị lỗi: {error}")


@app.put("/api/skills/{skill_id}")
async def put_skill(
    skill_id: str, request: Request, skill: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    store = _store(request)
    try:
        saved = store.save({**skill, "id": skill_id})
    except SkillValidationError as error:
        raise HTTPException(status_code=422, detail=f"Kỹ năng không hợp lệ: {error}")
    _export_teach_eval_set(store, saved)
    return store.get_dict(saved.id)


def _export_teach_eval_set(store: SkillStore, recipe: Any) -> None:
    """Taught skills: the teacher's own frames become an auto-labelled eval set
    (evals/<skill>/<step>/{done,not_done}/0auto_*.jpg) so eval_detectors.py works at once."""
    media = store.media_dir(recipe.id)
    try:
        from skill_verify import TEACH_META_FILENAME, export_eval_set

        if (media / TEACH_META_FILENAME).is_file():
            counts = export_eval_set(media, recipe, default_evals_root(BACKEND_DIR))
            logger.info("teach eval set for %s: %s", recipe.id, counts)
    except Exception:  # noqa: BLE001 - never fail a save because of the eval export
        logger.exception("teach eval export failed for %s", recipe.id)


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request) -> dict[str, Any]:
    if not _store(request).delete(skill_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy kỹ năng.")
    return {"ok": True}


@app.get("/skills-media/{skill_id}/{rel_path:path}")
async def skill_media(skill_id: str, rel_path: str, request: Request) -> FileResponse:
    # Equivalent to a StaticFiles mount on backend/skills, but resolved per request
    # (skills dir is configurable) and restricted to safe relative paths.
    path = _store(request).media_path(skill_id, rel_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    return FileResponse(path)


# ---- REST: teach / ask -------------------------------------------------------


@app.post("/api/teach/compile")
async def teach_compile(request: Request, body: dict[str, Any] = Body(...)) -> Any:
    try:
        from skill_compiler import CompileRequest, compile_skill  # lazy: built in parallel
    except ImportError as error:
        logger.exception("skill_compiler unavailable")
        raise HTTPException(
            status_code=503, detail=f"Bộ học kỹ năng chưa sẵn sàng: {error}"
        )
    try:
        req = CompileRequest.model_validate(body)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"Dữ liệu dạy không hợp lệ: {error}")
    job_id = _job_key(req.job_id)
    jobs = _teach_jobs(request)

    def on_phase(phase: str, **info: Any) -> None:
        if job_id:
            jobs[job_id] = {"phase": phase, **info, "updated": time.monotonic()}

    on_phase("learning")
    task = asyncio.create_task(
        compile_skill(
            req,
            provider=_provider(request),
            model=compiler_model(),
            skills_root=_store(request).root,
            on_phase=on_phase,
        )
    )
    try:
        # A real compile takes 10-30 s. If the teacher cancels (the browser aborts the
        # request), stop the model call and drop the draft instead of finishing for nobody.
        while not task.done():
            await asyncio.wait({task}, timeout=1.0)
            if not task.done() and await request.is_disconnected():
                task.cancel()
                with suppress(BaseException):
                    await task
                logger.info("teach compile cancelled by client")
                raise HTTPException(status_code=499, detail="Đã huỷ.")
        result = task.result()
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Không học được kỹ năng: {error}")
    except Exception:
        # Details (upstream LLM response bodies, URLs) go to the log only, never to the client.
        logger.exception("compile_skill failed")
        raise HTTPException(status_code=502, detail="AI học kỹ năng thất bại. Thử lại sau nhé.")
    finally:
        if job_id:
            jobs.pop(job_id, None)
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    return result


_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
_MAX_TEACH_JOBS = 200


def _job_key(value: str | None) -> str:
    return value if value and _JOB_ID_RE.match(value) else ""


def _teach_jobs(request: Request) -> dict[str, dict[str, Any]]:
    jobs = getattr(request.app.state, "teach_jobs", None)
    if jobs is None:
        jobs = request.app.state.teach_jobs = {}
    if len(jobs) > _MAX_TEACH_JOBS:  # bounded: drop the stalest entries
        for key in sorted(jobs, key=lambda k: jobs[k].get("updated", 0))[: len(jobs) // 2]:
            jobs.pop(key, None)
    return jobs


@app.get("/api/teach/progress/{job_id}")
async def teach_progress(job_id: str, request: Request) -> dict[str, Any]:
    """Phase of a running compile ("learning" → "verifying") for the 2-phase loading screen."""
    info = _teach_jobs(request).get(_job_key(job_id))
    if info is None:
        return {"phase": "unknown"}
    return {k: v for k, v in info.items() if k != "updated"}


def _teach_media_root(store: SkillStore, draft_id: str | None, skill_id: str | None) -> Path:
    from skill_store import sanitize_draft_id, slugify_id

    if draft_id and sanitize_draft_id(draft_id):
        root = store.draft_dir(draft_id)
    elif skill_id and slugify_id(skill_id):
        root = store.media_dir(skill_id)
    else:
        raise HTTPException(status_code=422, detail="Cần draft_id hoặc skill_id.")
    if not (root / "frames").is_dir():
        raise HTTPException(status_code=404, detail="Không tìm thấy khung hình của buổi dạy.")
    return root


@app.get("/api/teach/frames")
async def teach_frames(request: Request, draft_id: str | None = None, skill_id: str | None = None) -> dict[str, Any]:
    """Every frame captured while teaching (for the reference-frame picker in Review)."""
    from skill_verify import read_teach_frames

    store = _store(request)
    root = _teach_media_root(store, draft_id, skill_id)
    prefix = f"/skills-media/_drafts/{root.name}" if draft_id else f"/skills-media/{root.name}"
    return {
        "frames": [
            {"index": f.index, "t": f.t, "reason": f.reason, "file": f"frames/{f.file}", "url": f"{prefix}/frames/{f.file}"}
            for f in read_teach_frames(root)
        ]
    }


class VerifyStepRequest(BaseModel):
    draft_id: str | None = Field(None, max_length=64)
    skill_id: str | None = Field(None, max_length=80)
    step: str | int  # step id ("step_2") or 0-based index
    # the caller's working copy of the skill (e.g. edited in Review); default = the saved skill
    skill: dict[str, Any] | None = None
    # index into the teach frames (GET /api/teach/frames) to use as this step's new reference frame
    reference_frame: int | None = Field(None, ge=0, le=200)
    rewrite: bool = True  # let the AI rewrite the detector prompt once if the check fails
    save: bool = False  # skill_id only: persist the result into the saved skill (and its eval set)


@app.post("/api/teach/verify-step")
async def teach_verify_step(payload: VerifyStepRequest, request: Request) -> dict[str, Any]:
    """Re-run the teach self-check for one step (optionally with another reference frame).
    Returns the step patch (reference image + `verify`), its detector (maybe rewritten) and `quality`."""
    import copy

    from recipe_catalog import Recipe
    from skill_verify import read_teach_frames, verify_one_step

    store = _store(request)
    root = _teach_media_root(store, payload.draft_id, payload.skill_id)
    if payload.skill is not None:
        skill = copy.deepcopy(payload.skill)
    elif payload.skill_id:
        try:
            skill = store.get_dict(payload.skill_id)
        except SkillNotFound:
            raise HTTPException(status_code=404, detail="Không tìm thấy kỹ năng.")
    else:
        raise HTTPException(status_code=422, detail="Thiếu dữ liệu kỹ năng.")
    try:
        Recipe.model_validate(skill)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Kỹ năng không hợp lệ: {error}")
    steps = skill.get("steps") or []
    if isinstance(payload.step, int) and not isinstance(payload.step, bool):
        index = payload.step
    else:
        index = next((i for i, s in enumerate(steps) if s.get("id") == payload.step), -1)
    if not 0 <= index < len(steps):
        raise HTTPException(status_code=404, detail="Không tìm thấy bước.")
    step = steps[index]
    media_prefix = f"/skills-media/_drafts/{root.name}" if payload.draft_id else f"/skills-media/{root.name}"
    if payload.reference_frame is not None:
        frames = read_teach_frames(root)
        if payload.reference_frame >= len(frames):
            raise HTTPException(status_code=422, detail="Không có khung hình này.")
        rel = f"frames/{frames[payload.reference_frame].file}"
        step["reference_image"] = rel
        step["reference_image_url"] = f"{media_prefix}/{rel}"
        if index == len(steps) - 1:
            skill["cover_image"] = rel
    key = step.get("detector_key")
    provider = _provider(request)
    if provider.mode != "ai":
        summary = {
            "checked": 0, "agree": 0, "status": "unchecked",
            "reason": "Chế độ Demo: cần AI thật để tự kiểm tra bước này.",
        }
        step["verify"] = summary
    else:
        started = time.perf_counter()
        summary = await verify_one_step(
            skill, index, provider=provider, media_root=root,
            vision_model=vision_model(), compiler_model=compiler_model(),
            allow_rewrite=payload.rewrite,
        )
        logger.info("teach verify-step %s in %.1fs: %s", step.get("id"), time.perf_counter() - started, summary)
    if payload.save and payload.skill_id and not payload.draft_id:
        try:
            saved = store.save({**skill, "id": payload.skill_id})
        except SkillValidationError as error:
            raise HTTPException(status_code=422, detail=f"Kỹ năng không hợp lệ: {error}")
        _export_teach_eval_set(store, saved)
    return {
        "step_id": step.get("id"),
        "step": {
            "reference_image": step.get("reference_image"),
            "reference_image_url": step.get("reference_image_url"),
            "verify": summary,
        },
        "verify": summary,
        "detector_key": key,
        "detector": skill["detectors"].get(key),
        "quality": skill.get("quality"),
        "saved": bool(payload.save and payload.skill_id and not payload.draft_id),
    }


class AskRequest(BaseModel):
    skill_id: str = Field(..., max_length=80)
    step_id: str | None = Field(None, max_length=80)
    question: str = Field(..., max_length=500)
    image: str | None = Field(None, max_length=MAX_FRAME_DATA_URL_CHARS)


@app.post("/api/ask")
async def ask(payload: AskRequest, request: Request) -> dict[str, str]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Câu hỏi đang trống.")
    try:
        answer, source = await _manager(request).answer_question_with_source(
            skill_id=payload.skill_id,
            step_id=payload.step_id,
            question=question,
            image=payload.image,
        )
    except SkillNotFound:
        raise HTTPException(status_code=404, detail="Không tìm thấy kỹ năng.")
    # source: "ai" | "mock" (demo mode) | "fallback" (AI failed → answer from the skill text)
    return {"answer": answer, "source": source}


# ---- Eval capture (labelled frames for eval_detectors.py) ---------------------


class EvalCaptureRequest(BaseModel):
    skill_id: str = Field(..., max_length=80)
    step_id: str = Field(..., max_length=80)
    label: str = Field(..., max_length=16)  # "done" | "not_done"
    image: str = Field(..., max_length=MAX_FRAME_DATA_URL_CHARS)


def _eval_store() -> EvalStore:
    return EvalStore(default_evals_root(BACKEND_DIR))


@app.post("/api/eval/capture")
async def eval_capture(payload: EvalCaptureRequest) -> dict[str, Any]:
    store = _eval_store()
    try:
        store.save(payload.skill_id, payload.step_id, payload.label, payload.image)
        counts = store.counts(payload.skill_id)
    except EvalError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return {"ok": True, "counts": counts}


@app.get("/api/eval/{skill_id}")
async def eval_counts(skill_id: str) -> dict[str, Any]:
    try:
        return {"counts": _eval_store().counts(skill_id)}
    except EvalError as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.delete("/api/eval/{skill_id}/{step_id}/{label}/last")
async def eval_undo(skill_id: str, step_id: str, label: str) -> dict[str, Any]:
    store = _eval_store()
    try:
        removed = store.delete_last(skill_id, step_id, label)
        counts = store.counts(skill_id)
    except EvalError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return {"removed": removed, "counts": counts}


# ---- WebSocket ---------------------------------------------------------------


@app.websocket("/session/control")
async def session_control(websocket: WebSocket) -> None:
    current: ShadowSessionManager = websocket.app.state.manager
    await websocket.accept()
    session_id = await current.create_control_session(websocket)

    try:
        while True:
            try:
                payload = await websocket.receive_json()
            except (ValueError, KeyError, TypeError):  # bad JSON or a binary frame
                await websocket.send_json(
                    {"type": "hud.error", "message": "Tin nhắn phải là JSON hợp lệ."}
                )
                continue
            if not isinstance(payload, dict):
                await websocket.send_json(
                    {
                        "type": "hud.error",
                        "message": "Tin nhắn điều khiển phải là một đối tượng JSON.",
                    }
                )
                continue
            await current.handle_control_message(session_id, payload)
    except WebSocketDisconnect:
        pass
    finally:
        await current.destroy_session(
            session_id, reason="control websocket disconnected"
        )


# ---- Built-in skill "Trợ lý tủ đồ": wardrobe / weather / stylist / try-on ------

from wardrobe_api import router as wardrobe_router  # noqa: E402

app.include_router(wardrobe_router)
app.include_router(intent_api)
app.include_router(conversation_api)
app.include_router(voice_api)
app.include_router(image_api)
app.include_router(vidu_api)


# ---- Static web app (mounted last so it never shadows API routes) -------------

app.mount(
    "/",
    StaticFiles(directory=WEB_DIR, html=True, check_dir=False),
    name="web",
)


if __name__ == "__main__":
    import uvicorn

    # ws_max_size: frames are ~50-150KB base64 (server rejects > MAX_FRAME_DATA_URL_CHARS ≈ 4 MB).
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        ws_max_size=6 * 1024 * 1024,
    )

"""Persisted jobs survive client disconnects. Explicit retry avoids duplicate billed calls."""
import asyncio
import json
import os
import shutil
from pathlib import Path
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .storage import JobStore
from .processor import MAX_BYTES, MAX_SECONDS, process

router = APIRouter(prefix="/api/teach/video")


class StartRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    teacher_name: str = Field("", max_length=100)
    notes: str = Field("", max_length=4000)


class VideoJobs:
    def __init__(self, root, provider, voice, skills):
        self.store = JobStore(root)
        self.provider, self.voice, self.skills = provider, voice, skills
        self.tasks = {}
        self.lock = asyncio.Semaphore(1)
        # A dedicated file lock disallows two workers consuming this directory.
        import fcntl
        self.worker_lock = (root / "worker.lock").open("a")
        fcntl.flock(self.worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.store.recover()
        for job in self.store.all():
            if job["status"] == "queued":
                self.start(job["job_id"])

    def start(self, ident):
        if ident not in self.tasks:
            self.tasks[ident] = asyncio.create_task(self.run(ident))

    async def run(self, ident):
        try:
            async with self.lock:
                await process(self.store.get(ident), self.store, self.provider, self.voice, self.skills)
        except asyncio.CancelledError:
            if self.store.get(ident)["status"] != "cancelled":
                self.store.update(ident, status="interrupted", error="Tác vụ bị gián đoạn. Có thể thử lại.")
            raise
        except Exception as error:
            # Never expose provider responses/URLs or private video paths.
            message = str(error) if isinstance(error, ValueError) else "Xử lý video thất bại. Kiểm tra cấu hình AI/giọng nói rồi thử lại."
            self.store.update(ident, status="failed", error=message)
        finally:
            self.tasks.pop(ident, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.worker_lock.close()


def service(request):
    jobs = getattr(request.app.state, "video_jobs", None)
    if jobs is None:
        raise HTTPException(503, "Học từ video chưa được bật trên máy chủ.")
    return jobs


def authorized(request, ident):
    svc = service(request)
    token = request.headers.get("x-shadow-job-token", "")
    if not token:
        raise HTTPException(404, "Không tìm thấy tác vụ.")
    try:
        return svc, svc.store.get(ident, token)
    except KeyError:
        raise HTTPException(404, "Không tìm thấy tác vụ.") from None


@router.get("/config")
def config(request: Request):
    svc = getattr(request.app.state, "video_jobs", None)
    return {"available": svc is not None, "max_bytes": MAX_BYTES, "max_seconds": MAX_SECONDS,
            "transcription_available": svc is not None and svc.voice is not None}


@router.post("/uploads", status_code=201)
async def upload(request: Request):
    svc = service(request)
    if request.headers.get("content-type", "").split(";")[0] not in ("video/mp4", "video/quicktime", "video/webm", "application/octet-stream"):
        raise HTTPException(415, "Cần video MP4, MOV hoặc WebM.")
    # Bound disk usage even when many authenticated clients upload concurrently.
    active = [j for j in svc.store.all() if j["status"] not in ("deleted",)]
    if len(active) >= 30:
        raise HTTPException(429, "Kho video thử nghiệm đã đầy. Xóa tác vụ/video không cần trước khi tải thêm.")
    job, token = svc.store.create()
    ident = job["job_id"]
    target = svc.store.directory(ident) / "source.video"
    count = 0
    try:
        with target.open("wb") as file:
            async for chunk in request.stream():
                count += len(chunk)
                if count > MAX_BYTES:
                    raise HTTPException(413, "Video vượt giới hạn 32 MB.")
                file.write(chunk)
        if count == 0:
            raise HTTPException(422, "Video trống.")
        svc.store.update(ident, status="uploaded", bytes=count)
        return {"job_id": ident, "token": token, "status": "uploaded"}
    except BaseException:
        target.unlink(missing_ok=True)
        svc.store.update(ident, status="deleted")
        raise


@router.post("/jobs/{ident}/start", status_code=202)
async def start(ident: str, payload: StartRequest, request: Request):
    svc, job = authorized(request, ident)
    if job["status"] != "uploaded":
        return {"job_id": ident, "status": job["status"]}  # idempotent: no second job
    if not payload.title.strip():
        raise HTTPException(422, "Cần tên kỹ năng.")
    svc.store.update(ident, status="queued", **payload.model_dump())
    svc.start(ident)
    return {"job_id": ident, "status": "queued"}


@router.get("/jobs/{ident}")
def status(ident: str, request: Request):
    svc, job = authorized(request, ident)
    result = {k: job.get(k) for k in ("job_id", "status", "progress", "error", "duration_seconds", "warnings")}
    if job["status"] == "ready":
        result["result"] = json.loads((svc.store.directory(ident) / "result.json").read_text())
    return result


@router.post("/jobs/{ident}/retry", status_code=202)
async def retry(ident: str, payload: StartRequest, request: Request):
    svc, job = authorized(request, ident)
    if job["status"] not in ("failed", "interrupted", "cancelled") or not (svc.store.directory(ident) / "source.video").exists():
        raise HTTPException(409, "Tác vụ này không thể thử lại.")
    if not payload.title.strip():
        raise HTTPException(422, "Cần tên kỹ năng.")
    svc.store.update(ident, status="queued", error=None, **payload.model_dump())
    svc.start(ident)
    return {"job_id": ident, "status": "queued"}


@router.post("/jobs/{ident}/cancel")
async def cancel(ident: str, request: Request):
    svc, job = authorized(request, ident)
    if job["status"] == "ready":
        return {"job_id": ident, "status": "ready"}
    svc.store.update(ident, status="cancelled")
    task = svc.tasks.get(ident)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    return {"job_id": ident, "status": "cancelled"}


@router.delete("/jobs/{ident}")
async def delete(ident: str, request: Request):
    svc, job = authorized(request, ident)
    if job["status"] == "deleted":
        return {"ok": True}
    await cancel(ident, request)
    with suppress(FileNotFoundError):
        shutil.rmtree(svc.store.directory(ident))
    svc.store.update(ident, status="deleted")
    return {"ok": True}


@router.get("/jobs/{ident}/source")
def source(ident: str, request: Request):
    svc, _ = authorized(request, ident)
    path = svc.store.directory(ident) / "source.video"
    if not path.exists():
        raise HTTPException(404, "Video đã bị xóa.")
    return FileResponse(path, media_type="application/octet-stream", filename="teaching-video", headers={"Cache-Control": "no-store"})

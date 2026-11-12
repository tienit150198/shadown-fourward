import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from teach_video.storage import JobStore
from teach_video.processor import inspect


def test_jobs_are_private_and_survive_restart(tmp_path):
    store = JobStore(tmp_path)
    job, token = store.create()
    ident = job["job_id"]
    with pytest.raises(KeyError):
        store.get(ident, "wrong-token")
    store.update(ident, status="learning")
    reopened = JobStore(tmp_path)
    reopened.recover()
    assert reopened.get(ident, token)["status"] == "interrupted"
    assert token not in reopened.db.read_bytes().decode(errors="ignore")


def make_video(path):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-c:v", "mpeg4", str(path)], check=True)


@pytest.mark.asyncio
async def test_real_video_probe_and_invalid_media(tmp_path):
    video = tmp_path / "input.mp4"
    make_video(video)
    duration, audio = await inspect(video)
    assert 0 < duration <= 2
    assert not audio
    video.write_bytes(b"not a video")
    with pytest.raises(ValueError):
        await inspect(video)


def test_video_job_upload_process_reopen_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("SHADOW_VIDEO_TEACH_ENABLED", "1")
    monkeypatch.setenv("SHADOW_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("SHADOW_RATE_LIMIT", "off")
    import main
    video = tmp_path / "input.mp4"
    make_video(video)
    with TestClient(main.app) as client:
        assert client.get("/api/teach/video/config").json()["available"]
        uploaded = client.post("/api/teach/video/uploads", content=video.read_bytes(), headers={"Content-Type": "video/mp4"})
        assert uploaded.status_code == 201, uploaded.text
        ticket = uploaded.json()
        route = "/api/teach/video/jobs/" + ticket["job_id"]
        headers = {"x-shadow-job-token": ticket["token"]}
        assert client.get(route).status_code == 404
        assert client.get(route, headers={"x-shadow-job-token": "wrong"}).status_code == 404
        body = {"title": "Gấp quần", "notes": "Trải quần phẳng. Gấp đôi theo chiều dọc. Gấp gọn."}
        assert client.post(route + "/start", headers=headers, json=body).status_code == 202
        assert client.post(route + "/start", headers=headers, json=body).status_code == 202
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            result = client.get(route, headers=headers).json()
            if result["status"] in ("ready", "failed"):
                break
            time.sleep(0.05)
        assert result["status"] == "ready", result
        assert result["result"]["compiled_by"] == "mock"
        assert result["result"]["skill"]["steps"]
        assert client.get(route + "/source", headers=headers).status_code == 200
    # Restart: the finished result and token remain valid without re-running AI.
    with TestClient(main.app) as client:
        assert client.get(route, headers=headers).json()["status"] == "ready"
        assert client.delete(route, headers=headers).status_code == 200
        assert client.delete(route, headers=headers).status_code == 200
        assert client.get(route + "/source", headers=headers).status_code == 404


def test_disabled_video_api_does_not_accept_uploads(tmp_path, monkeypatch):
    monkeypatch.delenv("SHADOW_VIDEO_TEACH_ENABLED", raising=False)
    monkeypatch.setenv("SHADOW_SKILLS_DIR", str(tmp_path / "skills"))
    import main
    with TestClient(main.app) as client:
        assert not client.get("/api/teach/video/config").json()["available"]
        assert client.post("/api/teach/video/uploads", content=b"x").status_code == 503

@pytest.mark.asyncio
async def test_detector_refresh_uses_new_instruction_and_requires_ai(tmp_path):
    from teach_video.detector import refresh
    from fastapi import HTTPException
    skill = {"steps": [{"instruction": "Gấp theo chiều ngang", "detector_key": "d", "verify": {"status": "ok"}}],
             "detectors": {"d": {"field": "flag", "prompt": "old vertical criterion"}}, "quality": {"score": 1}}
    class Fake:
        mode = "ai"
        async def chat_json(self, **kwargs):
            assert "Gấp theo chiều ngang" in kwargs["content"][0]["text"]
            return {"prompt": "Return JSON flag true only when the trousers are visibly folded horizontally; hidden means false."}
    await refresh(skill, 0, Fake(), tmp_path)
    assert "horizontally" in skill["detectors"]["d"]["prompt"]
    assert "verify" not in skill["steps"][0]
    assert "quality" not in skill
    provider = Fake()
    provider.mode = "mock"
    with pytest.raises(HTTPException) as error:
        await refresh(skill, 0, provider, tmp_path)
    assert error.value.status_code == 503

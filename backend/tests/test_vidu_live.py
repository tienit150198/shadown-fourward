import asyncio
import io
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import vidu_live
from wardrobe_store import jpeg_data_url


def reference_image():
    image = io.BytesIO()
    Image.new("RGB", (64, 128), "blue").save(image, format="JPEG")
    return jpeg_data_url(image.getvalue())


def vidu_response():
    return {
        "live": {"id": "995200676634042368", "status": "waiting", "live_duration": 30},
        "rtc": {
            "app_id": "app-id",
            "channel_id": "channel",
            "user_id": "100",
            "token": "rtc-token",
            "provider": "agora",
            "provider_only": "discard-me",
        },
        "client_secret": "scoped-session-secret",
        "render_uid": "101",
        "provider_only": "discard-me",
    }


def test_session_request_and_scoped_response(monkeypatch):
    monkeypatch.setenv("VIDU_API_KEY", "vda_backend-only")
    monkeypatch.setenv("VIDU_API_BASE_URL", "https://api.vidu.cn/")
    monkeypatch.setenv("VIDU_MAX_SESSION_SECONDS", "50")

    def provider(request):
        assert request.url == "https://api.vidu.cn/live/s_editing/realtime"
        assert request.headers["authorization"] == "vda_backend-only"
        assert len(request.headers["x-request-id"]) == 36
        body = json.loads(request.content)
        assert body["image_url"].startswith("data:image/jpeg;base64,")
        assert body["editing_type"] == "virtual_tryon"
        assert body["managed_rtc"] == {"provider": "agora", "enable_recording": False}
        return httpx.Response(200, json=vidu_response())

    result = asyncio.run(vidu_live.create_live_session(reference_image(), transport=httpx.MockTransport(provider)))
    assert result["ws_url"] == "wss://api.vidu.cn/live/ws/live/connect"
    assert result["live_id"] == "995200676634042368"
    assert result["max_session_seconds"] == 30
    assert result["client_secret"] == "scoped-session-secret"
    assert result["render_uid"] == "101"
    assert result["rtc"]["token"] == "rtc-token"
    assert "backend-only" not in json.dumps(result)
    assert "discard-me" not in json.dumps(result)


@pytest.mark.parametrize("status, body, expected", [
    (400, {"reason": "CreditInsufficient", "message": "private upstream data"}, 503),
    (401, {"message": "private upstream data"}, 503),
    (429, {}, 429),
    (503, {}, 503),
    (200, {"live": None}, 502),
])
def test_upstream_errors_do_not_reflect_provider_body(monkeypatch, status, body, expected):
    monkeypatch.setenv("VIDU_API_KEY", "backend-only")
    monkeypatch.delenv("VIDU_API_BASE_URL", raising=False)

    def provider(request):
        assert request.url.host == "api.vidu.com"
        assert request.headers["authorization"] == "vda_backend-only"
        return httpx.Response(status, json=body)

    with pytest.raises(vidu_live.ViduLiveError) as exc:
        asyncio.run(vidu_live.create_live_session(reference_image(), transport=httpx.MockTransport(provider)))
    assert exc.value.status == expected
    assert "private upstream data" not in str(exc.value)


def test_route_rejects_external_reference_and_reports_unconfigured_key(monkeypatch):
    app = FastAPI()
    app.include_router(vidu_live.router)
    client = TestClient(app)
    path = "/api/tryon/live-session"

    monkeypatch.delenv("VIDU_API_KEY", raising=False)
    assert vidu_live.live_config()["available"] is False
    result = client.post(path, json={"reference_image": reference_image()})
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "NO_VIDU_KEY"

    monkeypatch.setenv("VIDU_API_KEY", "backend-only")
    monkeypatch.delenv("VIDU_API_BASE_URL", raising=False)
    assert vidu_live.live_config()["available"] is True
    result = client.post(path, json={"reference_image": "https://example.com/image.jpg"})
    assert result.status_code == 422


def test_route_marks_session_credentials_no_store(monkeypatch):
    async def fake_session(image):
        assert image == reference_image()
        return {"live_id": "live-id", "client_secret": "short-lived"}

    monkeypatch.setattr(vidu_live, "create_live_session", fake_session)
    app = FastAPI()
    app.include_router(vidu_live.router)
    result = TestClient(app).post("/api/tryon/live-session", json={"reference_image": reference_image()})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"

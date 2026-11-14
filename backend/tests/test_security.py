"""Regression tests for the internet-exposure hardening (security.py + related fixes)."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def make_client(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from main import app

    return TestClient(app)


@pytest.fixture
def gated(monkeypatch, skills_dir):
    with make_client(monkeypatch, SHADOW_ACCESS_CODE="hoa-sen-42") as client:
        yield client


@pytest.fixture
def limited(monkeypatch, skills_dir):
    with make_client(monkeypatch, SHADOW_RATE_LIMIT="on") as client:
        yield client


def png_bytes(size=(64, 64), mode="RGB") -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, 0).save(buf, format="PNG")
    return buf.getvalue()


# ---- path traversal / media ------------------------------------------------------------------


def test_skill_media_only_serves_images_inside_the_skill_dir(client, skills_dir):
    assert client.get("/skills-media/test-skill/frames/f01.jpg").status_code == 200
    # empty slug ("--") used to map to the skills root → skill JSON readable as "media"
    assert client.get("/skills-media/--/test-skill.json").status_code == 404
    (skills_dir / "test-skill" / "notes.txt").write_text("x")
    assert client.get("/skills-media/test-skill/notes.txt").status_code == 404
    assert client.get("/skills-media/test-skill/..%2Ftest-skill.json").status_code == 404


def test_skill_media_refuses_symlink_escape(client, skills_dir, tmp_path):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xffsecret")
    (skills_dir / "test-skill" / "frames" / "leak.jpg").symlink_to(secret)
    assert client.get("/skills-media/test-skill/frames/leak.jpg").status_code == 404


def test_skill_store_media_path_rejects_unsafe(skills_dir):
    from skill_store import SkillStore

    store = SkillStore(skills_dir)
    assert store.media_path("test-skill", "frames/f01.jpg") is not None
    for rel in ("../test-skill.json", "/etc/passwd", "frames/../../x.jpg", "frames\\f01.jpg", "frames/f01.jpg\x00"):
        assert store.media_path("test-skill", rel) is None
    assert store.media_path("", "test-skill.json") is None


def test_wardrobe_media_refuses_non_jpeg_and_symlinks(monkeypatch, tmp_path, client):
    root = tmp_path / "wardrobe"
    (root / "images").mkdir(parents=True)
    (root / "items.json").write_text("[]")
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xffsecret")
    (root / "images" / "leak.jpg").symlink_to(secret)
    monkeypatch.setenv("SHADOW_WARDROBE_DIR", str(root))
    assert client.get("/wardrobe-media/items.json").status_code == 404
    assert client.get("/wardrobe-media/images/leak.jpg").status_code == 404


# ---- secret / detail leakage -------------------------------------------------------------------


def test_compile_failure_does_not_leak_upstream_error(client, monkeypatch):
    import skill_compiler
    from providers import ProviderError

    async def boom(*args, **kwargs):
        raise ProviderError("LLM HTTP 401: invalid api key sk-or-v1-SECRET for https://gw/v1")

    monkeypatch.setattr(skill_compiler, "compile_skill", boom)
    response = client.post("/api/teach/compile", json={"title": "x"})
    assert response.status_code == 502
    assert "SECRET" not in response.text and "https://gw" not in response.text


def test_tryon_errors_are_generic(monkeypatch):
    from tryon import TryOnService

    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")
    monkeypatch.setenv("TRYON_WORKER_URL", "http://10.0.0.7:8188")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        raise httpx.ConnectError("connect to 10.0.0.7 failed token=abc", request=request)

    def broken_hf(*args):
        raise RuntimeError("Space https://hf.co/x?token=hf_SECRET exploded")

    from wardrobe_store import jpeg_data_url, to_jpeg

    person = jpeg_data_url(to_jpeg(png_bytes((300, 400))))
    garment = to_jpeg(png_bytes((200, 200)))
    item = {"id": "t", "category": "top", "name": "Áo", "description_en": "white shirt"}
    service = TryOnService(worker_transport=httpx.MockTransport(handler), hf_runner=broken_hf)
    result = asyncio.run(service.run(person, [item], {"t": garment}))
    assert result["provider"] == "mock"
    reason = result["fallback_reason"]
    assert "worker" in reason and "hf_space" in reason
    assert "10.0.0.7" not in reason and "token" not in reason and "SECRET" not in reason


def test_health_exposes_no_secrets(client, monkeypatch):
    body = client.get("/api/health").text
    for name in ("LLM_API_KEY", "DECART_API_KEY", "HF_TOKEN"):
        value = os.environ.get(name)
        if value:
            assert value not in body
    assert set(json.loads(body)) == {"ok", "mode", "vision_model", "compiler_model", "access_required"}


# ---- Decart cost guard ------------------------------------------------------------------------


def test_live_token_always_sends_session_cap(monkeypatch):
    from tryon import create_live_token

    monkeypatch.setenv("DECART_API_KEY", "sk-test")
    monkeypatch.delenv("DECART_TOKEN_TTL_S", raising=False)
    monkeypatch.delenv("DECART_MAX_SESSION_S", raising=False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"apiKey": "ek_1"})

    result = asyncio.run(create_live_token(transport=httpx.MockTransport(handler)))
    assert seen["body"] == {"expiresIn": 60, "constraints": {"realtime": {"maxSessionDuration": 180}}}
    assert result["max_session_seconds"] == 180

    for raw, expected in (("0", 180), ("-5", 180), ("99999", 1800), ("240", 240)):
        monkeypatch.setenv("DECART_MAX_SESSION_S", raw)
        asyncio.run(create_live_token(transport=httpx.MockTransport(handler)))
        assert seen["body"]["constraints"]["realtime"]["maxSessionDuration"] == expected


# ---- payload caps / DoS -----------------------------------------------------------------------


def test_body_size_cap(monkeypatch, skills_dir):
    with make_client(monkeypatch, SHADOW_MAX_BODY_MB="1") as client:
        big = {"skill_id": "test-skill", "question": "x", "image": "data:image/jpeg;base64," + "A" * (2 * 1024 * 1024)}
        response = client.post("/api/ask", json=big)
        assert response.status_code == 413
        # chunked upload without Content-Length is cut off too
        def gen():
            for _ in range(3):
                yield b"A" * (512 * 1024)
        response = client.post("/api/wardrobe/rules", content=gen(), headers={"content-type": "application/json"})
        assert response.status_code == 413


def test_ask_and_compile_field_caps(client):
    assert client.post("/api/ask", json={"skill_id": "test-skill", "question": "x" * 501}).status_code == 422
    frames = [{"t": i, "image": "data:image/jpeg;base64,AAAA"} for i in range(65)]
    assert client.post("/api/teach/compile", json={"title": "x", "frames": frames}).status_code == 422
    assert client.post("/api/teach/compile", json={"title": "x" * 201}).status_code == 422


def test_to_jpeg_rejects_decompression_bomb():
    from wardrobe_store import WardrobeError, to_jpeg

    bomb = png_bytes((9000, 9000), mode="1")  # 81 Mpx declared, tiny file
    assert len(bomb) < 1024 * 1024
    with pytest.raises(WardrobeError):
        to_jpeg(bomb)
    assert to_jpeg(png_bytes((50, 50)))[:3] == b"\xff\xd8\xff"


def test_pillow_threshold_lowered():
    import main  # noqa: F401  (configures Pillow on import)
    from PIL import Image

    import security

    assert Image.MAX_IMAGE_PIXELS == security.MAX_IMAGE_PIXELS


def test_wardrobe_item_count_cap(tmp_path, monkeypatch):
    import wardrobe_store
    from wardrobe_store import WardrobeError, WardrobeStore, to_jpeg

    monkeypatch.setattr(wardrobe_store, "MAX_ITEMS", 2)
    store = WardrobeStore(tmp_path / "w")
    jpeg = to_jpeg(png_bytes())
    store.create_item({"name": "a"}, jpeg)
    store.create_item({"name": "b"}, jpeg)
    with pytest.raises(WardrobeError):
        store.create_item({"name": "c"}, jpeg)


# ---- rate limits / concurrency -------------------------------------------------------------------


def test_live_token_is_rate_limited_per_ip(limited):
    statuses = [limited.post("/api/tryon/live-token").status_code for _ in range(5)]
    assert statuses[:3] == [503, 503, 503]  # no DECART_API_KEY in tests → 503, but counted
    assert statuses[-1] == 429
    assert limited.post("/api/tryon/live-token").json()["code"] == "rate_limited"


def test_failed_session_requests_do_not_use_the_hourly_budget(monkeypatch, skills_dir):
    with make_client(monkeypatch, SHADOW_RATE_LIMIT="on", SHADOW_LIVE_TOKENS_PER_HOUR="2") as client:
        assert [client.post("/api/tryon/live-token").status_code for _ in range(3)] == [503, 503, 503]
        # Failed requests still obey the short-term per-IP limiter.
        assert client.post("/api/tryon/live-token").json()["code"] == "rate_limited"


def test_chat_and_tryon_have_separate_budgets_and_failed_reservations_are_released(monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from security import GuardState, SecurityMiddleware
    monkeypatch.setenv("SHADOW_RATE_LIMIT", "on")
    monkeypatch.setenv("SHADOW_LIVE_TOKENS_PER_HOUR", "1")
    monkeypatch.setenv("SHADOW_CHAT_SESSIONS_PER_HOUR", "1")
    monkeypatch.delenv("SHADOW_ACCESS_CODE", raising=False)
    app = FastAPI()
    status = [503]
    @app.post("/api/live/session")
    @app.post("/api/tryon/xmax/session")
    async def issue():
        return JSONResponse({"ok": status[0] == 200}, status_code=status[0])
    state = GuardState()
    with TestClient(SecurityMiddleware(app, state)) as client:
        assert client.post("/api/live/session").status_code == 503
        assert not state.session_slots["chat_session"]
        status[0] = 200
        assert client.post("/api/tryon/xmax/session").status_code == 200
        assert client.post("/api/tryon/xmax/session").json()["code"] == "quota"
        # A full try-on budget must not stop voice on the same phone/IP.
        assert client.post("/api/live/session").status_code == 200
        blocked = client.post("/api/live/session")
        assert blocked.status_code == 429 and blocked.json()["code"] == "chat_quota"
        assert 0 < int(blocked.headers["retry-after"]) <= 3600


def test_inflight_sessions_reserve_slots_until_failure_or_hourly_expiry(monkeypatch):
    import security
    monkeypatch.setenv("SHADOW_RATE_LIMIT", "on")
    monkeypatch.setenv("SHADOW_CHAT_SESSIONS_PER_HOUR", "1")
    clock = [100.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: clock[0])
    state = security.GuardState()
    first = state.reserve_session("chat_session")
    assert first is not None
    assert state.reserve_session("chat_session") is None
    state.release_session("chat_session", first)
    assert state.reserve_session("chat_session") is not None
    clock[0] += 3600
    assert state.reserve_session("chat_session") is not None


def test_session_per_ip_bursts_are_independent(limited):
    for _ in range(3):
        assert limited.post("/api/tryon/live-token").status_code == 503
    assert limited.post("/api/tryon/live-token").json()["code"] == "rate_limited"
    assert limited.post("/api/live/session", json={}).status_code == 503


def test_ask_rate_limit_and_get_not_limited_by_llm_bucket(limited):
    codes = [
        limited.post("/api/ask", json={"skill_id": "test-skill", "question": "?"}).status_code
        for _ in range(12)
    ]
    assert codes[0] == 200 and 429 in codes
    assert limited.get("/api/skills").status_code == 200


def test_tryon_photo_concurrency_cap(client):
    import security

    security.GUARD.tryon_active = security.GUARD.tryon_max
    try:
        response = client.post("/api/tryon/photo", json={"person_image": "x", "item_ids": ["a"]})
        assert response.status_code == 429 and response.json()["code"] == "busy"
    finally:
        security.GUARD.tryon_active = 0


def test_rate_limiter_refills_and_bounds_memory(monkeypatch):
    import security

    limiter = security.RateLimiter({"x": (60, 2)})
    assert limiter.allow("x", "ip") and limiter.allow("x", "ip") and not limiter.allow("x", "ip")
    monkeypatch.setattr(security.RateLimiter, "MAX_KEYS", 50)
    for i in range(500):
        limiter.allow("x", f"ip{i}")
    assert len(limiter._buckets) <= 50


# ---- access gate ----------------------------------------------------------------------------------


def test_gate_blocks_api_but_not_shell_or_health(gated):
    response = gated.get("/api/skills")
    assert response.status_code == 401 and response.json()["code"] == "access_required"
    assert gated.get("/skills-media/test-skill/frames/f01.jpg").status_code == 401
    assert gated.get("/api/health").json()["access_required"] is True
    assert gated.get("/").status_code == 200


def test_gate_header_query_and_cookie(gated):
    assert gated.get("/api/skills", headers={"X-Shadow-Access": "wrong"}).status_code == 401
    assert gated.get("/api/skills", headers={"X-Shadow-Access": "hoa-sen-42"}).status_code == 200
    response = gated.get("/api/skills?code=hoa-sen-42")
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "hoa-sen-42" not in cookie
    assert gated.get("/api/skills").status_code == 200  # TestClient keeps the cookie


def test_gate_access_endpoint_and_shareable_link(gated):
    assert gated.post("/api/access", json={"code": "nope"}).status_code == 401
    assert gated.get("/api/skills").status_code == 401
    assert gated.post("/api/access", json={"code": "hoa-sen-42"}).status_code == 200
    assert gated.get("/api/skills").status_code == 200
    gated.cookies.clear()
    assert "shadow_access=" in gated.get("/?code=hoa-sen-42").headers.get("set-cookie", "")
    assert gated.get("/api/skills").status_code == 200


def test_gate_forged_cookie_rejected(gated):
    gated.cookies.set("shadow_access", "hoa-sen-42")
    assert gated.get("/api/skills").status_code == 401


def test_gate_websocket(gated):
    with pytest.raises(WebSocketDisconnect):
        with gated.websocket_connect("/session/control") as ws:
            ws.receive_json()
    gated.post("/api/access", json={"code": "hoa-sen-42"})
    with gated.websocket_connect("/session/control") as ws:
        assert ws.receive_json()["type"] == "session.ready"


def test_gate_off_by_default(client):
    assert client.get("/api/skills").status_code == 200


# ---- websocket limits ------------------------------------------------------------------------------


def test_websocket_rejects_cross_origin(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/session/control", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with client.websocket_connect("/session/control", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "session.ready"


def test_websocket_per_ip_cap(monkeypatch, skills_dir):
    with make_client(monkeypatch, SHADOW_RATE_LIMIT="on", SHADOW_WS_MAX_PER_IP="2") as client:
        with client.websocket_connect("/session/control") as a, client.websocket_connect("/session/control") as b:
            a.receive_json()
            b.receive_json()
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/session/control") as c:
                    c.receive_json()
        # slots are released on disconnect
        with client.websocket_connect("/session/control") as d:
            assert d.receive_json()["type"] == "session.ready"


def test_websocket_message_flood_closes_socket(limited):
    import security

    with limited.websocket_connect("/session/control") as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as info:
            for _ in range(int(security.WS_MSG_RATE[1]) + 50):
                ws.send_json({"type": "noop"})
            while True:
                ws.receive_json()
        assert info.value.code == 1008


def test_security_headers(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/").headers["x-content-type-options"] == "nosniff"


def test_eval_capture_folder_cap(tmp_path, monkeypatch):
    import eval_store
    from eval_store import EvalError, EvalStore

    monkeypatch.setattr(eval_store, "MAX_PER_LABEL", 2)
    store = EvalStore(tmp_path)
    url = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
    store.save("s", "a", "done", url)
    import time as _t
    _t.sleep(0.002)
    store.save("s", "a", "done", url)
    with pytest.raises(EvalError):
        _t.sleep(0.002)
        store.save("s", "a", "done", url)


def test_write_bucket_limits_mutations(limited, monkeypatch, tmp_path):
    monkeypatch.setenv("SHADOW_WARDROBE_DIR", str(tmp_path / "wardrobe"))  # never the real data dir
    codes = [limited.post("/api/wardrobe/rules", json={"text": f"quy tắc {i}"}).status_code for i in range(40)]
    assert 429 in codes
    assert limited.get("/api/health").status_code == 200


def test_websocket_binary_frame_does_not_kill_session(client):
    with client.websocket_connect("/session/control") as ws:
        assert ws.receive_json()["type"] == "session.ready"
        ws.receive_json()  # initial hud.state
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["type"] == "hud.error"
        ws.send_json({"type": "session.start", "skill_id": "test-skill"})
        assert ws.receive_json()["type"] in ("hud.state", "speech")


def test_hf_result_temp_file_is_removed(tmp_path, monkeypatch):
    import tempfile

    import tryon

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    out = tmp_path / "gradio" / "result.png"
    out.parent.mkdir()
    out.write_bytes(png_bytes())
    assert tryon._result_to_jpeg(str(out))[:3] == b"\xff\xd8\xff"
    assert not out.exists()

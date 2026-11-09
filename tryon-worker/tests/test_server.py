"""API contract tests with TRYON_MOCK=1 (no torch needed).

Run:  TRYON_MOCK=1 python -m pytest tests -q        (from tryon-worker/)
"""

from __future__ import annotations

import base64
import io
import os
import sys
import threading
import time

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("TRYON_MOCK", "1")

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def make_data_url(size=(600, 900), color=(180, 120, 90), fmt="JPEG", mode="RGB") -> str:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    mime = "image/png" if fmt == "PNG" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()


def decode_data_url(value: str) -> Image.Image:
    assert value.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1])))


def mock_settings(**kw) -> server.Settings:
    base = dict(mock=True, size=(384, 512), steps=30)
    base.update(kw)
    return server.Settings(**base)


@pytest.fixture()
def client():
    with TestClient(server.create_app(mock_settings())) as c:
        yield c


def body(**kw) -> dict:
    b = {"person_image": make_data_url(), "garment_image": make_data_url((500, 500), (20, 60, 200), "PNG"),
         "category": "upper"}
    b.update(kw)
    return b


def test_health_contract(client):
    r = client.get("/health")
    assert r.status_code == 200
    h = r.json()
    for key, typ in (("ok", bool), ("model", str), ("device", str), ("loaded", bool), ("warm", bool)):
        assert isinstance(h[key], typ), key
    assert h["ok"] is True
    assert h["model"] == "catvton"
    assert h["device"] in ("mps", "cpu", "cuda")
    assert h["mock"] is True


@pytest.mark.parametrize("category", ["upper", "lower", "overall"])
def test_tryon_contract(client, category):
    r = client.post("/tryon", json=body(category=category))
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data["seconds"], float)
    assert (data["width"], data["height"]) == (384, 512)
    img = decode_data_url(data["image"])
    assert img.format == "JPEG"
    assert img.size == (data["width"], data["height"])
    assert data["mock"] is True
    assert data["mask"] is None


def test_optional_fields_and_mask(client):
    r = client.post("/tryon", json=body(garment_des="white linen shirt", steps=12, guidance=3.0, seed=7,
                                        return_mask=True))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["steps"] == 12 and data["seed"] == 7
    assert decode_data_url(data["mask"]).size == (384, 512)


def test_client_mask_and_raw_base64(client):
    raw_person = make_data_url().split(",", 1)[1]  # bare base64 is accepted too
    mask = make_data_url((600, 900), (255, 255, 255))
    r = client.post("/tryon", json=body(person_image=raw_person, mask_image=mask))
    assert r.status_code == 200, r.text
    assert r.json()["mask_mode"] == "client"


def test_transparent_png_garment(client):
    r = client.post("/tryon", json=body(garment_image=make_data_url((300, 300), (0, 0, 0, 0), "PNG", "RGBA")))
    assert r.status_code == 200, r.text


def test_health_after_request_reports_warm(client):
    client.post("/tryon", json=body())
    h = client.get("/health").json()
    assert h["loaded"] is True and h["warm"] is True and h["served"] >= 1


@pytest.mark.parametrize("patch,status", [
    ({"category": "shoes"}, 422),
    ({"steps": 0}, 422),
    ({"steps": 500}, 422),
    ({"guidance": -1}, 422),
    ({"person_image": "data:image/jpeg;base64,!!!notbase64"}, 400),
    ({"person_image": "data:image/jpeg;base64," + base64.b64encode(b"hello").decode()}, 400),
    ({"garment_image": ""}, 400),
    ({"person_image": make_data_url((32, 32))}, 400),
])
def test_validation(client, patch, status):
    r = client.post("/tryon", json=body(**patch))
    assert r.status_code == status, r.text


def test_missing_field(client):
    r = client.post("/tryon", json={"person_image": make_data_url()})
    assert r.status_code == 422


def test_image_too_large():
    with TestClient(server.create_app(mock_settings(max_image_mb=1))) as c:
        big = Image.effect_noise((1500, 1500), 100).convert("RGB")
        buf = io.BytesIO()
        big.save(buf, format="PNG")
        assert len(buf.getvalue()) > 1024 * 1024
        url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        r = c.post("/tryon", json=body(person_image=url))
        # 413 = whole-body cap (before JSON parsing), 400 = per-image cap
        assert r.status_code in (400, 413)
        assert "large" in r.json()["detail"]


def test_body_cap_rejects_before_parsing():
    with TestClient(server.create_app(mock_settings(max_image_mb=1))) as c:
        r = c.post("/tryon", content=b"{" + b" " * (5 * 1024 * 1024) + b"}",
                   headers={"content-type": "application/json"})
        assert r.status_code == 413


def test_per_image_cap_is_400():
    with TestClient(server.create_app(mock_settings(max_image_mb=1))) as c:
        url = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (1100 * 1024)).decode()
        r = c.post("/tryon", json=body(person_image=url))
        assert r.status_code == 400 and "larger than" in r.json()["detail"]


def test_decompression_bomb_is_rejected(client):
    # 20000x20000 = 400 Mpx declared in a tiny 1-bit PNG: must be refused before decoding.
    buf = io.BytesIO()
    Image.new("1", (20000, 20000), 0).save(buf, format="PNG")
    assert len(buf.getvalue()) < 1024 * 1024
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    r = client.post("/tryon", json=body(person_image=url))
    assert r.status_code == 400 and "pixels" in r.json()["detail"]


def test_token_prefix_is_rejected():
    with TestClient(server.create_app(mock_settings(token="s3cret"))) as c:
        assert c.post("/tryon", json=body(), headers={"X-Tryon-Token": "s3cre"}).status_code == 401
        assert c.post("/tryon", json=body(), headers={"X-Tryon-Token": "s3cret"}).status_code == 200


def test_token_required():
    with TestClient(server.create_app(mock_settings(token="s3cret"))) as c:
        assert c.get("/health").status_code == 200  # health stays open for the backend's probe
        assert c.get("/health").json()["auth"] is True
        assert c.post("/tryon", json=body()).status_code == 401
        assert c.post("/tryon", json=body(), headers={"X-Tryon-Token": "wrong"}).status_code == 401
        r = c.post("/tryon", json=body(), headers={"X-Tryon-Token": "s3cret"})
        assert r.status_code == 200, r.text


def test_requests_are_serialised():
    """With a slow mock, concurrent requests must run one at a time (the second one queues)."""
    app = server.create_app(mock_settings(mock_delay=0.4))
    results = []
    with TestClient(app) as c:
        def call():
            results.append(c.post("/tryon", json=body()).json())

        threads = [threading.Thread(target=call) for _ in range(2)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        time.sleep(0.15)
        mid = c.get("/health").json()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - t0
    assert len(results) == 2 and all("image" in r for r in results)
    assert elapsed >= 0.8 - 0.05, elapsed  # 2 x 0.4 s, not overlapping
    assert mid["busy"] is True
    assert max(r["queue_seconds"] for r in results) >= 0.3


def test_queue_full_returns_503():
    app = server.create_app(mock_settings(mock_delay=0.5, max_queue=1))
    codes = []
    with TestClient(app) as c:
        def call():
            codes.append(c.post("/tryon", json=body()).status_code)

        threads = [threading.Thread(target=call) for _ in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.1)
        for t in threads:
            t.join()
    assert sorted(codes) == [200, 200, 503]


def test_warmup_flag():
    with TestClient(server.create_app(mock_settings(warmup=True))) as c:
        for _ in range(50):
            h = c.get("/health").json()
            if h["warm"]:
                break
            time.sleep(0.02)
        assert h["loaded"] is True and h["warm"] is True


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("TRYON_RESOLUTION", "770x1030")
    monkeypatch.setenv("TRYON_STEPS", "20")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("TRYON_TOKEN", " abc ")
    monkeypatch.setenv("TRYON_DEVICE", "MPS")
    monkeypatch.setenv("TRYON_ATTN_CHUNK", "2048")
    s = server.Settings.from_env()
    assert s.size == (768, 1024)  # rounded down to multiples of 64
    assert (s.steps, s.port, s.token, s.device, s.attn_chunk) == (20, 9000, "abc", "mps", 2048)
    monkeypatch.setenv("TRYON_RESOLUTION", "big")
    with pytest.raises(ValueError):
        server.Settings.from_env()


def test_defaults(monkeypatch):
    for k in ("TRYON_RESOLUTION", "TRYON_STEPS", "PORT", "TRYON_DEVICE", "TRYON_TOKEN", "TRYON_ATTN_CHUNK"):
        monkeypatch.delenv(k, raising=False)
    s = server.Settings.from_env()
    assert s.size == (576, 768) and s.port == 8188 and s.device == "auto" and 20 <= s.steps <= 50
    assert s.token == "" and s.attn_chunk is None


def test_idm_is_rejected_with_explanation():
    with pytest.raises(RuntimeError, match="IDM-VTON"):
        server.create_app(server.Settings(mock=False, model="idm"))

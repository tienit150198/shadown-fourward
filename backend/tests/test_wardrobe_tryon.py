from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from wardrobe_testlib import data_url, item, json_body, make_jpeg, wardrobe_dir, wclient  # noqa: F401

from tryon import TryOnError, TryOnService, create_live_token, live_prompt, plan_garments
from wardrobe_store import to_jpeg


def run(coro):
    return asyncio.run(coro)


TOP = item("t", "top", description_en="white cotton shirt")
BOTTOM = item("b", "bottom", description_en="black trousers")
DRESS = item("d", "dress", description_en="red midi dress")
COAT = item("o", "outerwear", description_en="beige trench coat")


def garments(*items):
    return {it["id"]: to_jpeg(make_jpeg(color=(30, 60, 160))) for it in items}


def person():
    return data_url(make_jpeg(color=(230, 220, 210), size=(96, 128)))


def result_jpeg(color=(10, 200, 10), *, fmt="JPEG"):
    """Provider-like result with real spatial detail so the output quality gate can inspect it."""
    from PIL import Image, ImageDraw
    import io

    image = Image.new("RGB", (96, 128), (220, 210, 200))
    draw = ImageDraw.Draw(image)
    draw.ellipse((28, 8, 68, 48), fill=(190, 150, 125))
    draw.rectangle((24, 42, 72, 92), fill=color)
    draw.rectangle((28, 92, 45, 124), fill=(45, 50, 60))
    draw.rectangle((51, 92, 68, 124), fill=(45, 50, 60))
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def test_plan_garments():
    assert [(i["id"], k) for i, k in plan_garments([BOTTOM, TOP])] == [("t", "upper"), ("b", "lower")]
    assert [(i["id"], k) for i, k in plan_garments([TOP, DRESS, BOTTOM])] == [("d", "overall")]
    assert [(i["id"], k) for i, k in plan_garments([COAT, BOTTOM])] == [("o", "upper"), ("b", "lower")]
    assert plan_garments([item("s", "shoes")]) == []


def test_live_prompt_formats():
    assert live_prompt([TOP, BOTTOM]) == {
        "prompt": "Substitute the current outfit with white cotton shirt on top and black trousers on the bottom",
        "reference_item_id": "t",
    }
    assert live_prompt([DRESS])["prompt"] == "Substitute the current outfit with red midi dress"
    assert "beige trench coat over white cotton shirt" in live_prompt([TOP, COAT])["prompt"]
    assert live_prompt([item("x", "bottom", colors=["đen"], name="Quần tây")])["prompt"] == \
        "Substitute the current pants with black pants"


def test_live_prompt_uses_structured_descriptor_when_present():
    structured = item("knit", "top", description_en="blue shirt", tryon_descriptor={
        "category": "top", "color": "navy", "material": "cotton knit", "pattern": "cable-knit",
        "fit": "relaxed", "length": "hip length", "details": ["crew neck", "ribbed cuffs"],
        "confidence": 0.91, "needs_confirmation": False,
        "description": "navy cable-knit cotton knit relaxed-fit sweater, hip length, crew neck, ribbed cuffs",
    })

    assert live_prompt([structured])["prompt"] == (
        "Substitute the current top with navy cable-knit cotton knit relaxed-fit sweater, "
        "hip length, crew neck, ribbed cuffs"
    )


# ---- Decart client token --------------------------------------------------------------------


def test_live_token_requires_key(monkeypatch):
    monkeypatch.delenv("DECART_API_KEY", raising=False)
    with pytest.raises(TryOnError) as info:
        run(create_live_token())
    assert info.value.status == 503


def test_live_token_mirrors_sdk_call(monkeypatch):
    monkeypatch.setenv("DECART_API_KEY", "sk-test-secret")
    monkeypatch.setenv("DECART_TOKEN_TTL_S", "90")
    monkeypatch.setenv("DECART_MAX_SESSION_S", "300")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["body"] = json_body(request)
        return httpx.Response(200, json={"apiKey": "ek_123", "token": "eyJ.jwt", "expiresAt": "2026-11-09T10:00:00Z"})

    result = run(create_live_token(transport=httpx.MockTransport(handler)))
    assert seen == {
        "url": "https://api.decart.ai/v1/client/tokens",
        "key": "sk-test-secret",
        "body": {"expiresIn": 90, "constraints": {"realtime": {"maxSessionDuration": 300}}},
    }
    assert result["apiKey"] == "ek_123" and result["token"] == "eyJ.jwt"
    assert result["model"] == "lucy-vton-latest" and "sk-test-secret" not in str(result)


def test_live_token_upstream_errors(monkeypatch):
    monkeypatch.setenv("DECART_API_KEY", "sk-test")
    for response in (httpx.Response(401, text="bad key"), httpx.Response(200, json={"nope": 1})):
        with pytest.raises(TryOnError) as info:
            run(create_live_token(transport=httpx.MockTransport(lambda r, resp=response: resp)))
        assert info.value.status == 502


def test_live_token_route_503(wclient):
    response = wclient.post("/api/tryon/live-token")
    assert response.status_code == 503 and "DECART_API_KEY" in response.json()["detail"]


# ---- photo providers ---------------------------------------------------------------------------


def hf_runner(calls):
    def runner(person_path, garment_path, garment_des):
        calls.append(garment_des)
        out = person_path + ".out.png"
        with open(out, "wb") as handle:
            handle.write(result_jpeg(color=(10, 200, 10), fmt="PNG"))
        return out

    return runner


def test_hf_space_renders_top_only(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "hf_space")
    calls: list = []
    service = TryOnService(hf_runner=hf_runner(calls))
    result = run(service.run(person(), [TOP, BOTTOM], garments(TOP, BOTTOM)))
    assert result["provider"] == "hf_space" and result["jpeg"][:3] == b"\xff\xd8\xff"
    assert result["rendered_item_ids"] == ["t"] and result["skipped_item_ids"] == ["b"]
    assert "quần" in result["note"] and calls == ["white cotton shirt"]
    with pytest.raises(TryOnError) as info:
        run(service.run(person(), [BOTTOM], garments(BOTTOM)))
    assert info.value.status == 422


def test_hf_space_failure_is_502_when_explicit(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "hf_space")

    def broken(*args):
        raise RuntimeError("queue full")

    with pytest.raises(TryOnError) as info:
        run(TryOnService(hf_runner=broken).run(person(), [TOP], garments(TOP)))
    # status kept; upstream exception text is logged, not shown to the client
    assert info.value.status == 502 and "Hugging Face" in str(info.value)


def test_fashn_renders_top_and_bottom_with_private_base64_io(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "fashn")
    monkeypatch.setenv("FASHN_API_KEY", "fashn-test-secret")
    submitted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fashn-test-secret"
        if request.url.path == "/v1/run":
            body = json_body(request)
            submitted.append(body)
            return httpx.Response(200, json={"id": f"prediction-{len(submitted)}", "error": None})
        prediction = request.url.path.rsplit("/", 1)[-1]
        color = (10, 200, 10) if prediction == "prediction-1" else (20, 30, 220)
        return httpx.Response(200, json={
            "id": prediction,
            "status": "completed",
            "output": [data_url(result_jpeg(color=color))],
            "error": None,
        })

    service = TryOnService(fashn_transport=httpx.MockTransport(handler))
    result = run(service.run(person(), [TOP, BOTTOM], garments(TOP, BOTTOM)))

    assert result["provider"] == "fashn"
    assert result["rendered_item_ids"] == ["t", "b"] and result["skipped_item_ids"] == []
    assert [call["inputs"]["category"] for call in submitted] == ["tops", "bottoms"]
    assert all(call["model_name"] == "tryon-v1.6" for call in submitted)
    assert all(call["inputs"]["return_base64"] is True for call in submitted)
    assert all(call["inputs"]["output_format"] == "jpeg" for call in submitted)
    assert submitted[1]["inputs"]["model_image"] != submitted[0]["inputs"]["model_image"]
    assert "fashn-test-secret" not in str(result)


def test_fashn_config_reports_missing_key_instead_of_ready(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "fashn")
    monkeypatch.delenv("FASHN_API_KEY", raising=False)

    photo = run(TryOnService().config())["photo"]

    assert photo["provider"] == "fashn" and photo["available"] is False
    assert "FASHN_API_KEY" in photo["notice"]


def worker_transport(log, *, healthy=True, fail_tryon=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200 if healthy else 503, json={"ok": healthy})
        body = json_body(request)
        log.append(body["category"])
        assert body["person_image"].startswith("data:image/jpeg;base64,")
        assert body["garment_image"].startswith("data:image/jpeg;base64,")
        if fail_tryon:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"image": data_url(result_jpeg(color=(35, 45, 60))), "seconds": 1.0})

    return httpx.MockTransport(handler)


def test_auto_chain_prefers_healthy_worker_with_two_passes(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")
    monkeypatch.setenv("TRYON_WORKER_URL", "http://mac-mini.local:8001/")
    log: list = []
    service = TryOnService(worker_transport=worker_transport(log), hf_runner=hf_runner([]))
    assert run(service.provider_chain()) == ["worker", "hf_space", "mock"]
    result = run(service.run(person(), [TOP, BOTTOM], garments(TOP, BOTTOM)))
    assert result["provider"] == "worker" and log == ["upper", "lower"]
    assert result["rendered_item_ids"] == ["t", "b"] and "fallback_reason" not in result


def test_auto_chain_falls_back_when_worker_fails(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")
    monkeypatch.setenv("TRYON_WORKER_URL", "http://mac-mini.local:8001")
    calls: list = []
    service = TryOnService(worker_transport=worker_transport([], fail_tryon=True), hf_runner=hf_runner(calls))
    result = run(service.run(person(), [DRESS], garments(DRESS)))
    assert result["provider"] == "hf_space" and calls == ["red midi dress"]
    assert "worker" in result["fallback_reason"]


def test_unhealthy_worker_is_skipped_and_health_is_cached(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")
    monkeypatch.setenv("TRYON_WORKER_URL", "http://mac-mini.local:8001")
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(503)

    service = TryOnService(worker_transport=httpx.MockTransport(handler), hf_runner=hf_runner([]))
    assert run(service.provider_chain()) == ["hf_space", "mock"]
    assert run(service.provider_chain()) == ["hf_space", "mock"]
    assert hits["n"] == 1


def test_mock_provider_composites(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "mock")
    result = run(TryOnService().run(person(), [TOP, BOTTOM], garments(TOP, BOTTOM)))
    assert result["provider"] == "mock" and result["rendered_item_ids"] == ["t", "b"]
    with pytest.raises(TryOnError):
        run(TryOnService().run(person(), [item("s", "shoes")], garments(item("s", "shoes"))))


def test_provider_success_with_invalid_image_is_rejected(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "mock")
    service = TryOnService()

    async def invalid_provider(*_args):
        return {
            "jpeg": make_jpeg(color=(0, 0, 0), size=(16, 16)),
            "rendered_item_ids": ["t"], "skipped_item_ids": [], "note": "",
        }

    service._run_provider = invalid_provider
    with pytest.raises(TryOnError) as info:
        run(service.run(person(), [TOP], garments(TOP)))
    assert "không đạt kiểm tra kỹ thuật" in str(info.value)


# ---- API ------------------------------------------------------------------------------------------


def test_tryon_routes(wclient):
    config = wclient.get("/api/tryon/config").json()
    assert config["live"] == {"available": False, "model": "lucy-vton-latest", "max_session_seconds": 180}
    assert config["photo"]["provider"] == "mock"

    wclient.post("/api/wardrobe/seed-demo")
    ids = ["demo-ao-polo-hong", "demo-quan-jean-xanh"]
    response = wclient.post("/api/tryon/photo", json={"person_image": person(), "item_ids": ids})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "mock" and body["image_url"].startswith("/wardrobe-media/tryon/")
    image = wclient.get(body["image_url"])
    assert image.status_code == 200 and image.content[:3] == b"\xff\xd8\xff"

    assert wclient.post("/api/tryon/photo", json={"person_image": person(), "item_ids": ["ghost"]}).status_code == 404
    bad = wclient.post("/api/tryon/photo", json={"person_image": "data:image/gif;base64,AAAA", "item_ids": ids})
    assert bad.status_code == 422
    shoes = wclient.post("/api/tryon/photo", json={"person_image": person(), "item_ids": ["demo-giay-sneaker-trang"]})
    assert shoes.status_code == 422

    prompt = wclient.post("/api/tryon/live-prompt", json={"item_ids": ids}).json()
    assert prompt["prompt"].startswith("Substitute the current outfit with ")
    assert prompt["reference_item_id"] == "demo-ao-polo-hong"
    assert prompt["reference_image_url"] == "/wardrobe-media/images/demo-ao-polo-hong.jpg"


def test_photo_route_with_injected_hf(wclient, monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "hf_space")
    wclient.app.state.wardrobe_tryon = TryOnService(hf_runner=hf_runner([]))
    wclient.post("/api/wardrobe/seed-demo")
    body = wclient.post("/api/tryon/photo", json={
        "person_image": person(), "item_ids": ["demo-ao-polo-hong", "demo-quan-jean-xanh"],
    }).json()
    assert body["provider"] == "hf_space" and body["skipped_item_ids"] == ["demo-quan-jean-xanh"]
    assert base64 and body["note"]


def test_hf_quota_error_falls_back_and_backs_off(monkeypatch):
    monkeypatch.delenv("TRYON_PHOTO_PROVIDER", raising=False)
    monkeypatch.delenv("TRYON_WORKER_URL", raising=False)
    calls: list = []

    def out_of_quota(*args):
        calls.append(1)
        raise RuntimeError("You have exceeded your ZeroGPU quota (60s requested vs. 9s left). Try again in 2:10:05.")

    service = TryOnService(hf_runner=out_of_quota)
    result = run(service.run(person(), [TOP], garments(TOP)))
    assert result["provider"] == "mock" and "hết lượt GPU" in result["fallback_reason"]
    assert "2 giờ 10 phút" in result["fallback_reason"]
    config = run(service.config())
    assert config["photo"]["provider"] == "mock" and "hết lượt GPU" in config["photo"]["notice"]
    again = run(service.run(person(), [TOP], garments(TOP)))
    assert len(calls) == 1  # Space skipped while the quota is exhausted
    assert again["provider"] == "mock" and "hết lượt GPU" in again["fallback_reason"]

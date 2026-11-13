from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import qwen_image
from wardrobe_testlib import json_body, make_jpeg


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("HYPERTEK_API_KEY", "test-image-key")
    monkeypatch.setenv("QWEN_IMAGE_BASE_URL", "https://images.example/v1/")
    monkeypatch.setenv("QWEN_IMAGE_MODEL", "Qwen/Qwen-Image-2.1")


def generated_image():
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(make_jpeg(fmt="PNG")).decode()}]})


def test_image_api_returns_jpeg(monkeypatch):
    def provider(request):
        assert str(request.url) == "https://images.example/v1/images/generations"
        assert request.headers["authorization"] == "Bearer test-image-key"
        assert json_body(request) == {
            "model": "Qwen/Qwen-Image-2.1", "prompt": "Một đôi giày đỏ", "n": 1,
            "size": "768x768", "response_format": "b64_json",
        }
        return generated_image()

    app = FastAPI()
    app.include_router(qwen_image.router)
    app.state.qwen_image = qwen_image.QwenImage(transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        assert client.get("/api/images/config").json() == {"available": True, "model": "Qwen/Qwen-Image-2.1"}
        assert client.post("/api/images/generate", json={"prompt": "  "}).status_code == 422
        assert client.post("/api/images/generate", json={"prompt": "image", "size": "99999x99"}).status_code == 422
        response = client.post("/api/images/generate", json={"prompt": "  Một đôi giày đỏ  "})
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "qwen" and result["model"] == "Qwen/Qwen-Image-2.1"
    assert result["image"].startswith("data:image/jpeg;base64,/9j/")


def test_shared_key_requires_matching_base_url(monkeypatch):
    monkeypatch.delenv("HYPERTEK_API_KEY")
    monkeypatch.setenv("LLM_API_KEY", "shared-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://different.example/v1")
    assert not qwen_image.QwenImage().available()
    monkeypatch.setenv("LLM_BASE_URL", "https://images.example/v1")
    assert qwen_image.QwenImage().available()


@pytest.mark.parametrize("provider_response,status", [
    (httpx.Response(200, json={"data": [{"b64_json": "bad"}]}), 502),
    (httpx.Response(429, text="upstream private error"), 429),
    (httpx.Response(401, text="upstream private error"), 503),
])
def test_cloud_errors_do_not_expose_provider_response(provider_response, status):
    image = qwen_image.QwenImage(transport=httpx.MockTransport(lambda _: provider_response))
    with pytest.raises(qwen_image.QwenImageError) as error:
        asyncio.run(image.generate("image"))
    assert error.value.status == status
    assert "upstream private error" not in str(error.value)


def test_disconnected_client_cancels_generation(monkeypatch):
    cancelled = False

    async def provider(_request):
        nonlocal cancelled
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def disconnected(_request):
        return True

    monkeypatch.setattr(Request, "is_disconnected", disconnected)
    monkeypatch.setattr(qwen_image, "DISCONNECT_POLL_S", 0.01)
    monkeypatch.setenv("QWEN_IMAGE_TIMEOUT_S", "1")
    app = FastAPI()
    app.include_router(qwen_image.router)
    app.state.qwen_image = qwen_image.QwenImage(transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        response = client.post("/api/images/generate", json={"prompt": "image"})
    assert response.status_code == 499
    assert cancelled

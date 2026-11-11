from __future__ import annotations

import asyncio
import io

import httpx
import pytest
from PIL import Image

import vidu_image
from tryon import TryOnError, TryOnService
from wardrobe_store import WardrobeError, decode_image_data_url
from wardrobe_testlib import data_url, item, json_body, make_jpeg, wardrobe_dir, wclient  # noqa: F401


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("VIDU_API_KEY", "test-vidu-key")
    monkeypatch.setenv("VIDU_API_BASE_URL", "https://api.vidu.com/")
    monkeypatch.setattr(vidu_image, "POLL_INTERVAL_S", 0)


def test_photo_route_sends_references_in_order_and_saves_image(wclient, monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.method == "POST":
            assert str(request.url) == "https://api.vidu.com/ent/v2/reference2image"
            assert request.headers["authorization"] == "Token test-vidu-key"
            body = json_body(request)
            assert body["model"] == "viduq2" and body["aspect_ratio"] == "auto" and body["resolution"] == "1080p"
            assert len(body["images"]) == 4
            with Image.open(io.BytesIO(decode_image_data_url(body["images"][0]))) as person:
                assert person.width >= 128 and person.height > person.width
            for fragment in ("identity", "pose", "unselected", "Image 2", "Image 3", "Image 4", "upper-body", "lower-body", "footwear"):
                assert fragment in body["prompt"]
            return httpx.Response(200, json={"task_id": "test-task", "state": "created"})
        if request.url.host == "api.vidu.com":
            assert request.headers["authorization"] == "Token test-vidu-key"
            if calls.count(request.url.path) == 1:
                return httpx.Response(200, json={"state": "processing"})
            return httpx.Response(200, json={"state": "success", "creations": [{"url": "https://cdn.example/try-on.png"}]})
        assert request.url.host == "cdn.example" and "authorization" not in request.headers
        return httpx.Response(200, content=make_jpeg(fmt="PNG"))

    wclient.app.state.wardrobe_tryon = TryOnService(vidu_image=vidu_image.ViduImage(transport=httpx.MockTransport(provider)))
    config = wclient.get("/api/tryon/config").json()
    assert config["photo"]["provider"] == "vidu" and config["photo"]["renders_bottoms"]
    assert wclient.post("/api/wardrobe/seed-demo").is_success
    ids = ["demo-ao-polo-hong", "demo-quan-jean-xanh", "demo-giay-sneaker-trang"]
    response = wclient.post("/api/tryon/photo", json={"person_image": data_url(make_jpeg()), "item_ids": ids})
    assert response.is_success, response.text
    result = response.json()
    assert result["provider"] == "vidu" and result["rendered_item_ids"] == ids
    assert result["skipped_item_ids"] == []
    assert wclient.get(result["image_url"]).content.startswith(b"\xff\xd8\xff")
    assert calls == ["/ent/v2/reference2image", "/ent/v2/tasks/test-task/creations", "/ent/v2/tasks/test-task/creations", "/try-on.png"]


def test_photo_dress_keeps_outerwear_shoes_and_accessories_and_respects_reference_limit(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "vidu")

    class ImageProvider(vidu_image.ViduImage):
        async def edit(self, prompt, images):
            assert all(isinstance(image, bytes) for image in images)
            assert len(images) == 7
            for fragment in ("full-body", "outerwear", "footwear", "accessory"):
                assert fragment in prompt
            assert "upper-body" not in prompt and "lower-body" not in prompt
            return make_jpeg()

    items = [item("t", "top"), item("d", "dress"), item("b", "bottom"), item("j", "outerwear"), item("s", "shoes")]
    items += [item(f"a{i}", "accessory") for i in range(4)]
    service = TryOnService(vidu_image=ImageProvider())
    images = {i["id"]: make_jpeg() for i in items}
    result = asyncio.run(service.run(data_url(make_jpeg()), items, images))
    assert result["rendered_item_ids"] == ["d", "j", "s", "a0", "a1", "a2"]
    assert result["skipped_item_ids"] == ["t", "b", "a3"]
    with pytest.raises(WardrobeError):
        asyncio.run(service.run("data:image/gif;base64,AAAA", items, images))


def test_failed_provider_task_does_not_fall_back(monkeypatch):
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "auto")

    def provider(request):
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "test-task"})
        return httpx.Response(200, json={"state": "failed", "err_code": "private error"})

    service = TryOnService(vidu_image=vidu_image.ViduImage(transport=httpx.MockTransport(provider)))
    with pytest.raises(TryOnError) as error:
        asyncio.run(service.run(data_url(make_jpeg()), [item("s", "shoes")], {"s": make_jpeg()}))
    assert error.value.status == 502 and "private error" not in str(error.value)


def test_explicit_provider_requires_key(monkeypatch):
    monkeypatch.delenv("VIDU_API_KEY")
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "vidu")
    service = TryOnService()
    with pytest.raises(TryOnError) as error:
        asyncio.run(service.run(data_url(make_jpeg()), [item("s", "shoes")], {"s": make_jpeg()}))
    assert error.value.status == 503

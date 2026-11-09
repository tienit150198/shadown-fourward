"""Shared helpers/fixtures for the wardrobe tests (offline: fake LLM, fake HTTP transports)."""

from __future__ import annotations

import base64
import io
import json
from datetime import timedelta
from typing import Any, Callable

import httpx
import pytest


def make_jpeg(color=(200, 30, 40), size=(64, 80), fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    from PIL import Image

    image = Image.new(mode, size, color if mode == "RGB" else (*color, 255))
    if mode == "RGBA":
        # transparent border → exercises alpha flattening
        for x in range(size[0]):
            image.putpixel((x, 0), (0, 0, 0, 0))
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def data_url(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


class FakeProvider:
    """mode "ai" provider that returns queued replies (dicts, exceptions or callables)."""

    mode = "ai"

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        self.calls.append({"model": model, "system": system, "content": content, "max_tokens": max_tokens})
        if not self.replies:
            raise RuntimeError("no fake reply queued")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(content)
        return reply

    async def aclose(self) -> None:
        return None


def weather_transport(
    *, t_min=18.0, t_max=24.0, rain=60, code=61, counter: dict | None = None, fail: bool = False
) -> httpx.MockTransport:
    """Fake Open-Meteo: geocoding returns a foreign 'Hanoi' first and the VN one second."""

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter[request.url.host] = counter.get(request.url.host, 0) + 1
        if fail:
            raise httpx.ConnectError("offline", request=request)
        if request.url.host.startswith("geocoding"):
            name = request.url.params.get("name", "")
            if "xyz" in name.lower():
                return httpx.Response(200, json={"generationtime_ms": 0.1})
            return httpx.Response(200, json={"results": [
                {"name": "Hanoi (US)", "latitude": 40.0, "longitude": -80.0, "country_code": "US"},
                {"name": "Hà Nội", "latitude": 21.0245, "longitude": 105.84117, "country_code": "VN"},
            ]})
        day = request.url.params.get("start_date")
        return httpx.Response(200, json={"daily": {
            "time": [day],
            "temperature_2m_max": [t_max],
            "temperature_2m_min": [t_min],
            "precipitation_probability_max": [rain],
            "weather_code": [code],
        }})

    return httpx.MockTransport(handler)


@pytest.fixture
def wardrobe_dir(tmp_path, monkeypatch):
    root = tmp_path / "wardrobe"
    monkeypatch.setenv("SHADOW_WARDROBE_DIR", str(root))
    monkeypatch.setenv("TRYON_PHOTO_PROVIDER", "mock")
    monkeypatch.setenv("TRYON_WORKER_URL", "")
    monkeypatch.setenv("DECART_API_KEY", "")
    monkeypatch.setenv("STYLIST_MODEL", "")
    return root


@pytest.fixture
def wclient(client, wardrobe_dir):
    """conftest `client` (mock mode) + wardrobe dir + offline weather/try-on services."""
    from tryon import TryOnService
    from weather import WeatherService

    state = client.app.state
    state.wardrobe_store = None
    state.wardrobe_weather = WeatherService(transport=weather_transport())
    state.wardrobe_tryon = TryOnService()
    yield client
    state.wardrobe_store = None
    state.wardrobe_weather = None
    state.wardrobe_tryon = None


def item(item_id: str, category: str, *, warmth=2, colors=("trắng",), occasions=("đi làm",), **extra) -> dict:
    return {
        "id": item_id,
        "name": extra.pop("name", f"{category} {item_id}"),
        "category": category,
        "colors": list(colors),
        "style": extra.pop("style", "thường ngày"),
        "warmth": warmth,
        "occasions": list(occasions),
        **extra,
    }


def sample_wardrobe() -> list[dict]:
    return [
        item("t1", "top", warmth=1, colors=["trắng"], occasions=["đi chơi"], name="Áo hai dây trắng"),
        item("t2", "top", warmth=3, colors=["xanh navy"], occasions=["đi làm"], name="Sơ mi navy", style="công sở"),
        item("t3", "top", warmth=4, colors=["be"], occasions=["đi làm", "đi chơi"], name="Áo len be"),
        item("b1", "bottom", warmth=1, colors=["xanh dương"], occasions=["đi chơi", "đi biển"], name="Quần short jean"),
        item("b2", "bottom", warmth=3, colors=["đen"], occasions=["đi làm", "phỏng vấn"], name="Quần tây đen", style="công sở"),
        item("d1", "dress", warmth=2, colors=["đỏ"], occasions=["dự tiệc", "hẹn hò"], name="Váy đỏ"),
        item("o1", "outerwear", warmth=5, colors=["đen"], occasions=["đi làm"], name="Áo phao đen"),
        item("o2", "outerwear", warmth=3, colors=["be"], occasions=["đi làm"], name="Blazer be"),
        item("s1", "shoes", warmth=2, colors=["trắng"], occasions=["đi chơi"], name="Sneaker trắng"),
    ]


def weather(t_min, t_max, rain=10, code=1) -> dict:
    return {"available": True, "city": "Hà Nội", "t_min": t_min, "t_max": t_max, "rain_prob": rain, "code": code,
            "summary_vi": f"Hà Nội, {t_min}–{t_max}°C"}


JsonHandler = Callable[[httpx.Request], httpx.Response]


def json_body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode("utf-8") or "{}")


__all__ = [
    "FakeProvider", "make_jpeg", "data_url", "weather_transport", "wardrobe_dir", "wclient",
    "item", "sample_wardrobe", "weather", "json_body", "timedelta",
]

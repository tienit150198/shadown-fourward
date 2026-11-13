from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize(("message", "intent"), [
    ("  Thêm áo vào tủ đồ!  ", "wardrobe_add"),
    ("phoi do", "outfit_suggest"),
    ("Mặc thử bằng ảnh", "tryon_photo"),
    ("Mặc thử trực tiếp", "tryon_live"),
    ("Mình muốn thử đồ trực tiếp", "tryon_live"),
    ("Cho mình thử đồ bằng ảnh", "tryon_photo"),
    ("Thêm giày sneaker vào tủ đồ", "wardrobe_add"),
    ("Mình không muốn thử đồ trực tiếp", None),
    ("Tôi chỉ bạn cách làm", "teach_skill"),
    ("Bạn chỉ tôi cách làm", "guide_skill"),
    ("Thời tiết hôm nay", "weather"),
    ("Xử lý cái áo này giúp tôi", None),
    ("Tủ đồ của tôi có những gì?", "wardrobe_manage"),
    ("Trong tủ có áo len không?", "wardrobe_manage"),
    ("Tôi muốn mặc thử áo trong tủ đồ", "tryon_live"),
    ("Xem cái tủ trong phòng tôi đang chỉ qua camera", None),
])
def test_intent_without_credentials(client, message, intent):
    response = client.post("/api/intent", json={"text": message})
    assert response.status_code == 200
    assert response.json() == {"intent": intent, "suggestions": [], "source": "local"}


def test_intent_validates_input(client):
    for text in ("   ", "x" * 501):
        assert client.post("/api/intent", json={"text": text}).status_code == 422


@pytest.mark.parametrize(("provider", "url", "model", "key"), [
    ("jev", "https://openrouter.ai/api/alpha/decisions", "typesafe/jev-1.14", "router-key"),
    ("laya", "http://laya:8011/v1/systemone", "multilingual", ""),
])
def test_intent_provider_request_and_probabilities(client, monkeypatch, provider, url, model, key):
    from intent_router import IntentRouter

    monkeypatch.setenv("SHADOW_INTENT_PROVIDER", provider)
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    monkeypatch.setenv("SHADOW_LAYA_URL", "http://laya:8011/")
    monkeypatch.setenv("SHADOW_LAYA_API_KEY", "")
    sent = []

    async def post(self, target, *, json):
        sent.append((target, json, self.headers.get("Authorization")))
        return httpx.Response(200, request=httpx.Request("POST", target), json={
            "answers": {"intent": {"choice": "guide_skill", "probabilities": {
                "guide_skill": 0.91, "teach_skill": 0.06, "other": 0.03,
            }}},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = IntentRouter.from_env()
    monkeypatch.setattr(client.app.state, "intent_router", router)
    response = client.post("/api/intent", json={"text": "Anh chỉ em cách thay chân đế nhé"})
    assert response.json() == {"intent": "guide_skill", "suggestions": [], "source": provider}
    [(target, body, authorization)] = sent
    assert target == url
    assert authorization == (f"Bearer {key}" if key else None)
    assert body["model"] == model
    assert body["state"] == "Anh chỉ em cách thay chân đế nhé"
    assert body["questions"]["intent"]["type"] == "choice"
    assert "other" in body["questions"]["intent"]["criteria"]
    client.portal.call(router.aclose)


@pytest.mark.parametrize(("choice", "probabilities", "suggestions"), [
    ("wardrobe_add", {"wardrobe_add": 0.51, "tryon_photo": 0.46, "other": 0.03},
     ["wardrobe_add", "tryon_photo"]),
    ("other", {"other": 0.93, "guide_skill": 0.07}, []),
    ("unexpected", {"unexpected": 1}, []),
])
def test_intent_abstains(client, monkeypatch, choice, probabilities, suggestions):
    from intent_router import IntentRouter

    monkeypatch.setenv("SHADOW_INTENT_PROVIDER", "laya")

    async def post(self, url, *, json):
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "answers": {"intent": {"choice": choice, "probabilities": probabilities}},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = IntentRouter.from_env()
    monkeypatch.setattr(client.app.state, "intent_router", router)
    response = client.post("/api/intent", json={"text": "Cái áo này tính sao đây"})
    assert response.json()["intent"] is None
    assert response.json()["suggestions"] == suggestions
    client.portal.call(router.aclose)


def test_intent_provider_timeout_falls_back(client, monkeypatch):
    from intent_router import IntentRouter

    monkeypatch.setenv("SHADOW_INTENT_PROVIDER", "laya")

    async def post(self, url, *, json):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    router = IntentRouter.from_env()
    monkeypatch.setattr(client.app.state, "intent_router", router)
    assert client.post("/api/intent", json={"text": "Cái này xử lý sao"}).json() == {
        "intent": None, "suggestions": [], "source": "local",
    }
    client.portal.call(router.aclose)

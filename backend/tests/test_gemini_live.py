import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from gemini_live import CAMERA_COMMANDS, DECISION_CRITERIA, DECISION_MODEL, LABELS, LIVE_MODEL


def answer(choice="guide_coffee", confidence=.97):
    return {"answers": {"intent": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {key: confidence if key == choice else (1 - confidence) / (len(LABELS) - 1) for key in LABELS},
    }}}


@pytest.fixture
def install_gemini(client):
    previous = client.app.state.gemini_live_client
    clients = []
    def install(handler):
        upstream = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com",
            headers={"x-goog-api-key": "private-test-key"}, transport=httpx.MockTransport(handler),
        )
        clients.append(upstream)
        client.app.state.gemini_live_client = upstream
    yield install
    client.app.state.gemini_live_client = previous
    for upstream in clients:
        client.portal.call(upstream.aclose)


def test_live_without_key_is_explicit_and_keeps_existing_chat_available(client):
    assert client.get("/api/live/config").json()["available"] is False
    assert client.post("/api/live/session", json={}).status_code == 503
    assert client.post("/api/live/classify", json={"text": "Pha cà phê"}).status_code == 503
    assert client.post("/api/conversation", json={"text": "Thêm áo vào tủ đồ"}).status_code == 200


@pytest.mark.parametrize("with_history", [False, True])
def test_live_token_locks_setup_uses_short_expiry_and_keeps_permanent_key_on_server(client, install_gemini, with_history):
    def respond(request):
        assert request.url.path == "/v1beta/auth_tokens"
        assert request.headers["x-goog-api-key"] == "private-test-key"
        body = json.loads(request.content)
        assert body["uses"] == 1
        expiry = datetime.fromisoformat(body["expireTime"])
        new_session = datetime.fromisoformat(body["newSessionExpireTime"])
        assert (expiry - new_session).total_seconds() == 240
        setup = body["bidiGenerateContentSetup"]
        assert setup["model"] == "models/" + LIVE_MODEL
        assert setup["tools"][0]["functionDeclarations"][0]["behavior"] == "BLOCKING"
        assert bool(setup.get("historyConfig")) is with_history
        return httpx.Response(200, json={"name": "short-lived-token"})
    install_gemini(respond)
    payload = {"model": "untrusted-model"}
    if with_history:
        payload["history"] = [{"role": "user", "text": "Tôi đang pha cà phê."}]
    response = client.post("/api/live/session", json=payload)
    assert response.status_code == 200
    assert response.json()["token"] == "short-lived-token"
    assert response.json()["maxSeconds"] == 300
    assert "private-test-key" not in response.text


def test_decisions_contract_and_practical_topics_match_the_original_app(client, install_gemini):
    assert set(DECISION_CRITERIA) == set(LABELS)
    def respond(request):
        assert request.url.path == "/v1beta/decisions"
        body = json.loads(request.content)
        assert body["model"] == DECISION_MODEL
        assert body["input"] == "Hướng dẫn tôi gấp áo phông."
        assert list(body["questions"]) == ["intent"]
        assert body["questions"]["intent"]["type"] == "choice"
        return httpx.Response(200, json=answer("guide_folding"))
    install_gemini(respond)
    response = client.post("/api/live/classify", json={"text": "Hướng dẫn tôi gấp áo phông."})
    assert response.status_code == 200
    assert response.json()["intent"] == "guide_folding"
    assert response.json()["label"] == "Hướng dẫn gấp quần áo"
    assert response.json()["source"] == "gemini"
    assert response.json()["context"]["available_skills"][0]["id"] == "test-skill"


def test_live_gets_the_saved_lesson_and_a_link_to_the_real_guide(client, install_gemini):
    install_gemini(lambda _: httpx.Response(200, json=answer("guide_skill")))
    result = client.post("/api/live/classify", json={"text": "Hướng dẫn tôi Pha trà thử nghiệm."}).json()
    assert result["context"]["lesson"]["name"] == "Pha trà thử nghiệm"
    assert result["context"]["lesson"]["steps"][0]["instruction"] == "Cầm ấm lên"
    assert result["actions"][0]["route"] == "guide"
    assert result["actions"][0]["id"] == "test-skill"


def test_live_outfit_context_uses_the_existing_wardrobe_and_taught_rules(client, install_gemini, monkeypatch):
    from wardrobe_store import wardrobe_root
    monkeypatch.setattr(client.app.state, "wardrobe_store", SimpleNamespace(
        root=wardrobe_root(),
        list_items=lambda: [{"id": "shirt-1", "name": "Sơ mi trắng", "colors": ["trắng"], "private_path": "/private/data"}],
        list_rules=lambda: [{"id": "rule-1", "text": "Đi làm mặc lịch sự", "taught_by": "Chị Lan"}],
    ), raising=False)
    install_gemini(lambda _: httpx.Response(200, json=answer("outfit_suggest")))
    result = client.post("/api/live/classify", json={"text": "Mai tôi đi làm nên mặc gì?"}).json()
    wardrobe = result["context"]["wardrobe"]
    assert wardrobe["items"][0]["name"] == "Sơ mi trắng"
    assert "private_path" not in wardrobe["items"][0]
    assert wardrobe["rules"][0]["taught_by"] == "Chị Lan"
    assert result["actions"][0]["route"] == "stylist"


def test_system_wardrobe_meaning_and_lookup_are_preserved_beyond_first_page(client, install_gemini, monkeypatch):
    from wardrobe_store import wardrobe_root
    from gemini_live import session_setup
    items = [{'id': f'item-{i}', 'name': f'Áo mẫu {i}', 'category': 'top'} for i in range(70)]
    items.append({'id': 'winter-pants', 'name': 'Quần giữ nhiệt HEATTECH', 'category': 'bottom'})
    monkeypatch.setattr(client.app.state, 'wardrobe_store', SimpleNamespace(root=wardrobe_root(), list_items=lambda: items, list_rules=lambda: []), raising=False)
    def respond(request):
        body = json.loads(request.content)
        assert 'app inventory by default' in body['questions']['intent']['instructions']
        return httpx.Response(200, json=answer('wardrobe_manage'))
    install_gemini(respond)
    result = client.post('/api/live/classify', json={'text': 'Tủ đồ của tôi có quần giữ nhiệt không?'}).json()
    wardrobe = result['context']['wardrobe']
    assert wardrobe['source'] == 'SHADOW_SYSTEM_WARDROBE'
    assert wardrobe['total_items'] == 71
    assert wardrobe['category_counts']['bottom'] == 1
    assert wardrobe['items'][0]['id'] == 'winter-pants'
    assert wardrobe['items_are_excerpt'] is True
    assert 'TỦ ĐỒ TRONG HỆ THỐNG SHADOW' in session_setup()['systemInstruction']['parts'][0]['text']


def test_live_classification_prepares_an_executable_fitting_from_spoken_words(client, install_gemini, monkeypatch):
    from wardrobe_store import wardrobe_root
    from types import SimpleNamespace
    async def choose(**kwargs):
        return {'action': 'apply', 'item_ids': ['shirt', 'pants']}
    monkeypatch.setattr(client.app.state, 'provider', SimpleNamespace(mode='ai', chat_json=choose))
    monkeypatch.setattr(client.app.state, 'wardrobe_store', SimpleNamespace(root=wardrobe_root(), list_items=lambda: [
        {'id': 'shirt', 'name': 'Áo trắng', 'category': 'top', 'image_url': '/shirt.jpg'},
        {'id': 'pants', 'name': 'Quần xám', 'category': 'bottom', 'image_url': '/pants.jpg'},
    ], list_rules=lambda: []), raising=False)
    install_gemini(lambda _: httpx.Response(200, json=answer('tryon_live')))
    result = client.post('/api/live/classify', json={'text': 'Mặc áo trắng quần xám cho tôi'}).json()
    assert result['context']['tryon_plan']['status'] == 'ready'
    assert result['context']['tryon_plan']['item_ids'] == ['shirt', 'pants']


def test_live_weather_uses_the_existing_forecast_service(client, install_gemini, monkeypatch):
    async def forecast(**kwargs):
        assert kwargs["lat"] == 21.03 and kwargs["lon"] == 105.85
        assert "city" not in kwargs
        return {"available": True, "summary_vi": "Vị trí của bạn 26 độ, trời nắng."}
    monkeypatch.setattr(client.app.state, "wardrobe_weather", SimpleNamespace(get=forecast), raising=False)
    install_gemini(lambda _: httpx.Response(200, json=answer("weather")))
    result = client.post("/api/live/classify", json={"text": "Thời tiết ở đây", "location": {"lat": 21.03, "lon": 105.85}}).json()
    assert "Vị trí của bạn 26 độ" in result["context"]["weather"]["summary_vi"]


def test_live_summer_outfits_use_real_ids_and_ignore_current_cold_weather(client, install_gemini, monkeypatch, tmp_path):
    from wardrobe_testlib import sample_wardrobe
    items = sample_wardrobe()
    monkeypatch.setenv("SHADOW_WARDROBE_DIR", str(tmp_path))
    monkeypatch.setattr(client.app.state, "wardrobe_store", SimpleNamespace(
        root=tmp_path, list_items=lambda: items, list_rules=lambda: [],
    ), raising=False)
    async def must_not_fetch(**kwargs):
        pytest.fail("Summer styling must not use the current GPS forecast")
    monkeypatch.setattr(client.app.state, "wardrobe_weather", SimpleNamespace(get=must_not_fetch), raising=False)
    install_gemini(lambda _: httpx.Response(200, json=answer("outfit_suggest")))
    result = client.post("/api/live/classify", json={"text": "Phối đồ mùa hè đi biển",
        "location": {"lat": 21, "lon": 105}}).json()["context"]
    assert result["weather"]["source"] == "requested" and not result["weather"]["is_forecast"]
    assert result["outfits"]
    known = {item["id"] for item in items}
    for outfit in result["outfits"]:
        assert set(outfit["item_ids"]) <= known
        assert outfit["item_names"] and outfit["reason"]
        assert not any(v["code"] == "too_hot" for v in outfit["violations"])


@pytest.mark.parametrize("status", [400, 403, 429, 500])
def test_live_provider_failures_are_sanitized_without_mock_fallback(client, install_gemini, status):
    install_gemini(lambda _: httpx.Response(status, text="echoed private-test-key"))
    result = client.post("/api/live/classify", json={"text": "Pha cà phê"})
    assert result.status_code == (429 if status == 429 else 502)
    assert "private-test-key" not in result.text
    assert "intent" not in result.json()


@pytest.mark.parametrize("data", [{}, {"answers": None}, {"answers": {"intent": []}}, {"answers": {"intent": {"choice": []}}}])
def test_invalid_decisions_data_cannot_make_actions_or_context(client, install_gemini, data):
    install_gemini(lambda _: httpx.Response(200, json=data))
    result = client.post("/api/live/classify", json={"text": "Giúp tôi"})
    assert result.status_code == 502


def test_live_routes_use_the_existing_access_code_and_token_limits(client, install_gemini, monkeypatch):
    import security
    monkeypatch.setenv("SHADOW_ACCESS_CODE", "private-code")
    install_gemini(lambda _: httpx.Response(200, json={"name": "ephemeral"}))
    assert client.post("/api/live/session", json={}).status_code == 401
    assert client.post("/api/live/session", json={}, headers={"X-Shadow-Access": "private-code"}).status_code == 200
    assert security.rate_rule_for("POST", "/api/live/session") == "live_token"
    assert security.rate_rule_for("POST", "/api/live/classify") == "conversation"


def test_live_rejects_empty_and_oversized_text_before_google(client, install_gemini):
    def unexpected(_):
        pytest.fail("Invalid input must not call Google")
    install_gemini(unexpected)
    for text in ("", " " * 3, "a" * 12001):
        assert client.post("/api/live/classify", json={"text": text}).status_code == 422


@pytest.mark.parametrize("command,confidence,expected", [
    ("continue_task", .98, "continue_task"),
    ("finish_teaching", .98, "finish_teaching"),
    ("finish_teaching", .55, "continue_task"),
    ("cancel_task", .99, "cancel_task"),
])
def test_camera_control_uses_decisions_and_requires_confident_explicit_finish(client, install_gemini, command, confidence, expected):
    def respond(request):
        body = json.loads(request.content)
        context = json.loads(body["input"])
        assert context["active_camera_task"] == "teaching"
        assert body["questions"]["camera_command"]["criteria"] == CAMERA_COMMANDS
        data = answer("teach_skill")
        data["answers"]["camera_command"] = {
            "type": "choice", "choice": command, "confidence": confidence,
            "probabilities": {key: confidence if key == command else (1-confidence)/(len(CAMERA_COMMANDS)-1) for key in CAMERA_COMMANDS},
        }
        return httpx.Response(200, json=data)
    install_gemini(respond)
    result = client.post("/api/live/classify", json={"text": "Tôi đã xong bước một.", "current_task": "teaching", "task_title": "Gấp áo"})
    assert result.status_code == 200
    assert result.json()["camera_command"] == expected


def test_missing_camera_control_cannot_finish_or_cancel_a_recording(client, install_gemini):
    install_gemini(lambda _: httpx.Response(200, json=answer("teach_skill")))
    result = client.post("/api/live/classify", json={"text": "Xong rồi", "current_task": "teaching"})
    assert result.status_code == 502

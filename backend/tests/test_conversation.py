import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image


def events(response):
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-ndjson"
    return [json.loads(line) for line in response.text.splitlines()]


def test_manual_actions_are_links(client):
    result = events(client.post("/api/conversation", json={"text": "Thêm áo vào tủ đồ"}))
    assert result[0]["type"] == "text"
    assert result[1] == {"type": "action", "route": "wardrobe", "label": "Mở tủ đồ"}
    assert result[2]["intent"] == "wardrobe_add"


def test_weather_uses_gps_and_does_not_guess_a_city(client, monkeypatch):
    from styling_context import LOCATION_MESSAGE
    from weather import local_today
    from datetime import timedelta
    calls = []
    async def forecast(**kwargs):
        calls.append(kwargs)
        return {"available": True, "summary_vi": "Vị trí của bạn 26°C, có thể mưa."}
    monkeypatch.setattr(client.app.state, "wardrobe_weather", SimpleNamespace(get=forecast), raising=False)
    first = events(client.post("/api/conversation", json={"text": "Thời tiết ngày mai"}))
    assert first[0]["text"] == LOCATION_MESSAGE
    assert not calls
    second = events(client.post("/api/conversation", json={
        "text": "Ngày mai có mưa không?", "location": {"lat": 10.8, "lon": 106.6},
    }))
    assert "26°C" in second[0]["text"]
    assert calls == [{"lat": 10.8, "lon": 106.6, "date": (local_today() + timedelta(days=1)).isoformat()}]


def test_saved_guide_follows_up_with_exact_lesson(client):
    from conversation import GUIDE_FOLLOWUP
    response = client.post("/api/conversation", json={
        "text": "Pha trà",
        "history": [{"role": "assistant", "text": GUIDE_FOLLOWUP}],
    })
    result = events(response)
    assert "Cầm ấm lên" in result[0]["text"]
    assert result[1]["id"] == "test-skill"
    assert result[1]["route"] == "guide"


@pytest.mark.parametrize("camera", [False, True])
def test_conversation_streams_selected_model(client, monkeypatch, camera):
    from providers import OpenAICompatibleProvider

    def respond(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["stream"] is True
        assert body["messages"][1] == {"role": "user", "content": "Chào bạn"}
        # The coordinator remains tool-capable even with an attached image.
        # Vision is called through inspect_image, covered by tool-loop tests.
        assert body['model'] == 'conversation-test'
        assert body['messages'][-1]['content'] == 'Mình có câu hỏi.'
        assert any(tool['function']['name'] == 'inspect_image' for tool in body['tools'])
        return httpx.Response(200, text=(
            'data: {"choices":[{"delta":{"reasoning_content":"internal"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"Xin chào."}}]}\n\n'
            'data: {"choices":[{"delta":{"content":" Bạn cần gì?"}}]}\n\n'
            'data: [DONE]\n\n'
        ))

    monkeypatch.setenv("CONVERSATION_MODEL", "conversation-test")
    monkeypatch.setenv("VISION_MODEL", "vision-test")
    provider = OpenAICompatibleProvider(
        base_url="https://test.invalid/v1", api_key="test", transport=httpx.MockTransport(respond),
    )
    monkeypatch.setattr(client.app.state, "provider", provider)
    payload = {
        "text": "Mình có câu hỏi.",
        "history": [{"role": "user", "text": "Chào bạn"}],
    }
    if camera:
        with io.BytesIO() as data:
            Image.new("RGB", (128, 128), "red").save(data, format="JPEG")
            payload["image"] = "data:image/jpeg;base64," + base64.b64encode(data.getvalue()).decode()
    try:
        result = events(client.post("/api/conversation", json=payload))
        assert [event["text"] for event in result if event["type"] == "text"] == ["Xin chào.", " Bạn cần gì?"]
        assert result[-1]["type"] == "done"
    finally:
        client.portal.call(provider.aclose)


def test_hypertek_voice_proxy(client):
    def respond(request):
        if request.url.path == "/v1/audio/transcriptions":
            assert b"large-v3-turbo" in request.content
            assert b"fake-wav" in request.content
            return httpx.Response(200, json={"text": " Xin chào "})
        assert request.url.path == "/v1/audio/speech"
        assert json.loads(request.content)["model"] == "vieneu-v3-turbo"
        return httpx.Response(200, content=b"RIFFfake-wav")

    voice = httpx.AsyncClient(base_url="https://test.invalid/v1", transport=httpx.MockTransport(respond))
    previous = client.app.state.voice_client
    client.app.state.voice_client = voice
    try:
        assert client.get("/api/voice/config").json() == {"available": True}
        assert client.post("/api/voice/transcribe", content=b"fake-wav", headers={"Content-Type": "audio/wav"}).json() == {"text": "Xin chào"}
        assert client.post("/api/voice/transcribe", content=b"fake", headers={"Content-Type": "text/plain"}).status_code == 415
        result = client.post("/api/voice/speech", json={"text": "Xin chào"})
        assert result.headers["content-type"] == "audio/wav"
        assert result.content == b"RIFFfake-wav"
    finally:
        client.app.state.voice_client = previous
        client.portal.call(voice.aclose)


def test_truncated_upstream_reply_is_not_reported_complete(client):
    from providers import OpenAICompatibleProvider

    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, text='data: {"choices":[{"delta":{"content":"Xin chào"}}]}\n\n',
    ))
    provider = OpenAICompatibleProvider(base_url="https://test.invalid/v1", api_key="test", transport=transport)
    previous = client.app.state.provider
    client.app.state.provider = provider
    try:
        result = events(client.post("/api/conversation", json={"text": "Bạn ơi"}))
        assert [row["type"] for row in result] == ["text", "error"]
    finally:
        client.app.state.provider = previous
        client.portal.call(provider.aclose)

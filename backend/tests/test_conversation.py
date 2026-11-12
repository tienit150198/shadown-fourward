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


def test_weather_followup_uses_real_weather_service(client, monkeypatch):
    from conversation import CITY_FOLLOWUP
    calls = []

    async def forecast(**kwargs):
        calls.append(kwargs)
        return {"summary_vi": "Hà Nội 26°C, có thể mưa."}

    monkeypatch.setattr(client.app.state, "wardrobe_weather", SimpleNamespace(get=forecast), raising=False)
    first = events(client.post("/api/conversation", json={"text": "Thời tiết ngày mai"}))
    assert first[0]["text"] == CITY_FOLLOWUP
    assert not calls
    second = events(client.post("/api/conversation", json={
        "text": "Hà Nội",
        "history": [
            {"role": "user", "text": "Thời tiết ngày mai"},
            {"role": "assistant", "text": CITY_FOLLOWUP},
        ],
    }))
    assert second[0]["text"] == "Hà Nội 26°C, có thể mưa."
    assert calls[0]["city"] == "Hà Nội"
    assert calls[0]["date"]


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
        assert sum(m["role"] == "system" for m in body["messages"]) == 1
        assert {"role": "user", "content": "Chào bạn"} in body["messages"]
        assert body["model"] == "conversation-test"
        assert "inspect_image" in {tool["function"]["name"] for tool in body["tools"]}
        if camera:
            assert "Ảnh đính kèm tin này: True" in body["messages"][0]["content"]
            assert body["messages"][-1]["content"] == "Mình có câu hỏi."
        else:
            assert body["model"] == "conversation-test"
            assert body["messages"][-1]["content"] == "Mình có câu hỏi."
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

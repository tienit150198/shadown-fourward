from __future__ import annotations

import json

import httpx
import pytest

from providers import (
    MockProvider,
    OpenAICompatibleProvider,
    ProviderError,
    build_provider_from_env,
    extract_json_object,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"flag": true}', {"flag": True}),
        ('```json\n{"state": "open", "hint": "Mở nắp ra"}\n```', {"state": "open", "hint": "Mở nắp ra"}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here is the result: {"flag": false} hope it helps', {"flag": False}),
        ('noise {bad} then {"level": 7, "x": {"y": [1, 2]}} tail', {"level": 7, "x": {"y": [1, 2]}}),
    ],
)
def test_extract_json_object(text, expected):
    assert extract_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", "{broken"])
def test_extract_json_object_failures(text):
    with pytest.raises(ProviderError):
        extract_json_object(text)


def test_build_provider_modes(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("SHADOW_MODE", "auto")
    assert build_provider_from_env().mode == "mock"
    monkeypatch.setenv("SHADOW_MODE", "ai")
    assert build_provider_from_env().mode == "mock"  # no key -> safe fallback
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    assert build_provider_from_env().mode == "ai"
    monkeypatch.setenv("SHADOW_MODE", "mock")
    assert isinstance(build_provider_from_env(), MockProvider)
    monkeypatch.setenv("SHADOW_MODE", "auto")
    assert build_provider_from_env().mode == "ai"


@pytest.mark.asyncio
async def test_openai_compatible_provider_request_and_retry():
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append({"body": body, "headers": dict(request.headers), "url": str(request.url)})
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "```json\n{\"flag\": true}\n```"}}]},
        )

    provider = OpenAICompatibleProvider(
        base_url="https://openrouter.ai/api/v1/",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    content = [
        {"type": "text", "text": "is it done?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
    ]
    result = await provider.chat_json(model="m", system="sys", content=content)
    await provider.aclose()

    assert result == {"flag": True}
    assert len(calls) == 2
    first = calls[0]
    assert first["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert first["headers"]["authorization"] == "Bearer sk-test"
    assert first["headers"]["x-title"] == "SHADOW"
    assert "http-referer" in first["headers"]
    assert first["body"]["response_format"] == {"type": "json_object"}
    assert first["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert first["body"]["messages"][1]["content"] == content
    assert first["body"]["max_tokens"] == 400 and first["body"]["temperature"] == 0.1


@pytest.mark.asyncio
async def test_openai_compatible_provider_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1", api_key="k", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderError, match="500"):
        await provider.chat_json(model="m", system="s", content=[])
    await provider.aclose()


@pytest.mark.asyncio
async def test_mock_provider_has_mode():
    provider = MockProvider()
    assert provider.mode == "mock"
    with pytest.raises(NotImplementedError):
        await provider.chat_json(model="m", system="s", content=[])

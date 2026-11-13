"""Provider failures must remain actionable without exposing upstream bodies/secrets."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from openrouter_image import OpenRouterImage, OpenRouterImageError
from qwen_image import QwenImage, QwenImageError
from tryon import TryOnError, TryOnService
from vidu_image import ViduImageError, _task_json
from voice_api import send_audio
from wardrobe_testlib import data_url, item, make_jpeg


@pytest.mark.parametrize('status,code,retryable', [
    (401, 'provider_auth_failed', False), (403, 'provider_auth_failed', False),
    (402, 'insufficient_credits', False), (429, 'rate_limited', True),
    (500, 'provider_unavailable', True),
])
def test_image_provider_failures(monkeypatch, status, code, retryable):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'not-a-real-key')
    monkeypatch.setenv('HYPERTEK_API_KEY', 'not-a-real-key')
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={'secret': 'must-not-escape'}))
    for provider, error_type, call in [
        (OpenRouterImage(transport=transport), OpenRouterImageError, lambda p: p.edit('outfit', [make_jpeg()] * 2)),
        (QwenImage(transport=transport), QwenImageError, lambda p: p.generate('outfit')),
    ]:
        with pytest.raises(error_type) as exc:
            asyncio.run(call(provider))
        assert exc.value.code == code and exc.value.retryable is retryable
        assert 'must-not-escape' not in str(exc.value)
    with pytest.raises(ViduImageError) as exc:
        _task_json(httpx.Response(status, json={}))
    assert exc.value.code == code and exc.value.retryable is retryable


def test_missing_keys_are_not_retryable(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    monkeypatch.setenv('HYPERTEK_API_KEY', '')
    monkeypatch.setenv('LLM_API_KEY', '')
    for call, cls in [(lambda: OpenRouterImage().edit('outfit', [make_jpeg()] * 2), OpenRouterImageError),
                      (lambda: QwenImage().generate('outfit'), QwenImageError)]:
        with pytest.raises(cls) as exc:
            asyncio.run(call())
        assert exc.value.code == 'missing_api_key' and not exc.value.retryable


def test_tryon_preserves_provider_error_metadata(monkeypatch):
    monkeypatch.setenv('TRYON_PHOTO_PROVIDER', 'openrouter')
    class FailedImage:
        async def edit(self, *args):
            raise OpenRouterImageError('Credit required', 503, code='insufficient_credits')
    service = TryOnService(openrouter_image=FailedImage())
    garment = item('top', 'top')
    with pytest.raises(TryOnError) as exc:
        asyncio.run(service.run(data_url(make_jpeg()), [garment], {'top': make_jpeg()}))
    assert exc.value.code == 'insufficient_credits'
    assert exc.value.service == 'photo_tryon' and not exc.value.retryable


@pytest.mark.parametrize('status,code,retryable', [(401, 'provider_auth_failed', False), (402, 'insufficient_credits', False), (429, 'rate_limited', True), (500, 'provider_unavailable', True)])
def test_voice_failure_headers(status, code, retryable):
    async def exercise():
        async with httpx.AsyncClient(base_url='https://example.test', transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(voice_client=client)))
            with pytest.raises(HTTPException) as exc:
                await send_audio(request, '/audio/speech', json={})
            assert exc.value.headers['X-Shadow-Error-Code'] == code
            assert exc.value.headers['X-Shadow-Service'] == 'voice'
            assert exc.value.headers['X-Shadow-Retryable'] == str(retryable).lower()
    asyncio.run(exercise())

import json

import httpx
import pytest

from gemini_live import client_from_env
from providers import build_provider_from_env, compiler_model, llm_base_url, vision_model


@pytest.mark.asyncio
async def test_openai_key_enables_direct_provider_defaults(monkeypatch):
    for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'VISION_MODEL', 'COMPILER_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-key')
    monkeypatch.setenv('SHADOW_MODE', 'auto')
    assert llm_base_url() == 'https://api.openai.com/v1'
    assert vision_model() == compiler_model() == 'gpt-4.1-mini'
    provider = build_provider_from_env()
    assert provider.mode == 'ai'
    assert provider._api_key == 'private-test-key'
    await provider.aclose()


def test_openai_session_keeps_key_server_side_and_preserves_tools(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'private-test-key')
    monkeypatch.setenv('SHADOW_LIVE_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_LIVE_API', 'realtime')
    seen = []
    def handler(request):
        assert request.url.path == '/v1/realtime/client_secrets'
        assert request.headers['authorization'] == 'Bearer private-test-key'
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'value': 'ek_short_lived'})
    previous = client.app.state.gemini_live_client
    upstream = httpx.AsyncClient(base_url='https://api.openai.com',
        headers={'Authorization':'Bearer private-test-key'}, transport=httpx.MockTransport(handler))
    client.app.state.gemini_live_client = upstream
    try:
        config = client.get('/api/live/config').json()
        assert config['available'] and config['provider'] == 'openai'
        response = client.post('/api/live/session', json={'configured_city':'Hà Nội',
            'history':[{'role':'user','text':'Chỉ tôi pha cà phê'}]})
        assert response.status_code == 200, response.text
        assert 'private-test-key' not in response.text
        assert response.json()['token'] == 'ek_short_lived'
        assert response.json()['callUrl'] == 'https://api.openai.com/v1/realtime/calls'
        session = seen[0]['session']
        assert session['type'] == 'realtime'
        assert session['audio']['output']['voice'] == 'marin'
        assert 'Hà Nội' in session['instructions']
        assert 'Chỉ tôi pha cà phê' in session['instructions']
        assert {'lesson_control','camera_task','list_wardrobe'} <= {t['name'] for t in session['tools']}
        assert all(t['type'] == 'function' and 'parameters' in t for t in session['tools'])
        assert seen[0]['expires_after']['seconds'] == 60
    finally:
        client.app.state.gemini_live_client = previous
        client.portal.call(upstream.aclose)


def test_openai_billing_error_is_sanitized(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-test-key')
    monkeypatch.setenv('OPENAI_LIVE_API','realtime')
    previous = client.app.state.gemini_live_client
    upstream = httpx.AsyncClient(base_url='https://api.openai.com', transport=httpx.MockTransport(
        lambda r: httpx.Response(429,json={'error':{'code':'insufficient_quota','message':'private-test-key'}})))
    client.app.state.gemini_live_client = upstream
    try:
        response = client.post('/api/live/session', json={})
        assert response.status_code == 429
        assert response.json()['detail']['code'] == 'insufficient_credits'
        assert response.json()['detail']['service'] == 'openai_live'
        assert 'private-test-key' not in response.text
    finally:
        client.app.state.gemini_live_client = previous
        client.portal.call(upstream.aclose)


def test_openai_fallback_voice_uses_audio_models(client, monkeypatch):
    monkeypatch.setenv('SHADOW_VOICE_PROVIDER', 'openai')
    previous = client.app.state.voice_client
    seen = []
    def handler(request):
        seen.append((request.url.path, request.content))
        if request.url.path.endswith('/transcriptions'):
            return httpx.Response(200,json={'text':'Xin chào'})
        return httpx.Response(200,content=b'RIFF' + b'\0' * 40)
    upstream = httpx.AsyncClient(base_url='https://api.openai.com/v1', transport=httpx.MockTransport(handler))
    client.app.state.voice_client = upstream
    try:
        response = client.post('/api/voice/transcribe', content=b'audio', headers={'Content-Type':'audio/webm'})
        assert response.status_code == 200
        assert response.json() == {'text':'Xin chào'}
        assert b'gpt-4o-mini-transcribe' in seen[0][1]
        response = client.post('/api/voice/speech', json={'text':'Xin chào'})
        assert response.status_code == 200
        assert response.headers['content-type'] == 'audio/wav'
        assert json.loads(seen[1][1])['model'] == 'gpt-4o-mini-tts'
        assert json.loads(seen[1][1])['voice'] == 'marin'
    finally:
        client.app.state.voice_client = previous
        client.portal.call(upstream.aclose)


def test_explicit_openai_without_key_does_not_fall_back_to_gemini(monkeypatch):
    monkeypatch.setenv('SHADOW_LIVE_PROVIDER','openai')
    monkeypatch.setenv('GEMINI_API_KEY','depleted-key')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    assert client_from_env() is None

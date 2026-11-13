"""Offline Gemini relay contract tests: never connects to Google or spends credits."""
from __future__ import annotations

import asyncio
import base64
import json
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import gemini_live
from main import app


class FakeUpstream:
    def __init__(self, messages=(), *, on_send=None, setup_response=None):
        self.incoming = asyncio.Queue()
        for message in messages:
            self.incoming.put_nowait(message)
        self.sent = []
        self.on_send = on_send
        self.setup_response = {'setupComplete': {}} if setup_response is None else setup_response
        self.closed = False

    async def send(self, text):
        value = json.loads(text)
        self.sent.append(value)
        if self.on_send:
            self.on_send(self, value)

    async def recv(self):
        return json.dumps(self.setup_response)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.incoming.get())


class FakeConnector:
    def __init__(self, messages=(), *, on_send=None, setup_response=None):
        self.messages, self.on_send, self.setup_response = messages, on_send, setup_response
        self.calls = []
        self.upstream = None

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.upstream = FakeUpstream(self.messages, on_send=self.on_send, setup_response=self.setup_response)
        return self

    async def __aenter__(self):
        return self.upstream

    async def __aexit__(self, *args):
        self.upstream.closed = True


@pytest.fixture
def relay_client(monkeypatch, tmp_path):
    monkeypatch.setenv('SHADOW_SKILLS_DIR', str(tmp_path / 'skills'))
    monkeypatch.setenv('SHADOW_WARDROBE_DIR', str(tmp_path / 'wardrobe'))
    monkeypatch.setenv('SHADOW_ACCESS_CODE', '')
    monkeypatch.setenv('GEMINI_LIVE_ENABLED', 'on')
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-gemini-test-key')
    monkeypatch.setenv('GEMINI_LIVE_MODEL', 'gemini-3.8-live')
    monkeypatch.setenv('GEMINI_LIVE_MAX_SESSION_SECONDS', '180')
    monkeypatch.setattr(gemini_live, '_active', 0)
    connector = FakeConnector()
    monkeypatch.setattr(app.state, 'gemini_live_connect', connector, raising=False)
    with TestClient(app) as client:
        yield client, connector



@pytest.mark.parametrize('enabled,key,expected', [('off','test-key','service_disabled'), ('on','','missing_api_key')])
def test_disabled_or_missing_key_never_connects(relay_client, monkeypatch, enabled, key, expected):
    client, connector = relay_client
    monkeypatch.setenv('GEMINI_LIVE_ENABLED', enabled)
    monkeypatch.setenv('GEMINI_API_KEY', key)
    cfg = client.get('/api/voice/live/config').json()
    assert cfg['available'] is False and cfg['code'] == expected
    with client.websocket_connect('/session/voice') as ws:
        error = ws.receive_json()
        assert error['type'] == 'error' and error['code'] == expected
        assert error['service'] == 'gemini_live' and error['retryable'] is False
    assert connector.calls == [] and gemini_live._active == 0


def test_setup_uses_backend_key_audio_config_and_json_schemas(relay_client):
    client, connector = relay_client
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start', 'configured_city':'Đà Nẵng', 'history':[{'role':'user','text':'I prefer loose fit'}]})
        ready = ws.receive_json()
        assert ready['type'] == 'ready' and ready['model'] == 'gemini-3.8-live'
        assert 'offline-gemini-test-key' not in json.dumps(ready)
        url, options = connector.calls[0]
        assert url == gemini_live.UPSTREAM_URL and '?key=' not in url
        assert options['additional_headers'] == {'x-goog-api-key':'offline-gemini-test-key'}
        setup = connector.upstream.sent[0]['setup']
        assert setup['model'] == 'models/gemini-3.8-live'
        assert setup['generationConfig']['responseModalities'] == ['AUDIO']
        assert setup['inputAudioTranscription'] == {} and setup['outputAudioTranscription'] == {}
        assert setup['realtimeInputConfig']['automaticActivityDetection']['disabled'] is False
        instruction = setup['systemInstruction']['parts'][0]['text']
        assert 'Đà Nẵng' in instruction and 'I prefer loose fit' in instruction
        declarations = setup['tools'][0]['functionDeclarations']
        assert 'inspect_image' not in {tool['name'] for tool in declarations}
        assert {'get_style_profile','list_wardrobe','get_weather','showcase_outfits'} <= {tool['name'] for tool in declarations}
        assert all('parametersJsonSchema' in tool and 'parameters' not in tool for tool in declarations)
        ws.send_json({'type':'stop'})
    assert connector.upstream.closed and gemini_live._active == 0


def test_audio_and_transcripts_in_same_event_are_all_relayed(relay_client):
    client, connector = relay_client
    output = base64.b64encode(b'\x00\x00' * 12).decode()
    connector.messages = [{'serverContent': {
        'inputTranscription': {'text':'Xin chào'}, 'outputTranscription': {'text':'Chào bạn'},
        'modelTurn': {'parts':[{'inlineData':{'mimeType':'audio/pcm;rate=24000','data':output}}]},
        'turnComplete': True,
    }}]
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        assert ws.receive_json()['type'] == 'ready'
        events = [ws.receive_json() for _ in range(4)]
        assert [event['type'] for event in events] == ['input_transcription','text','audio','turn_complete']
        assert events[0]['text'] == 'Xin chào' and events[1]['text'] == 'Chào bạn'
        assert events[2] == {'type':'audio','data':output,'sample_rate':24000}
        ws.send_json({'type':'stop'})


def test_client_audio_text_and_end_are_forwarded(relay_client):
    client, connector = relay_client
    encoded = base64.b64encode(b'\x00\x00' * 160).decode()
    def echo(upstream, value):
        if value.get('realtimeInput', {}).get('audioStreamEnd'):
            upstream.incoming.put_nowait({'serverContent':{'turnComplete':True}})
    connector.on_send = echo
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        ws.receive_json()
        ws.send_json({'type':'audio','data':encoded})
        ws.send_json({'type':'text','text':'  Hello  '})
        ws.send_json({'type':'audio_end'})
        assert ws.receive_json()['type'] == 'turn_complete'
        ws.send_json({'type':'stop'})
    assert {'realtimeInput':{'audio':{'data':encoded,'mimeType':'audio/pcm;rate=16000'}}} in connector.upstream.sent
    assert {'realtimeInput':{'text':'Hello'}} in connector.upstream.sent
    assert {'realtimeInput':{'audioStreamEnd':True}} in connector.upstream.sent


def test_real_server_tool_result_uses_matching_call_id(relay_client):
    client, connector = relay_client
    connector.messages = [{'toolCall':{'functionCalls':[{'id':'call-profile','name':'get_style_profile','args':{}}]}}]
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        ws.receive_json()
        assert ws.receive_json()['state'] == 'running'
        assert ws.receive_json()['state'] == 'done'
        # A follow-up marker ensures the tool response has reached the fake upstream.
        ws.send_json({'type':'text','text':'marker'})
        ws.send_json({'type':'stop'})
    responses = [item['toolResponse']['functionResponses'][0] for item in connector.upstream.sent if 'toolResponse' in item]
    assert len(responses) == 1
    assert responses[0]['id'] == 'call-profile' and responses[0]['name'] == 'get_style_profile'
    assert responses[0]['response']['profile'] == {}


def test_cancelled_tool_cannot_send_late_response(relay_client, monkeypatch):
    client, connector = relay_client
    cancelled = threading.Event()
    async def execute(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(gemini_live, 'execute', execute)
    connector.messages = [{'toolCall':{'functionCalls':[{'id':'slow','name':'list_wardrobe','args':{}}]}}]
    def cancel(upstream, value):
        if value.get('realtimeInput', {}).get('text') == 'cancel':
            upstream.incoming.put_nowait({'toolCallCancellation':{'ids':['slow']}, 'serverContent':{'interrupted':True, 'turnComplete':True}})
    connector.on_send = cancel
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        ws.receive_json()
        assert ws.receive_json()['state'] == 'running'
        ws.send_json({'type':'text','text':'cancel'})
        seen = []
        while not any(row['type'] == 'turn_complete' for row in seen):
            seen.append(ws.receive_json())
        assert any(row['type'] == 'interrupted' for row in seen)
        ws.send_json({'type':'stop'})
    assert cancelled.is_set()
    assert not [row for row in connector.upstream.sent if 'toolResponse' in row]


@pytest.mark.parametrize('payload', ['{bad-json', '[]', '{"type":"unknown"}', '{"type":"audio","data":"bad!"}', '{"type":"audio","data":"AA=="}'])
def test_malformed_client_messages_are_invalid_request(relay_client, payload):
    client, connector = relay_client
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        ws.receive_json()
        ws.send_text(payload)
        error = ws.receive_json()
        assert error['type'] == 'error' and error['code'] == 'invalid_request'
    assert connector.upstream.closed and gemini_live._active == 0


def test_hard_session_limit_closes_upstream_and_releases_slot(relay_client, monkeypatch):
    client, connector = relay_client
    monkeypatch.setenv('GEMINI_LIVE_MAX_SESSION_SECONDS', '1')
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        assert ws.receive_json()['max_session_seconds'] == 1
        assert ws.receive_json() == {'type':'closed','reason':'session_limit'}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert connector.upstream.closed and gemini_live._active == 0


def test_auth_gate_rejects_before_upstream_connect(relay_client, monkeypatch):
    client, connector = relay_client
    monkeypatch.setenv('SHADOW_ACCESS_CODE', 'private-access')
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect('/session/voice'):
            pass
    assert error.value.code == 1008 and connector.calls == []
    with client.websocket_connect('/session/voice', headers={'X-Shadow-Access':'private-access'}) as ws:
        ws.send_json({'type':'start'})
        assert ws.receive_json()['type'] == 'ready'
        ws.send_json({'type':'stop'})


@pytest.mark.parametrize('status,code,retryable', [(401,'provider_auth_failed',False),(402,'insufficient_credits',False),(429,'rate_limited',True)])
def test_upstream_setup_errors_are_actionable_and_redacted(relay_client, status, code, retryable):
    client, connector = relay_client
    connector.setup_response = {'error':{'code':status,'message':'secret upstream body'}}
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        error = ws.receive_json()
        assert error['code'] == code and error['retryable'] is retryable
        assert 'secret upstream body' not in json.dumps(error)
    assert connector.upstream.closed and gemini_live._active == 0


@pytest.mark.parametrize('before_ready', [True, False])
def test_binary_client_frames_are_invalid_request(relay_client, before_ready):
    client, connector = relay_client
    with client.websocket_connect('/session/voice') as ws:
        if not before_ready:
            ws.send_json({'type':'start'})
            assert ws.receive_json()['type'] == 'ready'
        ws.send_bytes(b'not-json-protocol')
        error = ws.receive_json()
        assert error['type'] == 'error' and error['code'] == 'invalid_request'
    if before_ready:
        assert connector.calls == []
    else:
        assert connector.upstream.closed
    assert gemini_live._active == 0


@pytest.mark.parametrize('start_payload', ['[]', '{bad-json', '{"type":"start","extra":true}', '{"type":"audio","data":"AAA="}'])
def test_invalid_start_does_not_open_google_connection(relay_client, start_payload):
    client, connector = relay_client
    with client.websocket_connect('/session/voice') as ws:
        ws.send_text(start_payload)
        assert ws.receive_json()['code'] == 'invalid_request'
    assert connector.calls == [] and gemini_live._active == 0


@pytest.mark.parametrize('city,retains_weather', [('Hà Nội', True), ('Đà Nẵng', False)])
def test_voice_copies_chat_preferences_and_invalidates_weather_on_city_change(relay_client, city, retains_weather):
    client, connector = relay_client
    store = app.state.conversations
    conversation_id, previous = store.get(None)
    previous.context = {'configured_city':'Hà Nội', 'weather':{'available':True, 'summary_vi':'cached-weather-marker'}, 'style_profile':{'fit_preferences':'loose-fit-marker'}}
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start','configured_city':city,'conversation_id':conversation_id})
        assert ws.receive_json()['type'] == 'ready'
        instruction = connector.upstream.sent[0]['setup']['systemInstruction']['parts'][0]['text']
        assert 'loose-fit-marker' in instruction
        assert ('cached-weather-marker' in instruction) is retains_weather
        ws.send_json({'type':'stop'})
    assert previous.context['weather']['summary_vi'] == 'cached-weather-marker'


def test_google_depleted_credits_close_has_actionable_error(relay_client,monkeypatch):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    async def depleted(self):
        raise ConnectionClosedError(Close(1011, 'Your prepayment credits are depleted. private upstream details'), None)
    monkeypatch.setattr(FakeUpstream,'recv',depleted)
    client,connector=relay_client
    with client.websocket_connect('/session/voice') as ws:
        ws.send_json({'type':'start'})
        error=ws.receive_json()
        assert error['type']=='error' and error['code']=='insufficient_credits' and error['retryable'] is False
        assert 'private upstream' not in error['message']

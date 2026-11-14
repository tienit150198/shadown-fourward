import json
from uuid import uuid4

import pytest

from live_tools import sessions
from types import SimpleNamespace


def batch(session_id, **fields):
    return {'session_id':session_id, 'events':[{'seq':1, 'at_ms':1000, 'kind':'transcript',
                                             'role':'user', 'text':'Tôi làm xong rồi.', **fields}]}


@pytest.fixture
def trace_session(client, monkeypatch):
    monkeypatch.setenv('SHADOW_LIVE_TRACE', 'on')
    store = sessions(SimpleNamespace(app=client.app))
    key = store.create('')
    yield key, store.get(key)
    store.sessions.pop(key, None)


def test_trace_requires_issued_session_and_can_be_disabled(client, monkeypatch):
    monkeypatch.setenv('SHADOW_LIVE_TRACE', 'off')
    assert client.post('/api/live/trace', json=batch(str(uuid4()))).json()['accepted'] == 0
    monkeypatch.setenv('SHADOW_LIVE_TRACE', 'on')
    assert client.post('/api/live/trace', json=batch(str(uuid4()))).status_code == 410


def test_transcript_is_correlated_redacted_and_retries_are_deduplicated(client, trace_session, caplog):
    key, session = trace_session
    secret = 'ghp_' + 'a' * 36
    payload = batch(key, text='Tôi lưu lại nhé. '+secret+'\nDòng hai', guide_session_id=str(uuid4()), step=1)
    with caplog.at_level('INFO', logger='uvicorn.error'):
        assert client.post('/api/live/trace', json=payload).json()['accepted'] == 1
        assert client.post('/api/live/trace', json=payload).json()['accepted'] == 0
    assert secret not in caplog.text
    assert '[redacted]' in caplog.text and 'Tôi lưu lại nhé.' in caplog.text
    assert f'session={key}' in caplog.text
    lines = [r.message for r in caplog.records if 'SKI live_trace' in r.message]
    assert len(lines) == 1
    event = json.loads(lines[0].split(' event=', 1)[1])
    assert event['step'] == 1 and '\n' in event['text']
    assert len(session.trace_seen) == 1


def test_trace_rejects_media_and_limits_storage(client, trace_session):
    key, session = trace_session
    payload = batch(key)
    payload['events'][0]['image'] = 'data:image/jpeg;base64,private'
    assert client.post('/api/live/trace', json=payload).status_code == 422
    assert client.post('/api/live/trace', json=batch(key, text='a'*4001)).status_code == 422
    session.trace_bytes = 512000
    assert client.post('/api/live/trace', json=batch(key)).status_code == 429


def test_out_of_order_batches_do_not_lose_earlier_events(client, trace_session):
    key, session = trace_session
    assert client.post('/api/live/trace', json=batch(key, seq=2)).json()['accepted'] == 1
    assert client.post('/api/live/trace', json=batch(key, seq=1)).json()['accepted'] == 1
    assert session.trace_seen == {1, 2}

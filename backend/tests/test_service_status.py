import asyncio

import pytest
from fastapi.testclient import TestClient

import service_status
from main import app
from tryon import TryOnService


@pytest.fixture(autouse=True)
def reset_observations():
    service_status._observations.clear()
    yield
    service_status._observations.clear()


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('SHADOW_SKILLS_DIR', str(tmp_path / 'skills'))
    monkeypatch.setenv('SHADOW_WARDROBE_DIR', str(tmp_path / 'wardrobe'))
    monkeypatch.setenv('TRYON_PHOTO_PROVIDER', 'openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    monkeypatch.setenv('VIDU_API_KEY', '')
    monkeypatch.setenv('SHADOW_VOICE_PROVIDER', '')
    for key in ('SNAP_CAMERA_KIT_API_TOKEN', 'SNAP_CAMERA_KIT_LENS_GROUP_ID', 'SNAP_CAMERA_KIT_LENS_ID'):
        monkeypatch.setenv(key, '')
    with TestClient(app) as client:
        yield client


def test_status_no_keys_and_no_paid_probes(client, monkeypatch):
    # Explicit provider_chain does not contact any provider or validate credits.
    response = client.get('/api/services/status')
    assert response.status_code == 200
    body = response.json()
    services = body['services']
    assert services['photo_tryon']['state'] == 'unconfigured'
    assert services['photo_tryon']['missing_configuration'] == ['OPENROUTER_API_KEY']
    assert services['live_tryon']['code'] == 'missing_api_key'
    assert services['voice']['state'] == 'disabled'
    assert services['ar_tryon']['state'] == 'unconfigured'
    assert services['llm']['state'] == 'demo'
    assert body['check_type'] == 'configuration_and_recent_requests'


def test_recent_errors_recovery_and_expiry(client, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'do-not-leak-this-key')
    service_status.record_failure('photo_tryon', 'insufficient_credits', 'secret raw provider error', False)
    response = client.get('/api/services/status')
    assert 'do-not-leak' not in response.text and 'secret raw' not in response.text
    status = response.json()['services']['photo_tryon']
    assert status['state'] == 'degraded' and not status['last_observation']['error']['retryable']
    service_status.record_success('photo_tryon')
    status = client.get('/api/services/status').json()['services']['photo_tryon']
    assert status['state'] == 'configured' and status['last_observation']['outcome'] == 'success'
    at, observation = service_status._observations['photo_tryon']
    service_status._observations['photo_tryon'] = (at - 301, observation)
    assert client.get('/api/services/status').json()['services']['photo_tryon']['last_observation'] is None


def test_status_protected_and_standard_errors(client, monkeypatch):
    monkeypatch.setenv('SHADOW_ACCESS_CODE', 'test-access')
    response = client.get('/api/services/status')
    assert response.status_code == 401
    assert response.json()['code'] == 'access_required'
    assert response.json()['retryable'] is False
    assert client.get('/api/services/status', headers={'X-Shadow-Access':'test-access'}).status_code == 200
    monkeypatch.setenv('SHADOW_ACCESS_CODE', '')
    response = client.post('/api/tryon/photo', json={})
    assert response.status_code == 422 and response.json()['code'] == 'validation_error'
    assert isinstance(response.json()['details'], list)


def test_forced_ai_missing_key_is_visible(client, monkeypatch):
    monkeypatch.setenv('SHADOW_MODE', 'ai')
    monkeypatch.setenv('LLM_API_KEY', '')
    assert client.get('/api/services/status').json()['services']['chat']['state'] == 'unconfigured'


def test_http_metadata_drives_status(client, monkeypatch):
    from fastapi import HTTPException
    @app.get('/api/test-service-failure', include_in_schema=False)
    async def fail():
        raise HTTPException(503, 'Not enough credits', headers={'X-Shadow-Error-Code':'insufficient_credits', 'X-Shadow-Service':'photo_tryon', 'X-Shadow-Retryable':'false'})
    app.router.routes.insert(0, app.router.routes.pop())
    try:
        monkeypatch.setenv('OPENROUTER_API_KEY', 'configured-test-key')
        response = client.get('/api/test-service-failure')
        assert response.json() == {'message':'Not enough credits','code':'insufficient_credits','service':'photo_tryon','retryable':False}
        assert client.get('/api/services/status').json()['services']['photo_tryon']['state'] == 'degraded'
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/api/test-service-failure']


def test_photo_missing_key_returns_actionable_error(client):
    from wardrobe_testlib import data_url, make_jpeg
    image = data_url(make_jpeg())
    created = client.post('/api/wardrobe/items', json={'image': image, 'name': 'Test shirt', 'category': 'top'})
    assert created.status_code == 200
    response = client.post('/api/tryon/photo', json={'person_image': image, 'item_ids':[created.json()['id']]})
    assert response.status_code == 503
    body = response.json()
    assert body['code'] == 'missing_api_key' and body['service'] == 'photo_tryon'
    assert body['retryable'] is False
    assert 'OPENROUTER_API_KEY' in body['message']


def test_latest_shared_dependency_failure_is_visible(client, monkeypatch):
    monkeypatch.setenv('SHADOW_MODE', 'ai')
    monkeypatch.setenv('LLM_API_KEY', 'test-configured-key')
    service_status.record_success('chat')
    service_status.record_failure('llm', 'provider_auth_failed', 'private', False)
    status = client.get('/api/services/status').json()['services']['chat']
    assert status['state'] == 'degraded' and status['code'] == 'provider_auth_failed'


def test_invalid_access_code_is_distinct_from_missing_access(client, monkeypatch):
    monkeypatch.setenv('SHADOW_ACCESS_CODE', 'correct-access')
    denied = client.post('/api/access', json={'code': 'wrong-access'})
    assert denied.status_code == 401
    assert denied.json() == {'message': 'Mã truy cập không đúng.', 'code': 'invalid_access_code', 'retryable': False}
    missing = client.get('/api/services/status')
    assert missing.json()['code'] == 'access_required'
    assert isinstance(missing.json()['message'], str)


def test_error_message_is_not_duplicated(client):
    response = client.post('/api/tryon/photo', json={})
    body = response.json()
    assert 'detail' not in body
    assert body['details'] and all('input' not in issue and 'ctx' not in issue for issue in body['details'])
    assert all({'loc', 'message', 'code'} <= issue.keys() for issue in body['details'])

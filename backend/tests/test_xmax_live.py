import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from xmax_live import XmaxClient
from wardrobe_store import ItemNotFound, wardrobe_root


@pytest.fixture
def install_xmax(client, monkeypatch):
    previous = client.app.state.xmax_client
    values = []
    def get_item(item_id):
        if item_id != 'shirt': raise ItemNotFound(item_id)
        return {'id': 'shirt', 'name': 'Áo trắng', 'image_url': '/wardrobe-media/shirt.jpg'}
    monkeypatch.setattr(client.app.state, 'wardrobe_store', SimpleNamespace(root=wardrobe_root(), get_item=get_item), raising=False)
    def install(handler):
        value = XmaxClient('private-permanent-key', transport=httpx.MockTransport(handler))
        values.append(value)
        client.app.state.xmax_client = value
    yield install
    client.app.state.xmax_client = previous
    for value in values: client.portal.call(value.aclose)


def test_xmax_without_key_never_claims_generation_available(client):
    assert client.get('/api/tryon/xmax/config').json()['available'] is False
    assert client.post('/api/tryon/xmax/session', json={'item_id': 'shirt'}).status_code == 503


def test_only_short_lived_bounded_key_and_selected_garment_reach_browser(client, install_xmax):
    def respond(request):
        assert request.headers['X-Api-Key'] == 'private-permanent-key'
        body = json.loads(request.content)
        assert 0 < body['expireSeconds'] <= 180
        assert body['temporaryApiKeySessionLimit'] == 1
        assert body['pointsLimit'] <= 200
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': 'tk-ephemeral', 'expireTimestamp': 'soon'}})
    install_xmax(respond)
    response = client.post('/api/tryon/xmax/session', json={'item_id': 'shirt', 'pointsLimit': 999999})
    assert response.status_code == 200
    assert response.json()['token'] == 'tk-ephemeral'
    assert response.json()['item']['id'] == 'shirt'
    assert response.json()['model'] == 'x2.1'
    assert 'private-permanent-key' not in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_nonexistent_garment_does_not_mint_a_key(client, install_xmax):
    def unexpected(_): pytest.fail('No upstream request for an invalid garment')
    install_xmax(unexpected)
    assert client.post('/api/tryon/xmax/session', json={'item_id': '../../private'}).status_code == 404


def test_outfit_reference_and_prompt_include_both_shirt_and_trousers(client, install_xmax, monkeypatch, tmp_path):
    from PIL import Image
    from wardrobe_store import WardrobeStore
    import io
    store = WardrobeStore(tmp_path)
    for item_id, category, color in [('shirt', 'top', 'red'), ('pants', 'bottom', 'blue')]:
        output = io.BytesIO()
        Image.new('RGB', (200, 300), color).save(output, 'JPEG')
        store.create_item({'name': item_id, 'category': category}, output.getvalue(), item_id=item_id)
    import xmax_live
    monkeypatch.setattr(xmax_live, '_store', lambda _: store)
    install_xmax(lambda _: httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': 'tk-outfit'}}))
    result = client.post('/api/tryon/xmax/session', json={'item_ids': ['shirt', 'pants']})
    assert result.status_code == 200
    data = result.json()
    assert [item['id'] for item in data['items']] == ['shirt', 'pants']
    assert 'TOP: shirt' in data['prompt'] and 'BOTTOM: pants' in data['prompt']
    path = store.media_path(data['reference_image_url'].removeprefix('/wardrobe-media/'))
    with Image.open(path) as board:
        assert board.size == (1024, 768)
        assert board.getpixel((256, 400))[0] > 200
        assert board.getpixel((768, 400))[2] > 200


@pytest.mark.parametrize('ids', [[], ['shirt', 'shirt'], ['shirt', 'shirt', 'shirt', 'shirt']])
def test_invalid_outfit_is_rejected_before_minting_a_paid_key(client, install_xmax, ids):
    install_xmax(lambda _: pytest.fail('Invalid selection must not mint a key'))
    assert client.post('/api/tryon/xmax/session', json={'item_ids': ids}).status_code == 422


def test_close_can_only_deactivate_keys_issued_by_this_app(client, install_xmax):
    calls = []
    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': 'tk-ours'}})
    install_xmax(respond)
    assert client.post('/api/tryon/xmax/close', json={'token': 'tk-someone-else'}).status_code == 200
    assert not calls
    client.post('/api/tryon/xmax/session', json={'item_id': 'shirt'})
    assert client.post('/api/tryon/xmax/close', json={'token': 'tk-ours'}).status_code == 200
    assert calls[-1] == ('PUT', '/open/api/v1/temporary-api-key/tk-ours/inactive')


@pytest.mark.parametrize('status', [200, 401, 429, 500])
def test_provider_errors_never_echo_a_permanent_key(client, install_xmax, status):
    install_xmax(lambda _: httpx.Response(status, json={'success': False, 'message': 'private-permanent-key'}))
    response = client.post('/api/tryon/xmax/session', json={'item_id': 'shirt'})
    assert response.status_code in (429, 502)
    assert 'private-permanent-key' not in response.text
    assert 'xmax' not in response.json()['detail'].lower()
    assert 'x2.1' not in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_pool_rotates_per_session_and_closes_each_token_with_its_issuing_key():
    calls, counter = [], 0
    def respond(request):
        nonlocal counter
        key = request.headers['X-Api-Key']
        calls.append((request.method, key, request.url.path))
        if request.method == 'POST':
            body = json.loads(request.content)
            assert body['pointsLimit'] <= 200 and body['temporaryApiKeySessionLimit'] == 1
            counter += 1
            return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': f'tk-session-{counter}'}})
        return httpx.Response(200, json={'success': True, 'data': {}})
    pool = XmaxClient(['first', 'second'], transport=httpx.MockTransport(respond))
    try:
        tokens = [(await pool.issue())['temporaryApiKey'] for _ in range(3)]
        assert [key for method, key, _ in calls if method == 'POST'] == ['first', 'second', 'first']
        await pool.close_token(tokens[1])
        assert calls[-1][1:] == ('second', '/open/api/v1/temporary-api-key/tk-session-2/inactive')
        await pool.close_token('tk-unowned')
        assert len(calls) == 4
    finally:
        await pool.aclose()
    assert [(key, path) for method, key, path in calls if method == 'PUT'] == [
        ('second', '/open/api/v1/temporary-api-key/tk-session-2/inactive'),
        ('first', '/open/api/v1/temporary-api-key/tk-session-1/inactive'),
        ('first', '/open/api/v1/temporary-api-key/tk-session-3/inactive'),
    ]
    assert all(client.is_closed for client in pool.clients)


@pytest.mark.asyncio
async def test_priority_uses_primary_until_it_fails_then_uses_backup():
    used = []
    def respond(request):
        if request.method == 'PUT':
            return httpx.Response(200, json={'success': True, 'data': {}})
        key = request.headers['X-Api-Key']; used.append(key)
        if key == 'primary' and len(used) >= 3:
            return httpx.Response(429, json={'success': False, 'code': 46003})
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': f'tk-priority-{len(used)}'}})
    pool = XmaxClient(['primary', 'backup'], strategy='priority', transport=httpx.MockTransport(respond))
    try:
        for _ in range(4):
            await pool.issue()
        assert used == ['primary', 'primary', 'primary', 'backup', 'backup']
    finally:
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(('status', 'code'), [(429, 429), (200, 46003), (200, 46032), (401, 401), (403, 403), (503, 503)])
async def test_unavailable_key_fails_over_and_is_cooled_down(status, code):
    used = []
    def respond(request):
        if request.method == 'PUT':
            return httpx.Response(200, json={'success': True, 'data': {}})
        key = request.headers['X-Api-Key']; used.append(key)
        if key == 'bad':
            return httpx.Response(status, json={'success': False, 'code': code, 'message': 'Xmax private details'})
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': f'tk-ok-{len(used)}'}})
    pool = XmaxClient(['bad', 'good'], transport=httpx.MockTransport(respond))
    try:
        assert (await pool.issue())['temporaryApiKey'].startswith('tk-ok')
        assert (await pool.issue())['temporaryApiKey'].startswith('tk-ok')
        assert used == ['bad', 'good', 'good']
        assert all(owner == 1 for owner in pool.owners.values())
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_all_exhausted_keys_stop_requests_until_cooldown_ends():
    used = []
    def respond(request):
        used.append(request.headers['X-Api-Key'])
        return httpx.Response(429, json={'success': False, 'code': 46003})
    pool = XmaxClient(['first', 'second'], transport=httpx.MockTransport(respond))
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as first:
            await pool.issue()
        assert first.value.status_code == 429
        with pytest.raises(HTTPException) as second:
            await pool.issue()
        assert second.value.status_code == 429
        assert int(second.value.headers['Retry-After']) > 0
        assert used == ['first', 'second']
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_invalid_upstream_request_is_not_replayed_across_all_keys():
    used = []
    def respond(request):
        used.append(request.headers['X-Api-Key'])
        return httpx.Response(400, json={'success': False, 'message': 'invalid arguments'})
    pool = XmaxClient(['first', 'second'], transport=httpx.MockTransport(respond))
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await pool.issue()
        assert used == ['first']
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_concurrent_sessions_rotate_and_keep_the_correct_token_owner():
    used, owners = [], {}
    async def respond(request):
        key = request.headers['X-Api-Key']
        if request.method == 'PUT':
            token = request.url.path.split('/')[-2]
            assert owners[token] == key
            return httpx.Response(200, json={'success': True, 'data': {}})
        used.append(key); token = f'tk-concurrent-{len(used)}'; owners[token] = key
        await asyncio.sleep(.001)
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': token}})
    pool = XmaxClient(['first', 'second'], transport=httpx.MockTransport(respond))
    try:
        results = await asyncio.gather(*(pool.issue() for _ in range(8)))
        assert used.count('first') == used.count('second') == 4
        assert len({row['temporaryApiKey'] for row in results}) == 8
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_slow_pool_has_one_total_deadline_instead_of_waiting_for_every_key(monkeypatch):
    import xmax_live
    monkeypatch.setattr(xmax_live, 'ISSUE_TIMEOUT', .03)
    monkeypatch.setattr(xmax_live, 'KEY_ATTEMPT_TIMEOUT', .01)
    async def respond(request):
        await asyncio.Future()
    pool = XmaxClient(['first', 'second', 'third', 'fourth', 'fifth'], transport=httpx.MockTransport(respond))
    started = time.monotonic()
    try:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await pool.issue()
        assert error.value.status_code == 504
        assert time.monotonic() - started < .3
    finally:
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(('raw', 'expected'), [('[" first ", "second", "first"]', ['first', 'second']), ('first,second,first', ['first', 'second']), ('first\nsecond', ['first', 'second']), ('[]', ['legacy']), ('', ['legacy'])])
async def test_pool_env_supports_lists_deduplicates_and_preserves_legacy_key(monkeypatch, raw, expected):
    from xmax_live import client_from_env
    monkeypatch.setenv('XMAX_API_KEYS', raw)
    monkeypatch.setenv('XMAX_API_KEY', 'legacy')
    pool = client_from_env()
    try:
        assert [value.headers['X-Api-Key'] for value in pool.clients] == expected
    finally:
        await pool.aclose()


@pytest.mark.parametrize('raw', ['[invalid-private-value', '["first", 123]'])
def test_malformed_pool_env_does_not_echo_credentials(monkeypatch, raw):
    from xmax_live import client_from_env
    monkeypatch.setenv('XMAX_API_KEYS', raw)
    with pytest.raises(ValueError) as error:
        client_from_env()
    assert raw not in str(error.value)


@pytest.mark.asyncio
async def test_priority_skips_empty_balance_before_creating_a_temporary_key():
    calls = []
    def respond(request):
        key = request.headers['X-Api-Key']; calls.append((request.method, key))
        if request.method == 'GET':
            return httpx.Response(200, json={'success': True, 'data': {'currentBalance': 0 if key == 'empty' else 200}})
        return httpx.Response(200, json={'success': True, 'data': {'temporaryApiKey': 'tk-funded'}})
    pool = XmaxClient(['empty', 'funded'], strategy='priority', check_balance=True, transport=httpx.MockTransport(respond))
    try:
        assert (await pool.issue())['temporaryApiKey'] == 'tk-funded'
        assert calls == [('GET', 'empty'), ('GET', 'funded'), ('POST', 'funded')]
        assert pool.cooldowns[0] > time.monotonic()
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_production_pool_checks_balance_before_issuing(monkeypatch):
    from xmax_live import client_from_env
    monkeypatch.setenv('XMAX_API_KEYS', '["configured"]')
    pool = client_from_env()
    try:
        assert pool.check_balance is True
    finally:
        await pool.aclose()

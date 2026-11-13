import json
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

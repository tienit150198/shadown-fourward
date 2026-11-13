import asyncio
import base64
import json

import httpx
import pytest

from openrouter_image import MODEL, OpenRouterImage, OpenRouterImageError
from tryon import TryOnService
from wardrobe_testlib import make_jpeg, data_url, item


def test_full_layered_outfit(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    monkeypatch.setenv('TRYON_PHOTO_PROVIDER', 'openrouter')
    items = [item('inner', 'top'), item('outer', 'top'), item('pants', 'bottom'), item('shoes', 'shoes')]
    photo = make_jpeg()
    def handler(request):
        assert str(request.url) == 'https://openrouter.ai/api/v1/images'
        assert request.headers['authorization'] == 'Bearer test-key'
        body = json.loads(request.content)
        assert body['model'] == MODEL and body['n'] == 1 and body['resolution'] == '1K'
        assert len(body['input_references']) == 5
        assert all(x['image_url']['url'].startswith('data:image/jpeg;base64,') for x in body['input_references'])
        assert 'intentional layers' in body['prompt'] and 'Image 5:' in body['prompt']
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(photo).decode()}]})
    service = TryOnService(openrouter_image=OpenRouterImage(transport=httpx.MockTransport(handler)))
    result = asyncio.run(service.run(data_url(photo), items, {i['id']: photo for i in items}))
    assert result['provider'] == 'openrouter'
    assert result['rendered_item_ids'] == [i['id'] for i in items]
    assert result['skipped_item_ids'] == [] and result['jpeg'].startswith(b'\xff\xd8')
    assert asyncio.run(service.config())['photo']['renders_bottoms']


@pytest.mark.parametrize('status,expected', [(401,503),(402,503),(429,429),(500,502)])
def test_errors(monkeypatch, status, expected):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterImage(transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(OpenRouterImageError) as exc:
        asyncio.run(provider.edit('edit', [make_jpeg()] * 2))
    assert exc.value.status == expected


def test_bad_image(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    provider = OpenRouterImage(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={'data':[{'b64_json':'invalid!'}]})))
    with pytest.raises(OpenRouterImageError, match='không hợp lệ'):
        asyncio.run(provider.edit('edit', [make_jpeg()] * 2))

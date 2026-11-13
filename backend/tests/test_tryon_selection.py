import json
from types import SimpleNamespace

import pytest

from tryon_selection import merge_selection, plan_tryon


ITEMS = [
    {'id': 'white-shirt', 'name': 'Áo thun trắng UNIQLO', 'category': 'top', 'colors': ['trắng'], 'image_url': '/shirt.jpg'},
    {'id': 'grey-pants', 'name': 'Quần nỉ xám UNIQLO', 'category': 'bottom', 'colors': ['xám'], 'image_url': '/pants.jpg'},
    {'id': 'jeans', 'name': 'Quần jeans xanh', 'category': 'bottom', 'colors': ['xanh'], 'image_url': '/jeans.jpg'},
]
STORE = SimpleNamespace(list_items=lambda: ITEMS, list_rules=lambda: [])


class Provider:
    mode = 'ai'
    def __init__(self, response):
        self.response, self.requests = response, []
    async def chat_json(self, **kwargs):
        self.requests.append(json.loads(kwargs['content'][0]['text']))
        return self.response


@pytest.mark.asyncio
async def test_spoken_outfit_resolves_actual_ids_without_browser_selection():
    provider = Provider({'action': 'apply', 'item_ids': ['white-shirt', 'grey-pants']})
    result = await plan_tryon(provider, STORE, 'Mặc áo trắng và quần nỉ xám cho tôi')
    assert result['status'] == 'ready'
    assert result['item_ids'] == ['white-shirt', 'grey-pants']
    assert {item['id'] for item in provider.requests[0]['wardrobe']} == {item['id'] for item in ITEMS}


@pytest.mark.asyncio
async def test_voice_change_preserves_shirt_and_uses_previous_recommendation():
    provider = Provider({'action': 'apply', 'item_ids': ['jeans']})
    history = [{'role': 'assistant', 'text': 'Bạn có thể đổi sang quần jeans xanh.'}]
    result = await plan_tryon(provider, STORE, 'Đổi sang quần bạn vừa gợi ý', selected_ids=['white-shirt', 'grey-pants'], history=history)
    assert result['item_ids'] == ['white-shirt', 'jeans']
    assert provider.requests[0]['recent_conversation'] == history


@pytest.mark.asyncio
@pytest.mark.parametrize('ids', [['invented'], ['white-shirt', 'white-shirt'], ['grey-pants', 'jeans']])
async def test_hallucinated_or_conflicting_selection_cannot_start_xmax(ids):
    result = await plan_tryon(Provider({'action': 'apply', 'item_ids': ids}), STORE, 'Mặc thử cho tôi')
    assert result['status'] == 'needs_clarification'
    assert result['item_ids'] == []


def test_selecting_a_dress_replaces_shirt_and_pants_but_keeps_outerwear():
    items = ITEMS + [{'id': 'dress', 'category': 'dress'}, {'id': 'coat', 'category': 'outerwear'}]
    assert merge_selection(items, ['white-shirt', 'grey-pants', 'coat'], ['dress']) == ['coat', 'dress']


@pytest.mark.asyncio
async def test_known_selected_ids_do_not_need_another_model_lookup():
    provider = Provider(None)
    result = await plan_tryon(provider, STORE, 'Mặc bộ đã chọn', selected_ids=['white-shirt'], requested_ids=['grey-pants'])
    assert result['item_ids'] == ['white-shirt', 'grey-pants']
    assert not provider.requests


@pytest.mark.asyncio
async def test_planner_failure_is_visible_and_never_reports_started_generation():
    async def fail(**kwargs):
        raise RuntimeError('private provider details')
    result = await plan_tryon(SimpleNamespace(mode='ai', chat_json=fail), STORE, 'Mặc bộ áo trắng quần xám')
    assert result['status'] == 'unavailable'
    assert result['item_ids'] == []
    assert 'private' not in result['message']

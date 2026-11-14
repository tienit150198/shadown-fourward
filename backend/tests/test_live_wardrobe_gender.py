"""Clothing audience constraints survive paging, follow-ups and fitting tools."""
import copy
import json
from types import SimpleNamespace

import pytest

import conversation_tools
import live_tools
from test_live_tools import invoke


@pytest.fixture
def catalog(client, monkeypatch, tmp_path):
    items = [
        {'id': 'men-shirt', 'gender': 'men', 'category': 'top'},
        {'id': 'women-shirt', 'gender': 'women', 'category': 'top'},
        {'id': 'shared-pants', 'gender': 'unisex', 'category': 'bottom'},
        {'id': 'unknown-shirt', 'category': 'top'},
    ]
    for item in items:
        item.update(name=item['id'], image_url=f"/wardrobe-media/{item['id']}.jpg")
    store = SimpleNamespace(root=tmp_path, list_items=lambda: copy.deepcopy(items), list_rules=lambda: [])
    monkeypatch.setattr(conversation_tools, '_store', lambda _: store)
    monkeypatch.setattr(live_tools, '_store', lambda _: store)
    monkeypatch.setattr(client.app.state, 'live_tool_sessions', live_tools.LiveToolStore(), raising=False)
    key = client.app.state.live_tool_sessions.create('')
    return key, items


@pytest.mark.parametrize('gender,expected', [
    ('nam', ['men-shirt', 'shared-pants']),
    ('male', ['men-shirt', 'shared-pants']),
    ('nữ', ['women-shirt', 'shared-pants']),
    ('female', ['women-shirt', 'shared-pants']),
    ('non-binary', ['men-shirt', 'women-shirt', 'shared-pants', 'unknown-shirt']),
])
def test_declared_profile_filters_before_paging_without_guessing(client, catalog, gender, expected):
    key, _ = catalog
    invoke(client, key, 'update_style_profile', {'gender': gender}, call='profile')
    collected = []
    offset = 0
    while True:
        page = invoke(client, key, 'list_wardrobe', {'limit': 1, 'offset': offset}, call=f'page-{offset}').json()['result']
        collected.extend(item['id'] for item in page['items'])
        assert all('gender' in item for item in page['items'])
        assert page['total'] == len(expected)
        if not page['has_more']:
            assert page['next_offset'] is None
            break
        offset = page['next_offset']
    assert collected == expected


def test_explicit_clothing_choice_overrides_profile_and_survives_followup(client, catalog):
    key, _ = catalog
    invoke(client, key, 'update_style_profile', {'gender': 'nam'}, call='profile')
    result = invoke(client, key, 'list_wardrobe', {'gender': 'women'}, call='women').json()['result']
    assert [i['id'] for i in result['items']] == ['women-shirt', 'shared-pants']
    page = invoke(client, key, 'list_wardrobe', {'category': 'top'}, call='followup').json()['result']
    assert [i['id'] for i in page['items']] == ['women-shirt']
    assert page['gender_filter'] == 'women'
    profile = invoke(client, key, 'get_style_profile', call='read').json()['result']
    assert profile['profile']['gender'] == 'nam'
    assert profile['gender_filter'] == 'women'
    all_items = invoke(client, key, 'list_wardrobe', {'gender': 'all'}, call='all').json()['result']
    assert all_items['total'] == 4
    unisex = invoke(client, key, 'list_wardrobe', {'gender': 'unisex'}, call='unisex').json()['result']
    assert [i['id'] for i in unisex['items']] == ['shared-pants']
    invoke(client, key, 'update_style_profile', {'gender': None}, call='clear')
    assert invoke(client, key, 'list_wardrobe', call='after-clear').json()['result']['total'] == 4


def test_missing_womens_stock_does_not_silently_return_mens_or_unknown_items(client, catalog):
    key, items = catalog
    items[:] = [i for i in items if i.get('gender') != 'women']
    result = invoke(client, key, 'list_wardrobe', {'gender': 'women'}, call='women').json()['result']
    assert [i['id'] for i in result['items']] == ['shared-pants']
    assert result['available_gender_counts'] == {'men': 1, 'women': 0, 'unisex': 1, 'unknown': 1}
    tops = invoke(client, key, 'list_wardrobe', {'category': 'top'}, call='tops').json()['result']
    assert tops['total'] == 0 and tops['items'] == [] and not tops['has_more']


def test_stale_wrong_group_cannot_be_displayed_or_fitted(client, catalog):
    key, _ = catalog
    invoke(client, key, 'update_style_profile', {'gender': 'nữ'}, call='profile')
    args = {'introduction': 'Bộ thử', 'outfits': [{'title': 'Bộ thử', 'item_ids': ['men-shirt', 'shared-pants']}]}
    wrong = invoke(client, key, 'showcase_outfits', args, call='wrong-show').json()
    assert wrong['result']['invalid_item_ids'] == ['men-shirt'] and not wrong['events']
    wrong_try = invoke(client, key, 'start_tryon', {'item_ids': ['men-shirt']}, call='wrong-try').json()
    assert wrong_try['result']['invalid_item_ids'] == ['men-shirt'] and not wrong_try['events']
    args['outfits'][0]['item_ids'] = ['women-shirt', 'shared-pants']
    shown = invoke(client, key, 'showcase_outfits', args, call='show').json()
    assert any(e['type'] == 'outfits' for e in shown['events'])
    assert [i['gender'] for i in shown['result']['outfits'][0]['items']] == ['women', 'unisex']
    fitted = invoke(client, key, 'start_tryon', {'outfit_number': 1}, call='fit').json()
    assert fitted['result']['item_ids'] == ['women-shirt', 'shared-pants']


def test_live_camera_filters_auto_selection_and_validates_returned_ids(client, catalog, monkeypatch):
    key, _ = catalog
    invoke(client, key, 'update_style_profile', {'gender': 'nữ'}, call='profile')
    async def select(**kwargs):
        data = json.loads(kwargs['content'][0]['text'])
        assert data['gender_filter'] == 'women'
        assert {i['id'] for i in data['wardrobe']} == {'women-shirt', 'shared-pants'}
        return {'action': 'apply', 'item_ids': ['men-shirt']}
    monkeypatch.setattr(client.app.state, 'provider', SimpleNamespace(mode='ai', chat_json=select))
    result = invoke(client, key, 'camera_task', {'intent': 'tryon_live', 'request': 'Chọn bộ cho tôi'}, call='auto').json()['result']
    assert result['camera_decision']['context']['tryon_plan']['status'] == 'needs_clarification'
    result = invoke(client, key, 'camera_task', {'intent': 'tryon_live', 'request': 'Thử đồ nam', 'gender': 'men', 'item_ids': ['men-shirt', 'shared-pants']}, call='explicit').json()['result']
    assert result['camera_decision']['context']['tryon_plan']['item_ids'] == ['men-shirt', 'shared-pants']
    profile = invoke(client, key, 'get_style_profile', call='read').json()['result']
    assert profile['profile']['gender'] == 'nữ' and profile['gender_filter'] == 'men'


def test_profile_and_filter_are_isolated_to_the_conversation(client, catalog):
    key, _ = catalog
    invoke(client, key, 'update_style_profile', {'gender': 'nam'}, call='profile')
    other = client.app.state.live_tool_sessions.create('')
    result = invoke(client, other, 'list_wardrobe').json()['result']
    assert result['gender_filter'] is None and result['total'] == 4

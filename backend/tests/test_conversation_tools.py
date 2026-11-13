"""Offline integration regressions for the model-driven conversation tool loop."""
import copy
import json
from types import SimpleNamespace

import pytest

from wardrobe_testlib import data_url, make_jpeg, sample_wardrobe


def call(name, args=None, id='call-1'):
    return {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args or {})}}


def tools(*calls):
    return {'role': 'assistant', 'content': None, 'tool_calls': list(calls)}


def answer(text='Được nhé.'):
    return {'role': 'assistant', 'content': text}


class ScriptedProvider:
    mode = 'ai'

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.vision_calls = []

    async def stream_chat(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        assert self.replies, 'Unexpected extra model request'
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if reply.get('content'):
            yield {'type': 'text', 'text': reply['content']}
        yield {'type': 'message', 'message': reply}

    async def chat_json(self, **kwargs):
        self.vision_calls.append(kwargs)
        return {'observation': 'Áo màu đỏ.'}


def post(client, **payload):
    response = client.post('/api/conversation', json={'text': 'Giúp mình với', **payload})
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines()]


def results(provider, index=-1):
    return [json.loads(m['content']) for m in provider.calls[index]['messages'] if m['role'] == 'tool']


@pytest.fixture
def setup_chat(client, monkeypatch, tmp_path):
    import conversation
    import conversation_tools

    items = [{**i, 'image_url': f"/api/wardrobe/items/{i['id']}/image"} for i in sample_wardrobe()]
    store = SimpleNamespace(root=tmp_path, list_items=lambda: copy.deepcopy(items), list_rules=lambda: [{'text': 'Ưu tiên xanh'}])
    monkeypatch.setattr(conversation_tools, '_store', lambda request: store)
    monkeypatch.setattr(client.app.state, 'conversations', conversation.ConversationStore(), raising=False)

    async def forbidden(*args, **kwargs):
        pytest.fail('Real conversation must bypass intent routing')

    monkeypatch.setattr(conversation, 'resolve_intent', forbidden)

    def install(*replies):
        provider = ScriptedProvider(*replies)
        monkeypatch.setattr(client.app.state, 'provider', provider)
        return provider

    return install


def test_inventory_results_are_grounded_and_followup_retains_tool_ids(client, setup_chat):
    provider = setup_chat(tools(call('list_wardrobe', id='inventory')), answer('Có áo sơ mi navy.'), answer('Áo ấy màu navy.'))
    first = post(client, text='Phối đồ giúp tôi')
    assert first[-1]['type'] == 'done'
    inventory = results(provider)[0]
    assert inventory['total'] == len(sample_wardrobe())
    assert next(i for i in inventory['items'] if i['id'] == 't2')['name'] == 'Sơ mi navy'
    assert inventory['rules'] == [{'text': 'Ưu tiên xanh'}]
    cid = first[-1]['conversation_id']
    second = post(client, text='Áo đó màu gì?', conversation_id=cid)
    assert second[-1]['conversation_id'] == cid
    history = provider.calls[-1]['messages']
    assert any(m.get('tool_call_id') == 'inventory' for m in history)
    assert any(m.get('tool_calls', [{}])[0].get('id') == 'inventory' for m in history)
    assert {'role': 'assistant', 'content': 'Có áo sơ mi navy.'} in history
    assert history[-1] == {'role': 'user', 'content': 'Áo đó màu gì?'}


def test_weather_persists_into_later_outfit_request(client, setup_chat, monkeypatch):
    import conversation_tools
    forecasts = []
    weather = {'city': 'Hà Nội', 't_min': 18, 't_max': 24}
    async def forecast(**kwargs):
        forecasts.append(kwargs)
        return weather
    monkeypatch.setattr(conversation_tools, '_weather', lambda request: SimpleNamespace(get=forecast))
    provider = setup_chat(tools(call('get_weather')), answer(), tools(call('showcase_outfits', {'introduction': 'Hợp trời mát.', 'outfits': [{'title': 'Đi làm', 'reason': 'Phù hợp trời mát', 'item_ids': ['t2','b2','o2','s1']}]})))
    first = post(client, configured_city='Hà Nội')
    second = post(client, conversation_id=first[-1]['conversation_id'], text='Vậy mặc gì đi làm?')
    cards = next(r['outfits'] for r in second if r['type']=='outfits')
    assert cards[0]['item_ids'] == ['t2','b2','o2','s1']
    assert all(i['image_url'] for i in cards[0]['items'])
    assert forecasts == [{'city':'Hà Nội','date':None}]
    assert any(m.get('role')=='tool' and 'Hà Nội' in m.get('content','') for m in provider.calls[-1]['messages'])
    assert len(provider.calls)==3


def test_camera_uses_conversation_coordinator_and_explicit_vision_tool(client, setup_chat, monkeypatch):
    monkeypatch.setenv('CONVERSATION_MODEL', 'chat-test')
    monkeypatch.setenv('VISION_MODEL', 'vision-test')
    provider = setup_chat(tools(call('inspect_image', {'question': 'Áo màu gì?'})), answer())
    result = post(client, image=data_url(make_jpeg()))
    assert result[-1]['type'] == 'done'
    assert all(c['model'] == 'chat-test' for c in provider.calls)
    assert provider.calls[0]['messages'][-1]['content'] == 'Giúp mình với'
    vision = provider.vision_calls[0]
    assert vision['model'] == 'vision-test'
    assert vision['content'][0]['text'] == 'Áo màu gì?'
    assert vision['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert results(provider)[0] == {'observation': 'Áo màu đỏ.'}


def test_invalid_and_unknown_tools_recover_without_weather_or_actions(client, setup_chat, monkeypatch):
    import conversation_tools

    def forbidden(*args, **kwargs):
        pytest.fail('Invalid tools must not access services')

    monkeypatch.setattr(conversation_tools, '_weather', forbidden)
    provider = setup_chat(tools(call('delete_wardrobe', id='unknown'), call('get_weather', {'city': 'Hanoi', 'unexpected': True}, id='invalid'), call('inspect_image', {'question': 'Nhìn gì?'}, id='no-camera')), answer('Bạn gửi ảnh nhé.'))
    result = post(client)
    assert [r['type'] for r in result if r['type'] != 'tool_status'] == ['text', 'done']
    statuses = [r for r in result if r['type'] == 'tool_status']
    assert [(r['id'], r['state']) for r in statuses] == [('unknown', 'running'), ('unknown', 'error'), ('invalid', 'running'), ('invalid', 'error'), ('no-camera', 'running'), ('no-camera', 'error')]
    assert all(set(r) == {'type', 'id', 'state', 'label'} and r['label'] for r in statuses)
    errors = results(provider)
    assert 'Unknown tool' in errors[0]['error']
    assert errors[1]['error'] == 'Invalid tool arguments'
    assert 'No image attached' in errors[2]['error']
    assert not provider.vision_calls


@pytest.mark.parametrize('ids,valid', [(['t2', 'b2', 'o2', 's1'], True), (['invented'], False), (['t2', 't2'], False), ([], False)])
def test_open_screen_validates_ids_and_preserves_layer_order(client, setup_chat, ids, valid):
    provider = setup_chat(tools(call('open_screen', {'screen': 'tryon-photo', 'item_ids': ids})), answer())
    result = post(client)
    actions = [r for r in result if r['type'] == 'action']
    assert not actions  # Try-on buttons belong to the card, never a duplicate standalone action.
    assert not [r for r in result if r['type'] == 'tool_status']
    if valid:
        assert results(provider)[0]['status'] == 'offered_to_user'
        cards = [r for r in result if r['type'] == 'outfits']
        assert len(cards) == 1
        assert cards[0]['outfits'][0]['item_ids'] == ids
        assert [i['id'] for i in cards[0]['outfits'][0]['items']] == ids
        assert all(i['image_url'] for i in cards[0]['outfits'][0]['items'])
        assert results(provider)[0]['outfits'] == cards[0]['outfits']
    else:
        assert 'existing wardrobe item IDs' in results(provider)[0]['error']


def test_tool_round_budget_returns_error_without_committing(client, setup_chat):
    import conversation
    provider = setup_chat(*(tools(call('list_skills', id=f'loop-{i}')) for i in range(conversation.MAX_ROUNDS)))
    result = post(client)
    assert result[-1]['type'] == 'error'
    assert not any(r['type'] == 'done' for r in result)
    assert len(provider.calls) == conversation.MAX_ROUNDS
    assert all(not session.turns and not session.context for session in client.app.state.conversations.sessions.values())


def test_failed_followup_does_not_commit_partial_messages_or_weather(client, setup_chat, monkeypatch):
    import conversation_tools
    from providers import ProviderError

    async def forecast(**kwargs):
        return {'city': 'Hanoi', 't_max': 99}

    monkeypatch.setattr(conversation_tools, '_weather', lambda request: SimpleNamespace(get=forecast))
    provider = setup_chat(answer('Chào bạn.'), tools(call('get_weather', {'city': 'Hanoi'}, id='failed-weather')), ProviderError('offline'), answer('Thử lại.'))
    first = post(client)
    cid = first[-1]['conversation_id']
    session = client.app.state.conversations.sessions[cid]
    before = copy.deepcopy(session.turns)
    failed = post(client, conversation_id=cid, text='Tin thất bại')
    assert failed[-1]['type'] == 'error'
    assert session.turns == before
    assert session.context == {}
    post(client, conversation_id=cid, text='Thử lại')
    assert not any(m.get('tool_call_id') == 'failed-weather' for m in provider.calls[-1]['messages'])
    assert not any(m.get('content') == 'Tin thất bại' for m in provider.calls[-1]['messages'])


def test_bootstrap_history_survives_the_next_server_managed_turn(client, setup_chat):
    provider = setup_chat(answer('Nhớ rồi.'), answer('Bạn thích màu xanh.'))
    first = post(client, text='Nhớ giúp nhé', history=[{'role': 'user', 'text': 'Tôi thích màu xanh.'}])
    post(client, text='Tôi thích màu gì?', conversation_id=first[-1]['conversation_id'])
    assert {'role': 'user', 'content': 'Tôi thích màu xanh.'} in provider.calls[-1]['messages']


def test_explicit_style_profile_persists_and_can_be_cleared(client, setup_chat):
    provider = setup_chat(
        tools(call('update_style_profile', {'clothing_preferences': ['unisex'], 'age_range': '25-34'}, id='save-profile')),
        answer('Mình đã nhớ sở thích.'),
        tools(call('get_style_profile', id='read-profile')), answer('Bạn thích unisex.'),
        tools(call('update_style_profile', {'age_range': None}, id='clear-age')), answer('Đã bỏ khoảng tuổi.'),
    )
    first = post(client, text='Tôi thích đồ unisex, 25-34 tuổi.')
    cid = first[-1]['conversation_id']
    post(client, text='Sở thích của tôi là gì?', conversation_id=cid)
    assert results(provider)[-1]['profile'] == {'clothing_preferences': ['unisex'], 'age_range': '25-34'}
    post(client, text='Đừng dùng tuổi của tôi nữa', conversation_id=cid)
    assert results(provider)[-1]['profile'] == {'clothing_preferences': ['unisex']}


def test_conversation_store_eviction_and_expired_ids():
    import conversation
    from uuid import uuid4
    store = conversation.ConversationStore(max_sessions=2, ttl=10)
    first, session = store.get(None)
    second, _ = store.get(None)
    session.touched -= 20
    replacement, _ = store.get(first)
    assert replacement != first and first not in store.sessions
    store.get(uuid4())
    assert len(store.sessions) == 2


def test_configured_city_is_app_context_and_defaults_weather_across_followups(client, setup_chat, monkeypatch):
    import conversation_tools
    forecasts = []

    async def forecast(**kwargs):
        forecasts.append(kwargs)
        return {'city': kwargs['city'], 't_max': 27}

    monkeypatch.setattr(conversation_tools, '_weather', lambda request: SimpleNamespace(get=forecast))
    provider = setup_chat(answer('Chào bạn.'), tools(call('get_weather', id='weather-default')), answer('Trời mát.'))
    first = post(client, configured_city='Đà Nẵng')
    cid = first[-1]['conversation_id']
    assert client.app.state.conversations.sessions[cid].context['configured_city'] == 'Đà Nẵng'
    second = post(client, conversation_id=cid, text='Thời tiết thế nào?')
    assert forecasts == [{'city': 'Đà Nẵng', 'date': None}]
    for request in provider.calls:
        system = [m for m in request['messages'] if m['role'] == 'system']
        assert len(system) == 1
        assert 'Đà Nẵng' in system[0]['content']
        assert 'GPS' in system[0]['content']
        assert any(term in system[0]['content'].lower() for term in ['app', 'ứng dụng'])
    status = [r for r in second if r['type'] == 'tool_status']
    assert [(r['id'], r['state']) for r in status] == [('weather-default', 'running'), ('weather-default', 'done')]
    assert all(set(r) == {'type', 'id', 'state', 'label'} and r['label'] for r in status)


def test_changed_configured_city_clears_previous_forecast_before_suggestions(client, setup_chat, monkeypatch):
    import conversation_tools
    forecasts, suggested_weather = [], []

    async def forecast(**kwargs):
        forecasts.append(kwargs)
        return {'city': kwargs['city'], 't_max': 27}

    monkeypatch.setattr(conversation_tools, '_weather', lambda request: SimpleNamespace(get=forecast))
    setup_chat(tools(call('get_weather')), answer(), answer(), tools(call('get_weather')), answer())
    first = post(client, configured_city='Hà Nội')
    cid = first[-1]['conversation_id']
    assert client.app.state.conversations.sessions[cid].context['weather']['city'] == 'Hà Nội'
    post(client, conversation_id=cid, configured_city='Huế')
    assert not client.app.state.conversations.sessions[cid].context.get('weather')
    post(client, conversation_id=cid)
    assert forecasts == [{'city': 'Hà Nội', 'date': None}, {'city': 'Huế', 'date': None}]


def test_weather_without_city_recovers_without_guessing_or_service_call(client, setup_chat, monkeypatch):
    import conversation_tools

    def forbidden(*args, **kwargs):
        pytest.fail('Missing city must not call weather service')

    monkeypatch.setattr(conversation_tools, '_weather', forbidden)
    provider = setup_chat(tools(call('get_weather', id='missing-city')), answer('Bạn ở thành phố nào?'))
    rows = post(client)
    assert rows[-1]['type'] == 'done'
    assert 'error' in results(provider)[0]
    assert [(r['id'], r['state']) for r in rows if r['type'] == 'tool_status'] == [('missing-city', 'running'), ('missing-city', 'error')]


def test_configured_city_length_is_validated_before_model(client, setup_chat):
    provider = setup_chat()
    response = client.post('/api/conversation', json={'text': 'Chào', 'configured_city': 'x' * 81})
    assert response.status_code == 422
    assert provider.calls == []


def test_showcase_introduces_cards_and_redundant_tryon_adds_no_ui(client, setup_chat, monkeypatch):
    import conversation_tools

    provider = setup_chat(
        tools(call('showcase_outfits', {'introduction': 'Mình chọn bộ hai lớp cho trời mát.', 'outfits': [{'title':'Hai lớp áo','reason':'Hợp trời mát','item_ids':['t1','t2','b2']}]}, id='show')),
        tools(call('open_screen', {'screen': 'tryon-live', 'item_ids': ['t1', 't2', 'b2']}, id='duplicate')),
        answer('Bạn thích bộ này không?'),
    )
    first = post(client)
    assert first[-1]['type'] == 'done'
    assert len(provider.calls) == 1  # Showcase completes the turn without an acknowledgement round.
    second = post(client, text='Cho tôi thử trực tiếp', conversation_id=first[-1]['conversation_id'])
    result = first + second
    visible = [r['type'] for r in result if r['type'] not in ('tool_status', 'done')]
    assert visible == ['text', 'outfits', 'text']
    assert next(r for r in result if r['type'] == 'text')['text'] == 'Mình chọn bộ hai lớp cho trời mát.'
    cards = next(r for r in result if r['type'] == 'outfits')['outfits']
    assert cards[0]['item_ids'] == ['t1', 't2', 'b2']
    assert [r['id'] for r in result if r['type'] == 'tool_status'] == []
    assert not [r for r in result if r['type'] == 'action']
    assert results(provider)[-1]['status'] == 'already_available_on_outfit_card'


@pytest.mark.parametrize('ids', [['missing'], ['t1','t1']])
def test_showcase_rejects_invalid_ids_without_cards(client, setup_chat, ids):
    provider = setup_chat(tools(call('showcase_outfits', {'introduction':'Bộ này nhé.', 'outfits':[{'title':'Bộ thử','item_ids':ids}]})), answer('Mình cần kiểm tra lại tủ đồ.'))
    result = post(client)
    assert not [r for r in result if r['type'] in ('outfits','action')]
    assert 'error' in results(provider)[0]


def test_chat_has_no_separate_stylist_tool():
    from conversation_tools import tool_schemas
    assert 'suggest_outfits' not in {t['function']['name'] for t in tool_schemas()}

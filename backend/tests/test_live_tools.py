import copy
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import conversation_tools
import live_tools


@pytest.fixture
def tool_session(client,monkeypatch,tmp_path):
    upstream=httpx.AsyncClient(base_url='https://generativelanguage.googleapis.com',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'name':'ephemeral-test-token'})))
    previous=client.app.state.gemini_live_client
    client.app.state.gemini_live_client=upstream
    client.app.state.live_tool_sessions=live_tools.LiveToolStore()
    items=[{'id':f'item-{i}','name':f'Shirt {i}','category':'top','image_url':f'/wardrobe-media/{i}.jpg'} for i in range(70)]
    store=SimpleNamespace(root=tmp_path,list_items=lambda:copy.deepcopy(items),list_rules=lambda:[])
    monkeypatch.setattr(conversation_tools,'_store',lambda r:store)
    response=client.post('/api/live/session',json={'configured_city':'Hà Nội'})
    assert response.status_code==200
    key=response.json()['toolSessionId']
    yield key,store
    client.app.state.gemini_live_client=previous
    client.portal.call(upstream.aclose)


def invoke(client,key,name,args=None,call='call-1',**extra):
    return client.post('/api/live/tool',json={'session_id':key,'call_id':call,'name':name,'arguments':args or {},**extra})


def test_mint_does_not_read_wardrobe_and_setup_is_not_classifier_gated(client,monkeypatch):
    import gemini_live
    seen=[]
    def response(r):
        seen.append(json.loads(r.content))
        return httpx.Response(200,json={'name':'ephemeral-test-token'})
    upstream=httpx.AsyncClient(base_url='https://generativelanguage.googleapis.com',transport=httpx.MockTransport(response))
    previous=client.app.state.gemini_live_client
    client.app.state.gemini_live_client=upstream
    monkeypatch.setattr(gemini_live,'wardrobe_store',lambda r:pytest.fail('No inventory read on session start'))
    try:
        result=client.post('/api/live/session',json={'configured_city':'Hồ Chí Minh'})
        assert result.status_code==200
        setup=seen[0]['bidiGenerateContentSetup']
        names={item['name'] for item in setup['tools'][0]['functionDeclarations']}
        assert {'list_wardrobe','get_weather','showcase_outfits','camera_task'} <= names
        assert 'classify_request' not in names
        assert 'suggest_outfits' not in names
        assert 'Hồ Chí Minh' in setup['systemInstruction']['parts'][0]['text']
        assert 'SHADOW_SYSTEM_WARDROBE' not in json.dumps(setup)
    finally:
        client.app.state.gemini_live_client=previous
        client.portal.call(upstream.aclose)


def test_inventory_is_paginated_on_demand(client,tool_session):
    key,_=tool_session
    result=invoke(client,key,'list_wardrobe').json()['result']
    assert len(result['items'])==50 and result['has_more'] and result['next_offset']==50
    page=invoke(client,key,'list_wardrobe',{'offset':50},call='page2').json()['result']
    assert len(page['items'])==20 and not page['has_more']


def test_call_id_replay_is_idempotent_and_arguments_cannot_change(client,tool_session,monkeypatch):
    key,_=tool_session
    count=[]
    async def execute(*args,**kwargs):
        count.append(1)
        kwargs['context']['updated']=True
        return {'ok':True},None
    monkeypatch.setattr(live_tools,'execute',execute)
    first=invoke(client,key,'get_style_profile')
    again=invoke(client,key,'get_style_profile')
    assert first.json()==again.json() and len(count)==1
    assert invoke(client,key,'get_style_profile',{'different':True}).status_code==409


def test_weather_and_preferences_are_reused_by_stylist_and_cards(client,tool_session,monkeypatch):
    key,_=tool_session
    weather_calls=[]
    async def forecast(**kwargs):
        weather_calls.append(kwargs)
        return {'available':True,'city':'Hà Nội','t_min':20,'t_max':25}
    monkeypatch.setattr(conversation_tools,'_weather',lambda r:SimpleNamespace(get=forecast))
    assert invoke(client,key,'get_weather',call='weather').json()['result']['available']
    invoke(client,key,'update_style_profile',{'fit_preferences':'loose'},call='profile')
    result=invoke(client,key,'showcase_outfits',{'introduction':'Bộ này phù hợp.','outfits':[{'title':'Bộ thử','item_ids':['item-0','item-1'],'reason':'Layers'}]},call='show').json()
    assert [event['type'] for event in result['events']]==['text','outfits']
    assert result['events'][1]['outfits'][0]['items'][0]['image_url']
    assert result['events'][1]['outfits'][0]['item_ids']==['item-0','item-1']
    assert invoke(client,key,'get_style_profile',call='read-profile').json()['result']['profile']['fit_preferences']=='loose'
    assert len(weather_calls)==1 and weather_calls[0]['city']=='Hà Nội'


def test_expiry_unknown_session_and_call_limit_are_explicit(client,tool_session):
    key,_=tool_session
    session=client.app.state.live_tool_sessions.get(key)
    session.results={str(i):(None,{}) for i in range(60)}
    assert invoke(client,key,'get_style_profile',call='new').status_code==429
    session.expires=time.monotonic()-1
    assert invoke(client,key,'get_style_profile').status_code==410
    assert invoke(client,str(uuid4()),'get_style_profile').status_code==410


def test_camera_tool_keeps_main_coordinator_without_decisions_call(client,tool_session,monkeypatch):
    key,_=tool_session
    async def forbidden(*args,**kwargs):
        pytest.fail('No Decisions request for direct tool')
    import gemini_live
    monkeypatch.setattr(gemini_live,'_post',forbidden)
    result=invoke(client,key,'camera_task',{'intent':'teach_skill','request':'Dạy bài mới','command':'route_request'}).json()['result']['camera_decision']
    assert result['intent']=='teach_skill' and result['source']=='tool_call'
    assert 'confidence' not in result
    assert result['camera_command']=='route_request'

import json
import httpx
import pytest


def test_live_sdp_exchange_uses_sol_and_preserves_tool_bridge(client, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-test-key')
    monkeypatch.setenv('SHADOW_LIVE_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_LIVE_API','live')
    monkeypatch.setenv('CONVERSATION_MODEL','gpt-6.1-sol')
    monkeypatch.setenv('OPENAI_REASONING_EFFORT','none')
    seen=[]
    def handler(request):
        assert request.url.path == '/v1/live/sessions'
        seen.append(json.loads(request.content))
        return httpx.Response(201,json={'session':{'id':'live_test'},'transport':{'type':'webrtc','sdp':'answer'}})
    previous=client.app.state.gemini_live_client
    upstream=httpx.AsyncClient(base_url='https://api.openai.com',transport=httpx.MockTransport(handler))
    client.app.state.gemini_live_client=upstream
    try:
        config=client.get('/api/live/config').json()
        assert config['provider']=='openai_live' and config['model']=='gpt-live-1'
        start=client.post('/api/live/session',json={'configured_city':'Hà Nội','history':[{'role':'user','text':'Xin chào'}]})
        assert start.status_code==200
        assert not seen
        assert 'token' not in start.json()
        session_id=start.json()['toolSessionId']
        response=client.post('/api/live/connect',json={'session_id':session_id,'sdp':'offer\r\n'})
        assert response.status_code==200, response.text
        assert response.json()['transport']['sdp']=='answer'
        assert 'private-test-key' not in response.text
        assert seen[0]['transport']['sdp'] == 'offer\r\n'
        config=seen[0]['session']
        assert config['model']=='gpt-live-1'
        backend=config['delegation']['responses']
        assert backend['model']=='gpt-6.1-sol'
        assert backend['reasoning']['effort']=='low'
        assert 'Hà Nội' in backend['instructions']
        assert {'camera_task','lesson_control','list_wardrobe'} <= {t['name'] for t in backend['tools']}
        assert config['input'][0]['content'][0]['text']=='Xin chào'
        assert client.post('/api/live/connect',json={'session_id':session_id,'sdp':'offer'}).status_code==409
        assert len(seen)==1
    finally:
        client.app.state.gemini_live_client=previous
        client.portal.call(upstream.aclose)


@pytest.mark.asyncio
async def test_sol_responses_json_images_and_minimum_reasoning(monkeypatch):
    from providers import OpenAICompatibleProvider
    monkeypatch.setenv('OPENAI_REASONING_EFFORT','none')
    def handler(request):
        assert request.url.path=='/v1/responses'
        body=json.loads(request.content)
        assert body['reasoning']=={'effort':'low'}
        assert not {'temperature','max_tokens','logprobs'} & body.keys()
        assert body['input'][1]['content'][1]['type']=='input_image'
        assert body['text']['format']=={'type':'json_object'}
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}]})
    provider=OpenAICompatibleProvider(base_url='https://api.openai.com/v1',api_key='test',transport=httpx.MockTransport(handler))
    try:
        assert await provider.chat_json(model='gpt-6.1-sol',system='Return JSON.',content=[{'type':'text','text':'Inspect'},
            {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,abc'}}])=={'ok':True}
    finally: await provider.aclose()


@pytest.mark.asyncio
async def test_sol_responses_stream_tool_continuation():
    from providers import OpenAICompatibleProvider
    def handler(request):
        body=json.loads(request.content)
        assert request.url.path=='/v1/responses'
        assert body['tools'][0]['name']=='get_weather'
        assert body['input'][-1]=={'type':'function_call_output','call_id':'call1','output':'{"sunny":true}'}
        events=[{'type':'response.output_text.delta','delta':'Trời nắng.'},
            {'type':'response.output_item.done','item':{'type':'function_call','call_id':'call2','name':'get_weather','arguments':'{}'}},
            {'type':'response.completed'}]
        return httpx.Response(200,text=''.join('data: '+json.dumps(e)+'\n\n' for e in events))
    provider=OpenAICompatibleProvider(base_url='https://api.openai.com/v1',api_key='test',transport=httpx.MockTransport(handler))
    try:
        events=[event async for event in provider.stream_chat(model='gpt-6.1-sol',messages=[
            {'role':'user','content':'Thời tiết?'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'call1','function':{'name':'get_weather','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'call1','content':'{"sunny":true}'}],
            tools=[{'type':'function','function':{'name':'get_weather','parameters':{'type':'object'}}}])]
        assert events[0]=={'type':'text','text':'Trời nắng.'}
        assert events[1]['message']['tool_calls'][0]['id']=='call2'
    finally: await provider.aclose()


def test_live_handshake_counts_only_one_paid_session(monkeypatch):
    from security import rate_rule_for
    monkeypatch.setenv('OPENAI_API_KEY','test')
    monkeypatch.setenv('SHADOW_LIVE_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_LIVE_API','live')
    assert rate_rule_for('POST','/api/live/session') == 'conversation'
    assert rate_rule_for('POST','/api/live/connect') == 'live_token'
    monkeypatch.setenv('OPENAI_LIVE_API','realtime')
    assert rate_rule_for('POST','/api/live/session') == 'live_token'

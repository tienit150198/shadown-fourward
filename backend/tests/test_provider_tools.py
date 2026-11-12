import json

import httpx
import pytest

from providers import OpenAICompatibleProvider, ProviderError


def delta(content=None, calls=None, finish=None, **extra):
    payload = {**extra}
    if content is not None:
        payload['content'] = content
    if calls is not None:
        payload['tool_calls'] = calls
    return {'choices': [{'index': 0, 'delta': payload, 'finish_reason': finish}]}


def call(index, id=None, name=None, arguments=None):
    result = {'index': index, 'function': {}}
    if id is not None:
        result['id'] = id
        result['type'] = 'function'
    if name is not None:
        result['function']['name'] = name
    if arguments is not None:
        result['function']['arguments'] = arguments
    return result


async def collect(events, messages=None):
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        stream = ''.join('data: ' + (item if isinstance(item, str) else json.dumps(item)) + '\n\n' for item in events)
        return httpx.Response(200, text=stream)
    provider = OpenAICompatibleProvider(base_url='https://test/v1', api_key='x', transport=httpx.MockTransport(handle))
    try:
        output = [event async for event in provider.stream_chat(model='test', messages=messages or [], tools=[{'type': 'function'}])]
        return output, captured[0]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['tool_calls', 'stop', None])
async def test_fragmented_multiple_calls_and_history(finish):
    history = [{'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'previous'}]},
               {'role': 'tool', 'tool_call_id': 'previous', 'content': '{"ok": true}'}]
    events = [delta('Checking', reasoning='secret', calls=[call(0, 'call_', 'find_', '{"q":')]),
              delta(calls=[call(1, 'second', 'other', '{}'), call(0, 'one', 'item', '"shirt"}')]),
              delta(finish=finish), '[DONE]']
    output, request = await collect(events, history)
    assert request['messages'] == history
    assert request['max_tokens'] == 800 and request['stream'] is True
    assert output == [{'type': 'text', 'text': 'Checking'}, {'type': 'message', 'message': {
        'role': 'assistant', 'content': 'Checking', 'tool_calls': [
            {'id': 'call_one', 'type': 'function', 'function': {'name': 'find_item', 'arguments': '{"q":"shirt"}'}},
            {'id': 'second', 'type': 'function', 'function': {'name': 'other', 'arguments': '{}'}}]}}]


@pytest.mark.asyncio
async def test_text_only_and_usage():
    output, _ = await collect([{'choices': [], 'usage': {}}, delta('Hello', reasoning_content='secret'), delta(' world', finish='stop')])
    assert output[-1] == {'type': 'message', 'message': {'role': 'assistant', 'content': 'Hello world'}}
    assert [e['text'] for e in output[:-1]] == ['Hello', ' world']


@pytest.mark.asyncio
@pytest.mark.parametrize('events', [
    [delta('cut off')], [delta('cut', finish='length')], [{'error': {'message': 'bad'}}],
    ['{broken'], [delta(calls=[call(0, 'id', 'name', '{')]), '[DONE]'],
    [delta(calls=[call(0, None, 'name', '{}')]), '[DONE]'],
    [delta(calls=[call(1, 'id', 'name', '{}')]), '[DONE]'],
    [delta(calls=[call(0, 'id', 'name', '[]')]), '[DONE]'],
    [delta(finish='tool_calls')], [delta('x' * 131073), '[DONE]'],
    [delta(calls=[{'index': -1}]), '[DONE]'], [delta(calls=[{'index': 0, 'function': 'bad'}]), '[DONE]'],
    [delta(finish='content_filter')], [{'choices': 'bad'}],
])
async def test_invalid_or_truncated_stream(events):
    with pytest.raises(ProviderError):
        await collect(events)

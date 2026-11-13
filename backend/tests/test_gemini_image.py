import asyncio
import base64
import json

import httpx
import pytest

from gemini_image import DEFAULT_MODEL, GeminiImage, GeminiImageError
from tryon import TryOnService
from wardrobe_testlib import data_url, make_jpeg, item


def test_direct_google_edit_preserves_all_selected_layers(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-google-key')
    monkeypatch.setenv('TRYON_PHOTO_PROVIDER', 'gemini')
    photo = make_jpeg()
    items = [item('inner', 'top'), item('outer', 'top'), item('pants', 'bottom'), item('shoes', 'shoes')]
    def handler(request):
        assert str(request.url) == f'https://generativelanguage.googleapis.com/v1/models/{DEFAULT_MODEL}:generateContent'
        assert request.headers['x-goog-api-key'] == 'test-google-key'
        assert 'authorization' not in request.headers
        body = json.loads(request.content)
        parts = body['contents'][0]['parts']
        assert len(parts) == 6
        assert 'intentional layers' in parts[0]['text'] and 'Image 5:' in parts[0]['text']
        assert all(part['inlineData']['mimeType'] == 'image/jpeg' for part in parts[1:])
        assert base64.b64decode(parts[1]['inlineData']['data']).startswith(b'\xff\xd8')
        assert body['generationConfig'] == {'candidateCount':1, 'responseModalities':['IMAGE'], 'imageConfig':{'imageSize':'1K','aspectRatio':'4:5'}}
        return httpx.Response(200, json={'candidates':[{'finishReason':'STOP', 'content':{'parts':[
            {'thought':True,'inlineData':{'mimeType':'image/png','data':base64.b64encode(b'not-a-final-image').decode()}},
            {'inlineData':{'mimeType':'image/jpeg','data':base64.b64encode(photo).decode()}}
        ]}}]})
    service = TryOnService(gemini_image=GeminiImage(transport=httpx.MockTransport(handler)))
    result = asyncio.run(service.run(data_url(photo), items, {it['id']:photo for it in items}))
    assert result['provider'] == 'gemini' and result['rendered_item_ids'] == [it['id'] for it in items]
    assert result['skipped_item_ids'] == [] and result['jpeg'].startswith(b'\xff\xd8')
    assert asyncio.run(service.config())['photo']['renders_bottoms'] is True


@pytest.mark.parametrize('status,code,expected',[(401,'provider_auth_failed',503),(403,'provider_auth_failed',503),(402,'insufficient_credits',503),(429,'rate_limited',429),(404,'invalid_configuration',503),(500,'provider_unavailable',502)])
def test_provider_error_codes_are_redacted(monkeypatch,status,code,expected):
    monkeypatch.setenv('GEMINI_API_KEY','test-google-key')
    provider=GeminiImage(transport=httpx.MockTransport(lambda r:httpx.Response(status,json={'error':{'message':'secret upstream data'}})))
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code==code and exc.value.status==expected
    assert 'secret upstream' not in str(exc.value)
    assert exc.value.service=='photo_tryon'


def test_missing_key_and_invalid_model_never_call_google(monkeypatch):
    def fail(request):
        pytest.fail('Unexpected upstream request')
    provider=GeminiImage(transport=httpx.MockTransport(fail))
    monkeypatch.setenv('GEMINI_API_KEY','')
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code=='missing_api_key' and not exc.value.retryable
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    monkeypatch.setenv('GEMINI_IMAGE_MODEL','../../not-a-model')
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code=='invalid_configuration'


@pytest.mark.parametrize('body,code',[
    ({'promptFeedback':{'blockReason':'SAFETY'}},'content_blocked'),
    ({'candidates':[{'finishReason':'IMAGE_SAFETY'}]},'content_blocked'),
    ({'candidates':[{'content':{'parts':[{'text':'No image'}]}}]},'invalid_provider_response'),
    ({'candidates':[{'content':{'parts':[{'inlineData':{'mimeType':'image/png','data':'broken!'}}]}}]},'invalid_provider_response'),
])
def test_refusal_and_missing_or_corrupt_image(monkeypatch,body,code):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    provider=GeminiImage(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code==code


@pytest.mark.parametrize('reason,code', [('API_KEY_INVALID','provider_auth_failed'),('BILLING_DISABLED','billing_required')])
def test_google_structured_400_configuration_errors(monkeypatch,reason,code):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    provider=GeminiImage(transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':{'details':[{'reason':reason}]}})))
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code==code and exc.value.status==503 and not exc.value.retryable


def test_google_429_depleted_prepayment_is_not_a_retryable_rate_limit(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    provider=GeminiImage(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'error':{'message':'Your prepayment credits are depleted. private upstream details'}})))
    with pytest.raises(GeminiImageError) as exc:
        asyncio.run(provider.edit('edit',[make_jpeg()]*2))
    assert exc.value.code=='insufficient_credits' and exc.value.status==503 and not exc.value.retryable
    assert 'private upstream' not in str(exc.value)

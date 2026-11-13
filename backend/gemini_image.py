"""Direct Google AI Studio / Gemini Developer API photo try-on editing."""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import os
import re

import httpx
from PIL import Image

from gemini_errors import account_error_code
from wardrobe_store import MAX_IMAGE_BYTES, WardrobeError, to_jpeg

DEFAULT_MODEL = 'gemini-nano-banana-2.1'
MAX_REFERENCES = 14
BASE_URL = 'https://generativelanguage.googleapis.com/v1/models'


class GeminiImageError(RuntimeError):
    def __init__(self, message: str, status: int = 502, *, code: str = 'provider_unavailable'):
        super().__init__(message)
        self.status, self.code, self.service = status, code, 'photo_tryon'
        self.retryable = code in ('provider_unavailable', 'provider_timeout', 'rate_limited')


class GeminiImage:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    @staticmethod
    def model() -> str:
        return os.getenv('GEMINI_IMAGE_MODEL', DEFAULT_MODEL).strip() or DEFAULT_MODEL

    @classmethod
    def valid_model(cls) -> bool:
        return bool(re.fullmatch(r'gemini-[a-zA-Z0-9._-]{1,100}', cls.model()))

    @classmethod
    def available(cls) -> bool:
        return bool(os.getenv('GEMINI_API_KEY', '').strip()) and cls.valid_model()

    async def edit(self, prompt: str, images: list[bytes]) -> bytes:
        key = os.getenv('GEMINI_API_KEY', '').strip()
        if not key:
            raise GeminiImageError('Chưa cấu hình GEMINI_API_KEY cho ảnh mặc thử.', 503, code='missing_api_key')
        if not self.valid_model():
            raise GeminiImageError('GEMINI_IMAGE_MODEL phải là mã mô hình Gemini tạo ảnh hợp lệ.', 503, code='invalid_configuration')
        if not 2 <= len(images) <= MAX_REFERENCES:
            raise GeminiImageError('Cần ảnh người và từ một đến 13 ảnh đồ để mặc thử.', 422, code='invalid_request')
        with Image.open(io.BytesIO(images[0])) as person:
            ratio = person.width / person.height
        ratios = {'1:1':1, '2:3':2/3, '3:2':3/2, '3:4':3/4, '4:3':4/3, '4:5':4/5, '5:4':5/4, '9:16':9/16, '16:9':16/9}
        payload = {
            'contents': [{'role':'user', 'parts':[{'text':prompt}, *[
                {'inlineData':{'mimeType':'image/jpeg', 'data':base64.b64encode(raw).decode('ascii')}} for raw in images
            ]]}],
            'generationConfig': {'candidateCount':1, 'responseModalities':['IMAGE'],
                'imageConfig':{'imageSize':'1K', 'aspectRatio':min(ratios, key=lambda name:abs(ratios[name]-ratio))}},
        }
        try:
            # No automatic retries: upstream may have charged an accepted request before a timeout.
            async with asyncio.timeout(180):
                async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=15), transport=self._transport) as client:
                    async with client.stream('POST', f'{BASE_URL}/{self.model()}:generateContent',
                        headers={'x-goog-api-key':key}, json=payload) as response:
                        if not response.is_success:
                            status = response.status_code
                            code = {401:'provider_auth_failed', 403:'provider_auth_failed', 402:'insufficient_credits', 429:'rate_limited', 404:'invalid_configuration', 400:'provider_request_rejected'}.get(status, 'provider_unavailable')
                            error_bytes = bytearray()
                            async for chunk in response.aiter_bytes():
                                error_bytes.extend(chunk)
                                if len(error_bytes) > 32768:
                                    break
                            try:
                                error_info = json.loads(error_bytes).get('error', {})
                                reasons = {entry.get('reason') for entry in error_info.get('details', []) if isinstance(entry, dict)}
                                code = account_error_code(str(error_info.get('message', '')), reasons) or code
                            except (ValueError, TypeError, AttributeError):
                                pass
                            message = {'provider_auth_failed':'Gemini từ chối khóa API hoặc quyền mô hình. Kiểm tra khóa AI Studio và quyền tài khoản.',
                                'billing_required':'Dự án Gemini chưa bật thanh toán. Hãy bật thanh toán trong Google AI Studio.',
                                'insufficient_credits':'Tài khoản Gemini không đủ credit. Kiểm tra thanh toán Google AI Studio.',
                                'rate_limited':'Gemini đã đạt giới hạn lượt gọi hoặc quota. Kiểm tra quota và thanh toán rồi thử lại.',
                                'invalid_configuration':'Không tìm thấy mô hình ảnh Gemini. Kiểm tra GEMINI_IMAGE_MODEL và quyền dự án.',
                                'provider_request_rejected':'Gemini từ chối yêu cầu tạo ảnh. Kiểm tra mô hình và cấu hình.'}.get(code, 'Dịch vụ tạo ảnh Gemini tạm thời không khả dụng.')
                            raise GeminiImageError(message, 429 if status == 429 and code == 'rate_limited' else 503 if code in ('provider_auth_failed','insufficient_credits','billing_required','invalid_configuration') else 502, code=code)
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 256 * 1024:
                                raise GeminiImageError('Ảnh Gemini trả về quá lớn.', code='invalid_provider_response')
            data = json.loads(raw)
            if data.get('promptFeedback', {}).get('blockReason'):
                raise GeminiImageError('Gemini từ chối tạo ảnh với nội dung này. Hãy thử ảnh khác.', 422, code='content_blocked')
            candidate = data['candidates'][0]
            if candidate.get('finishReason') in ('SAFETY','IMAGE_SAFETY','BLOCKLIST','PROHIBITED_CONTENT','SPII','IMAGE_PROHIBITED_CONTENT'):
                raise GeminiImageError('Gemini từ chối tạo ảnh với nội dung này. Hãy thử ảnh khác.', 422, code='content_blocked')
            if candidate.get('finishReason', 'STOP') != 'STOP':
                raise ValueError('incomplete generation')
            for part in candidate['content']['parts']:
                inline = part.get('inlineData', {})
                if part.get('thought') or not inline.get('mimeType', '').startswith('image/'):
                    continue
                encoded = inline.get('data')
                if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
                    raise ValueError('invalid image data')
                image = base64.b64decode(encoded, validate=True)
                if len(image) > MAX_IMAGE_BYTES:
                    raise ValueError('oversized image')
                return await asyncio.to_thread(to_jpeg, image, max_side=1024, quality=90)
            raise ValueError('no image')
        except (asyncio.TimeoutError, httpx.TimeoutException):
            raise GeminiImageError('Gemini xử lý ảnh quá lâu. Bạn có thể thử lại.', 504, code='provider_timeout') from None
        except httpx.HTTPError:
            raise GeminiImageError('Không kết nối được với Gemini để tạo ảnh.') from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, binascii.Error, WardrobeError):
            raise GeminiImageError('Gemini không trả về ảnh mặc thử hợp lệ.', code='invalid_provider_response') from None

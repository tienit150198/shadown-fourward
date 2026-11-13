"""Xmax X2.1: keep the permanent key on the server; issue bounded client keys."""
import asyncio
import io
import os
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from PIL import Image, ImageOps, ImageDraw

from wardrobe_api import _store
from wardrobe_store import ItemNotFound

router = APIRouter()
MODEL = 'x2.1'
MAX_SECONDS = 120
POINTS_LIMIT = 200
BASES = {'global': 'https://api.xmax.cloud/open/api/v1', 'china': 'https://cloud.xmax.22duck.cn/open/api/v1'}


class XmaxClient:
    def __init__(self, key: str, environment: str = 'global', *, transport=None):
        self.environment = environment if environment in BASES else 'global'
        self.http = httpx.AsyncClient(base_url=BASES[self.environment], headers={'X-Api-Key': key}, timeout=25, transport=transport)
        self.issued: dict[str, float] = {}

    async def request(self, method: str, path: str, body=None) -> dict:
        try:
            response = await self.http.request(method, path, json=body)
            data = response.json()
        except httpx.TimeoutException:
            raise HTTPException(504, 'Xmax phản hồi quá lâu. Hãy thử lại.') from None
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'Chưa kết nối được Xmax.') from None
        if not response.is_success or not isinstance(data, dict) or data.get('success') is not True:
            code = data.get('code') if isinstance(data, dict) else None
            if response.status_code == 429 or code in (46003, 46032):
                raise HTTPException(429, 'Xmax chưa đủ lượt hoặc đang có phiên khác. Hãy kiểm tra số dư và thử lại.')
            raise HTTPException(502, 'Xmax từ chối yêu cầu. Kiểm tra API key, số dư và quyền dùng X2.1.')
        if not isinstance(data.get('data'), dict):
            raise HTTPException(502, 'Xmax trả về dữ liệu không hợp lệ.')
        return data['data']

    async def aclose(self):
        await asyncio.gather(*(self.request('PUT', f'/temporary-api-key/{token}/inactive') for token in self.issued), return_exceptions=True)
        self.issued.clear()
        await self.http.aclose()


def client_from_env():
    key = os.getenv('XMAX_API_KEY', '').strip()
    return XmaxClient(key, os.getenv('XMAX_ENVIRONMENT', 'global')) if key else None


def client(request: Request) -> XmaxClient:
    value = getattr(request.app.state, 'xmax_client', None)
    if value is None:
        raise HTTPException(503, 'Chưa cấu hình XMAX_API_KEY.')
    return value


@router.get('/api/tryon/xmax/config')
async def config(request: Request):
    value = getattr(request.app.state, 'xmax_client', None)
    return JSONResponse({'available': value is not None, 'provider': 'xmax', 'model': MODEL,
                         'environment': value.environment if value else 'global', 'maxSeconds': MAX_SECONDS}, headers={'Cache-Control': 'no-store'})


class SessionRequest(BaseModel):
    item_id: str | None = Field(None, min_length=1, max_length=100)
    item_ids: list[str] = Field(default_factory=list, max_length=3)


def outfit_reference(store, items: list[dict]) -> str:
    """X2.1 takes one reference: put every chosen garment on the same board."""
    board = Image.new('RGB', (512 * len(items), 768), 'white')
    draw = ImageDraw.Draw(board)
    labels = {'top': 'TOP / SHIRT', 'bottom': 'BOTTOM / TROUSERS', 'outerwear': 'OUTER LAYER', 'dress': 'DRESS'}
    for index, item in enumerate(items):
        path = store.item_image_path(item)
        if path is None:
            raise HTTPException(422, 'Một món trong bộ đồ chưa có ảnh mẫu hợp lệ.')
        with Image.open(path) as original:
            image = ImageOps.contain(ImageOps.exif_transpose(original).convert('RGB'), (480, 690))
        x = index * 512
        board.paste(image, (x + (512 - image.width) // 2, 55 + (690 - image.height) // 2))
        draw.text((x + 20, 16), labels[item['category']], fill='black')
    output = io.BytesIO()
    board.save(output, 'JPEG', quality=92)
    return '/wardrobe-media/' + store.save_tryon(output.getvalue())


@router.post('/api/tryon/xmax/session')
async def session(payload: SessionRequest, request: Request):
    upstream = client(request)
    ids = payload.item_ids or ([payload.item_id] if payload.item_id else [])
    if not ids or any(not isinstance(value, str) or not 0 < len(value) <= 100 for value in ids) or len(set(ids)) != len(ids):
        raise HTTPException(422, 'Chọn từ một đến ba món đồ khác nhau.')
    store = _store(request)
    try:
        items = [store.get_item(item_id) for item_id in ids]
    except ItemNotFound:
        raise HTTPException(404, 'Không tìm thấy món đồ muốn thử.') from None
    if any(not item.get('image_url') for item in items):
        raise HTTPException(422, 'Món đồ này chưa có ảnh mẫu để thử.')
    categories = [item.get('category', 'top') for item in items]
    if any(category not in ('top', 'bottom', 'outerwear', 'dress') for category in categories) or len(set(categories)) != len(categories) or ('dress' in categories and any(c in categories for c in ('top', 'bottom'))):
        raise HTTPException(422, 'Mỗi bộ chọn một áo, một quần và tùy chọn một áo khoác; váy thay cho áo và quần.')
    image = await asyncio.to_thread(outfit_reference, store, items) if len(items) > 1 else items[0]['image_url']
    descriptions = '; '.join(f"{category.upper()}: {item.get('name', category)}" for category, item in zip(categories, items))
    prompt = (
        f"Photorealistic virtual try-on. Dress the person in ALL selected garments SIMULTANEOUSLY: {descriptions}. "
        "The reference board shows separate clothing pieces of ONE complete outfit, not alternative choices. "
        "Apply each selected garment only to its matching body region: TOP on the upper body, BOTTOM on the lower body. "
        "When both TOP and BOTTOM are selected, apply both at the same time; never omit the selected shirt when applying trousers. "
        "If an outer layer is selected, wear it over the selected top. Keep the entire selected outfit consistent across frames. "
        "Preserve all unselected clothing, face, identity, body shape, pose, movement and background. Do not copy labels or the board background."
    )
    data = await upstream.request('POST', '/temporary-api-key', {
        'expireSeconds': MAX_SECONDS + 30, 'pointsLimit': POINTS_LIMIT, 'temporaryApiKeySessionLimit': 1,
    })
    token = data.get('temporaryApiKey')
    if not isinstance(token, str) or not token.startswith('tk-'):
        raise HTTPException(502, 'Xmax không trả về key tạm hợp lệ.')
    now = time.monotonic()
    upstream.issued = {key: expiry for key, expiry in upstream.issued.items() if expiry > now}
    upstream.issued[token] = now + MAX_SECONDS + 60
    return JSONResponse({'token': token, 'provider': 'xmax', 'model': MODEL, 'environment': upstream.environment,
                         'maxSeconds': MAX_SECONDS, 'expireTime': data.get('expireTimestamp'),
                         'item': {'id': items[0]['id'], 'name': items[0].get('name', 'Trang phục'), 'image_url': image},
                         'items': [{'id': item['id'], 'name': item.get('name'), 'category': item.get('category')} for item in items],
                         'reference_image_url': image, 'prompt': prompt},
                        headers={'Cache-Control': 'no-store'})


class CloseRequest(BaseModel):
    token: str = Field(..., min_length=4, max_length=200)


@router.post('/api/tryon/xmax/close')
async def close(payload: CloseRequest, request: Request):
    upstream = client(request)
    if payload.token in upstream.issued:
        await upstream.request('PUT', f'/temporary-api-key/{payload.token}/inactive')
        upstream.issued.pop(payload.token, None)
    return {'ok': True}

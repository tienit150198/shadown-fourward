"""Xmax X2.1: keep the permanent key on the server; issue bounded client keys."""
import asyncio
import io
import json
import math
import os
import re
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
ISSUE_TIMEOUT = 25
KEY_ATTEMPT_TIMEOUT = 8
BASES = {'global': 'https://api.xmax.cloud/open/api/v1', 'china': 'https://cloud.xmax.22duck.cn/open/api/v1'}


class ProviderFailure(HTTPException):
    def __init__(self, status, detail, *, cooldown=0):
        super().__init__(status, detail)
        self.cooldown = cooldown


class XmaxClient:
    def __init__(self, key: str | list[str], environment: str = 'global', *, strategy='round_robin', check_balance=False, transport=None):
        self.environment = environment if environment in BASES else 'global'
        keys = list(dict.fromkeys(value.strip() for value in ([key] if isinstance(key, str) else key) if isinstance(value, str) and value.strip()))
        if not keys:
            raise ValueError('Empty try-on credentials')
        self.clients = [httpx.AsyncClient(base_url=BASES[self.environment], headers={'X-Api-Key': value}, timeout=25, transport=transport) for value in keys]
        self.issued: dict[str, float] = {}
        self.owners: dict[str, int] = {}
        self.cooldowns = [0.0] * len(keys)
        self.cursor = 0
        self.strategy = strategy if strategy in ('round_robin', 'priority') else 'round_robin'
        self.rotation_lock = asyncio.Lock()
        self.check_balance = check_balance

    async def request(self, method: str, path: str, body=None, *, owner=0, timeout=25) -> dict:
        try:
            response = await self.clients[owner].request(method, path, json=body, timeout=timeout)
            data = response.json()
        except httpx.TimeoutException:
            raise ProviderFailure(504, 'Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.', cooldown=15) from None
        except httpx.HTTPError:
            raise ProviderFailure(502, 'Chưa kết nối được dịch vụ thay đồ. Bạn thử lại nhé.', cooldown=15) from None
        except ValueError:
            raise ProviderFailure(502, 'Dịch vụ thay đồ trả về dữ liệu không hợp lệ.', cooldown=15) from None
        if not response.is_success or not isinstance(data, dict) or data.get('success') is not True:
            code = data.get('code') if isinstance(data, dict) else None
            if response.status_code == 429 or code in (46003, 46032):
                raise ProviderFailure(429, 'Hiện chưa thể thay đồ. Bạn thử lại sau ít phút nhé.', cooldown=10 if code == 46032 else 60)
            if response.status_code in (401, 403):
                raise ProviderFailure(502, 'Chức năng thay đồ tạm chưa sẵn sàng. Bạn thử lại sau nhé.', cooldown=300)
            raise ProviderFailure(502, 'Chưa thể thay đồ lúc này. Bạn thử lại nhé.', cooldown=15 if response.status_code >= 500 else 0)
        if not isinstance(data.get('data'), dict):
            raise ProviderFailure(502, 'Dịch vụ thay đồ trả về dữ liệu không hợp lệ.', cooldown=15)
        return data['data']

    async def _issue_for_key(self, owner: int, timeout: float) -> dict:
        # Xmax can mint a temporary key even when the owning account has no credit.
        # Check before starting a paid generation so priority mode skips empty accounts.
        if self.check_balance:
            data = await self.request('GET', '/balance', owner=owner, timeout=timeout)
            balance = data.get('currentBalance')
            if not isinstance(balance, (int, float)) or isinstance(balance, bool) or not math.isfinite(balance):
                raise ProviderFailure(502, 'Chưa kiểm tra được phiên thay đồ. Bạn thử lại nhé.', cooldown=15)
            if balance <= 0:
                raise ProviderFailure(429, 'Hiện chưa thể thay đồ. Bạn thử lại sau ít phút nhé.', cooldown=60)
        return await self.request('POST', '/temporary-api-key', {
            'expireSeconds': MAX_SECONDS + 30, 'pointsLimit': POINTS_LIMIT, 'temporaryApiKeySessionLimit': 1,
        }, owner=owner, timeout=timeout)

    async def issue(self) -> dict:
        now = time.monotonic()
        async with self.rotation_lock:
            start = 0 if self.strategy == 'priority' else self.cursor
            order = [(start + offset) % len(self.clients) for offset in range(len(self.clients))]
            candidates = [index for index in order if self.cooldowns[index] <= now]
            if not candidates:
                delay = max(1, math.ceil(min(self.cooldowns) - now))
                raise HTTPException(429, 'Hiện chưa thể thay đồ. Bạn thử lại sau ít phút nhé.', headers={'Retry-After': str(delay)})
            self.cursor = (candidates[0] + 1) % len(self.clients)
        deadline = now + ISSUE_TIMEOUT
        last_error = None
        for owner in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                attempt_timeout = min(KEY_ATTEMPT_TIMEOUT, remaining)
                data = await asyncio.wait_for(self._issue_for_key(owner, attempt_timeout), timeout=attempt_timeout)
                token = data.get('temporaryApiKey')
                if not isinstance(token, str) or not token.startswith('tk-'):
                    raise ProviderFailure(502, 'Chưa tạo được phiên thay đồ. Bạn thử lại nhé.', cooldown=15)
            except asyncio.TimeoutError:
                self.cooldowns[owner] = time.monotonic() + 15
                last_error = ProviderFailure(504, 'Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.', cooldown=15)
                continue
            except ProviderFailure as error:
                if not error.cooldown:
                    raise
                self.cooldowns[owner] = time.monotonic() + error.cooldown
                last_error = error
                continue
            current = time.monotonic()
            for old in [value for value, expiry in self.issued.items() if expiry <= current]:
                self.issued.pop(old, None)
                self.owners.pop(old, None)
            self.issued[token] = current + MAX_SECONDS + 60
            self.owners[token] = owner
            return data
        if last_error:
            raise last_error
        raise HTTPException(504, 'Thay đồ mất nhiều thời gian hơn dự kiến. Bạn thử lại nhé.')

    async def close_token(self, token):
        owner = self.owners.get(token)
        if owner is not None:
            await self.request('PUT', f'/temporary-api-key/{token}/inactive', owner=owner)
            self.issued.pop(token, None)
            self.owners.pop(token, None)

    async def aclose(self):
        await asyncio.gather(*(self.close_token(token) for token in list(self.issued)), return_exceptions=True)
        self.issued.clear()
        self.owners.clear()
        await asyncio.gather(*(value.aclose() for value in self.clients))


def client_from_env():
    raw = os.getenv('XMAX_API_KEYS', '').strip()
    if raw:
        if raw.startswith('['):
            try:
                keys = json.loads(raw)
            except ValueError:
                raise ValueError('XMAX_API_KEYS must be a JSON array or a comma/newline separated list') from None
            if not isinstance(keys, list) or any(not isinstance(value, str) for value in keys):
                raise ValueError('XMAX_API_KEYS must contain strings')
        else:
            keys = re.split(r'[,;\s]+', raw)
        keys = [value.strip() for value in keys if value.strip()]
    else:
        keys = []
    if not keys:
        keys = [os.getenv('XMAX_API_KEY', '').strip()]
    keys = list(dict.fromkeys(value for value in keys if value))
    return XmaxClient(keys, os.getenv('XMAX_ENVIRONMENT', 'global'), strategy=os.getenv('XMAX_KEY_STRATEGY', 'round_robin').strip().lower(), check_balance=True) if keys else None


def client(request: Request) -> XmaxClient:
    value = getattr(request.app.state, 'xmax_client', None)
    if value is None:
        raise HTTPException(503, 'Chức năng thay đồ tạm chưa sẵn sàng. Bạn vẫn có thể trò chuyện và tư vấn trang phục.')
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
    data = await upstream.issue()
    token = data['temporaryApiKey']
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
    await upstream.close_token(payload.token)
    return {'ok': True}

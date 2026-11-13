"""Credential-safe configuration and recent observed service status; never probes paid APIs."""
from __future__ import annotations

import importlib.util
import os
import time
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()
OBSERVATION_TTL_S = 300
_observations: dict[str, tuple[float, dict[str, Any]]] = {}
MESSAGES = {
    'service_disabled': 'Dịch vụ đang tắt trong cấu hình máy chủ.',
    'missing_api_key': 'Chưa cấu hình khóa API cho dịch vụ.',
    'invalid_configuration': 'Cấu hình dịch vụ chưa hợp lệ.',
    'provider_auth_failed': 'Nhà cung cấp từ chối khóa API hoặc quyền sử dụng. Kiểm tra khóa và quyền tài khoản.',
    'insufficient_credits': 'Tài khoản nhà cung cấp không đủ credit. Hãy nạp credit rồi thử lại.',
    'rate_limited': 'Dịch vụ đang giới hạn lượt gọi. Đợi một chút rồi thử lại.',
    'provider_timeout': 'Dịch vụ phản hồi quá lâu. Bạn có thể thử lại.',
    'provider_unavailable': 'Không kết nối được dịch vụ hoặc dịch vụ tạm thời không khả dụng.',
    'provider_request_rejected': 'Nhà cung cấp từ chối yêu cầu. Kiểm tra mô hình và cấu hình dịch vụ.',
    'invalid_provider_response': 'Nhà cung cấp trả về dữ liệu không hợp lệ.',
}



class ObservedError(BaseModel):
    code: str
    message: str
    retryable: bool


class ServiceObservation(BaseModel):
    observed_at: str
    outcome: Literal['success', 'error']
    error: ObservedError | None = None


class ServiceStatus(BaseModel):
    provider: str
    state: Literal['configured', 'unconfigured', 'disabled', 'demo', 'invalid_configuration', 'degraded']
    configured: bool
    required_configuration: list[str]
    missing_configuration: list[str]
    code: str | None
    message: str
    dependency: str | None
    last_observation: ServiceObservation | None


class ServicesStatusResponse(BaseModel):
    services: dict[str, ServiceStatus]
    checked_at: str
    check_type: Literal['configuration_and_recent_requests']
    observation_ttl_seconds: int
    notice: str

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_failure(service: str, code: str, message: str, retryable: bool) -> None:
    # Store only controlled messages: provider exception text may contain sensitive request data.
    _observations[service] = (time.monotonic(), {
        'observed_at': _now(), 'outcome': 'error',
        'error': {'code': code, 'message': MESSAGES.get(code, MESSAGES['provider_unavailable']), 'retryable': retryable},
    })


def record_success(service: str) -> None:
    _observations[service] = (time.monotonic(), {'observed_at': _now(), 'outcome': 'success'})


def error_headers(error: Exception, service: str) -> dict[str, str]:
    return {'X-Shadow-Error-Code': getattr(error, 'code', 'provider_unavailable'),
            'X-Shadow-Service': service,
            'X-Shadow-Retryable': str(getattr(error, 'retryable', True)).lower()}


def _env(name: str, default: str = '') -> str:
    return os.getenv(name, '').strip() or default


def _status(provider: str, *, required: list[str] | None = None, missing: list[str] | None = None,
            state: str | None = None, code: str | None = None, dependency: str | None = None) -> dict[str, Any]:
    missing = missing or []
    state = state or ('unconfigured' if missing else 'configured')
    code = None if state in ('disabled', 'demo') else code or (('missing_api_key' if all('KEY' in name or 'TOKEN' in name for name in missing) else 'invalid_configuration') if missing else None)
    return {'provider': provider, 'state': state, 'configured': not missing and state not in ('disabled', 'invalid_configuration'),
            'required_configuration': required or [], 'missing_configuration': missing,
            'code': code, 'message': MESSAGES.get(code, '') if code else {'configured': 'Đã cấu hình; chưa kiểm tra kết nối hoặc credit.', 'demo': 'Đang dùng chế độ minh hoạ, không gọi AI.', 'disabled': 'Dịch vụ đã tắt.'}.get(state, ''),
            'dependency': dependency, 'last_observation': None}


async def service_status(request: Request) -> dict[str, Any]:
    from providers import resolve_mode
    from qwen_image import QwenImage
    from tryon import TryOnService
    from vidu_live import BASE_URLS, DEFAULT_BASE_URL

    missing_llm = [] if _env('LLM_API_KEY') else ['LLM_API_KEY']
    mode = resolve_mode()
    requested_mode = _env('SHADOW_MODE', 'auto').lower()
    llm = _status('llm', required=['LLM_API_KEY'], missing=missing_llm,
                  state='demo' if mode == 'mock' and requested_mode != 'ai' else None)
    services = {'llm': llm}
    for feature in ('chat', 'vision', 'stylist', 'teaching'):
        services[feature] = {**llm, 'dependency': 'llm'}
    tryon = getattr(request.app.state, 'wardrobe_tryon', None) or TryOnService()
    photo_provider = (await tryon.provider_chain())[0]
    required = {'openrouter': ['OPENROUTER_API_KEY'], 'vidu': ['VIDU_API_KEY'], 'worker': ['TRYON_WORKER_URL']}.get(photo_provider, [])
    services['photo_tryon'] = _status(photo_provider, required=required, missing=[k for k in required if not _env(k)], state='demo' if photo_provider == 'mock' else None)
    if photo_provider == 'hf_space' and not importlib.util.find_spec('gradio_client'):
        services['photo_tryon'] = _status(photo_provider, state='invalid_configuration', code='invalid_configuration', missing=['gradio_client'])
    services['legacy_live_tryon'] = _status('decart', required=['DECART_API_KEY'], missing=[] if _env('DECART_API_KEY') else ['DECART_API_KEY'])
    vidu_required = ['VIDU_API_KEY']
    services['live_tryon'] = _status('vidu', required=vidu_required, missing=[k for k in vidu_required if not _env(k)])
    if _env('VIDU_API_BASE_URL', DEFAULT_BASE_URL).rstrip('/') not in BASE_URLS:
        for name in ('live_tryon', 'photo_tryon'):
            if services[name]['provider'] == 'vidu':
                services[name] = _status('vidu', state='invalid_configuration', code='invalid_configuration', missing=['VIDU_API_BASE_URL'])
    image = QwenImage()
    services['image_generation'] = _status('hypertek', required=['HYPERTEK_API_KEY or matching LLM_API_KEY'], missing=[] if image.available() else ['HYPERTEK_API_KEY or matching LLM_API_KEY'])
    voice_provider = _env('SHADOW_VOICE_PROVIDER').lower()
    voice_key = _env('HYPERTEK_API_KEY') or (_env('LLM_API_KEY') if _env('LLM_BASE_URL').rstrip('/') == 'https://llm.hypertek.vn/v1' else '')
    services['voice'] = _status(voice_provider or 'none', required=['HYPERTEK_API_KEY or Hypertek LLM_API_KEY'], missing=[] if voice_key else ['HYPERTEK_API_KEY or Hypertek LLM_API_KEY'], state='disabled' if not voice_provider else ('invalid_configuration' if voice_provider != 'hypertek' else None), code='invalid_configuration' if voice_provider and voice_provider != 'hypertek' else None)
    ar_required = ['SNAP_CAMERA_KIT_API_TOKEN', 'SNAP_CAMERA_KIT_LENS_GROUP_ID', 'SNAP_CAMERA_KIT_LENS_ID']
    services['ar_tryon'] = _status('snap', required=ar_required, missing=[k for k in ar_required if not _env(k)], code='invalid_configuration' if any(not _env(k) for k in ar_required) else None)
    services['weather'] = _status('open-meteo')
    for name, status in services.items():
        candidates = [value for value in (_observations.get(name), _observations.get(status['dependency'] or '')) if value]
        observed = max(candidates, key=lambda value: value[0]) if candidates else None
        if observed and time.monotonic() - observed[0] <= OBSERVATION_TTL_S:
            status['last_observation'] = observed[1]
            if status['state'] == 'configured' and observed[1]['outcome'] == 'error':
                status.update(state='degraded', code=observed[1]['error']['code'], message=observed[1]['error']['message'])
    return {'services': services, 'checked_at': _now(), 'check_type': 'configuration_and_recent_requests',
            'observation_ttl_seconds': OBSERVATION_TTL_S,
            'notice': 'No paid probes are performed. Configuration does not verify credentials, balances or connectivity. Recent observations are process-local and expire.'}


@router.get('/api/services/status', response_model=ServicesStatusResponse, tags=['System'], summary='Configuration and recent observed status of each service')
async def get_service_status(request: Request) -> dict[str, Any]:
    return await service_status(request)

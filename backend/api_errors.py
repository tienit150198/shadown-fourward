"""Consistent HTTP errors for web and native clients."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from service_status import record_failure


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        headers = dict(error.headers or {})
        metadata = {k.lower(): v for k, v in headers.items()}
        code = metadata.get('x-shadow-error-code')
        service = metadata.get('x-shadow-service')
        retryable = metadata.get('x-shadow-retryable', '').lower() == 'true'
        if not code and isinstance(error.detail, dict):
            code = error.detail.get('code')
        if not code:
            code = {400: 'invalid_request', 401: 'access_required', 403: 'forbidden', 404: 'not_found',
                    409: 'state_conflict', 413: 'payload_too_large', 415: 'unsupported_media_type',
                    422: 'validation_error', 429: 'rate_limited', 499: 'request_cancelled',
                    502: 'provider_unavailable', 503: 'service_unavailable', 504: 'provider_timeout'}.get(error.status_code, 'request_failed')
            retryable = error.status_code in (429, 502, 503, 504)
        message = error.detail if isinstance(error.detail, str) else (error.detail.get('message', 'Yêu cầu không thành công.') if isinstance(error.detail, dict) else 'Dữ liệu yêu cầu không hợp lệ.')
        body = {'message': message, 'code': code, 'retryable': retryable}
        if isinstance(error.detail, list):
            body['details'] = error.detail
        elif isinstance(error.detail, dict):
            details = {k: v for k, v in error.detail.items() if k not in ('message', 'code')}
            if error.detail.get('code') and error.detail['code'] != code:
                details['provider_code'] = error.detail['code']
            if details:
                body['details'] = details
        if service:
            body['service'] = service
            if error.status_code >= 500 or error.status_code == 429:
                record_failure(service, code, '', retryable)
        return JSONResponse(body, status_code=error.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        details = [{'loc': list(issue['loc']), 'message': issue['msg'], 'code': issue['type']} for issue in error.errors()]
        return JSONResponse({'details': details, 'message': 'Dữ liệu yêu cầu không hợp lệ.', 'code': 'validation_error', 'retryable': False}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        import logging
        logging.getLogger("uvicorn.error").error("Unhandled API exception on %s", request.url.path)
        return JSONResponse({'message': 'Máy chủ gặp lỗi nội bộ. Vui lòng thử lại sau.', 'code': 'internal_error', 'retryable': True}, status_code=500)

"""Describe middleware and non-JSON transports that route inference cannot discover.

This only changes documentation; runtime responses retain provider-specific fields.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

import security


def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": True}


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
NULLABLE_NUMBER = {"anyOf": [NUMBER, {"type": "null"}]}


def mobile_schemas() -> dict[str, Any]:
    schemas = {
        "ApiError": obj({"message": STRING, "code": STRING, "service": STRING, "retryable": BOOL, "details": {"description": "Optional structured field-validation issues or provider metadata. Never a duplicate of message.", "anyOf": [{"type": "array", "items": {"type": "object", "additionalProperties": True}}, {"type": "object", "additionalProperties": True}]}}, ("message", "code", "retryable")),
        "HealthResponse": obj({"ok": BOOL, "mode": STRING, "vision_model": STRING, "compiler_model": STRING, "access_required": BOOL}, ("ok", "mode", "access_required")),
        "AccessResponse": obj({"ok": BOOL, "access_required": BOOL}, ("ok", "access_required")),
        "WardrobeItem": obj({"id": STRING, "name": STRING, "category": STRING, "colors": STRINGS, "style": STRING, "warmth": {"type": "integer", "minimum": 1, "maximum": 5}, "occasions": STRINGS, "material": STRING, "subtype": STRING, "image_url": {"anyOf": [STRING, {"type": "null"}], "description": "Relative protected media URL. Resolve against the API origin and send access credentials."}, "attrs_vi": {"type": "object", "additionalProperties": True}, "created_at": STRING}, ("id",)),
        "PhotoTryOnResponse": obj({"image_url": STRING, "provider": STRING, "seconds": NUMBER, "rendered_item_ids": STRINGS, "skipped_item_ids": STRINGS, "note": STRING, "fallback_reason": STRING}, ("image_url", "provider", "seconds", "rendered_item_ids", "skipped_item_ids")),
        "WeatherResponse": obj({"available": BOOL, "message": STRING, "city": STRING, "date": {"type": "string", "format": "date"}, "lat": NUMBER, "lon": NUMBER, "t_min": NULLABLE_NUMBER, "t_max": NULLABLE_NUMBER, "rain_prob": NULLABLE_NUMBER, "code": NULLABLE_NUMBER, "description_vi": STRING, "summary_vi": STRING}, ("available",)),

    }

    return schemas


def _tag(path: str) -> str:
    for prefix, name in (("/api/wardrobe", "Wardrobe"), ("/api/stylist", "Stylist"), ("/api/tryon", "Try-on"), ("/api/voice", "Voice"), ("/api/images", "Images"), ("/api/teach", "Teaching"), ("/api/skills", "Skills"), ("/api/eval", "Evaluation"), ("/api/conversation", "Conversation"), ("/api/weather", "Weather"), ("/wardrobe-media", "Media"), ("/skills-media", "Media")):
        if path.startswith(prefix):
            return name
    return "System"


def install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title="SHADOW API", version="1.0.0", description="Shared wardrobe and assistant API for web and native mobile clients. Authentication is an optional deployment-level shared access code, not per-user identity. Relative media URLs require the same access credentials. Conversation responses are newline-delimited JSON, not SSE. In Swagger UI, click Authorize and enter the shared access code under ShadowAccessHeader (without a Bearer prefix). Then use Try it out. GET /api/services/status explains missing configuration and recent service errors. Executing generation endpoints can incur provider charges. Authorization is not persisted across page reloads.", routes=app.routes)
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {}).update(mobile_schemas())
        components.setdefault("securitySchemes", {}).update({
            "ShadowAccessHeader": {"type": "apiKey", "in": "header", "name": "X-Shadow-Access", "description": "Shared deployment access code; required when /api/health reports access_required=true. Native clients should use this header."},
            "ShadowAccessCookie": {"type": "apiKey", "in": "cookie", "name": "shadow_access", "description": "Cookie obtained from POST /api/access; alternative to X-Shadow-Access."},
        })
        error = {"application/json": {"schema": ref("ApiError")}}
        for path, item in schema["paths"].items():
            for method, operation in item.items():
                if method not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                    continue
                operation.setdefault("tags", [_tag(path)])
                responses = operation.setdefault("responses", {})
                if security.is_gated(path):
                    operation["security"] = [{"ShadowAccessHeader": []}, {"ShadowAccessCookie": []}]
                    responses.setdefault("401", {"description": "Deployment access code is required or invalid.", "content": deepcopy(error)})
                else:
                    operation["security"] = []
                if "422" in responses:
                    responses["422"]["content"] = deepcopy(error)
                if path == "/api/access":
                    responses.setdefault("401", {"description": "Incorrect access code (invalid_access_code).", "content": {"application/json": {"schema": ref("ApiError"), "example": {"message": "Mã truy cập không đúng.", "code": "invalid_access_code", "retryable": False}}}})
                if path.startswith("/api/"):
                    responses.setdefault("429", {"description": "Rate limit, concurrent generation cap, or live quota exceeded. Honor Retry-After.", "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Seconds to wait before retrying."}}, "content": deepcopy(error)})
                if method in {"post", "put", "patch"}:
                    responses.setdefault("413", {"description": "Request exceeds deployment body cap (16 MiB by default).", "content": deepcopy(error)})
                # FastAPI normally infers dict/Any responses; identify the stable mobile contract.
                name = {("get", "/api/health"): "HealthResponse", ("post", "/api/access"): "AccessResponse", ("get", "/api/weather"): "WeatherResponse", ("post", "/api/tryon/photo"): "PhotoTryOnResponse"}.get((method, path))
                if path in ("/api/wardrobe/items", "/api/wardrobe/items/{item_id}") and method in ("get", "post", "put"):
                    name = "WardrobeItem"
                if name:
                    result = ref(name)
                    if path == "/api/wardrobe/items" and method == "get":
                        result = {"type": "array", "items": result}
                    responses["200"] = {"description": "Successful response", "content": {"application/json": {"schema": result}}}
                if path in ("/api/tryon/photo", "/api/images/generate", "/api/tryon/live-session"):
                    for code in ("499", "502", "503", "504"):
                        responses.setdefault(code, {"description": {"499": "Client disconnected; result discarded. Upstream billing may already have occurred.", "502": "Provider failed or returned invalid data.", "503": "Provider unavailable, unconfigured, or lacks credits/permissions.", "504": "Provider timed out."}[code], "content": deepcopy(error)})
        paths = schema["paths"]
        paths["/api/voice/transcribe"]["post"]["requestBody"] = {"required": True, "description": "Raw audio bytes, not multipart or JSON. Maximum 4 MiB.", "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in ("audio/webm", "video/webm", "audio/mp4", "audio/mpeg", "audio/ogg", "audio/wav")}}
        paths["/api/voice/speech"]["post"]["responses"]["200"] = {"description": "WAV audio bytes", "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}}
        for path in paths:
            if path.startswith(("/wardrobe-media/", "/skills-media/")):
                paths[path]["get"]["responses"]["200"] = {"description": "Protected media bytes; content type depends on file.", "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}
        app.openapi_schema = schema
        return schema
    app.openapi = custom_openapi

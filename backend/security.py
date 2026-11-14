"""Demo hardening for SHADOW when it is exposed to the internet (ngrok tunnel).

One pure-ASGI middleware (`SecurityMiddleware`, installed in main.py) that, before any route runs:

* **Access gate** (optional, off by default): if ``SHADOW_ACCESS_CODE`` is set, every ``/api/*``,
  ``/session/*``, ``/skills-media/*`` and ``/wardrobe-media/*`` request must carry the code, as
  - the cookie ``shadow_access`` (set by ``POST /api/access {"code": ...}`` or by ``?code=``), or
  - the header ``X-Shadow-Access: <code>``, or
  - the query ``?code=<code>`` once on any URL, e.g. ``https://<host>/?code=<code>`` (the response
    then sets the cookie).
  The web shell (``/``, JS, CSS) and ``GET /api/health`` stay public so the code prompt can load.
  Failures → ``401 {"detail": "...", "code": "access_required"}``.
* **Body size cap**: ``SHADOW_MAX_BODY_MB`` (default 16) for every HTTP request → 413.
* **Per-IP rate limits** (in-memory token buckets, ``SHADOW_RATE_LIMIT=off`` disables) with tight
  buckets for the endpoints that spend money (LLM calls, Decart live tokens, photo try-on) → 429.
* **Global caps**: at most ``SHADOW_TRYON_MAX_CONCURRENT`` (default 2) photo try-ons at once and
  ``SHADOW_LIVE_TOKENS_PER_HOUR`` (default 60) Decart tokens per hour for the whole server.
* **WebSocket** ``/session/control``: same-origin check, ≤ ``SHADOW_WS_MAX_PER_IP`` (3) concurrent
  sockets per IP and ``SHADOW_WS_MAX_TOTAL`` (30) overall, and a per-socket message-rate limit
  (a flood closes the socket with 1008).
* Security headers on every HTTP response (nosniff, referrer policy, frame denial).

The client IP is ``scope["client"]``: uvicorn already rewrites it from ``X-Forwarded-For`` for
connections from 127.0.0.1 (the ngrok agent), taking the right-most untrusted hop, so it cannot
be spoofed by a header sent through the tunnel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

ACCESS_COOKIE = "shadow_access"
ACCESS_HEADER = b"x-shadow-access"
ACCESS_COOKIE_MAX_AGE_S = 12 * 3600
GATED_PREFIXES = ("/api/", "/session/", "/skills-media/", "/wardrobe-media/")
PUBLIC_PATHS = frozenset({"/api/health", "/api/access"})

# Pillow: refuse images above this many pixels (decompression bombs). Phone uploads are
# resized to ≤1024 px in the browser, so real requests are far below this.
MAX_IMAGE_PIXELS = 40_000_000

# Per-IP token buckets: name → (requests per minute, burst capacity).
RATE_RULES: dict[str, tuple[float, float]] = {
    "api": (300, 120),
    "write": (60, 30),  # any non-GET /api request (disk writes)
    "eval": (30, 15),
    "access": (10, 5),
    "ask": (30, 10),
    "conversation": (45, 12),
    "voice": (40, 10),
    "compile": (6, 3),
    "tag": (20, 10),
    "shop_import": (10, 4),  # POST /api/wardrobe/import → outbound shop fetch + one tagger call
    "suggest": (12, 5),
    "rule_parse": (20, 8),  # POST/PUT /api/wardrobe/rules → one compiler-model call each
    "weather": (60, 20),
    "tryon_photo": (6, 4),
    "live_token": (6, 3),
    "ws_connect": (12, 6),
}
WS_MSG_RATE = (30.0, 60.0)  # per socket: messages / second, burst

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def configure_pillow() -> None:
    """Lower Pillow's decompression-bomb threshold (it raises at 2x this value)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def access_code() -> str:
    return _env("SHADOW_ACCESS_CODE")


def access_token(code: str) -> str:
    """Cookie value derived from the code (the cookie never holds the code itself)."""
    return hmac.new(code.encode("utf-8"), b"shadow-access-v1", hashlib.sha256).hexdigest()


def check_access_code(candidate: str | None) -> bool:
    code = access_code()
    if not code:
        return True
    return hmac.compare_digest((candidate or "").encode("utf-8"), code.encode("utf-8"))


def access_cookie_header(code: str, *, secure: bool) -> bytes:
    parts = [
        f"{ACCESS_COOKIE}={access_token(code)}",
        "Path=/",
        f"Max-Age={ACCESS_COOKIE_MAX_AGE_S}",
        "HttpOnly",
        "SameSite=Lax",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts).encode("latin-1")


class TokenBucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, capacity: float) -> None:
        self.tokens = capacity
        self.updated = time.monotonic()


@dataclass
class _Rule:
    rate_per_s: float
    capacity: float


class RateLimiter:
    """In-memory per-key token buckets (bounded: idle/full buckets are pruned)."""

    MAX_KEYS = 20_000

    def __init__(self, rules: dict[str, tuple[float, float]], *, enabled: bool = True) -> None:
        self.rules = {name: _Rule(per_min / 60.0, float(burst)) for name, (per_min, burst) in rules.items()}
        self.enabled = enabled
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, rule_name: str, key: str, cost: float = 1.0) -> bool:
        if not self.enabled:
            return True
        rule = self.rules[rule_name]
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get((rule_name, key))
            if bucket is None:
                if len(self._buckets) >= self.MAX_KEYS:
                    self._prune(now)
                bucket = self._buckets[(rule_name, key)] = TokenBucket(rule.capacity)
            bucket.tokens = min(rule.capacity, bucket.tokens + (now - bucket.updated) * rule.rate_per_s)
            bucket.updated = now
            if bucket.tokens < cost:
                return False
            bucket.tokens -= cost
            return True

    def _prune(self, now: float) -> None:
        stale = [
            key for key, bucket in self._buckets.items()
            if now - bucket.updated > 600 or bucket.tokens >= self.rules[key[0]].capacity
        ]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= self.MAX_KEYS:  # still full: start over rather than grow
            self._buckets.clear()


class GuardState:
    """Mutable limiter state; `reset()` re-reads the environment (called at app startup)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        enabled = _env("SHADOW_RATE_LIMIT", "on").lower() not in ("0", "off", "false", "no")
        self.limiter = RateLimiter(RATE_RULES, enabled=enabled)
        self.max_body = max(1, _env_int("SHADOW_MAX_BODY_MB", 16)) * 1024 * 1024
        self.tryon_max = max(1, _env_int("SHADOW_TRYON_MAX_CONCURRENT", 2))
        self.tryon_active = 0
        self.live_tokens_per_hour = max(0, _env_int("SHADOW_LIVE_TOKENS_PER_HOUR", 60))
        self.live_token_times: list[float] = []
        self.ws_max_per_ip = max(1, _env_int("SHADOW_WS_MAX_PER_IP", 3))
        self.ws_max_total = max(1, _env_int("SHADOW_WS_MAX_TOTAL", 30))
        self.ws_by_ip: dict[str, int] = {}
        self.ws_total = 0

    def take_live_token_slot(self) -> bool:
        if not self.limiter.enabled:
            return True
        now = time.monotonic()
        self.live_token_times = [t for t in self.live_token_times if now - t < 3600]
        if len(self.live_token_times) >= self.live_tokens_per_hour:
            return False
        self.live_token_times.append(now)
        return True


GUARD = GuardState()


def rate_rule_for(method: str, path: str) -> str | None:
    if path == "/api/access":
        return "access"
    if path == "/api/ask":
        return "ask"
    if path in ("/api/conversation", "/api/intent", "/api/live/classify", "/api/live/tool"):
        return "conversation"
    if path in ("/api/voice/transcribe", "/api/voice/speech"):
        return "voice"
    if path == "/api/teach/compile":
        return "compile"
    if path == "/api/teach/verify-step":
        return "ask"  # ~10-20 vision calls + maybe one compiler call
    if path == "/api/wardrobe/items" and method == "POST":
        return "tag"
    if path == "/api/wardrobe/import" and method == "POST":
        return "shop_import"
    if path.startswith("/api/wardrobe/rules") and method in ("POST", "PUT"):
        return "rule_parse"
    if path == "/api/stylist/suggest":
        return "suggest"
    if path == "/api/weather":
        return "weather"
    if path in ("/api/tryon/photo", "/api/images/generate"):
        return "tryon_photo"
    if path in ("/api/tryon/live-token", "/api/tryon/live-session", "/api/live/session", "/api/tryon/xmax/session"):
        return "live_token"
    if path == "/api/eval/capture":
        return "eval"
    return None


SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
]


def _headers(scope: Scope) -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    for name, value in scope.get("headers") or []:
        out.setdefault(name.lower(), value)
    return out


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


def _cookie(headers: dict[bytes, bytes], name: str) -> str | None:
    raw = headers.get(b"cookie")
    if not raw:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw.decode("latin-1"))
    except Exception:  # noqa: BLE001 - malformed cookie header
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


def _query_code(scope: Scope) -> str | None:
    raw = scope.get("query_string") or b""
    if b"code=" not in raw:
        return None
    values = parse_qs(raw.decode("latin-1")).get("code")
    return values[0] if values else None


def is_gated(path: str) -> bool:
    return path.startswith(GATED_PREFIXES) and path not in PUBLIC_PATHS


def access_state(scope: Scope) -> tuple[bool, bool]:
    """→ (allowed, set_cookie_from_query)."""
    code = access_code()
    if not code:
        return True, False
    headers = _headers(scope)
    cookie = _cookie(headers, ACCESS_COOKIE)
    if cookie and hmac.compare_digest(cookie.encode("latin-1", "replace"), access_token(code).encode()):
        return True, False
    header = headers.get(ACCESS_HEADER)
    if header is not None and check_access_code(header.decode("utf-8", "replace")):
        return True, False
    query = _query_code(scope)
    if query is not None and check_access_code(query):
        return True, True
    return False, False


async def _send_json(send: Send, status: int, payload: dict[str, Any], extra: list | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        *SECURITY_HEADERS,
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


ACCESS_REQUIRED = {"detail": "Cần mã truy cập để dùng SHADOW.", "code": "access_required"}
RATE_LIMITED = {"detail": "Bạn thao tác nhanh quá — đợi một chút rồi thử lại nhé.", "code": "rate_limited"}
TRYON_BUSY = {"detail": "Máy đang mặc thử cho người khác — đợi khoảng 30 giây rồi thử lại nhé.", "code": "busy"}
LIVE_QUOTA = {"detail": "Đã hết lượt mặc thử trực tiếp trong giờ này — thử lại sau nhé.", "code": "quota"}
TOO_LARGE = {"detail": "Dữ liệu gửi lên quá lớn.", "code": "too_large"}


def _same_origin(headers: dict[bytes, bytes]) -> bool:
    origin = headers.get(b"origin")
    if not origin:
        return True  # non-browser clients (tests, curl) send no Origin
    value = origin.decode("latin-1").lower()
    netloc = value.split("://", 1)[-1].rstrip("/")
    hosts = {
        headers.get(b"host", b"").decode("latin-1").lower(),
        # ngrok --host-header=rewrite keeps the public host here
        headers.get(b"x-forwarded-host", b"").decode("latin-1").lower(),
    } - {""}
    return netloc in hosts


class SecurityMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]], state: GuardState | None = None) -> None:
        self.app = app
        self.state = state or GUARD

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ---- HTTP ------------------------------------------------------------------
    async def _http(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = self.state
        path = scope.get("path") or ""
        method = scope.get("method") or "GET"
        ip = _client_ip(scope)
        set_cookie = False

        if is_gated(path):
            allowed, set_cookie = access_state(scope)
            if not allowed:
                await _send_json(send, 401, ACCESS_REQUIRED)
                return
        elif access_code() and (q := _query_code(scope)) is not None:
            # Shareable demo link https://<host>/?code=<code> → sets the cookie on the first page load.
            set_cookie = check_access_code(q)
        if path.startswith("/api/"):
            if not state.limiter.allow("api", ip) or (
                method not in ("GET", "HEAD", "OPTIONS") and not state.limiter.allow("write", ip)
            ):
                await _send_json(send, 429, RATE_LIMITED, [(b"retry-after", b"10")])
                return
            rule = rate_rule_for(method, path)
            if (rule and method != "GET") or rule == "weather":
                if not state.limiter.allow(rule, ip):
                    await _send_json(send, 429, RATE_LIMITED, [(b"retry-after", b"20")])
                    return
            if rule == "live_token" and method == "POST" and not state.take_live_token_slot():
                await _send_json(send, 429, LIVE_QUOTA, [(b"retry-after", b"600")])
                return

        headers = _headers(scope)
        try:
            declared = int(headers.get(b"content-length", b"0") or 0)
        except ValueError:
            declared = 0
        if declared > state.max_body:
            await _send_json(send, 413, TOO_LARGE)
            return

        tryon_slot = path in ("/api/tryon/photo", "/api/images/generate") and method == "POST"
        if tryon_slot:
            if state.tryon_active >= state.tryon_max:
                await _send_json(send, 429, TRYON_BUSY, [(b"retry-after", b"30")])
                return
            state.tryon_active += 1

        received = 0
        started = False
        cut_off = False  # body cap hit mid-stream: 413 already sent, app output is dropped

        async def limited_receive() -> dict[str, Any]:
            nonlocal received, started, cut_off
            if cut_off:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > state.max_body:
                    # Answer here (frameworks turn exceptions raised while reading into 400s)
                    # and make the app see a disconnected client.
                    cut_off = True
                    if not started:
                        started = True
                        await _send_json(send, 413, TOO_LARGE)
                    return {"type": "http.disconnect"}
            return message

        cookie_header = None
        if set_cookie:
            secure = scope.get("scheme") in ("https", "wss")
            cookie_header = access_cookie_header(access_code(), secure=secure)

        async def wrapped_send(message: dict[str, Any]) -> None:
            nonlocal started
            if cut_off:
                return
            if message["type"] == "http.response.start":
                started = True
                extra = list(SECURITY_HEADERS)
                if cookie_header:
                    extra.append((b"set-cookie", cookie_header))
                existing = {name.lower() for name, _ in message.get("headers") or []}
                message = {
                    **message,
                    "headers": list(message.get("headers") or [])
                    + [(n, v) for n, v in extra if n == b"set-cookie" or n not in existing],
                }
            await send(message)

        try:
            await self.app(scope, limited_receive, wrapped_send)
        except Exception:
            if not cut_off:
                raise
        finally:
            if tryon_slot:
                state.tryon_active -= 1

    # ---- WebSocket ---------------------------------------------------------------
    async def _websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = self.state
        path = scope.get("path") or ""
        if not path.startswith("/session/"):
            await self.app(scope, receive, send)
            return
        ip = _client_ip(scope)
        headers = _headers(scope)
        allowed, _ = access_state(scope)
        reject = (
            not allowed
            or not _same_origin(headers)
            or not state.limiter.allow("ws_connect", ip)
            or (state.limiter.enabled and state.ws_by_ip.get(ip, 0) >= state.ws_max_per_ip)
            or (state.limiter.enabled and state.ws_total >= state.ws_max_total)
        )
        if reject:
            # Closing before accept → the handshake is answered with HTTP 403.
            await send({"type": "websocket.close", "code": 1008})
            return

        state.ws_by_ip[ip] = state.ws_by_ip.get(ip, 0) + 1
        state.ws_total += 1
        rate_per_s, burst = WS_MSG_RATE
        tokens = burst
        last = time.monotonic()
        closed = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal tokens, last, closed
            message = await receive()
            if message["type"] == "websocket.receive" and state.limiter.enabled:
                now = time.monotonic()
                tokens = min(burst, tokens + (now - last) * rate_per_s)
                last = now
                if tokens < 1:
                    if not closed:
                        closed = True
                        try:
                            await send({"type": "websocket.close", "code": 1008, "reason": "rate limited"})
                        except Exception:  # noqa: BLE001
                            pass
                    return {"type": "websocket.disconnect", "code": 1008}
                tokens -= 1
            return message

        try:
            await self.app(scope, limited_receive, send)
        finally:
            state.ws_total -= 1
            left = state.ws_by_ip.get(ip, 1) - 1
            if left > 0:
                state.ws_by_ip[ip] = left
            else:
                state.ws_by_ip.pop(ip, None)

"""LLM provider abstraction for SHADOW (new — not part of GlassKit).

All AI calls (per-frame vision judging, skill compilation, "Hỏi Shadow") go through
`LLMProvider.chat_json`, which talks to any OpenAI-compatible chat-completions API
(OpenRouter by default). `MockProvider` lets the whole app run offline: callers check
`provider.mode == "mock"` and implement their own deterministic fake behaviour.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger("uvicorn.error")

DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-2.5-flash"
DEFAULT_VISION_MIN_INTERVAL_MS = 1200
# HTTP-level ceiling for any single LLM request. Callers bound each kind of call more
# tightly with `asyncio.wait_for` (see vision_timeout_s / compile_timeout_s / ask_timeout_s),
# so a slow compile (~15-30 s with many frames) is never cut off by a vision-sized limit.
DEFAULT_TIMEOUT_SECONDS = 120.0


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def llm_base_url() -> str:
    return _env("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/")


def vision_model() -> str:
    return _env("VISION_MODEL", DEFAULT_MODEL)


def compiler_model() -> str:
    return _env("COMPILER_MODEL", DEFAULT_MODEL)


def conversation_model() -> str:
    return _env("CONVERSATION_MODEL", compiler_model())


def vision_min_interval_ms() -> int:
    return max(0, _env_int("VISION_MIN_INTERVAL_MS", DEFAULT_VISION_MIN_INTERVAL_MS))


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(_env(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def vision_timeout_s() -> float:
    """Per-frame judging budget: a stuck frame must not freeze the coach for long."""
    return _env_seconds("VISION_TIMEOUT_S", 10.0)


def ask_timeout_s() -> float:
    return _env_seconds("ASK_TIMEOUT_S", 20.0)


def compile_timeout_s() -> float:
    """Teach compile budget (one attempt). The web client waits up to 180 s in total."""
    return _env_seconds("COMPILE_TIMEOUT_S", 90.0)


# ---- Guide (vision judging) tuning — see .env.example ---------------------------------
DEFAULT_VISION_IMAGE_MAX_SIDE = 448
MAX_VISION_IMAGE_SIDE = 512  # the phone never captures more than this
VISION_REFERENCE_MODES = ("always", "first", "never", "auto")
DEFAULT_VISION_REFERENCE_MODE = "auto"
VISION_DECISIONS = ("json", "logprob")
DEFAULT_VISION_DONE_THRESHOLD = 0.9


def vision_image_max_side() -> int:
    """Longest side (px) of learner frames sent to the vision model (backend downsizes)."""
    return max(128, min(MAX_VISION_IMAGE_SIDE, _env_int("VISION_IMAGE_MAX_SIDE", DEFAULT_VISION_IMAGE_MAX_SIDE)))


def vision_reference_mode() -> str:
    """When to attach the teacher's reference image: always | first | never | auto."""
    mode = _env("VISION_REFERENCE_MODE", DEFAULT_VISION_REFERENCE_MODE).lower()
    return mode if mode in VISION_REFERENCE_MODES else DEFAULT_VISION_REFERENCE_MODE


def vision_decision() -> str:
    """json = model writes {"flag": …, "hint"?}; logprob = one yes/no (or letter) token + probability."""
    mode = _env("VISION_DECISION", "json").lower()
    return mode if mode in VISION_DECISIONS else "json"


def vision_done_threshold() -> float:
    """logprob mode: P(done) needed before a frame counts as 'done'."""
    try:
        value = float(_env("VISION_DONE_THRESHOLD", str(DEFAULT_VISION_DONE_THRESHOLD)))
    except ValueError:
        return DEFAULT_VISION_DONE_THRESHOLD
    return value if 0.0 < value <= 1.0 else DEFAULT_VISION_DONE_THRESHOLD


def vision_fast_confirm() -> bool:
    """Judge the confirmation frame right away (skip VISION_MIN_INTERVAL_MS once)."""
    return _env("VISION_FAST_CONFIRM", "on").lower() not in {"0", "off", "false", "no"}


# Import-time snapshots (convenience). Prefer the functions above when env may change.
LLM_BASE_URL = llm_base_url()
VISION_MODEL = vision_model()
COMPILER_MODEL = compiler_model()
VISION_MIN_INTERVAL_MS = vision_min_interval_ms()


class ProviderError(RuntimeError):
    """Raised when the upstream LLM call fails or returns unusable output."""

    def __init__(self, message: str, *, code: str = "invalid_provider_response", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _record_success() -> None:
    from service_status import record_success
    record_success("llm")


def _failure(message: str, *, code: str = "invalid_provider_response", retryable: bool = True) -> ProviderError:
    from service_status import record_failure
    # Status messages are deliberately fixed: upstream bodies can contain user data or credentials.
    record_failure("llm", code, "Dịch vụ AI chưa hoàn tất yêu cầu.", retryable)
    return ProviderError(message, code=code, retryable=retryable)


def _http_failure(status: int) -> ProviderError:
    code, retryable = {
        401: ("provider_auth_failed", False), 403: ("provider_auth_failed", False),
        402: ("insufficient_credits", False), 429: ("rate_limited", True),
    }.get(status, ("provider_unavailable", True) if status >= 500 else ("provider_request_rejected", False))
    return _failure(f"LLM HTTP {status}", code=code, retryable=retryable)


def _transport_failure(error: Exception) -> ProviderError:
    if isinstance(error, httpx.HTTPStatusError):
        return _http_failure(error.response.status_code)
    if isinstance(error, httpx.TimeoutException):
        return _failure("LLM request timed out", code="provider_timeout")
    if isinstance(error, httpx.HTTPError):
        return _failure("LLM connection failed", code="provider_unavailable")
    return _failure("LLM returned invalid data")


class LogprobsUnsupported(ProviderError):
    """The provider/gateway does not return token logprobs (caller falls back to JSON mode)."""


def _norm_token(token: str) -> str:
    return re.sub(r"[^\w]+", "", (token or "").strip().lower())


def aggregate_choice_probs(
    top_logprobs: list[dict[str, Any]], choices: list[str]
) -> dict[str, float]:
    """Sum the probability of every token variant of each choice ('yes', 'Yes', ' yes', 'YES'…).

    Returns probabilities renormalised over the choices (mass on other tokens is dropped);
    an empty dict when no listed token matches any choice.
    """
    wanted = {_norm_token(choice): choice for choice in choices}
    mass: dict[str, float] = {}
    for entry in top_logprobs or []:
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        logprob = entry.get("logprob")
        if not isinstance(token, str) or not isinstance(logprob, (int, float)):
            continue
        choice = wanted.get(_norm_token(token))
        if choice is None:
            continue
        mass[choice] = mass.get(choice, 0.0) + math.exp(float(logprob))
    total = sum(mass.values())
    if total <= 0:
        return {}
    return {choice: mass.get(choice, 0.0) / total for choice in choices}


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Robustly pull the first JSON object out of a model reply.

    Handles bare JSON, ```json fenced blocks, and JSON surrounded by prose.
    Raises ProviderError if no JSON object can be decoded.
    """
    if not isinstance(text, str):
        raise ProviderError("model reply is not text")
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    for match in _FENCE_RE.finditer(text):
        inner = match.group(1).strip()
        if inner:
            candidates.append(inner)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
        # Scan for the first decodable {...} inside the candidate.
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                data, _end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    raise ProviderError("no JSON object in model reply")


def _message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


class LLMProvider:
    """Interface: `mode` is "ai" or "mock"; `chat_json` returns a parsed JSON dict."""

    mode: str = "ai"

    def stream_text(
        self, *, model: str, messages: list[dict[str, Any]], max_tokens: int = 180
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    def stream_chat(
        self, *, model: str, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]], max_tokens: int = 800,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError

    async def chat_json(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        max_tokens: int = 400,
        temperature: float = 0.1,
    ) -> dict:
        raise NotImplementedError

    async def chat_choice_probs(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        choices: list[str],
        top_logprobs: int = 10,
    ) -> dict[str, float]:
        """One-token answer restricted (by prompt) to `choices`; returns P(choice).

        Raises LogprobsUnsupported when the backend has no logprobs.
        """
        raise LogprobsUnsupported(f"{type(self).__name__} has no logprobs")

    async def aclose(self) -> None:
        return None


class MockProvider(LLMProvider):
    """Offline provider. Callers must check `provider.mode == "mock"` and fake results."""

    mode = "mock"

    async def chat_json(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        max_tokens: int = 400,
        temperature: float = 0.1,
    ) -> dict:
        raise NotImplementedError(
            "MockProvider has no model; callers implement mock behaviour themselves"
        )


class OpenAICompatibleProvider(LLMProvider):
    mode = "ai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_body = dict(extra_body or {})
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = _env(
                "OPENROUTER_REFERER", "https://github.com/shadow-hackathon/shadow"
            )
            headers["X-Title"] = "SHADOW"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=headers,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as error:
            raise _transport_failure(error) from None

    async def stream_text(
        self, *, model: str, messages: list[dict[str, Any]], max_tokens: int = 180
    ) -> AsyncIterator[str]:
        body = {
            **self._extra_body,
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": True,
        }
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = line[5:].strip()
                    if event == "[DONE]":
                        _record_success()
                        return
                    chunk = json.loads(event)
                    if chunk.get("error"):
                        raise _failure("LLM returned a stream error")
                    choices = chunk.get("choices") or []
                    if choices:
                        choice = choices[0]
                        delta = _message_text(choice.get("delta"))
                        if delta:
                            yield delta
                        if choice.get("finish_reason"):
                            _record_success()
                            return
                raise _failure("LLM stream closed before its terminal event")
        except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
            raise _transport_failure(error) from None

    async def stream_chat(
        self, *, model: str, messages: list[dict[str, Any]],
        tools: list[dict[str, Any]], max_tokens: int = 800,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream visible text and a complete assistant message with function calls."""
        body = {**self._extra_body, "model": model, "messages": messages,
                "tools": tools, "max_tokens": max_tokens, "stream": True}
        content = ""
        calls: dict[int, dict[str, Any]] = {}
        size = 0
        terminal = False
        try:
            async with self._client.stream("POST", f"{self.base_url}/chat/completions", json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = line[5:].strip()
                    if len(event) > 262144:
                        raise _failure("LLM stream event exceeds limit")
                    if event == "[DONE]":
                        terminal = True
                        break
                    chunk = json.loads(event)
                    if not isinstance(chunk, dict) or chunk.get("error"):
                        raise _failure("LLM returned a stream error")
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list) or len(choices) > 1:
                        raise _failure("invalid stream choices")
                    if not choices:
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                        raise _failure("invalid stream choice")
                    delta = choice.get("delta", {})
                    if not isinstance(delta, dict) or delta.get("role", "assistant") != "assistant":
                        raise _failure("invalid assistant delta")
                    text = delta.get("content")
                    if text is not None and not isinstance(text, str):
                        raise _failure("invalid assistant content")
                    fragments = delta.get("tool_calls", [])
                    if not isinstance(fragments, list):
                        raise _failure("invalid tool-call deltas")
                    for fragment in fragments:
                        if not isinstance(fragment, dict):
                            raise _failure("invalid tool-call delta")
                        index = fragment.get("index")
                        if type(index) is not int or not 0 <= index < 32:
                            raise _failure("invalid tool-call index")
                        if fragment.get("type", "function") != "function":
                            raise _failure("unsupported tool-call type")
                        call = calls.setdefault(index, {"id": "", "type": "function",
                                                       "function": {"name": "", "arguments": ""}})
                        function = fragment.get("function", {})
                        if not isinstance(function, dict):
                            raise _failure("invalid tool-call function")
                        for source, key, target in ((fragment, "id", call), (function, "name", call["function"]),
                                                    (function, "arguments", call["function"])):
                            value = source.get(key, "")
                            if not isinstance(value, str):
                                raise _failure("invalid tool-call field")
                            size += len(value)
                            target[key] += value
                    if text:
                        size += len(text)
                        content += text
                    if size > 131072:
                        raise _failure("LLM stream output exceeds limit")
                    if text:
                        yield {"type": "text", "text": text}
                    reason = choice.get("finish_reason")
                    if reason is not None:
                        if reason not in {"stop", "tool_calls"}:
                            raise _failure("LLM stream truncated or rejected")
                        if reason == "tool_calls" and not calls:
                            raise _failure("missing tool calls")
                        terminal = True
                        break
            if not terminal:
                raise _failure("LLM stream closed before its terminal event")
            if sorted(calls) != list(range(len(calls))):
                raise _failure("noncontiguous tool-call indices")
            ordered = [calls[index] for index in sorted(calls)]
            ids: set[str] = set()
            for call in ordered:
                if not call["id"] or call["id"] in ids or not call["function"]["name"]:
                    raise _failure("incomplete tool call")
                ids.add(call["id"])
                if not isinstance(json.loads(call["function"]["arguments"]), dict):
                    raise _failure("tool-call arguments must be an object")
            message: dict[str, Any] = {"role": "assistant", "content": content or None}
            if ordered:
                message["tool_calls"] = ordered
            _record_success()
            yield {"type": "message", "message": message}
        except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
            raise _transport_failure(error) from None

    async def chat_json(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        max_tokens: int = 400,
        temperature: float = 0.1,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            **self._extra_body,
        }
        response = await self._post(body)
        if response.status_code == 400 and "response_format" in body:
            # Some models/providers reject json_object mode; retry with prompt-only JSON.
            logger.info("LLM rejected response_format (400); retrying without it")
            body.pop("response_format", None)
            response = await self._post(body)
        if not response.is_success:
            raise _http_failure(response.status_code)
        try:
            data = response.json()
            choice = data["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise _failure("malformed LLM response") from None

        text = _message_text(choice.get("message"))
        if not text.strip() and choice.get("finish_reason") == "length":
            # Reasoning models can burn the whole budget thinking; retry with more room.
            body["max_tokens"] = max_tokens * 4
            response = await self._post(body)
            if not response.is_success:
                raise _http_failure(response.status_code)
            try:
                text = _message_text(response.json()["choices"][0].get("message"))
            except (ValueError, KeyError, IndexError, TypeError) as error:
                raise _failure("malformed LLM response on retry") from error
        try:
            result = extract_json_object(text)
        except ProviderError:
            raise _failure("LLM returned invalid JSON") from None
        _record_success()
        return result

    async def chat_choice_probs(
        self,
        *,
        model: str,
        system: str,
        content: list[dict],
        choices: list[str],
        top_logprobs: int = 10,
    ) -> dict[str, float]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "max_tokens": 1,
            "temperature": 0.0,
            "logprobs": True,
            "top_logprobs": max(1, min(20, top_logprobs)),
            **self._extra_body,
        }
        response = await self._post(body)
        if response.status_code == 400 and "logprobs" in response.text.lower():
            raise LogprobsUnsupported("LLM rejected logprobs")
        if not response.is_success:
            raise _http_failure(response.status_code)
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise _failure("malformed LLM response") from None
        logprobs = choice.get("logprobs") if isinstance(choice, dict) else None
        tokens = logprobs.get("content") if isinstance(logprobs, dict) else None
        if not isinstance(tokens, list) or not tokens or not isinstance(tokens[0], dict):
            raise LogprobsUnsupported("LLM response has no logprobs")
        first = tokens[0]
        top = first.get("top_logprobs")
        candidates = list(top) if isinstance(top, list) else []
        if isinstance(first.get("token"), str) and isinstance(first.get("logprob"), (int, float)):
            # the sampled token itself (top_logprobs may be capped below it)
            if not any(isinstance(c, dict) and c.get("token") == first["token"] for c in candidates):
                candidates.append({"token": first["token"], "logprob": first["logprob"]})
        result = aggregate_choice_probs(candidates, choices)
        _record_success()
        return result


def resolve_mode() -> str:
    requested = _env("SHADOW_MODE", "auto").lower()
    has_key = bool(_env("LLM_API_KEY"))
    if requested == "mock":
        return "mock"
    if requested == "ai":
        if not has_key:
            logger.warning("SHADOW_MODE=ai but LLM_API_KEY is empty; falling back to mock")
            return "mock"
        return "ai"
    return "ai" if has_key else "mock"


def build_provider_from_env() -> LLMProvider:
    mode = resolve_mode()
    if mode == "mock":
        logger.info("SHADOW provider: mock (offline demo mode)")
        return MockProvider()
    extra_body: dict[str, Any] = {}
    raw_extra = _env("LLM_EXTRA_BODY")
    if raw_extra:
        try:
            parsed = json.loads(raw_extra)
            if isinstance(parsed, dict):
                extra_body = parsed
        except json.JSONDecodeError:
            logger.warning("LLM_EXTRA_BODY is not valid JSON; ignoring")
    base_url = llm_base_url()
    logger.info("SHADOW provider: ai base_url=%s", base_url)
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=_env("LLM_API_KEY"),
        extra_body=extra_body,
    )

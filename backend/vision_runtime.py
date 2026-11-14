"""Vision + speech runtime for SKI (new; replaces GlassKit session_runtime.py).

GlassKit streamed video to Overshoot over WebRTC and spoke through OpenAI Realtime.
SKI instead receives JPEG frames over the control WebSocket (`vision.frame`), asks a
vision LLM (OpenAI-compatible provider) to judge the active step's detector, and sends
speech to the phone as text (`speech` messages) for browser TTS.

Concurrency model: at most one detection in flight per session. Frames that arrive while
busy overwrite `session.pending_frame` (latest wins). The worker hands each result to the
session loop as a `vision.result` event and waits until the loop has evaluated it, so the
next frame is always judged against the up-to-date step.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from providers import (
    LLMProvider,
    LogprobsUnsupported,
    ask_timeout_s,
    vision_decision,
    vision_done_threshold,
    vision_fast_confirm,
    vision_image_max_side,
    vision_min_interval_ms,
    vision_model,
    vision_reference_mode,
    vision_timeout_s,
)
from recipe_catalog import Recipe, RecipeDetector, RecipeStep
from session_constants import MAX_FRAME_DATA_URL_CHARS, PHASE_GUIDING
from session_helpers import value_meeting_condition
from session_types import ControlSession, SessionEvent, StepRuntimeState
from skill_store import SkillStore

logger = logging.getLogger("uvicorn.error")

MOCK_WRONG_HINT = "Chưa đúng rồi, xem lại ảnh mẫu nhé."
MOCK_WRONG_STRING = "khác"
REFERENCE_IMAGE_LABEL = "Ảnh mẫu của người dạy khi hoàn thành bước này"

VISION_SYSTEM_PROMPT = """
Bạn là "SKI", giám khảo thị giác cho một ứng dụng dạy kỹ năng thực hành bằng camera điện thoại.
Người học (thường nói tiếng Việt) đang làm theo từng bước mà một người dạy đã làm mẫu.
Nhiệm vụ: nhìn ẢNH HIỆN TẠI từ camera của người học và trả lời đúng câu hỏi của bộ nhận diện (detector).
Quy tắc:
- Chỉ đánh giá dựa trên ẢNH HIỆN TẠI. Ảnh mẫu của người dạy (nếu có) chỉ dùng để so sánh trạng thái "đã xong" trông như thế nào; không bao giờ chấm ảnh mẫu.
- Thận trọng: nếu không chắc chắn hoặc không nhìn rõ, coi như bước CHƯA hoàn thành.
- Chỉ trả về MỘT đối tượng JSON, không giải thích, không markdown.
- Trường `hint` (tùy chọn): một câu tiếng Việt thật ngắn (tối đa ~15 từ), giọng thân thiện, nói người học cần sửa gì.
  Chỉ thêm `hint` khi người học đang làm SAI rõ ràng (khác ảnh mẫu / sai thao tác). Không thêm `hint` khi họ chỉ đang làm dở hoặc ảnh không rõ.
""".strip()

ASK_SYSTEM_PROMPT = """
Bạn là "SKI", trợ lý hướng dẫn kỹ năng thực hành qua camera điện thoại.
Trả lời câu hỏi của người học bằng tiếng Việt, tối đa 2 câu ngắn, cụ thể, thân thiện, dễ đọc thành tiếng.
Dựa vào bước hiện tại, danh sách các bước, mẹo/cảnh báo của người dạy, ảnh hiện tại từ camera và ảnh mẫu (nếu có).
Ưu tiên đúng lời người dạy; không bịa thông tin trái với hướng dẫn.
Chỉ trả về JSON dạng {"answer": "..."}.
""".strip()


_TRUE_WORDS = {"true", "yes", "y", "1", "done", "co", "có", "đúng", "dung", "rồi", "roi", "xong"}
_FALSE_WORDS = {"false", "no", "n", "0", "not_done", "khong", "không", "chưa", "chua", "sai"}
_UNKNOWN_WORDS = {"unknown", "unsure", "uncertain", "not_visible", "unclear", "n/a", "na", "?"}
MAX_HINT_CHARS = 140


def _norm_label(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().strip("\"'`.").lower())


def _coerce_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = _norm_label(value)
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None


def _known_labels(step: RecipeStep) -> list[str]:
    labels: list[Any] = [step.expected_value, step.complete_value, step.progress_value]
    labels += step.ignored_values_list()
    return [v for v in labels if isinstance(v, str) and v]


def _find_field(raw: dict[str, Any], field: str) -> Any:
    """`field` at the top level, else inside one nested object, else a lone scalar answer."""
    if field in raw:
        return raw[field]
    lowered = {str(k).strip().lower(): v for k, v in raw.items()}
    if field in lowered:
        return lowered[field]
    for value in raw.values():
        if isinstance(value, dict) and field in value:
            return value[field]
    scalars = {k: v for k, v in lowered.items() if k != "hint" and not isinstance(v, (dict, list))}
    if len(scalars) == 1:
        key, value = next(iter(scalars.items()))
        if key in {"value", "answer", "result", "output", "status", "done", "complete", "completed"}:
            return value
    return None


def normalize_vision_result(
    step: RecipeStep, detector: RecipeDetector, raw: Any
) -> dict[str, Any]:
    """Coerce a vision model reply into `{<field>: value, "hint"?: str}`.

    Small VLMs (e.g. Qwen3-VL-8B) sometimes answer `"false"` as a string, `"Locked"` with
    different casing, nest the answer, or rename the field. A string "false" would be
    truthy for `momentary_true_complete` and skip a step, so values are coerced strictly.
    Anything that cannot be understood becomes `None` (the workflow ignores that frame).
    """
    if not isinstance(raw, dict):
        return {detector.field: None}
    field = detector.field
    value = _find_field(raw, field)
    if field == "flag":
        value = _coerce_flag(value)
    elif field == "level":
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                value = None if _norm_label(value) not in _UNKNOWN_WORDS else "unknown"
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool):
            value = None
    elif field == "ingredients":
        if not isinstance(value, list):
            value = None
    else:  # string enums: state / color / ...
        if isinstance(value, bool):
            value = None
        elif isinstance(value, (int, float)):
            value = str(value)
        if isinstance(value, str):
            label = _norm_label(value)
            known = _known_labels(step)
            match = next((k for k in known if _norm_label(k) == label), None)
            if match is not None:
                value = match
            elif label in _UNKNOWN_WORDS and any(_norm_label(k) == "unknown" for k in known):
                value = next(k for k in known if _norm_label(k) == "unknown")
            else:
                value = label or None
        elif value is not None:
            value = None
    result: dict[str, Any] = {field: value}
    hint = raw.get("hint")
    if isinstance(hint, str):
        hint = re.sub(r"\s+", " ", hint).strip().strip('"').strip()
        if hint and hint.lower() not in {"null", "none", "n/a", "-"}:
            result["hint"] = hint[:MAX_HINT_CHARS]
    return result


# ---- image budget ------------------------------------------------------------------


def downscale_data_url(data_url: str, max_side: int, *, quality: int = 80) -> str:
    """Shrink a JPEG/PNG data URL so its longest side is ≤ `max_side` px.

    Fewer pixels = fewer vision tokens = faster prefill. Returns the input unchanged when it
    is already small enough or cannot be decoded (never fails the frame).
    """
    try:
        header, sep, payload = data_url.partition(",")
        if not sep or ";base64" not in header:
            return data_url
        from PIL import Image, ImageOps

        raw = base64.b64decode(payload, validate=False)
        with Image.open(io.BytesIO(raw)) as img:
            if max(img.size) <= max_side:
                return data_url
            img.draft("RGB", (max_side, max_side))  # fast JPEG DCT scaling for big inputs
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, "JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 — a bad image must not break judging
        return data_url


def should_include_reference(mode: str, state: StepRuntimeState) -> bool:
    """VISION_REFERENCE_MODE policy for the teacher's reference image.

    always/never: as named · first: only the first vision call of a step ·
    auto: the first call of a step and the call right after a 'not done' result that came
    with a hint (the learner is visibly doing it wrong — comparing with the reference helps).
    """
    if mode == "always":
        return True
    if mode == "never":
        return False
    first = state.detections_started == 0
    if mode == "first":
        return first
    return first or state.last_result_hint


# ---- logprob decision (VISION_DECISION=logprob) -----------------------------------

LOGPROB_SYSTEM_PROMPT = """
Bạn là "SKI", giám khảo thị giác cho một ứng dụng dạy kỹ năng thực hành bằng camera điện thoại.
Nhìn ẢNH HIỆN TẠI từ camera của người học và trả lời câu hỏi của bộ nhận diện.
Ảnh mẫu của người dạy (nếu có) chỉ để so sánh, không bao giờ chấm ảnh mẫu.
Thận trọng: nếu không chắc chắn hoặc không nhìn rõ, coi như bước CHƯA hoàn thành.
Reply with exactly ONE token as instructed (no JSON, no explanation).
""".strip()

HINT_ONLY_INSTRUCTION = (
    "The learner has NOT completed this step yet. Reply with ONLY one JSON object "
    '{"hint": "<one short friendly Vietnamese sentence, max 15 words, telling the learner what to '
    'fix or do next>"}. Use {"hint": ""} if the objects of this step are not visible in the image.'
)

LOGPROB_FIELDS = frozenset({"flag", "state", "color"})
_LETTERS = "ABCDEFGH"
_JSON_SENTENCE_RE = re.compile(r"[^.]*\bJSON\b[^.]*\.\s*", re.IGNORECASE)
_UNKNOWNISH = _UNKNOWN_WORDS | {"none", "other", "khác"}


def _strip_json_talk(prompt: str) -> str:
    return _JSON_SENTENCE_RE.sub("", prompt or "").strip() or (prompt or "").strip()


def flag_done_answer(step: RecipeStep) -> bool:
    """The flag value that means 'this step is done'."""
    if step.evaluation_mode == "momentary_true_complete":
        return step.complete_on is not False
    if step.evaluation_mode == "match_value" and isinstance(step.expected_value, bool):
        return step.expected_value
    return True


def state_done_value(step: RecipeStep) -> str | None:
    if step.evaluation_mode == "match_value" and isinstance(step.expected_value, str):
        return step.expected_value
    if step.evaluation_mode == "enum_progress_once_then_complete" and isinstance(step.complete_value, str):
        return step.complete_value
    return None


def state_options(step: RecipeStep, detector: RecipeDetector) -> list[tuple[str, bool]]:
    """Answer options for a string detector as (label, synthetic) — synthetic = our 'unknown'."""
    prompt = detector.prompt or ""
    found: list[str] = []
    one_of = re.search(r"one of\s*:?\s*(.+?)(?:\.\s|\.$|$)", prompt, re.IGNORECASE | re.DOTALL)
    if one_of:
        found += re.findall(r'["“]([^"”]+)["”]', one_of.group(1))
    pipes = re.search(r"([\w\-]+(?:\s*\|\s*[\w\-]+)+)", prompt)
    if pipes:
        found += [part.strip() for part in pipes.group(1).split("|")]
    found += _known_labels(step)
    options: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for label in found:
        key = _norm_label(label)
        if key and key not in seen:
            seen.add(key)
            options.append((label, False))
    options = options[: len(_LETTERS) - 1]
    if not any(_norm_label(label) in _UNKNOWNISH for label, _ in options):
        options.append(("unknown", True))
    return options


def _rewrite_flag_prompt(prompt: str) -> str:
    text = _strip_json_talk(prompt)
    text = re.sub(r"\breturn\s+`?true`?", "answer yes", text, flags=re.IGNORECASE)
    text = re.sub(r"\breturn\s+`?false`?", "answer no", text, flags=re.IGNORECASE)
    return text


def _rewrite_state_prompt(prompt: str, options: list[tuple[str, bool]]) -> str:
    text = _strip_json_talk(prompt)
    for letter, (label, _synthetic) in zip(_LETTERS, options):
        text = re.sub(
            r"\breturn\s+[\"“`']?" + re.escape(label) + r"[\"”`']?",
            f"answer {letter}",
            text,
            flags=re.IGNORECASE,
        )
    return text


def logprob_result(
    step: RecipeStep,
    detector: RecipeDetector,
    probs: dict[str, float],
    *,
    threshold: float,
    options: list[tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    """Map choice probabilities to the detector's result shape + `confidence` (= P(done))."""
    field = detector.field
    if not probs:
        return {field: None}
    if field == "flag":
        done_answer = flag_done_answer(step)
        p_done = probs.get("yes" if done_answer else "no", 0.0)
        value = done_answer if p_done >= threshold else (not done_answer)
        return {field: value, "confidence": round(p_done, 3)}
    options = options or state_options(step, detector)
    by_label = {label: probs.get(letter, 0.0) for letter, (label, _s) in zip(_LETTERS, options)}
    synthetic = {label for label, s in options if s}
    best = max(by_label, key=lambda label: by_label[label])
    done_value = state_done_value(step)
    p_done = by_label.get(done_value, 0.0) if done_value is not None else by_label[best]
    value: Any = best
    if done_value is not None and best == done_value and p_done < threshold:
        # Not sure enough to count as done: fall back to an ignored label (no mismatch speech).
        ignored = [v for v in step.ignored_values_list() if isinstance(v, str)]
        value = ignored[0] if ignored else None
    elif best in synthetic:
        value = None
    return {field: value, "confidence": round(p_done, 3)}


def _mock_latency_seconds() -> float:
    try:
        return max(0, int(os.getenv("SHADOW_MOCK_LATENCY_MS", "300"))) / 1000.0
    except ValueError:
        return 0.3


def _limit_sentences(text: str, max_sentences: int = 2) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return text
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return " ".join(parts[:max_sentences]).strip()


def _strip_end(text: str) -> str:
    return (text or "").strip().rstrip(".!?…。 ").strip()


def mock_detection(
    step: RecipeStep, detector: RecipeDetector, *, step_index: int, frame_number: int
) -> dict[str, Any]:
    """Deterministic fake detector for offline demo mode.

    Every step returns "not done" for 2 frames, then the completing value. On the 2nd
    step (index 1) the first result is a wrong value with a Vietnamese hint so the demo
    shows a correction. `frame_number` is 0-based per step.
    """
    field = detector.field
    mode = step.evaluation_mode
    ignored = step.ignored_values_list()
    wrong_first = step_index == 1 and frame_number == 0

    if mode == "count_rising_edges_true":
        # Alternate false/true so each pair of frames produces one rising edge.
        result: dict[str, Any] = {field: frame_number % 2 == 1}
        if wrong_first:
            result["hint"] = MOCK_WRONG_HINT
        return result

    complete = frame_number >= 2
    if mode == "momentary_true_complete":
        target = bool(step.complete_on)
        return {field: target} if complete else _with_hint({field: not target}, wrong_first)

    if mode == "match_value":
        if complete:
            return {field: step.expected_value}
        if wrong_first:
            wrong: Any = (not step.expected_value) if isinstance(step.expected_value, bool) else MOCK_WRONG_STRING
            return {field: wrong, "hint": MOCK_WRONG_HINT}
        return {field: ignored[0] if ignored else "unknown"}

    if mode == "enum_progress_once_then_complete":
        if complete:
            return {field: step.complete_value}
        if wrong_first:
            return {field: MOCK_WRONG_STRING, "hint": MOCK_WRONG_HINT}
        if step.progress_value is not None:
            return {field: step.progress_value}
        return {field: ignored[0] if ignored else "unknown"}

    if mode == "numeric_threshold_with_progress_once":
        if complete:
            return {field: value_meeting_condition(step.complete_condition)}
        if step.progress_condition is not None:
            not_done: Any = value_meeting_condition(step.progress_condition, 0)
        else:
            not_done = 0
        return _with_hint({field: not_done}, wrong_first)

    return {}


def _with_hint(result: dict[str, Any], add: bool) -> dict[str, Any]:
    if add:
        result["hint"] = MOCK_WRONG_HINT
    return result


def mock_answer(step: RecipeStep | None, question: str) -> str:
    if step is None:
        return "Bạn cứ làm chậm rãi theo từng bước nhé. Nếu chưa chắc, hãy so với ảnh mẫu của người dạy."
    instruction = _strip_end(step.instruction or step.on_enter_speech or "")
    first = f"Ở bước này, bạn cần: {instruction}." if instruction else "Bạn cứ làm theo hướng dẫn trên màn hình nhé."
    if step.tip:
        second = f"Mẹo của người dạy: {_strip_end(step.tip)}."
    elif step.warning:
        second = f"Lưu ý: {_strip_end(step.warning)}."
    else:
        second = "Làm chậm rãi và so với ảnh mẫu là được."
    return f"{first} {second}"


class VisionRuntimeMixin:
    if TYPE_CHECKING:
        _provider: LLMProvider
        _skills: SkillStore
        _ref_image_cache: dict[str, tuple[float, str]]

    # ---- outbound messages ---------------------------------------------------

    async def _send_control(
        self, session: ControlSession, payload: dict[str, Any]
    ) -> None:
        with suppress(Exception):
            await session.control_ws.send_json(payload)

    async def _speak_line(
        self, session: ControlSession, text: str, kind: str = "enter"
    ) -> None:
        line = (text or "").strip()
        if not line:
            return
        session.speech_epoch += 1
        session.current_speech_text = line
        logger.info(
            "session=%s speech epoch=%s kind=%s text=%s",
            session.session_id,
            session.speech_epoch,
            kind,
            line,
        )
        # GlassKit order: publish HUD (new speech_epoch) first, then the line itself.
        await self._publish_hud_state(session)  # type: ignore[attr-defined]
        await self._send_control(
            session,
            {"type": "speech", "text": line, "epoch": session.speech_epoch, "kind": kind},
        )

    # ---- inbound frames ------------------------------------------------------

    async def handle_vision_frame(
        self, session: ControlSession, payload: dict[str, Any]
    ) -> None:
        if session.destroyed or session.phase != PHASE_GUIDING:
            return
        image = payload.get("image")
        if not isinstance(image, str) or not image.startswith("data:image/"):
            await self._send_control(
                session,
                {"type": "hud.error", "message": "Ảnh từ camera không hợp lệ."},
            )
            return
        if len(image) > MAX_FRAME_DATA_URL_CHARS:
            await self._send_control(
                session,
                {"type": "hud.error", "message": "Ảnh từ camera quá lớn."},
            )
            return
        session.pending_frame = {
            "image": image,
            "ts": payload.get("ts"),
            "trigger": payload.get("trigger"),
        }
        if session.vision_task is None or session.vision_task.done():
            generation = session.vision_generation
            session.vision_task = asyncio.create_task(
                self._run_vision_worker(session, generation),
                name=f"vision-worker-{session.session_id}-{generation}",
            )

    async def _stop_vision_runtime(self, session: ControlSession) -> None:
        session.vision_generation += 1
        session.pending_frame = None
        session.fast_confirm = False
        for attr in ("vision_task", "hint_task"):
            task = getattr(session, attr)
            setattr(session, attr, None)
            if task is not None and not task.done() and task is not asyncio.current_task():
                task.cancel()
                with suppress(BaseException):
                    await task

    async def _run_vision_worker(self, session: ControlSession, generation: int) -> None:
        try:
            while not session.destroyed and generation == session.vision_generation:
                if session.pending_frame is None:
                    return
                min_interval = vision_min_interval_ms() / 1000.0
                if session.fast_confirm:
                    # A 'done' frame is waiting for confirmation: judge the next one right away.
                    min_interval = 0.0
                if session.vision_error_streak:
                    # AI unreachable / failing: back off (0.5 s, 1 s, 2 s … max 8 s extra)
                    # instead of hammering the gateway with every frame.
                    min_interval += min(8.0, 0.5 * 2 ** (session.vision_error_streak - 1))
                wait = min_interval - (time.monotonic() - session.last_vision_started_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue  # re-read: a newer frame may have replaced the pending one
                frame = session.pending_frame
                session.pending_frame = None
                if frame is None:
                    return
                session.fast_confirm = False
                if session.phase != PHASE_GUIDING or session.recipe is None:
                    continue
                step = session.step_lookup.get(session.current_step_id or "")
                if step is None:
                    continue
                detector = session.recipe.detectors.get(step.detector_key)
                if detector is None:
                    continue

                session.last_vision_started_at = time.monotonic()
                started = time.perf_counter()
                result: dict[str, Any] | None = None
                error: str | None = None
                try:
                    result = await self._detect(
                        session, session.recipe, step, detector, frame["image"]
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # provider/network/parse failure
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    if session.current_step_id == step.id:
                        session.step_state.detections_started += 1
                latency_ms = int((time.perf_counter() - started) * 1000)
                images = result.pop("_images", None) if isinstance(result, dict) else None
                decision = result.pop("_decision", None) if isinstance(result, dict) else None
                if session.destroyed or generation != session.vision_generation:
                    return
                done = asyncio.Event()
                await session.queue.put(
                    SessionEvent(
                        kind="vision.result",
                        payload={
                            "generation": generation,
                            "step_id": step.id,
                            "detector": step.detector_key,
                            "result": result,
                            "error": error,
                            "latency_ms": latency_ms,
                            "images": images,
                            "decision": decision,
                            "image": frame["image"],
                            "_done": done,
                        },
                    )
                )
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(done.wait(), timeout=10.0)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("session=%s vision worker crashed", session.session_id)

    # ---- detection -----------------------------------------------------------

    async def _detect(
        self,
        session: ControlSession,
        recipe: Recipe,
        step: RecipeStep,
        detector: RecipeDetector,
        image: str,
    ) -> dict[str, Any]:
        if self._provider.mode == "mock":
            frame_number = session.mock_frame_counts.get(step.id, 0)
            session.mock_frame_counts[step.id] = frame_number + 1
            await asyncio.sleep(_mock_latency_seconds())
            return mock_detection(
                step,
                detector,
                step_index=session.step_index_by_id.get(step.id, 0),
                frame_number=frame_number,
            )
        include_reference = should_include_reference(vision_reference_mode(), session.step_state)
        try:
            result = await asyncio.wait_for(
                self._judge_frame(
                    recipe,
                    step,
                    detector,
                    image,
                    model=vision_model(),
                    decision=vision_decision(),
                    image_side=vision_image_max_side(),
                    include_reference=include_reference,
                    threshold=vision_done_threshold(),
                ),
                timeout=vision_timeout_s(),
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"vision model did not answer within {vision_timeout_s():.0f}s") from None
        if result.get(detector.field) is None:
            logger.warning(
                "session=%s vision: unusable reply for step=%s field=%s raw=%s",
                session.session_id, step.id, detector.field, str(result.get("_raw"))[:300],
            )
        result.pop("_raw", None)
        return result

    async def _judge_frame(
        self,
        recipe: Recipe,
        step: RecipeStep,
        detector: RecipeDetector,
        image: str,
        *,
        model: str,
        decision: str = "json",
        image_side: int | None = None,
        include_reference: bool = True,
        threshold: float = 0.9,
    ) -> dict[str, Any]:
        """One vision call for one frame (shared by the live coach and eval_detectors.py).

        Returns the detector result (`{field: value, "hint"?, "confidence"?}`) plus private
        keys `_images` (images sent), `_decision` (json|logprob actually used) and `_raw`.
        """
        if image_side:
            image = await asyncio.to_thread(downscale_data_url, image, image_side)
        reference = (
            self._reference_image_data_url(recipe.id, step.reference_image, max_side=image_side)
            if include_reference
            else None
        )
        use_logprob = (
            decision == "logprob"
            and detector.field in LOGPROB_FIELDS
            and not getattr(self, "_logprobs_unsupported", False)
        )
        if use_logprob:
            try:
                return await self._judge_logprob(
                    recipe, step, detector, image, reference, model=model, threshold=threshold
                )
            except LogprobsUnsupported as error:
                logger.warning("vision: gateway has no logprobs (%s); falling back to JSON mode", error)
                self._logprobs_unsupported = True
        content = self._build_vision_content(recipe, step, detector, image, reference=reference)
        raw = await self._provider.chat_json(
            model=model,
            system=VISION_SYSTEM_PROMPT,
            content=content,
            max_tokens=200,
            temperature=0.0,
        )
        result = normalize_vision_result(step, detector, raw)
        result["_images"] = sum(1 for part in content if part.get("type") == "image_url")
        result["_decision"] = "json"
        result["_raw"] = raw
        return result

    async def _judge_logprob(
        self,
        recipe: Recipe,
        step: RecipeStep,
        detector: RecipeDetector,
        image: str,
        reference: str | None,
        *,
        model: str,
        threshold: float,
    ) -> dict[str, Any]:
        if detector.field == "flag":
            options = None
            choices = ["yes", "no"]
            question = (
                _rewrite_flag_prompt(detector.prompt)
                + "\n\nJudge ONLY the CURRENT image. Answer yes if the rules above would return true, "
                "no if they would return false (when unsure: no). Reply with exactly one word: yes or no."
            )
        else:
            options = state_options(step, detector)
            choices = list(_LETTERS[: len(options)])
            listing = "\n".join(f"{letter}) {label}" for letter, (label, _s) in zip(choices, options))
            question = (
                _rewrite_state_prompt(detector.prompt, options)
                + "\n\nOptions:\n" + listing
                + "\n\nJudge ONLY the CURRENT image. Reply with exactly one capital letter ("
                + ", ".join(choices) + ")."
            )
        content = self._vision_context_parts(recipe, step, question, image, reference)
        probs = await self._provider.chat_choice_probs(
            model=model, system=LOGPROB_SYSTEM_PROMPT, content=content, choices=choices
        )
        result = logprob_result(step, detector, probs, threshold=threshold, options=options)
        result["_images"] = sum(1 for part in content if part.get("type") == "image_url")
        result["_decision"] = "logprob"
        result["_raw"] = probs
        return result

    async def _request_lazy_hint(
        self, session: ControlSession, step: RecipeStep, image: str | None
    ) -> bool:
        """logprob mode: ask for a hint with one short JSON call, in the background.

        The hint comes back to the session loop as a `vision.hint` event (spoken there,
        through the usual hint rate limits). Returns False if a request is already running.
        """
        if self._provider.mode == "mock" or not image or session.recipe is None:
            return False
        if session.hint_task is not None and not session.hint_task.done():
            return False
        recipe = session.recipe
        detector = recipe.detectors.get(step.detector_key)
        if detector is None:
            return False
        generation = session.vision_generation

        async def run() -> None:
            hint = ""
            try:
                side = vision_image_max_side()
                small = await asyncio.to_thread(downscale_data_url, image, side)
                reference = self._reference_image_data_url(recipe.id, step.reference_image, max_side=side)
                question = detector.prompt + "\n\n" + HINT_ONLY_INSTRUCTION
                content = self._vision_context_parts(recipe, step, question, small, reference)
                raw = await asyncio.wait_for(
                    self._provider.chat_json(
                        model=vision_model(),
                        system=VISION_SYSTEM_PROMPT,
                        content=content,
                        max_tokens=80,
                        temperature=0.0,
                    ),
                    timeout=vision_timeout_s(),
                )
                hint = normalize_vision_result(step, detector, raw).get("hint", "")
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — a missing hint is harmless
                logger.info("session=%s lazy hint failed: %s", session.session_id, f"{type(error).__name__}: {error}"[:200])
            if hint and not session.destroyed and generation == session.vision_generation:
                await session.queue.put(
                    SessionEvent(
                        kind="vision.hint",
                        payload={"generation": generation, "step_id": step.id, "hint": hint},
                    )
                )

        session.hint_task = asyncio.create_task(run(), name=f"vision-hint-{session.session_id}")
        return True

    def _build_vision_content(
        self,
        recipe: Recipe,
        step: RecipeStep,
        detector: RecipeDetector,
        image: str,
        *,
        reference: str | None | bool = True,
    ) -> list[dict[str, Any]]:
        """JSON-mode prompt. `reference`: a data URL, None/False = no reference image,
        True = load the step's reference image (legacy default)."""
        field = detector.field
        field_type = {
            "flag": "boolean (true/false)",
            "level": "số nguyên 0-10 hoặc \"unknown\"",
            "ingredients": "mảng chuỗi",
        }.get(field, "chuỗi")
        context_lines = [f"Kỹ năng: {recipe.display_name}"]
        if step.instruction:
            context_lines.append(f"Bước hiện tại (hướng dẫn cho người học): {step.instruction}")
        if step.warning:
            context_lines.append(f"Cảnh báo của người dạy: {step.warning}")
        example = {"flag": "false", "level": "3", "ingredients": "[]"}.get(field, '"<one of the allowed values>"')
        text = (
            "\n".join(context_lines)
            + "\n\nCâu hỏi của bộ nhận diện:\n"
            + detector.prompt
            + f"\n\nAnswer with ONLY one JSON object, e.g. {{\"{field}\": {example}}}. "
            f"Field `{field}` must be {field_type}. "
            "Optional `hint`: one short Vietnamese sentence (max 15 words) telling the learner what to fix. "
            "Add `hint` ONLY if the objects of this step are clearly visible AND visibly set up wrong "
            "(wrong position, orientation or part). Do NOT add `hint` if those objects are not in the frame, "
            "the image is unclear, or the learner simply has not done the step yet."
        )
        if reference is True:
            reference = self._reference_image_data_url(recipe.id, step.reference_image)
        return self._image_parts(step, text, image, reference or None)

    def _vision_context_parts(
        self,
        recipe: Recipe,
        step: RecipeStep,
        question: str,
        image: str,
        reference: str | None,
    ) -> list[dict[str, Any]]:
        lines = [f"Kỹ năng: {recipe.display_name}"]
        if step.instruction:
            lines.append(f"Bước hiện tại (hướng dẫn cho người học): {step.instruction}")
        if step.warning:
            lines.append(f"Cảnh báo của người dạy: {step.warning}")
        text = "\n".join(lines) + "\n\nCâu hỏi của bộ nhận diện:\n" + question
        return self._image_parts(step, text, image, reference)

    @staticmethod
    def _image_parts(
        step: RecipeStep, text: str, image: str, reference: str | None
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {"type": "text", "text": text},
            {"type": "text", "text": "ẢNH HIỆN TẠI từ camera của người học (ảnh cần chấm):"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        if reference:
            label = REFERENCE_IMAGE_LABEL
            if step.instruction:
                label += f" (bước: {step.instruction})"
            content.append(
                {"type": "text", "text": label + " — chỉ để so sánh, KHÔNG phải ảnh cần chấm:"}
            )
            content.append({"type": "image_url", "image_url": {"url": reference}})
        return content

    def _reference_image_data_url(
        self, skill_id: str, rel: str | None, *, max_side: int | None = None
    ) -> str | None:
        path = self._skills.media_path(skill_id, rel)
        if path is None:
            return None
        data_url = self._load_image_data_url(path)
        if data_url is None or not max_side:
            return data_url
        version = self._ref_image_cache.get(str(path), (0.0, ""))[0]  # source mtime
        key = f"{path}@{max_side}"
        cached = self._ref_image_cache.get(key)
        if cached and cached[0] == version:
            return cached[1]
        small = downscale_data_url(data_url, max_side)
        self._ref_image_cache[key] = (version, small)
        return small

    def _load_image_data_url(self, path: Path) -> str | None:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        key = str(path)
        cached = self._ref_image_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return None
        data_url = f"data:{mime};base64,{encoded}"
        if len(self._ref_image_cache) > 64:  # entries are whole images (≤2.5 MB each)
            self._ref_image_cache.clear()
        self._ref_image_cache[key] = (mtime, data_url)
        return data_url

    # ---- "Hỏi SKI" --------------------------------------------------------

    async def answer_question(
        self,
        *,
        skill_id: str,
        step_id: str | None,
        question: str,
        image: str | None,
    ) -> str:
        answer, _source = await self.answer_question_with_source(
            skill_id=skill_id, step_id=step_id, question=question, image=image
        )
        return answer

    async def answer_question_with_source(
        self,
        *,
        skill_id: str,
        step_id: str | None,
        question: str,
        image: str | None,
    ) -> tuple[str, str]:
        """Returns (answer, source) where source is "ai", "mock" or "fallback" (AI failed)."""
        recipe = self._skills.get(skill_id)  # raises SkillNotFound
        step = next((s for s in recipe.steps if s.id == step_id), None)
        if self._provider.mode == "mock":
            return mock_answer(step, question), "mock"

        lines = [f"Kỹ năng: {recipe.display_name}"]
        if recipe.learned_from:
            lines.append(f"Người dạy: {recipe.learned_from}")
        if step is not None:
            index = recipe.steps.index(step)
            lines.append(f"Bước {index + 1}/{len(recipe.steps)}: {step.instruction or step.on_enter_speech or step.id}")
            if step.tip:
                lines.append(f"Mẹo của người dạy: {step.tip}")
            if step.warning:
                lines.append(f"Cảnh báo: {step.warning}")
        # Whole-skill outline: learners often ask about an earlier/later step ("mặt nào úp xuống?").
        outline = ["", "Toàn bộ các bước người dạy đã làm mẫu:"]
        for number, item in enumerate(recipe.steps, start=1):
            entry = f"{number}. {item.instruction or item.on_enter_speech or item.id}"
            extras = [
                text for text in (
                    item.on_enter_speech if item.on_enter_speech and item.on_enter_speech != item.instruction else None,
                    f"mẹo: {item.tip}" if item.tip else None,
                    f"lưu ý: {item.warning}" if item.warning else None,
                ) if text
            ]
            if extras:
                entry += " (" + "; ".join(extras) + ")"
            if item is step:
                entry += "  ← người học đang ở bước này"
            outline.append(entry)
        lines.extend(outline)
        lines.append(f"\nCâu hỏi của người học: {question}")
        content: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
        if isinstance(image, str) and image.startswith("data:image/") and len(image) <= MAX_FRAME_DATA_URL_CHARS:
            content.append({"type": "text", "text": "Ảnh hiện tại từ camera của người học:"})
            content.append({"type": "image_url", "image_url": {"url": image}})
        if step is not None:
            reference = self._reference_image_data_url(recipe.id, step.reference_image)
            if reference:
                content.append({"type": "text", "text": REFERENCE_IMAGE_LABEL + ":"})
                content.append({"type": "image_url", "image_url": {"url": reference}})
        try:
            data = await asyncio.wait_for(
                self._provider.chat_json(
                    model=vision_model(),
                    system=ASK_SYSTEM_PROMPT,
                    content=content,
                    max_tokens=300,
                    temperature=0.3,
                ),
                timeout=ask_timeout_s(),
            )
        except Exception as error:  # network / timeout / unusable JSON
            logger.warning("ask: provider failed (%s); using fallback answer", f"{type(error).__name__}: {error}"[:300])
            return mock_answer(step, question), "fallback"
        answer = data.get("answer") if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            logger.warning("ask: unusable reply %s; using fallback answer", str(data)[:300])
            return mock_answer(step, question), "fallback"
        return _limit_sentences(answer, 2), "ai"

"""Per-session state machine.

Forked from GlassKit `session_workflow.py` (MIT). SHADOW changes:
- removed inventory scan, recipe selection and OpenAI Realtime tool calls;
- `session.start` carries `skill_id` and goes straight to GUIDING at `start_step_id`;
- vision results arrive as internal `vision.result` events from vision_runtime.py
  instead of Overshoot WebSocket results (no prompt switching / patching);
- speech carries a `kind`, and dynamic vision `hint`s / teacher tips can be spoken;
- user-facing errors are Vietnamese; hud.state includes `skill_id` and a `step` object.
The step evaluators (`_evaluate_*`) are kept as in GlassKit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from providers import vision_fast_confirm
from recipe_catalog import RecipeStep
from session_constants import (
    COMPLETE_CONFIRM_FRAMES,
    DEFAULT_COMPLETION_SPEECH,
    HINT_EVALUATION_MODES,
    HINT_MIN_INTERVAL_SECONDS,
    HINT_SIMILARITY_THRESHOLD,
    HINT_STUCK_FRAMES,
    MAX_HINTS_PER_STEP,
    PHASE_COMPLETED,
    PHASE_ERROR,
    PHASE_GUIDING,
    PHASE_WAITING,
    TIP_AFTER_FRAMES,
    VISION_ERROR_MESSAGE,
    VISION_ERROR_NOTIFY_AFTER,
    VISION_ERROR_RENOTIFY_EVERY,
)
from session_helpers import (
    completed_task_ids_for_session,
    extract_result_value,
    matches_condition,
    parse_structured_result,
)
from session_types import ControlSession, SessionEvent, StepRuntimeState
from skill_store import SkillNotFound, SkillValidationError, media_url

logger = logging.getLogger("uvicorn.error")


def _hint_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if t not in {"bạn", "nhé", "nha", "ạ", "nào"}}


def hints_similar(a: str, b: str, threshold: float = HINT_SIMILARITY_THRESHOLD) -> bool:
    """True when two Vietnamese hints say essentially the same thing.

    Token containment (shared words / words of the shorter hint) catches re-phrasings such as
    "Đặt chân đế nằm phẳng trên bàn" vs "Đặt chân đế phẳng xuống bàn, mặt rộng hướng xuống".
    """
    if not a or not b:
        return False
    ta, tb = _hint_tokens(a), _hint_tokens(b)
    if ta and tb and len(ta & tb) / min(len(ta), len(tb)) >= threshold:
        return True
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.7


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except TypeError:
        return repr(value)


class SessionWorkflowMixin:
    if TYPE_CHECKING:
        _skills: Any

        async def _speak_line(
            self, session: ControlSession, text: str, kind: str = "enter"
        ) -> None: ...
        async def _send_control(
            self, session: ControlSession, payload: dict[str, Any]
        ) -> None: ...
        async def _stop_vision_runtime(self, session: ControlSession) -> None: ...
        async def _request_lazy_hint(
            self, session: ControlSession, step: RecipeStep, image: str | None
        ) -> bool: ...

    async def _run_session_loop(self, session: ControlSession) -> None:
        while True:
            event = await session.queue.get()
            if event.kind == "session.destroy":
                await self._reset_runtime_state(session)
                return
            try:
                await self._handle_event(session, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "session=%s event=%s failed", session.session_id, event.kind
                )
                await self._fail_session(
                    session, "Có lỗi xảy ra. Vui lòng bắt đầu lại."
                )
            finally:
                done = event.payload.get("_done")
                if isinstance(done, asyncio.Event):
                    done.set()

    async def _handle_event(self, session: ControlSession, event: SessionEvent) -> None:
        if event.kind == "session.start":
            await self._handle_start(session, event.payload)
            return
        if event.kind == "session.stop":
            await self._reset_runtime_state(session)
            await self._publish_hud_state(session)
            return
        if event.kind == "debug.step":
            await self._handle_debug_step(session, event.payload)
            return
        if event.kind == "vision.result":
            await self._handle_vision_result(session, event.payload)
            return
        if event.kind == "vision.hint":
            await self._handle_lazy_hint(session, event.payload)
            return
        logger.info(
            "session=%s ignoring unknown control message type=%s",
            session.session_id,
            event.kind,
        )

    async def _handle_start(
        self, session: ControlSession, payload: dict[str, Any]
    ) -> None:
        skill_id = str(payload.get("skill_id") or "").strip()
        # SHADOW: allow (re)starting from any phase, e.g. switching skills.
        await self._reset_runtime_state(session)
        if not skill_id:
            await self._fail_session(session, "Chưa chọn kỹ năng. Hãy chọn một kỹ năng.")
            return
        try:
            recipe = self._skills.get(skill_id)
        except SkillNotFound:
            await self._fail_session(
                session, "Không tìm thấy kỹ năng này. Hãy chọn lại từ danh sách."
            )
            return
        except SkillValidationError:
            logger.exception("session=%s invalid skill=%s", session.session_id, skill_id)
            await self._fail_session(
                session, "Dữ liệu kỹ năng bị lỗi. Hãy sửa lại hoặc dạy lại kỹ năng."
            )
            return

        session.skill_id = recipe.id
        session.recipe = recipe
        session.step_lookup = {step.id: step for step in recipe.steps}
        session.step_index_by_id = {
            step.id: index for index, step in enumerate(recipe.steps)
        }
        session.current_step_id = recipe.start_step_id
        session.phase = PHASE_GUIDING
        logger.info("session=%s started skill=%s", session.session_id, recipe.id)
        await self._enter_step(session, speak_on_enter=True)

    async def _handle_debug_step(
        self,
        session: ControlSession,
        payload: dict[str, Any],
    ) -> None:
        direction = str(payload.get("direction") or "").strip().lower()
        if direction not in {"forward", "backward"}:
            return
        if (
            session.phase not in {PHASE_GUIDING, PHASE_COMPLETED}
            or session.recipe is None
        ):
            return
        if session.current_step_id is None:
            return
        current_index = session.step_index_by_id.get(session.current_step_id)
        if current_index is None:
            return
        delta = 1 if direction == "forward" else -1
        if (
            direction == "forward"
            and session.phase == PHASE_GUIDING
            and current_index == len(session.recipe.steps) - 1
        ):
            # SHADOW: ▶ on the last step finishes the skill (demo rescue when the camera
            # cannot confirm the final state).
            step = session.recipe.steps[current_index]
            await self._advance_step(
                session,
                speech=step.complete_speech or step.success_speech,
                next_step_id=None,
            )
            return
        next_index = max(0, min(len(session.recipe.steps) - 1, current_index + delta))
        if next_index == current_index:
            return
        session.phase = PHASE_GUIDING
        session.current_step_id = session.recipe.steps[next_index].id
        await self._enter_step(session, speak_on_enter=True)

    async def _handle_vision_result(
        self,
        session: ControlSession,
        payload: dict[str, Any],
    ) -> None:
        if payload.get("generation") != session.vision_generation:
            return
        detector_key = payload.get("detector")
        latency_ms = payload.get("latency_ms")
        error = payload.get("error")
        if error:
            session.vision_error_streak += 1
            logger.warning(
                "session=%s vision error detector=%s error=%s",
                session.session_id,
                detector_key,
                error,
            )
            await self._send_control(
                session,
                {
                    "type": "vision.result",
                    "detector": detector_key,
                    "result": None,
                    "error": str(error),
                    "latency_ms": latency_ms,
                },
            )
            streak = session.vision_error_streak
            if streak == VISION_ERROR_NOTIFY_AFTER or (
                streak > VISION_ERROR_NOTIFY_AFTER
                and (streak - VISION_ERROR_NOTIFY_AFTER) % VISION_ERROR_RENOTIFY_EVERY == 0
            ):
                await self._send_control(
                    session,
                    {"type": "hud.error", "code": "vision_unavailable", "message": VISION_ERROR_MESSAGE},
                )
            return
        session.vision_error_streak = 0

        parsed = parse_structured_result(payload.get("result"))
        step_id = payload.get("step_id")
        stale = session.phase != PHASE_GUIDING or step_id != session.current_step_id
        logger.info(
            "session=%s vision result detector=%s step=%s stale=%s latency_ms=%s images=%s result=%s",
            session.session_id,
            detector_key,
            step_id,
            stale,
            latency_ms,
            payload.get("images"),
            _compact_json(parsed),
        )
        if parsed is not None and not stale and session.recipe is not None:
            await self._evaluate_current_step(
                session,
                parsed,
                lazy_hints=payload.get("decision") == "logprob",
                image=payload.get("image"),
            )
        # Fast confirmation: a 'done' frame still needs COMPLETE_CONFIRM_FRAMES-1 more. Tell the
        # phone to send a fresh frame now and let the worker judge it without the usual gap.
        confirming = (
            vision_fast_confirm()
            and not stale
            and session.phase == PHASE_GUIDING
            and session.current_step_id == step_id
            and session.step_state.complete_hits > 0
        )
        session.fast_confirm = confirming
        await self._send_control(
            session,
            {
                "type": "vision.result",
                "detector": detector_key,
                "result": parsed,
                "latency_ms": latency_ms,
                "step_id": step_id,
                "stale": stale,
                "confirming": confirming,
            },
        )

    async def _handle_lazy_hint(
        self, session: ControlSession, payload: dict[str, Any]
    ) -> None:
        """A hint produced by the background JSON call (VISION_DECISION=logprob)."""
        if payload.get("generation") != session.vision_generation:
            return
        if session.phase != PHASE_GUIDING or payload.get("step_id") != session.current_step_id:
            return
        step = session.step_lookup.get(session.current_step_id or "")
        hint = payload.get("hint")
        if step is None or not isinstance(hint, str) or session.step_state.complete_hits > 0:
            return
        if await self._maybe_speak_hint(session, step, hint):
            session.step_state.stuck_frames = 0

    async def _enter_step(
        self, session: ControlSession, *, speak_on_enter: bool
    ) -> None:
        if session.recipe is None or session.current_step_id is None:
            return
        step = session.step_lookup.get(session.current_step_id)
        if step is None:
            await self._fail_session(session, "Thiếu bước trong kỹ năng. Hãy bắt đầu lại.")
            return
        detector = session.recipe.detectors.get(step.detector_key)
        if detector is None:
            await self._fail_session(
                session, "Thiếu bộ nhận diện cho bước này. Hãy bắt đầu lại."
            )
            return

        session.step_state = StepRuntimeState()
        session.fast_confirm = False
        await self._publish_hud_state(session)
        if speak_on_enter:
            line = step.on_enter_speech or step.instruction
            if line:
                await self._speak_line(session, line, kind="enter")

    async def _evaluate_current_step(
        self,
        session: ControlSession,
        result: dict[str, Any],
        *,
        lazy_hints: bool = False,
        image: str | None = None,
    ) -> None:
        if session.recipe is None or session.current_step_id is None:
            return
        step = session.step_lookup.get(session.current_step_id)
        if step is None:
            return
        detector = session.recipe.detectors.get(step.detector_key)
        if detector is None:
            return
        session.step_state.last_result_hint = False

        value = extract_result_value(result, step.value_path or detector.field)
        if value is None:
            # Unusable / missing answer from the model: never let it count as a mismatch.
            session.step_state.frames_evaluated += 1
            return
        ignored_values = step.ignored_values_list()
        step_id_before = session.current_step_id
        spoken_epoch_before = session.speech_epoch

        if step.evaluation_mode == "match_value":
            await self._evaluate_match_value(session, step, value, ignored_values)
        elif step.evaluation_mode == "numeric_threshold_with_progress_once":
            await self._evaluate_numeric_threshold(session, step, value, ignored_values)
        elif step.evaluation_mode == "count_rising_edges_true":
            await self._evaluate_rising_edges(session, step, value)
        elif step.evaluation_mode == "enum_progress_once_then_complete":
            await self._evaluate_enum_progress(session, step, value, ignored_values)
        elif step.evaluation_mode == "momentary_true_complete":
            await self._evaluate_momentary_true(session, step, value)

        if session.phase == PHASE_GUIDING and session.current_step_id == step_id_before:
            await self._after_incomplete_result(
                session,
                step,
                value,
                ignored_values,
                result,
                already_spoke=session.speech_epoch != spoken_epoch_before,
                lazy_hints=lazy_hints,
                image=image,
            )

    async def _after_incomplete_result(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
        ignored_values: list[Any],
        result: dict[str, Any],
        *,
        already_spoke: bool,
        lazy_hints: bool = False,
        image: str | None = None,
    ) -> None:
        """SHADOW: speak the model's dynamic `hint`, or the teacher's tip when stuck."""
        state = session.step_state
        state.frames_evaluated += 1
        confirming = state.complete_hits > 0  # 'done', waiting for the confirmation frame
        hint = result.get("hint")
        hint = hint.strip() if isinstance(hint, str) else ""
        state.last_result_hint = bool(hint) and not confirming
        if confirming:
            state.stuck_frames = 0
            return
        if state.tip_spoken or (not step.tip and state.frames_evaluated >= TIP_AFTER_FRAMES):
            state.stuck_frames += 1
        if already_spoke:
            return

        hint_ok = (
            step.evaluation_mode in HINT_EVALUATION_MODES
            and step.allow_dynamic_hint
            and not step.mismatch_speech
            and value not in ignored_values
        )
        if hint and hint_ok and await self._maybe_speak_hint(session, step, hint):
            return

        if step.tip and not state.tip_spoken and state.frames_evaluated >= TIP_AFTER_FRAMES:
            state.tip_spoken = True
            await self._speak_line(session, f"Mẹo nhỏ: {step.tip}", kind="tip")
            return

        # VISION_DECISION=logprob has no per-frame hint: ask for one only when the learner is stuck.
        if (
            lazy_hints
            and hint_ok
            and state.stuck_frames >= HINT_STUCK_FRAMES
            and self._hint_allowed(session)
            and time.monotonic() - state.hint_requested_at >= HINT_MIN_INTERVAL_SECONDS
        ):
            if await self._request_lazy_hint(session, step, image):
                state.hint_requested_at = time.monotonic()

    def _hint_allowed(self, session: ControlSession, hint: str | None = None) -> bool:
        """Hint rate limits: per-step cap, growing per-step gap, global gap, no repeats."""
        state = session.step_state
        spoken = state.hints_spoken
        now = time.monotonic()
        step_gap = HINT_MIN_INTERVAL_SECONDS * (2 ** len(spoken)) if spoken else 0.0
        return (
            len(spoken) < MAX_HINTS_PER_STEP
            and not (hint and any(hints_similar(hint, previous) for previous in spoken))
            and now - session.last_hint_at >= HINT_MIN_INTERVAL_SECONDS
            and now - state.last_step_hint_at >= step_gap
        )

    async def _maybe_speak_hint(
        self, session: ControlSession, step: RecipeStep, hint: str
    ) -> bool:
        hint = (hint or "").strip()
        if not hint or not self._hint_allowed(session, hint):
            return False
        state = session.step_state
        now = time.monotonic()
        state.hints_spoken.append(hint)
        state.last_hint_text = hint
        state.last_step_hint_at = now
        session.last_hint_text = hint
        session.last_hint_at = now
        await self._speak_line(session, hint, kind="hint")
        return True

    # ---- GlassKit evaluators (logic unchanged; speech kinds added) -----------

    async def _evaluate_match_value(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
        ignored_values: list[Any],
    ) -> None:
        if value in ignored_values:
            session.step_state.last_observed_value = value
            return
        if value == step.expected_value:
            if self._confirm_complete(session):
                await self._advance_step(
                    session, speech=step.success_speech, next_step_id=step.next_step_id
                )
            return
        session.step_state.complete_hits = 0
        if step.mismatch_speech:
            should_speak = not step.speak_on_observation_change_only
            if step.speak_on_observation_change_only:
                should_speak = value != session.step_state.last_spoken_observation
            if should_speak:
                session.step_state.last_spoken_observation = value
                await self._speak_line(session, step.mismatch_speech, kind="mismatch")
        session.step_state.last_observed_value = value

    async def _evaluate_numeric_threshold(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
        ignored_values: list[Any],
    ) -> None:
        if value in ignored_values or not isinstance(value, (int, float)):
            return
        if step.complete_condition and matches_condition(
            step.complete_condition, value
        ):
            if self._confirm_complete(session):
                await self._advance_step(
                    session, speech=step.complete_speech, next_step_id=step.next_step_id
                )
            return
        session.step_state.complete_hits = 0
        if (
            step.progress_condition
            and matches_condition(step.progress_condition, value)
            and "progress_once" not in session.step_state.progress_flags
        ):
            session.step_state.progress_flags.add("progress_once")
            if step.progress_once_speech:
                await self._speak_line(session, step.progress_once_speech, kind="progress")

    async def _evaluate_rising_edges(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
    ) -> None:
        observed = bool(value)
        if observed and not session.step_state.previous_boolean_value:
            session.step_state.counter += 1
            milestone = next(
                (
                    item
                    for item in step.milestones
                    if item.count == session.step_state.counter
                ),
                None,
            )
            if milestone is not None:
                if milestone.next_step_id is not None:
                    await self._advance_step(
                        session,
                        speech=milestone.speech,
                        next_step_id=milestone.next_step_id,
                    )
                    session.step_state.previous_boolean_value = observed
                    return
                if milestone.speech:
                    await self._speak_line(session, milestone.speech, kind="progress")
            elif (
                step.target_count is not None
                and session.step_state.counter >= step.target_count
            ):
                await self._advance_step(
                    session, speech=step.complete_speech, next_step_id=step.next_step_id
                )
                session.step_state.previous_boolean_value = observed
                return
        session.step_state.previous_boolean_value = observed

    async def _evaluate_enum_progress(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
        ignored_values: list[Any],
    ) -> None:
        if value in ignored_values:
            return
        if value == step.complete_value:
            if self._confirm_complete(session):
                await self._advance_step(
                    session, speech=step.complete_speech, next_step_id=step.next_step_id
                )
            return
        session.step_state.complete_hits = 0
        if (
            value == step.progress_value
            and "progress_once" not in session.step_state.progress_flags
        ):
            session.step_state.progress_flags.add("progress_once")
            if step.progress_once_speech:
                await self._speak_line(session, step.progress_once_speech, kind="progress")

    async def _evaluate_momentary_true(
        self,
        session: ControlSession,
        step: RecipeStep,
        value: Any,
    ) -> None:
        if bool(value) is bool(step.complete_on):
            if self._confirm_complete(session):
                await self._advance_step(
                    session, speech=step.complete_speech, next_step_id=step.next_step_id
                )
            return
        session.step_state.complete_hits = 0

    def _confirm_complete(self, session: ControlSession) -> bool:
        """Count consecutive 'done' frames; True once COMPLETE_CONFIRM_FRAMES is reached."""
        session.step_state.complete_hits += 1
        return session.step_state.complete_hits >= COMPLETE_CONFIRM_FRAMES

    async def _advance_step(
        self,
        session: ControlSession,
        *,
        speech: str | None,
        next_step_id: str | None,
    ) -> None:
        if next_step_id is None:
            session.phase = PHASE_COMPLETED
            session.current_step_id = None
            session.pending_frame = None
            await self._publish_hud_state(session)
            await self._speak_line(
                session, speech or DEFAULT_COMPLETION_SPEECH, kind="complete"
            )
            return

        session.phase = PHASE_GUIDING
        session.current_step_id = next_step_id
        await self._enter_step(session, speak_on_enter=speech is None)
        if speech:
            await self._speak_line(session, speech, kind="success")

    async def _fail_session(self, session: ControlSession, message: str) -> None:
        await self._reset_runtime_state(session)
        session.phase = PHASE_ERROR
        await self._send_control(
            session,
            {
                "type": "hud.error",
                "message": message,
            },
        )
        await self._publish_hud_state(session)

    async def _reset_runtime_state(self, session: ControlSession) -> None:
        await self._stop_vision_runtime(session)
        session.phase = PHASE_WAITING
        session.skill_id = None
        session.recipe = None
        session.step_lookup.clear()
        session.step_index_by_id.clear()
        session.current_step_id = None
        session.step_state = StepRuntimeState()
        session.mock_frame_counts.clear()
        session.vision_error_streak = 0
        # SHADOW: speech_epoch stays monotonic per connection so the client can always
        # treat a higher epoch as "newer" (GlassKit reset it to 0 here).
        session.current_speech_text = None
        session.last_hint_text = None
        session.last_hint_at = 0.0

    def _step_payload(self, session: ControlSession) -> dict[str, Any] | None:
        if session.recipe is None or session.current_step_id is None:
            return None
        step = session.step_lookup.get(session.current_step_id)
        if step is None:
            return None
        return {
            "id": step.id,
            "index": session.step_index_by_id.get(step.id, 0),
            "total": len(session.recipe.steps),
            "instruction": step.instruction or step.on_enter_speech,
            "tip": step.tip,
            "warning": step.warning,
            "reference_image_url": media_url(session.recipe.id, step.reference_image),
        }

    async def _publish_hud_state(self, session: ControlSession) -> None:
        recipe_name = session.recipe.display_name if session.recipe else None
        active_task_id = None
        tasks_payload: list[dict[str, Any]] = []
        completed_task_ids = completed_task_ids_for_session(session)

        if session.recipe is not None and session.current_step_id is not None:
            step = session.step_lookup.get(session.current_step_id)
            if step is not None:
                active_task_id = step.task_id

        if session.recipe is not None:
            for task in session.recipe.tasks:
                tasks_payload.append(
                    {
                        "id": task.id,
                        "text": task.text,
                        "completed": task.id in completed_task_ids,
                    }
                )

        screen = "start" if session.phase == PHASE_WAITING else "running"
        await self._send_control(
            session,
            {
                "type": "hud.state",
                "screen": screen,
                "phase": session.phase,
                "recipe_name": recipe_name,
                "skill_id": session.skill_id,
                "tasks": tasks_payload,
                "active_task_id": active_task_id,
                "step": self._step_payload(session),
                "speech_epoch": session.speech_epoch,
            },
        )

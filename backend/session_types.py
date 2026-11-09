from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from recipe_catalog import Recipe, RecipeStep
from session_constants import PHASE_WAITING


@dataclass
class StepRuntimeState:
    last_observed_value: Any = None
    last_spoken_observation: Any = None
    progress_flags: set[str] = field(default_factory=set)
    counter: int = 0
    previous_boolean_value: bool = False
    # SHADOW additions
    frames_evaluated: int = 0
    last_hint_text: str | None = None
    tip_spoken: bool = False
    hints_spoken: list[str] = field(default_factory=list)
    last_step_hint_at: float = 0.0
    complete_hits: int = 0  # consecutive frames judged 'done'
    # Guide speed/accuracy (vision_runtime): reference-image policy + lazy hints (logprob mode)
    detections_started: int = 0  # vision calls made for this step
    last_result_hint: bool = False  # previous result was 'not done' with a hint
    stuck_frames: int = 0  # consecutive not-done frames after the tip phase
    hint_requested_at: float = 0.0


@dataclass
class SessionEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ControlSession:
    session_id: str
    control_ws: WebSocket
    queue: asyncio.Queue[SessionEvent] = field(default_factory=asyncio.Queue)
    loop_task: asyncio.Task[None] | None = None
    phase: str = PHASE_WAITING
    speech_epoch: int = 0
    destroyed: bool = False
    # Vision pipeline (SHADOW: frames over the control WS instead of Overshoot WebRTC)
    vision_generation: int = 0
    pending_frame: dict[str, Any] | None = None
    vision_task: asyncio.Task[None] | None = None
    last_vision_started_at: float = 0.0
    vision_error_streak: int = 0
    mock_frame_counts: dict[str, int] = field(default_factory=dict)
    # Set when a 'done' frame awaits confirmation: the next frame skips VISION_MIN_INTERVAL_MS.
    fast_confirm: bool = False
    hint_task: asyncio.Task[None] | None = None
    # Skill / step state
    skill_id: str | None = None
    recipe: Recipe | None = None
    step_lookup: dict[str, RecipeStep] = field(default_factory=dict)
    step_index_by_id: dict[str, int] = field(default_factory=dict)
    current_step_id: str | None = None
    step_state: StepRuntimeState = field(default_factory=StepRuntimeState)
    current_speech_text: str | None = None
    last_hint_at: float = 0.0
    last_hint_text: str | None = None

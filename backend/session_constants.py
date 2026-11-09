from __future__ import annotations

import os

from typing import Literal

# SHADOW: Overshoot / OpenAI Realtime constants, inventory-scan prompt and output schema
# from GlassKit were removed (frames now arrive over the control WebSocket and speech is
# sent to the phone as text for browser TTS).

PHASE_WAITING = "WAITING_FOR_START"
PHASE_GUIDING = "GUIDING"
PHASE_COMPLETED = "COMPLETED"
PHASE_ERROR = "ERROR"

SpeechKind = Literal["enter", "success", "mismatch", "hint", "progress", "complete", "tip"]
EvaluationMode = Literal[
    "match_value",
    "numeric_threshold_with_progress_once",
    "count_rising_edges_true",
    "enum_progress_once_then_complete",
    "momentary_true_complete",
]

# Evaluation modes where a vision `hint` may be spoken when the result is wrong.
HINT_EVALUATION_MODES = frozenset(
    {"match_value", "momentary_true_complete", "enum_progress_once_then_complete"}
)
HINT_MIN_INTERVAL_SECONDS = 6.0
# Real VLMs re-phrase the same correction on almost every frame. Per step, at most this many
# hints are spoken, each new one must say something different from the previous ones, and the
# required gap doubles after every hint (6 s, 12 s, 24 s …).
MAX_HINTS_PER_STEP = 3
HINT_SIMILARITY_THRESHOLD = 0.5
# Speak the teacher's tip once after this many non-completing vision results on a step.
TIP_AFTER_FRAMES = 5
# VISION_DECISION=logprob gives no per-frame hint: after the tip phase, this many consecutive
# 'not done' frames trigger one short JSON call that only writes a hint (rate limited as above).
HINT_STUCK_FRAMES = 3
# Consecutive vision failures before the user is told (session keeps running).
VISION_ERROR_NOTIFY_AFTER = 3
# …and reminded again every this many further failures while the outage lasts.
VISION_ERROR_RENOTIFY_EVERY = 10
VISION_ERROR_MESSAGE = (
    "Không kết nối được AI nên chưa chấm được hình. Đang thử lại… "
    "Bạn vẫn có thể chuyển bước bằng nút ◀ ▶ (nhấn giữ khung hướng dẫn)."
)
# Reject frames larger than this (data URL length).
MAX_FRAME_DATA_URL_CHARS = 4_000_000

DEFAULT_COMPLETION_SPEECH = "Hoàn thành rồi! Bạn làm tốt lắm."


def _confirm_frames() -> int:
    try:
        return max(1, int(os.getenv("COMPLETE_CONFIRM_FRAMES", "2")))
    except ValueError:
        return 2


# A step only completes after this many CONSECUTIVE frames are judged "done".
# One false positive from the vision model would otherwise skip a step (seen in AI E2E).
COMPLETE_CONFIRM_FRAMES = _confirm_frames()

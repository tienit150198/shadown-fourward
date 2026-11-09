"""SHADOW Teach mode: transcript + keyframes -> Skill JSON (GlassKit Recipe + SHADOW fields).

Pipeline
--------
1. Decode/validate the teacher's keyframes and save them as a draft:
   ``skills_root/_drafts/<draft_id>/frames/fNN.jpg``.
2. Build a *compact intermediate* skill:
   - AI mode: one ``provider.chat_json`` call (vision LLM sees transcript + frames), one retry.
   - Mock mode / fallback: deterministic sentence splitting of the transcript (or notes).
3. Deterministically repair the intermediate (clamp frames, default speech, ...) and convert it
   into a full Recipe dict, validated with ``recipe_catalog.Recipe``.

Speech semantics (matches session_workflow.py, forked from GlassKit):
when a step completes, the engine speaks that step's transition line (``complete_speech`` for
``momentary_true_complete``, ``success_speech`` for ``match_value``) and does NOT speak the next
step's ``on_enter_speech``. So the transition line of step k = praise + step k+1's enter line;
the last step's transition line is the final "done" message. ``on_enter_speech`` is still set on
every step (spoken for the first step and after manual/debug jumps).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import shutil
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from providers import compile_timeout_s
from recipe_catalog import Recipe

logger = logging.getLogger(__name__)

MAX_FRAMES = 24
MAX_FRAME_BYTES = 2_500_000
MAX_STEPS = 8
MAX_SLUG_LEN = 40
COMPILE_MAX_TOKENS = 4000
# Drafts that were never saved (teacher cancelled / left Review) are pruned after this long.
DRAFT_MAX_AGE_SECONDS = 6 * 3600

TIP_RE = re.compile(r"\b(nhớ|lưu ý|mẹo|bí quyết)\b", re.IGNORECASE)
WARNING_RE = re.compile(r"\b(đừng|cẩn thận|không được|tránh|coi chừng)\b", re.IGNORECASE)
FILLER_RE = re.compile(
    r"^(?:(?:rồi|thì|ờ+|à+|ừ+|ok(?:ay)?|vâng|nào|bây giờ|giờ|sau đó|tiếp theo|"
    r"tiếp|đầu tiên|trước tiên|trước hết|cuối cùng|sau cùng|kế tiếp|kế đến|xong|và|sẽ|mình|chúng ta|ta|các bạn|bạn)[\s,]+)+",
    re.IGNORECASE,
)
INTRO_RE = re.compile(
    r"^(xin chào|chào|hello|hi\b)|hôm nay (mình|tôi|em|anh|chị|chúng ta|ta)|"
    r"(mình|tôi|em) (sẽ )?(hướng dẫn|chỉ|dạy)|cảm ơn|vậy là xong|thế là xong",
    re.IGNORECASE,
)
PRAISES = ["Tốt lắm!", "Chuẩn rồi!", "Đúng rồi!", "Rất tốt!", "Tuyệt!", "Ngon rồi!"]
GENERIC_STEPS = [
    "Chuẩn bị đầy đủ dụng cụ trên bàn",
    "Thực hiện thao tác chính",
    "Kiểm tra lại kết quả",
]


# --------------------------------------------------------------------------- request model


# Request caps: up to 64 raw frames (~512 px JPEG; the server keeps the best 24, see
# teach_keyframes.py) and a few minutes of speech.
MAX_REQUEST_FRAMES = 64
MAX_TRANSCRIPT_LINES = 600


class TranscriptLine(BaseModel):
    t0: float = 0
    t1: float = 0
    text: str = Field("", max_length=2000)


class FrameIn(BaseModel):
    t: float = 0
    image: str = Field(..., max_length=MAX_FRAME_BYTES * 4 // 3 + 64)
    # why the client captured it: "interval" (every 2 s) | "speech" (right after a sentence)
    # | "scene" (the picture settled after a change) | "start" / "end"
    reason: str = Field("", max_length=16)


class CompileRequest(BaseModel):
    title: str = Field(..., max_length=200)
    teacher_name: str = Field("", max_length=100)
    transcript: list[TranscriptLine] = Field(default_factory=list, max_length=MAX_TRANSCRIPT_LINES)
    frames: list[FrameIn] = Field(default_factory=list, max_length=MAX_REQUEST_FRAMES)
    notes: str = Field("", max_length=4000)
    # optional client token for GET /api/teach/progress/{job_id} (2-phase loading screen)
    job_id: str = Field("", max_length=40)


# --------------------------------------------------------------------------- system prompt

SYSTEM_PROMPT = """\
You are SHADOW's Skill Compiler. A teacher recorded themselves doing a hands-on task with a phone camera while explaining it out loud in Vietnamese. You receive their speech transcript (with timestamps) and keyframes from the video. Turn this one demonstration into a step-by-step SKILL that a real-time coach can run for a learner.

How the skill is used later (design for this!):
- The learner points a phone camera at their own work. Every ~1.5 s ONE camera frame of the CURRENT step is sent to a fast vision model together with that step's `detector_prompt` (and the teacher's reference frame for the step). The vision model sees a single still image, no video, no history.
- When the detector says the step is done, the coach speaks praise and moves to the next step. A single false positive skips a step, so detectors must be strict.
- All spoken lines are read aloud by Vietnamese text-to-speech to someone whose hands are busy.

INPUT
- Transcript lines as `[t0–t1 s] text`. It comes from browser speech recognition: no punctuation, possible misheard words, filler ("ờ", "à", "rồi thì"). Use the frames and common sense to recover what was meant; never copy recognition errors into the skill.
- Frames as `Frame <index> @ <t> s`, in time order. Speech and frames share the same clock, so the frames right after a sentence usually show the result of what was just said. Frames marked "(right after a sentence ended)" or "(picture just settled after a change)" are the best end-state candidates.
- Title and optional notes from the teacher.

WHAT TO PRODUCE
1. Steps: 3 to 8 steps (fewer for a simple task). Each step = one physical action whose END STATE is visible in the camera AND looks different from the previous step's end state. Merge tiny actions; split an action only if its intermediate result is clearly visible. Drop talk that is not an action (greetings, "hôm nay mình sẽ…", "thế là xong") but keep its know-how.
   - Waiting / timing / checking talk ("ngâm 3 phút", "đợi cho nguội", "để yên 5 giây", "kiểm tra lại") is NOT a step when nothing new becomes visible: put it into the tip of the step it belongs to (e.g. tip "Ngâm khoảng 3 phút cho trà ra màu đậm.").
   - Every action the teacher showed must appear in some step; do not silently drop one.
2. For each step:
   - `instruction`: short Vietnamese imperative shown on screen, max ~10 words (e.g. "Cắm thanh đứng vào khe giữa chân đế").
   - `task`: 2–5 word Vietnamese checklist label (e.g. "Gắn thanh đứng").
   - `tip`: the teacher's expert know-how for this step — ONLY if they actually said something like it ("nhớ…", "mẹo là…", "nên…", "phải… thì mới…", "lưu ý nếu… thì…", a reason why, a duration or amount). Rewrite as one clear Vietnamese sentence in the teacher's spirit, keeping their concrete numbers. Otherwise null. Never invent tips. Every piece of know-how the teacher said must end up in exactly one step's tip or warning.
   - `warning`: ONLY a caution about a risk the teacher actually named — damage, injury, burn, spill, a typical mistake ("đừng…", "cẩn thận…", "không được…", "kẻo…"). Helpful advice ("lưu ý nếu không thấy thì kéo xuống") is a tip, not a warning. One sentence or null. Never invent.
   - `frame`: index of the frame that best shows this step COMPLETED (end state clearly visible, least occluded). Must be one of the given indexes; frames must be non-decreasing across steps. null only if no frames were given.
   - `check`: how the vision model decides the step is done from ONE frame:
       • Prefer `{"type": "flag", "prompt": "..."}`: the prompt describes the visible END STATE that is true only once this step is done (and not after the previous step alone). The vision model answers true/false.
       • Use `{"type": "state", "prompt": "...", "options": [...], "expected": "...", "mismatch_speech": "..."}` only when there is a typical visible MISTAKE worth correcting (wrong orientation, wrong slot, wrong part, wrong side). `options` = 2–4 short lowercase English snake_case labels covering the correct state and the typical wrong one(s) (e.g. ["facing_front", "facing_back"]); `expected` is one of them; do not include "unknown" (added automatically). `mismatch_speech` = one friendly Vietnamese sentence saying how to fix it. Use at most 1–2 state checks per skill.
     Detector prompt rules (English, 1–3 sentences):
       • Describe concrete visible evidence: objects, their position relative to each other, attachment, orientation, colour, fill level. Name objects the way they look in the frames ("the black clamp head", "the white cup"), not by brand.
       • Judge the end state, not the motion — never "is being placed", "the moment when".
       • Be robust to hands: the hand may still be touching or partly covering the object; judge from the visible parts.
       • Be strict: require clear visual evidence; say what makes it false (e.g. "If the rod is only lying next to the base, it is false").
       • Make it distinguishable from the previous step's end state: the false case should name that previous end state concretely (e.g. "If the tea bag is in the empty cup but there is no water yet, return false").
       • Never make it false because of a LATER step's result (e.g. "if the phone is already in the clamp, return false"): the learner's later progress must keep it true.
       • Keep it short: one sentence for the true case + one for the false case.
       • Don't depend on audio, timing, or anything outside the frame.
   - `say`: what the coach says when the step starts — friendly, natural spoken Vietnamese, 1 short sentence (≤ 18 words), tells the learner what to do (may add the key point briefly). Always address the learner as "bạn" (e.g. "Giờ bạn…", "Bạn…"), never as "các bạn", "em" or with a bare command. No emoji, no markdown, no quotes.
   - `praise`: 1–4 word Vietnamese praise when the step is done, varied across steps ("Chuẩn rồi!", "Tốt lắm!", "Đẹp!").
3. `summary`: one Vietnamese sentence describing what the skill teaches.
4. `done_speech`: one or two short Vietnamese sentences congratulating the learner at the end, mentioning what they made.

OUTPUT: only one JSON object, no markdown fences, no commentary, exactly this shape:
{
  "summary": "string",
  "done_speech": "string",
  "steps": [
    {
      "instruction": "string",
      "task": "string",
      "tip": "string or null",
      "warning": "string or null",
      "frame": 0,
      "check": {"type": "flag", "prompt": "string"},
      "say": "string",
      "praise": "string"
    }
  ]
}
(for a state check: "check": {"type": "state", "prompt": "string", "options": ["a", "b"], "expected": "a", "mismatch_speech": "string"})

EXAMPLE of a good step (teacher said "rồi cắm cái thanh này vào cái lỗ ở giữa nhé, nhớ đẩy tới khi nghe tách là được"):
{"instruction": "Cắm thanh đứng vào khe giữa chân đế", "task": "Gắn thanh đứng", "tip": "Đẩy thanh xuống đến khi nghe tiếng \\"tách\\" là đã khớp.", "warning": null, "frame": 4, "check": {"type": "flag", "prompt": "Return true only if the metal rod stands upright with its lower end inserted into the hole in the middle of the round black base. If the rod is lying flat, held in the air, or not touching the base, return false."}, "say": "Giờ bạn cắm thanh đứng vào khe giữa chân đế, đẩy đến khi nghe tách nhé.", "praise": "Chuẩn rồi!"}
"""

RETRY_NOTE = (
    "Your previous answer could not be parsed or had no usable steps. "
    "Answer again with ONLY the JSON object in the exact shape from the instructions."
)


# --------------------------------------------------------------------------- helpers


def slugify(value: str, max_len: int = MAX_SLUG_LEN) -> str:
    text = value.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    text = text[:max_len].strip("-")
    return text or "skill"


def _clean_text(value: Any, max_len: int = 300) -> str | None:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().strip('"').strip()
    if not text or text.lower() in {"null", "none", "không", "khong", "n/a", "-"}:
        return None
    return text[:max_len].rstrip()


def _sentence_case(text: str) -> str:
    text = text.strip()
    return text[:1].upper() + text[1:] if text else text


def _ensure_period(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…" else text + "."


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _decode_frame(image: str) -> tuple[bytes, str] | None:
    """Return (bytes, extension) for a valid JPEG/PNG data URL (or bare base64), else None."""
    if not isinstance(image, str) or not image:
        return None
    payload = image.strip()
    if payload.startswith("data:"):
        header, _, payload = payload.partition(",")
        if ";base64" not in header or not header.startswith("data:image/"):
            return None
    # quick size guard before decoding (base64 is ~4/3 of the raw size)
    if len(payload) > MAX_FRAME_BYTES * 4 // 3 + 16:
        return None
    try:
        data = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return None
    if not data or len(data) > MAX_FRAME_BYTES:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return data, "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "png"
    return None


class SavedFrame(BaseModel):
    index: int
    t: float
    filename: str  # e.g. "f03.jpg"
    data_url: str
    reason: str = ""


def save_draft_frames(
    frames: list[FrameIn], frames_dir: Path, *, speech_ends_ms: list[float] | None = None
) -> list[SavedFrame]:
    """Decode, pick the best ≤ MAX_FRAMES keyframes (teach_keyframes.py) and save them."""
    from teach_keyframes import RawFrame, select_keyframes

    raw: list[RawFrame] = []
    for frame in sorted(frames, key=lambda f: f.t):
        result = _decode_frame(frame.image)
        if result is None:
            logger.warning("skill_compiler: dropping invalid frame at t=%s", frame.t)
            continue
        data, ext = result
        reason = re.sub(r"[^a-z_]", "", (frame.reason or "").lower())[:16]
        raw.append(RawFrame(t=frame.t, payload=(ext, frame.image), data=data, reason=reason))
    chosen = select_keyframes(raw, cap=MAX_FRAMES, speech_ends_ms=speech_ends_ms or [])
    saved: list[SavedFrame] = []
    if chosen:
        frames_dir.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(chosen):
        ext, original = item.payload
        filename = f"f{index:02d}.{ext}"
        (frames_dir / filename).write_bytes(item.data)
        mime = "image/jpeg" if ext == "jpg" else "image/png"
        data_url = original if original.startswith("data:") else (
            f"data:{mime};base64," + base64.b64encode(item.data).decode("ascii")
        )
        saved.append(SavedFrame(index=index, t=item.t, filename=filename, data_url=data_url, reason=item.reason))
    return saved


# --------------------------------------------------------------------------- detector prompts


FLAG_PROMPT_PREFIX = "Return JSON with a single boolean `flag` field. "
FLAG_PROMPT_SUFFIX = (
    "Judge only the visible end state in this single frame; a hand may still touch or partly "
    "cover the object, so judge from the visible parts. If the view is unclear, the object is "
    "out of frame, or you are not sure, return false."
)
STATE_PROMPT_SUFFIX = (
    "Judge only what is visible in this single frame; a hand may partly cover the object. "
    'If the relevant object is not clearly visible or you are not sure, return "unknown".'
)
_STATE_HEAD_RE = re.compile(r'^Return JSON with a single string `state` field, one of: (.*?)\.\s', re.DOTALL)


def _flag_prompt(criterion: str) -> str:
    criterion = _ensure_period(criterion.strip())
    if "return json" in criterion.lower():
        return criterion
    return f"{FLAG_PROMPT_PREFIX}{criterion} {FLAG_PROMPT_SUFFIX}"


def _state_prompt(criterion: str, options: list[str]) -> str:
    criterion = _ensure_period(criterion.strip())
    if "return json" in criterion.lower():
        return criterion
    labels = ", ".join(f'"{o}"' for o in [*options, "unknown"])
    return (
        "Return JSON with a single string `state` field, one of: "
        f"{labels}. {criterion} {STATE_PROMPT_SUFFIX}"
    )


def split_detector_prompt(prompt: str) -> tuple[str, list[str]]:
    """Inverse of _flag_prompt/_state_prompt: (bare criterion, state options without "unknown")."""
    text = (prompt or "").strip()
    options: list[str] = []
    match = _STATE_HEAD_RE.match(text)
    if match:
        options = [o for o in re.findall(r'"([^"]+)"', match.group(1)) if o != "unknown"]
        text = text[match.end():]
    elif text.startswith(FLAG_PROMPT_PREFIX):
        text = text[len(FLAG_PROMPT_PREFIX):]
    for suffix in (FLAG_PROMPT_SUFFIX, STATE_PROMPT_SUFFIX):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip(), options


def wrap_detector_prompt(criterion: str, field: str, options: list[str]) -> str:
    """Wrap a bare criterion exactly like the compiler does for this detector field."""
    if field == "state" and options:
        return _state_prompt(criterion, options)
    return _flag_prompt(criterion)


def _derived_flag_criterion(instruction: str) -> str:
    return (
        "The learner must perform this step (Vietnamese): "
        f"\"{instruction.rstrip('.')}\". Return true only if the frame clearly shows the finished "
        "result of this step (the objects are in the final position/state the step describes)."
    )


# --------------------------------------------------------------------------- intermediate repair


def _norm_label(value: Any) -> str | None:
    text = _clean_text(value, 40)
    if text is None:
        return None
    text = slugify(text, 32).replace("-", "_")
    return None if text in {"", "skill", "unknown"} else text


def _evenly_spread_frame(i: int, n_steps: int, n_frames: int) -> int | None:
    if n_frames <= 0:
        return None
    if n_steps <= 1:
        return n_frames - 1
    # step i finishes around fraction (i+1)/n_steps of the recording
    return min(n_frames - 1, max(0, round((i + 1) * n_frames / n_steps) - 1))


def repair_intermediate(
    raw: dict[str, Any], *, title: str, n_frames: int
) -> dict[str, Any] | None:
    """Clamp/default every field. Returns None if no usable step exists."""
    if not isinstance(raw, dict):
        return None
    if "steps" not in raw:
        for key in ("skill", "result", "data"):
            if isinstance(raw.get(key), dict) and "steps" in raw[key]:
                raw = raw[key]
                break
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list):
        return None

    steps: list[dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        instruction = _clean_text(item.get("instruction"), 120) or _clean_text(
            item.get("task"), 120
        ) or _clean_text(item.get("say"), 120)
        if not instruction:
            continue
        steps.append({"_raw": item, "instruction": _sentence_case(instruction)})
    if not steps:
        return None
    steps = steps[:MAX_STEPS]
    n = len(steps)

    last_frame = -1
    out_steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        item = step.pop("_raw")
        instruction = step["instruction"].rstrip(".")

        frame: int | None = None
        raw_frame = item.get("frame")
        if n_frames > 0:
            if isinstance(raw_frame, bool) or raw_frame is None:
                frame = None
            else:
                try:
                    frame = int(raw_frame)
                except (TypeError, ValueError):
                    frame = None
            if frame is None:
                frame = _evenly_spread_frame(i, n, n_frames)
            frame = min(max(frame, 0), n_frames - 1)
            frame = max(frame, last_frame) if last_frame >= 0 else frame
            last_frame = frame

        check = item.get("check") if isinstance(item.get("check"), dict) else {}
        prompt = _clean_text(check.get("prompt"), 900) or _clean_text(
            item.get("detector_prompt"), 900
        )
        check_type = str(check.get("type") or "flag").lower()
        options: list[str] = []
        if isinstance(check.get("options"), list):
            for opt in check["options"]:
                label = _norm_label(opt)
                if label and label not in options:
                    options.append(label)
        expected = _norm_label(check.get("expected"))
        mismatch = _clean_text(check.get("mismatch_speech"), 200) or _clean_text(
            item.get("mismatch_speech"), 200
        )
        use_state = (
            check_type == "state"
            and prompt is not None
            and expected is not None
            and expected in options
            and len(options) >= 2
        )

        say = _clean_text(item.get("say"), 220) or _clean_text(
            item.get("on_enter_speech"), 220
        )
        if not say:
            say = f"Bây giờ bạn {_lower_first(instruction)} nhé."
        praise = _clean_text(item.get("praise"), 40) or PRAISES[i % len(PRAISES)]

        out: dict[str, Any] = {
            "instruction": instruction,
            "task": _clean_text(item.get("task"), 60) or instruction,
            "tip": _clean_text(item.get("tip"), 240),
            "warning": _clean_text(item.get("warning"), 240),
            "frame": frame,
            "say": _ensure_period(_sentence_case(say)),
            "praise": _ensure_period(_sentence_case(praise)),
        }
        if use_state:
            out["check"] = {
                "type": "state",
                "prompt": prompt,
                "options": options[:4] if expected in options[:4] else [expected, *options[:3]],
                "expected": expected,
                "mismatch_speech": _ensure_period(
                    mismatch or f"Chưa đúng lắm, bạn {_lower_first(instruction)} nhé"
                ),
            }
        else:
            out["check"] = {
                "type": "flag",
                "prompt": prompt or _derived_flag_criterion(instruction),
            }
        out_steps.append(out)

    summary = _clean_text(raw.get("summary"), 240) or (
        f"Hướng dẫn từng bước: {title.strip() or 'kỹ năng mới'}."
    )
    done = _clean_text(raw.get("done_speech"), 240) or (
        f"Xong rồi! Bạn đã hoàn thành {_lower_first(title.strip()) or 'kỹ năng này'}. Làm tốt lắm!"
    )
    return {
        "summary": _ensure_period(summary),
        "done_speech": _ensure_period(done),
        "steps": out_steps,
    }


# --------------------------------------------------------------------------- intermediate -> Recipe


def build_recipe(
    inter: dict[str, Any],
    *,
    title: str,
    teacher_name: str,
    frames: list[SavedFrame],
    draft_id: str,
) -> dict[str, Any]:
    steps_in = inter["steps"]
    n = len(steps_in)
    step_ids = [f"step_{i + 1}" for i in range(n)]
    tasks: list[dict[str, Any]] = []
    detectors: dict[str, dict[str, Any]] = {}
    steps: list[dict[str, Any]] = []

    for i, s in enumerate(steps_in):
        sid = step_ids[i]
        tid = f"task_{i + 1}"
        next_id = step_ids[i + 1] if i + 1 < n else None
        if next_id is not None:
            transition = f"{s['praise']} {steps_in[i + 1]['say']}"
        else:
            transition = f"{s['praise']} {inter['done_speech']}"

        frame_file = None
        frame_url = None
        if s.get("frame") is not None and frames:
            saved = frames[min(max(int(s["frame"]), 0), len(frames) - 1)]
            frame_file = f"frames/{saved.filename}"
            frame_url = f"/skills-media/_drafts/{draft_id}/frames/{saved.filename}"

        tasks.append({"id": tid, "text": s["task"]})
        check = s["check"]
        step: dict[str, Any] = {
            "id": sid,
            "task_id": tid,
            "detector_key": sid,
            "next_step_id": next_id,
            "instruction": s["instruction"],
            "tip": s.get("tip"),
            "warning": s.get("warning"),
            "reference_image": frame_file,
            "reference_image_url": frame_url,
            "allow_dynamic_hint": True,
            "on_enter_speech": s["say"],
        }
        if check["type"] == "state":
            detectors[sid] = {
                "field": "state",
                "prompt": _state_prompt(check["prompt"], check["options"]),
            }
            step.update(
                {
                    "evaluation_mode": "match_value",
                    "expected_value": check["expected"],
                    "ignored_values": ["unknown"],
                    "speak_on_observation_change_only": True,
                    "mismatch_speech": check["mismatch_speech"],
                    "success_speech": transition,
                }
            )
        else:
            detectors[sid] = {"field": "flag", "prompt": _flag_prompt(check["prompt"])}
            step.update(
                {
                    "evaluation_mode": "momentary_true_complete",
                    "complete_on": True,
                    "complete_speech": transition,
                }
            )
        steps.append(step)

    cover = steps[-1]["reference_image"] if steps else None
    if cover is None:
        cover = next((st["reference_image"] for st in reversed(steps) if st["reference_image"]), None)
    cover_url = (
        f"/skills-media/_drafts/{draft_id}/{cover}" if cover is not None else None
    )
    return {
        "id": slugify(title),
        "display_name": title.strip() or "Kỹ năng mới",
        "start_step_id": step_ids[0],
        "language": "vi",
        "learned_from": teacher_name.strip() or None,
        "summary": inter["summary"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cover_image": cover,
        "cover_image_url": cover_url,
        "draft_id": draft_id,
        "tasks": tasks,
        "detectors": detectors,
        "steps": steps,
    }


# --------------------------------------------------------------------------- mock compiler


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…;])\s+|\n+", text)
    return [p.strip(" .!?…;,-") for p in parts if p and p.strip(" .!?…;,-")]


def _strip_filler(text: str) -> str:
    out = FILLER_RE.sub("", text.strip()).strip(" ,")
    out = re.sub(r"\s+(nhé|nha|nhá|đi|thôi|ạ)$", "", out, flags=re.IGNORECASE)
    return out or text.strip()


def _timed_sentences(req: CompileRequest) -> list[tuple[str, float]]:
    """Sentences with an approximate end time (ms)."""
    out: list[tuple[str, float]] = []
    for line in sorted(req.transcript, key=lambda l: l.t0):
        text = line.text.strip()
        if not text:
            continue
        parts = _split_sentences(text)
        total = max(sum(len(p) for p in parts), 1)
        t1 = line.t1 if line.t1 >= line.t0 else line.t0
        acc = 0
        for p in parts:
            acc += len(p)
            out.append((p, line.t0 + (t1 - line.t0) * acc / total))
    return out


def _merge_short(items: list[tuple[str, float]], min_words: int = 4) -> list[tuple[str, float]]:
    merged: list[tuple[str, float]] = []
    for text, t in items:
        if merged and len(merged[-1][0].split()) < min_words:
            prev_text, _ = merged.pop()
            merged.append((f"{prev_text}, {text}", t))
        else:
            merged.append((text, t))
    if len(merged) > 1 and len(merged[-1][0].split()) < min_words:
        text, t = merged.pop()
        prev_text, _ = merged.pop()
        merged.append((f"{prev_text}, {text}", t))
    return merged


def _group_to_cap(items: list[tuple[str, float]], cap: int) -> list[tuple[str, float]]:
    if len(items) <= cap:
        return items
    groups: list[tuple[str, float]] = []
    size = len(items) / cap
    for g in range(cap):
        chunk = items[round(g * size): round((g + 1) * size)]
        if chunk:
            groups.append((". ".join(c[0] for c in chunk), chunk[-1][1]))
    return groups


def _nearest_frame(frames: list[SavedFrame], t: float) -> int | None:
    if not frames:
        return None
    # prefer the first frame at/after the sentence end (shows the result), else the closest
    after = [f for f in frames if f.t >= t]
    if after and after[0].t - t <= 4000:
        return after[0].index
    return min(frames, key=lambda f: abs(f.t - t)).index


def mock_intermediate(req: CompileRequest, frames: list[SavedFrame]) -> dict[str, Any]:
    timed = _timed_sentences(req)
    timed_mode = bool(timed)
    if not timed:
        timed = [(s, float(i)) for i, s in enumerate(_split_sentences(req.notes))]

    actions: list[tuple[str, float]] = []
    pending_tips: list[tuple[str, str]] = []  # tips seen before any action
    tips_for: dict[int, dict[str, str]] = {}

    def attach(kind: str, sentence: str) -> None:
        if actions:
            slot = tips_for.setdefault(len(actions) - 1, {})
            slot[kind] = f"{slot[kind]} {sentence}" if kind in slot else sentence
        else:
            pending_tips.append((kind, sentence))

    for text, t in timed:
        sentence = _ensure_period(_sentence_case(text))
        if INTRO_RE.search(text):
            continue
        if WARNING_RE.search(text):
            attach("warning", sentence)
        elif TIP_RE.search(text) and len(text.split()) <= 25:
            attach("tip", sentence)
        else:
            actions.append((text, t))

    if not actions and timed:
        actions = [(text, t) for text, t in timed if not INTRO_RE.search(text)] or list(timed)
        tips_for.clear()
        pending_tips.clear()

    raw_steps: list[dict[str, Any]] = []
    if actions:
        grouped = _group_to_cap(_merge_short(actions), MAX_STEPS)
        # re-map tips onto grouped steps by time
        for gi, (text, t) in enumerate(grouped):
            instruction = _sentence_case(_strip_filler(text.split(". ")[0] if len(text) > 120 else text))
            raw_steps.append(
                {"instruction": instruction[:120], "_t": t, "say": None, "tip": None, "warning": None}
            )
        action_times = [t for _, t in actions]
        for ai_idx, slot in tips_for.items():
            t = action_times[ai_idx]
            gi = next((k for k, (_, gt) in enumerate(grouped) if gt >= t), len(grouped) - 1)
            for kind, sentence in slot.items():
                cur = raw_steps[gi][kind]
                raw_steps[gi][kind] = f"{cur} {sentence}" if cur else sentence
        for kind, sentence in pending_tips:
            cur = raw_steps[0][kind]
            raw_steps[0][kind] = f"{cur} {sentence}" if cur else sentence
    else:
        raw_steps = [
            {"instruction": s, "_t": float(i), "tip": None, "warning": None}
            for i, s in enumerate(GENERIC_STEPS)
        ]

    n = len(raw_steps)
    for i, step in enumerate(raw_steps):
        if timed_mode and frames:
            step["frame"] = _nearest_frame(frames, step["_t"])
        else:
            step["frame"] = _evenly_spread_frame(i, n, len(frames))
        step.pop("_t", None)
        step["check"] = {"type": "flag", "prompt": None}
    title = req.title.strip() or "kỹ năng này"
    return {
        "summary": f"Học cách {_lower_first(title)} theo đúng từng bước"
        + (f" của {req.teacher_name.strip()}." if req.teacher_name.strip() else "."),
        "done_speech": f"Xong rồi! Bạn đã hoàn thành {_lower_first(title)}. Làm tốt lắm!",
        "steps": raw_steps,
    }


# --------------------------------------------------------------------------- AI compiler


FRAME_REASON_LABELS = {
    "speech": " (right after a sentence ended)",
    "scene": " (picture just settled after a change)",
}


def _fmt_s(ms: float) -> str:
    return f"{ms / 1000:.1f}"


def build_user_content(req: CompileRequest, frames: list[SavedFrame]) -> list[dict[str, Any]]:
    lines = [
        f"Title: {req.title.strip() or '(none)'}",
        f"Teacher: {req.teacher_name.strip() or '(unknown)'}",
    ]
    if req.notes.strip():
        lines.append(f"Teacher notes:\n{req.notes.strip()}")
    transcript = [l for l in sorted(req.transcript, key=lambda l: l.t0) if l.text.strip()]
    if transcript:
        lines.append("Transcript (Vietnamese speech recognition, times in seconds):")
        for l in transcript:
            lines.append(f"[{_fmt_s(l.t0)}–{_fmt_s(max(l.t1, l.t0))} s] {l.text.strip()}")
    else:
        lines.append("Transcript: (empty — rely on the frames and notes)")
    if frames:
        lines.append(
            f"Keyframes: {len(frames)} frames follow, indexes 0–{len(frames) - 1}, in time order."
        )
    else:
        lines.append("Keyframes: none (set every `frame` to null).")
    content: list[dict[str, Any]] = [{"type": "text", "text": "\n".join(lines)}]
    for f in frames:
        why = FRAME_REASON_LABELS.get(f.reason, "")
        content.append({"type": "text", "text": f"Frame {f.index} @ {_fmt_s(f.t)} s{why}"})
        content.append({"type": "image_url", "image_url": {"url": f.data_url}})
    content.append(
        {"type": "text", "text": "Now output the skill JSON object (3–8 steps, Vietnamese text)."}
    )
    return content


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start: end + 1])
                except json.JSONDecodeError:
                    return None
            return None
    return value


async def _ai_intermediate(
    req: CompileRequest, frames: list[SavedFrame], *, provider: Any, model: str
) -> dict[str, Any] | None:
    content = build_user_content(req, frames)
    for attempt in range(2):
        system = SYSTEM_PROMPT if attempt == 0 else f"{SYSTEM_PROMPT}\n\n{RETRY_NOTE}"
        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(
                provider.chat_json(
                    model=model,
                    system=system,
                    content=content,
                    max_tokens=COMPILE_MAX_TOKENS,
                    temperature=0.2,
                ),
                timeout=compile_timeout_s(),
            )
        except asyncio.TimeoutError:
            # A second attempt would most likely time out too and double the wait.
            logger.warning(
                "skill_compiler: model timed out after %.0fs (attempt %d); giving up on AI",
                compile_timeout_s(), attempt + 1,
            )
            return None
        except Exception as error:  # noqa: BLE001 - provider/network/parse errors
            logger.warning("skill_compiler: chat_json failed (attempt %d): %s", attempt + 1, error)
            continue
        logger.info(
            "skill_compiler: model answered in %.1fs (attempt %d)",
            time.perf_counter() - started, attempt + 1,
        )
        inter = repair_intermediate(_coerce_json(raw), title=req.title, n_frames=len(frames))
        if inter is not None:
            return inter
        logger.warning("skill_compiler: unusable compiler output (attempt %d)", attempt + 1)
    return None


# --------------------------------------------------------------------------- entry point


def _has_usable_input(req: CompileRequest, frames: list[SavedFrame]) -> bool:
    return bool(frames) or any(l.text.strip() for l in req.transcript) or bool(req.notes.strip())


def prune_old_drafts(drafts_root: Path, max_age_seconds: float = DRAFT_MAX_AGE_SECONDS) -> None:
    """Remove draft folders nobody saved (cancelled compiles, abandoned reviews)."""
    if not drafts_root.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    for child in drafts_root.iterdir():
        try:
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


async def compile_skill(
    req: CompileRequest,
    *,
    provider: Any,
    model: str,
    skills_root: Path,
    verify: bool | None = None,
    vision_model_name: str | None = None,
    on_phase: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Compile a draft skill. In AI mode the result is then self-checked (skill_verify.py)
    unless `verify` is False (None = env SHADOW_TEACH_VERIFY, default on).
    `on_phase(phase, **info)` reports "learning" → "verifying" for the loading screen."""
    draft_id = uuid.uuid4().hex[:12]
    prune_old_drafts(Path(skills_root) / "_drafts")
    draft_dir = Path(skills_root) / "_drafts" / draft_id
    try:
        return await _compile_into_draft(
            req, provider=provider, model=model, draft_id=draft_id, draft_dir=draft_dir,
            verify=verify, vision_model_name=vision_model_name, on_phase=on_phase,
        )
    except BaseException:
        # cancelled (teacher pressed "Huỷ" / closed the page) or crashed: drop the draft frames
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise


def _phase(on_phase: Callable[..., Any] | None, phase: str, **info: Any) -> None:
    if on_phase is None:
        return
    try:
        on_phase(phase, **info)
    except Exception:  # noqa: BLE001 - progress reporting is cosmetic
        logger.debug("skill_compiler: on_phase failed", exc_info=True)


async def _compile_into_draft(
    req: CompileRequest,
    *,
    provider: Any,
    model: str,
    draft_id: str,
    draft_dir: Path,
    verify: bool | None = None,
    vision_model_name: str | None = None,
    on_phase: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from skill_verify import TeachFrame, verify_enabled, verify_skill, write_teach_meta

    compile_started = time.perf_counter()
    _phase(on_phase, "learning")
    speech_ends = [max(l.t0, l.t1) for l in req.transcript if l.text.strip()]
    frames = save_draft_frames(req.frames, draft_dir / "frames", speech_ends_ms=speech_ends)
    if frames:
        write_teach_meta(
            draft_dir,
            [TeachFrame(f.index, f.t, f.filename, f.reason) for f in frames],
            {"title": req.title.strip(), "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        )

    inter: dict[str, Any] | None = None
    source = "mock"
    ai_failed = False
    use_ai = (
        provider is not None
        and getattr(provider, "mode", "mock") == "ai"
        and _has_usable_input(req, frames)
    )
    if use_ai:
        inter = await _ai_intermediate(req, frames, provider=provider, model=model)
        if inter is not None:
            source = "ai"
        else:
            ai_failed = True
    if inter is None:
        inter = repair_intermediate(
            mock_intermediate(req, frames), title=req.title, n_frames=len(frames)
        )
    assert inter is not None  # mock always yields >= 1 step

    skill = build_recipe(
        inter, title=req.title, teacher_name=req.teacher_name, frames=frames, draft_id=draft_id
    )
    try:
        Recipe.model_validate(skill)
    except ValidationError as error:
        logger.error("skill_compiler: %s skill failed validation, using mock: %s", source, error)
        inter = repair_intermediate(
            mock_intermediate(req, frames), title=req.title, n_frames=len(frames)
        )
        skill = build_recipe(
            inter, title=req.title, teacher_name=req.teacher_name, frames=frames, draft_id=draft_id
        )
        Recipe.model_validate(skill)
        source = "mock"
        ai_failed = ai_failed or use_ai
    skill["compiled_by"] = source
    compile_s = time.perf_counter() - compile_started
    logger.info(
        "skill_compiler: draft=%s source=%s steps=%d frames=%d in %.1fs",
        draft_id, source, len(skill["steps"]), len(frames), compile_s,
    )
    verify_s = None
    do_verify = verify if verify is not None else verify_enabled()
    if do_verify and source == "ai" and len(frames) >= 2 and getattr(provider, "mode", "mock") == "ai":
        _phase(on_phase, "verifying", judged=0, total=0)
        verify_started = time.perf_counter()
        try:
            from providers import vision_model as _env_vision_model

            await verify_skill(
                skill,
                provider=provider,
                media_root=draft_dir,
                vision_model=vision_model_name or _env_vision_model(),
                compiler_model=model,
                on_progress=on_phase,
            )
            Recipe.model_validate(skill)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the self-check must never break a compile
            logger.exception("skill_compiler: self-check failed; returning the unchecked skill")
            for step in skill["steps"]:
                step.pop("verify", None)
            skill.pop("quality", None)
        verify_s = time.perf_counter() - verify_started
    # Why a mock skill was produced, so the UI can tell the teacher:
    # "mock_mode" (offline demo), "ai_failed" (AI unreachable / unusable output → rule-based split).
    fallback_reason = None
    if source == "mock":
        if ai_failed:
            fallback_reason = "ai_failed"
        elif getattr(provider, "mode", "mock") == "ai":
            fallback_reason = "no_input"
        else:
            fallback_reason = "mock_mode"
    _phase(on_phase, "done")
    return {
        "skill": skill,
        "draft_id": draft_id,
        "compiled_by": source,
        "fallback_reason": fallback_reason,
        "timings": {
            "compile_s": round(compile_s, 1),
            "verify_s": round(verify_s, 1) if verify_s is not None else None,
        },
        # every frame the teacher captured, for the reference-frame picker in Review
        "frames": [
            {
                "index": f.index,
                "t": f.t,
                "reason": f.reason,
                "file": f"frames/{f.filename}",
                "url": f"/skills-media/_drafts/{draft_id}/frames/{f.filename}",
            }
            for f in frames
        ],
    }

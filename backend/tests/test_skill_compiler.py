from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from recipe_catalog import Recipe  # noqa: E402
from skill_compiler import (  # noqa: E402
    SYSTEM_PROMPT,
    CompileRequest,
    compile_skill,
    slugify,
)

# minimal valid-looking JPEG / PNG payloads (magic bytes are what the compiler checks)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x00" * 64 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG).decode()

TRANSCRIPT = [
    {"t0": 0, "t1": 2500, "text": "Xin chào hôm nay mình sẽ lắp giá đỡ điện thoại"},
    {"t0": 3000, "t1": 6000, "text": "Đầu tiên đặt chân đế tròn nằm phẳng giữa bàn"},
    {"t0": 6500, "t1": 9500, "text": "Rồi cắm thanh đứng vào cái lỗ ở giữa chân đế"},
    {"t0": 10000, "t1": 12000, "text": "Nhớ đẩy tới khi nghe tiếng tách"},
    {"t0": 12500, "t1": 15500, "text": "Sau đó gạt khóa xuống để siết chặt thanh"},
    {"t0": 16000, "t1": 18000, "text": "Đừng vặn mạnh quá kẻo nứt khóa"},
    {"t0": 18500, "t1": 22000, "text": "Cuối cùng gắn đầu kẹp lên trên rồi đặt điện thoại vào"},
]


def make_request(n_frames: int = 10, **overrides: Any) -> CompileRequest:
    frames = [{"t": i * 2200, "image": JPEG_URL} for i in range(n_frames)]
    body = {
        "title": "Lắp giá đỡ điện thoại",
        "teacher_name": "Tuấn",
        "transcript": TRANSCRIPT,
        "frames": frames,
        "notes": "",
    }
    body.update(overrides)
    return CompileRequest.model_validate(body)


class FakeProvider:
    def __init__(self, responses: list[Any], mode: str = "ai") -> None:
        self.mode = mode
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        self.calls.append(
            {"model": model, "system": system, "content": content, "max_tokens": max_tokens}
        )
        item = self.responses.pop(0) if self.responses else {}
        if isinstance(item, Exception):
            raise item
        return item


def run(coro):
    return asyncio.run(coro)


def assert_valid_chain(skill: dict[str, Any]) -> Recipe:
    recipe = Recipe.model_validate(skill)
    ids = [s.id for s in recipe.steps]
    assert recipe.start_step_id == ids[0]
    for step, nxt in zip(recipe.steps, ids[1:] + [None]):
        assert step.next_step_id == nxt
        assert step.detector_key in recipe.detectors
        assert step.task_id in {t.id for t in recipe.tasks}
        assert step.on_enter_speech
        assert step.instruction
        if step.evaluation_mode == "momentary_true_complete":
            assert step.complete_on is True
            assert step.complete_speech
            assert recipe.detectors[step.detector_key].field == "flag"
        else:
            assert step.evaluation_mode == "match_value"
            assert recipe.detectors[step.detector_key].field == "state"
            assert step.expected_value is not None
            assert "unknown" in step.ignored_values
            assert step.success_speech and step.mismatch_speech
    assert 1 <= len(recipe.steps) <= 8
    assert recipe.language == "vi"
    return recipe


# --------------------------------------------------------------------------- mock


def test_slugify_vietnamese():
    assert slugify("Lắp giá đỡ điện thoại") == "lap-gia-do-dien-thoai"
    assert slugify("Pha Cà Phê Phin — Đậm Đà!!") == "pha-ca-phe-phin-dam-da"
    assert len(slugify("a" * 100)) <= 40
    assert slugify("!!!") == "skill"


def test_mock_compile_from_transcript(tmp_path: Path):
    req = make_request()
    out = run(compile_skill(req, provider=FakeProvider([], mode="mock"), model="x", skills_root=tmp_path))
    skill, draft_id = out["skill"], out["draft_id"]
    recipe = assert_valid_chain(skill)

    assert skill["id"] == "lap-gia-do-dien-thoai"
    assert skill["draft_id"] == draft_id
    assert skill["compiled_by"] == "mock"
    assert recipe.learned_from == "Tuấn"
    assert recipe.created_at
    assert all(s.evaluation_mode == "momentary_true_complete" for s in recipe.steps)
    assert 3 <= len(recipe.steps) <= 8

    frames_dir = tmp_path / "_drafts" / draft_id / "frames"
    saved = sorted(p.name for p in frames_dir.iterdir())
    assert saved[0] == "f00.jpg" and len(saved) == 10

    for raw, step in zip(skill["steps"], recipe.steps):
        assert step.reference_image and step.reference_image.startswith("frames/f")
        assert (tmp_path / "_drafts" / draft_id / step.reference_image).exists()
        assert raw["reference_image_url"] == f"/skills-media/_drafts/{draft_id}/{step.reference_image}"
    assert recipe.cover_image == recipe.steps[-1].reference_image
    # reference frames move forward in time
    idx = [int(s.reference_image[8:10]) for s in recipe.steps]
    assert idx == sorted(idx)

    tips = [s.tip for s in recipe.steps if s.tip]
    warnings = [s.warning for s in recipe.steps if s.warning]
    assert any("tách" in t for t in tips)
    assert any("Đừng" in w for w in warnings)
    # the tip/warning sentences are not standalone steps
    assert not any(s.instruction.lower().startswith(("nhớ", "đừng")) for s in recipe.steps)
    # greeting / intro talk is not a step, sequencing filler is stripped
    assert not any("xin chào" in s.instruction.lower() for s in recipe.steps)
    assert recipe.steps[0].instruction.startswith("Đặt chân đế")
    assert recipe.steps[-1].instruction.startswith("Gắn đầu kẹp")
    # the "thanh đứng" tip is attached to the step that inserts the rod
    rod = next(s for s in recipe.steps if "thanh đứng" in s.instruction)
    assert rod.tip and "tách" in rod.tip
    # last step speaks the completion line
    assert "hoàn thành" in recipe.steps[-1].complete_speech
    # transition speech of step k leads into step k+1
    assert recipe.steps[1].on_enter_speech in recipe.steps[0].complete_speech


def test_mock_compile_without_provider_or_frames(tmp_path: Path):
    req = make_request(n_frames=0)
    out = run(compile_skill(req, provider=None, model="x", skills_root=tmp_path))
    recipe = assert_valid_chain(out["skill"])
    assert all(s.reference_image is None for s in recipe.steps)
    assert recipe.cover_image is None


def test_mock_compile_from_notes_and_generic(tmp_path: Path):
    req = make_request(
        n_frames=4,
        transcript=[],
        notes="Rửa sạch phin và cốc\nCho hai thìa cà phê vào phin\nRót nước sôi và chờ nhỏ giọt hết",
    )
    out = run(compile_skill(req, provider=None, model="x", skills_root=tmp_path))
    recipe = assert_valid_chain(out["skill"])
    assert len(recipe.steps) == 3
    assert recipe.steps[0].instruction.startswith("Rửa sạch phin")
    assert recipe.steps[-1].reference_image == "frames/f03.jpg"

    empty = make_request(n_frames=0, transcript=[], notes="", title="")
    out = run(compile_skill(empty, provider=None, model="x", skills_root=tmp_path))
    recipe = assert_valid_chain(out["skill"])
    assert len(recipe.steps) == 3
    assert out["skill"]["id"] == "skill"


def test_mock_caps_steps_at_eight(tmp_path: Path):
    transcript = [
        {"t0": i * 3000, "t1": i * 3000 + 2500, "text": f"Bước số {i} là lắp chi tiết thứ {i} vào khung"}
        for i in range(20)
    ]
    out = run(compile_skill(make_request(transcript=transcript), provider=None, model="x", skills_root=tmp_path))
    recipe = assert_valid_chain(out["skill"])
    assert len(recipe.steps) == 8


def test_frame_validation_and_cap(tmp_path: Path):
    frames = (
        [{"t": i * 500, "image": JPEG_URL} for i in range(40)]
        + [{"t": 99999, "image": "data:image/jpeg;base64,!!!notbase64"}]
        + [{"t": 99998, "image": "data:text/plain;base64,aGVsbG8="}]
        + [{"t": 99997, "image": "data:image/jpeg;base64," + base64.b64encode(b"GIF89a....").decode()}]
        + [{"t": -1, "image": PNG_URL}]
    )
    out = run(compile_skill(make_request(frames=frames), provider=None, model="x", skills_root=tmp_path))
    saved = list((tmp_path / "_drafts" / out["draft_id"] / "frames").iterdir())
    assert len(saved) == 24
    assert any(p.suffix == ".png" for p in saved)  # the png is first in time order (always kept)
    assert_valid_chain(out["skill"])


# --------------------------------------------------------------------------- AI path

GOOD = {
    "summary": "Lắp giá đỡ điện thoại để bàn trong 4 bước.",
    "done_speech": "Xong rồi! Giá đỡ đã sẵn sàng.",
    "steps": [
        {
            "instruction": "Đặt chân đế nằm phẳng giữa bàn",
            "task": "Đặt chân đế",
            "tip": None,
            "warning": None,
            "frame": 1,
            "check": {"type": "flag", "prompt": "Return true only if the round black base lies flat on the table."},
            "say": "Bạn đặt chân đế tròn nằm phẳng giữa bàn nhé.",
            "praise": "Tốt lắm!",
        },
        {
            "instruction": "Cắm thanh đứng vào khe giữa chân đế",
            "task": "Gắn thanh đứng",
            "tip": "Đẩy đến khi nghe tiếng tách là khớp.",
            "warning": None,
            "frame": 4,
            "check": {"type": "flag", "prompt": "Return true only if the rod stands upright inserted in the centre hole of the base."},
            "say": "Giờ bạn cắm thanh đứng vào lỗ giữa chân đế.",
            "praise": "Chuẩn rồi!",
        },
        {
            "instruction": "Gạt khóa xuống",
            "task": "Siết khóa",
            "tip": None,
            "warning": "Đừng vặn quá mạnh kẻo nứt khóa.",
            "frame": 6,
            "check": {
                "type": "state",
                "prompt": "Look at the lock lever where the rod meets the base.",
                "options": ["locked", "Open Lever"],
                "expected": "locked",
                "mismatch_speech": "Khóa còn mở, bạn gạt hẳn xuống nhé.",
            },
            "say": "Bạn gạt khóa xuống để siết chặt thanh.",
            "praise": "Chắc rồi!",
        },
        {
            "instruction": "Gắn đầu kẹp và đặt điện thoại",
            "task": "Đặt điện thoại",
            "tip": None,
            "warning": None,
            "frame": 9,
            "check": {"type": "flag", "prompt": "Return true only if a phone is held by the clamp on top of the rod."},
            "say": "Cuối cùng bạn gắn đầu kẹp rồi đặt điện thoại vào.",
            "praise": "Tuyệt vời!",
        },
    ],
}


def test_ai_good_output(tmp_path: Path):
    provider = FakeProvider([json.loads(json.dumps(GOOD))])
    out = run(compile_skill(make_request(), provider=provider, model="m-compiler", skills_root=tmp_path))
    skill = out["skill"]
    recipe = assert_valid_chain(skill)
    assert skill["compiled_by"] == "ai"
    assert len(provider.calls) == 1

    call = provider.calls[0]
    assert call["model"] == "m-compiler"
    assert call["system"] == SYSTEM_PROMPT
    assert call["max_tokens"] >= 2000
    images = [p for p in call["content"] if p["type"] == "image_url"]
    assert len(images) == 10 and images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text = "\n".join(p["text"] for p in call["content"] if p["type"] == "text")
    assert "Frame 3 @ 6.6 s" in text
    assert "[3.0–6.0 s] Đầu tiên đặt chân đế" in text

    assert [s.reference_image for s in recipe.steps] == [
        "frames/f01.jpg", "frames/f04.jpg", "frames/f06.jpg", "frames/f09.jpg"
    ]
    lock = recipe.steps[2]
    assert lock.evaluation_mode == "match_value"
    assert lock.expected_value == "locked"
    assert lock.speak_on_observation_change_only is True
    prompt = recipe.detectors[lock.detector_key].prompt
    assert '"locked"' in prompt and '"open_lever"' in prompt and '"unknown"' in prompt
    flag_prompt = recipe.detectors["step_1"].prompt
    assert flag_prompt.startswith("Return JSON with a single boolean `flag` field.")
    assert recipe.steps[0].complete_speech == "Tốt lắm! Giờ bạn cắm thanh đứng vào lỗ giữa chân đế."
    assert lock.success_speech.startswith("Chắc rồi! Cuối cùng")
    assert recipe.steps[-1].complete_speech == "Tuyệt vời! Xong rồi! Giá đỡ đã sẵn sàng."
    assert recipe.steps[1].tip == "Đẩy đến khi nghe tiếng tách là khớp."
    assert recipe.steps[2].warning.startswith("Đừng")
    assert recipe.summary == GOOD["summary"]
    assert recipe.cover_image == "frames/f09.jpg"


def test_ai_partially_broken_output_is_repaired(tmp_path: Path):
    broken = {
        "steps": [
            {"instruction": "đặt chân đế lên bàn", "frame": 99, "check": {"type": "flag"}},
            {"task": "Gắn thanh", "frame": -5, "say": "", "praise": None, "tip": "null"},
            {
                "instruction": "Gạt khóa",
                "frame": "abc",
                "check": {"type": "state", "prompt": "lock?", "options": ["a"], "expected": "b"},
            },
            "garbage-step",
            {"say": "Đặt điện thoại vào kẹp nhé", "frame": 2.0, "check": "nope"},
            {"tip": "no instruction -> dropped"},
        ]
    }
    provider = FakeProvider([broken])
    out = run(compile_skill(make_request(), provider=provider, model="m", skills_root=tmp_path))
    skill = out["skill"]
    recipe = assert_valid_chain(skill)
    assert skill["compiled_by"] == "ai"
    assert len(recipe.steps) == 4
    assert recipe.steps[0].instruction == "Đặt chân đế lên bàn"
    assert recipe.steps[1].instruction == "Gắn thanh"
    assert recipe.steps[1].tip is None
    # invalid state check degrades to a flag check
    assert recipe.steps[2].evaluation_mode == "momentary_true_complete"
    # frames clamped into range and kept non-decreasing
    idx = [int(s.reference_image[8:10]) for s in recipe.steps]
    assert all(0 <= i <= 9 for i in idx)
    assert idx == sorted(idx)
    assert idx[0] == 9
    # defaults filled
    assert all(s.on_enter_speech for s in recipe.steps)
    assert recipe.steps[1].on_enter_speech == "Bây giờ bạn gắn thanh nhé."
    assert recipe.summary and "hoàn thành" in recipe.steps[-1].complete_speech
    for key, det in recipe.detectors.items():
        assert det.prompt.startswith("Return JSON")


def test_ai_garbage_retries_then_falls_back_to_mock(tmp_path: Path):
    provider = FakeProvider(["not json at all", {"foo": "bar"}])
    out = run(compile_skill(make_request(), provider=provider, model="m", skills_root=tmp_path))
    assert len(provider.calls) == 2
    assert "could not be parsed" in provider.calls[1]["system"]
    assert out["skill"]["compiled_by"] == "mock"
    assert_valid_chain(out["skill"])


def test_ai_exception_then_success_on_retry(tmp_path: Path):
    fenced = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    provider = FakeProvider([RuntimeError("boom"), fenced])
    out = run(compile_skill(make_request(), provider=provider, model="m", skills_root=tmp_path))
    assert out["skill"]["compiled_by"] == "ai"
    assert len(out["skill"]["steps"]) == 4


def test_ai_too_many_steps_truncated(tmp_path: Path):
    many = {"steps": [dict(GOOD["steps"][0], instruction=f"Bước {i}") for i in range(12)]}
    out = run(compile_skill(make_request(), provider=FakeProvider([many]), model="m", skills_root=tmp_path))
    assert len(assert_valid_chain(out["skill"]).steps) == 8


# --------------------------------------------------------------------------- sample skill


def test_sample_skill_validates():
    path = BACKEND / "skills" / "lap-gia-do-dien-thoai.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    recipe = assert_valid_chain(data)
    assert recipe.id == path.stem
    assert recipe.display_name == "Lắp giá đỡ điện thoại"
    assert recipe.learned_from == "Tuấn"
    assert len(recipe.steps) == 5
    assert sum(s.evaluation_mode == "match_value" for s in recipe.steps) == 1
    assert any(s.tip for s in recipe.steps) and any(s.warning for s in recipe.steps)

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Must be set before importing backend modules.
os.environ["SHADOW_MODE"] = "mock"
os.environ["VISION_MIN_INTERVAL_MS"] = "0"
os.environ["SHADOW_MOCK_LATENCY_MS"] = "20"
# Pin config so a developer's .env (loaded by main.py via python-dotenv, which never
# overrides existing variables) cannot leak a real key/model into the test run.
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_BASE_URL"] = "https://openrouter.ai/api/v1"
os.environ["VISION_MODEL"] = "google/gemini-2.5-flash"
os.environ["COMPILER_MODEL"] = "google/gemini-2.5-flash"
os.environ["LLM_EXTRA_BODY"] = ""
os.environ["SHADOW_INTENT_PROVIDER"] = "local"
os.environ["SHADOW_VOICE_PROVIDER"] = ""
os.environ["HYPERTEK_API_KEY"] = ""
os.environ["VIDU_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["GEMINI_LIVE_ENABLED"] = "off"
# Legacy tests were written for single-frame completion; test_confirm_frames.py covers 2.
os.environ["COMPLETE_CONFIRM_FRAMES"] = "1"
# Per-IP rate limits off for functional tests (tests/test_security.py turns them on).
os.environ["SHADOW_RATE_LIMIT"] = "off"
os.environ["SHADOW_ACCESS_CODE"] = ""
os.environ["SHADOW_TEACH_VERIFY"] = "off"  # tests/test_skill_verify.py turns the teach self-check on

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"
FRAME_DATA_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//"


def make_test_skill(skill_id: str = "test-skill") -> dict:
    """Tiny 3-step Vietnamese skill used only by tests."""
    return {
        "id": skill_id,
        "display_name": "Pha trà thử nghiệm",
        "start_step_id": "s1",
        "language": "vi",
        "learned_from": "Cô Lan",
        "summary": "Kỹ năng mẫu cho test.",
        "created_at": "2026-11-09T00:00:00Z",
        "tasks": [
            {"id": "t1", "text": "Chuẩn bị ấm"},
            {"id": "t2", "text": "Mở nắp"},
            {"id": "t3", "text": "Rót nước"},
        ],
        "detectors": {
            "kettle_held": {"field": "flag", "prompt": "Is the kettle held? Return `flag`."},
            "lid_state": {"field": "state", "prompt": "Lid state: open|closed|unknown. Return `state`."},
            "pour_state": {"field": "state", "prompt": "Pour state: none|half|done. Return `state`."},
        },
        "steps": [
            {
                "id": "s1",
                "task_id": "t1",
                "evaluation_mode": "momentary_true_complete",
                "detector_key": "kettle_held",
                "complete_on": True,
                "on_enter_speech": "Cầm ấm lên nhé.",
                "instruction": "Cầm ấm lên",
                "tip": "Cầm bằng quai",
                "reference_image": "frames/f01.jpg",
                "next_step_id": "s2",
            },
            {
                "id": "s2",
                "task_id": "t2",
                "evaluation_mode": "match_value",
                "detector_key": "lid_state",
                "expected_value": "open",
                "ignored_values": ["unknown"],
                "on_enter_speech": "Giờ mở nắp ấm.",
                "instruction": "Mở nắp ấm",
                "warning": "Cẩn thận hơi nóng",
                "success_speech": "Đúng rồi! Giờ rót nước vào cốc.",
                "reference_image": "frames/f02.jpg",
                "next_step_id": "s3",
            },
            {
                "id": "s3",
                "task_id": "t3",
                "evaluation_mode": "enum_progress_once_then_complete",
                "detector_key": "pour_state",
                "progress_value": "half",
                "complete_value": "done",
                "ignored_values": ["unknown"],
                "progress_once_speech": "Rót tiếp đi.",
                "complete_speech": "Xong rồi, tuyệt vời!",
                "instruction": "Rót nước vào cốc",
                "next_step_id": None,
            },
        ],
    }


@pytest.fixture
def skills_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "skills"
    (root / "test-skill" / "frames").mkdir(parents=True)
    (root / "test-skill" / "frames" / "f01.jpg").write_bytes(FAKE_JPEG)
    (root / "test-skill" / "frames" / "f02.jpg").write_bytes(FAKE_JPEG)
    (root / "test-skill.json").write_text(
        json.dumps(make_test_skill(), ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("SHADOW_SKILLS_DIR", str(root))
    return root


@pytest.fixture
def client(skills_dir):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as test_client:
        yield test_client

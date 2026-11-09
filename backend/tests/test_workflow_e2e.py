from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from conftest import FRAME_DATA_URL


def recv_until(ws, predicate: Callable[[dict], bool], limit: int = 50) -> list[dict]:
    seen: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if predicate(msg):
            return seen
    raise AssertionError(f"predicate not met; got {[m.get('type') for m in seen]}")


def is_type(kind: str) -> Callable[[dict], bool]:
    return lambda m: m.get("type") == kind


def send_frame(ws) -> list[dict]:
    ws.send_json({"type": "vision.frame", "image": FRAME_DATA_URL, "ts": 1, "trigger": "interval"})
    return recv_until(ws, is_type("vision.result"))


def speeches(msgs: list[dict]) -> list[tuple[str, str]]:
    return [(m["kind"], m["text"]) for m in msgs if m.get("type") == "speech"]


def last_hud(msgs: list[dict]) -> dict:
    huds = [m for m in msgs if m.get("type") == "hud.state"]
    assert huds, "expected a hud.state"
    return huds[-1]


def test_full_skill_with_mock_correction(client):
    with client.websocket_connect("/session/control") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "session.ready" and ready["session_id"]
        initial = ws.receive_json()
        assert initial["type"] == "hud.state"
        assert initial["phase"] == "WAITING_FOR_START" and initial["screen"] == "start"
        assert initial["step"] is None and initial["skill_id"] is None

        ws.send_json({"type": "session.start", "skill_id": "test-skill"})
        msgs = recv_until(ws, is_type("speech"))
        assert speeches(msgs) == [("enter", "Cầm ấm lên nhé.")]
        hud = last_hud(msgs)
        assert hud["phase"] == "GUIDING" and hud["screen"] == "running"
        assert hud["skill_id"] == "test-skill"
        assert hud["recipe_name"] == "Pha trà thử nghiệm"
        assert hud["active_task_id"] == "t1"
        assert hud["step"] == {
            "id": "s1",
            "index": 0,
            "total": 3,
            "instruction": "Cầm ấm lên",
            "tip": "Cầm bằng quai",
            "warning": None,
            "reference_image_url": "/skills-media/test-skill/frames/f01.jpg",
        }
        assert set(hud) == {
            "type", "screen", "phase", "recipe_name", "skill_id", "tasks",
            "active_task_id", "step", "speech_epoch",
        }
        assert [t["completed"] for t in hud["tasks"]] == [False, False, False]

        # Step 1: two "not done" frames, then complete.
        for _ in range(2):
            msgs = send_frame(ws)
            result = msgs[-1]
            assert result["detector"] == "kettle_held"
            assert result["result"] == {"flag": False}
            assert isinstance(result["latency_ms"], int) and result["latency_ms"] >= 15
            assert speeches(msgs) == []
        msgs = send_frame(ws)
        assert msgs[-1]["result"] == {"flag": True}
        assert speeches(msgs) == [("enter", "Giờ mở nắp ấm.")]
        hud = last_hud(msgs)
        assert hud["step"]["id"] == "s2" and hud["step"]["index"] == 1
        assert hud["step"]["warning"] == "Cẩn thận hơi nóng"
        assert hud["tasks"][0]["completed"] is True

        # Step 2 (index 1): first result is wrong with a hint -> spoken correction.
        msgs = send_frame(ws)
        assert msgs[-1]["result"] == {"state": "khác", "hint": "Chưa đúng rồi, xem lại ảnh mẫu nhé."}
        assert speeches(msgs) == [("hint", "Chưa đúng rồi, xem lại ảnh mẫu nhé.")]
        msgs = send_frame(ws)
        assert msgs[-1]["result"] == {"state": "unknown"}
        assert speeches(msgs) == []
        msgs = send_frame(ws)
        assert msgs[-1]["result"] == {"state": "open"}
        # success_speech replaces on_enter speech for the next step (GlassKit behaviour)
        assert speeches(msgs) == [("success", "Đúng rồi! Giờ rót nước vào cốc.")]
        assert last_hud(msgs)["step"]["id"] == "s3"
        assert last_hud(msgs)["step"]["reference_image_url"] is None

        # Step 3: enum progress then complete.
        msgs = send_frame(ws)
        assert speeches(msgs) == [("progress", "Rót tiếp đi.")]
        msgs = send_frame(ws)
        assert speeches(msgs) == []
        msgs = send_frame(ws)
        assert speeches(msgs) == [("complete", "Xong rồi, tuyệt vời!")]
        hud = last_hud(msgs)
        assert hud["phase"] == "COMPLETED"
        assert hud["step"] is None
        assert all(t["completed"] for t in hud["tasks"])

        # Epochs are strictly increasing.
        epochs = [m["epoch"] for m in msgs if m.get("type") == "speech"]
        assert epochs and epochs[-1] == hud["speech_epoch"]

        # Frames after completion are ignored; restart works.
        ws.send_json({"type": "session.start", "skill_id": "test-skill"})
        msgs = recv_until(ws, is_type("speech"))
        assert last_hud(msgs)["step"]["id"] == "s1"
        assert msgs[-1]["epoch"] > epochs[-1]


def test_debug_step_and_stop(client):
    with client.websocket_connect("/session/control") as ws:
        recv_until(ws, is_type("hud.state"))
        ws.send_json({"type": "session.start", "skill_id": "test-skill"})
        recv_until(ws, is_type("speech"))
        ws.send_json({"type": "debug.step", "direction": "forward"})
        msgs = recv_until(ws, is_type("speech"))
        assert last_hud(msgs)["step"]["id"] == "s2"
        ws.send_json({"type": "debug.step", "direction": "backward"})
        msgs = recv_until(ws, is_type("speech"))
        assert last_hud(msgs)["step"]["id"] == "s1"
        ws.send_json({"type": "session.stop"})
        msgs = recv_until(ws, is_type("hud.state"))
        assert msgs[-1]["phase"] == "WAITING_FOR_START" and msgs[-1]["step"] is None


def test_unknown_skill_reports_vietnamese_error(client):
    with client.websocket_connect("/session/control") as ws:
        recv_until(ws, is_type("hud.state"))
        ws.send_json({"type": "session.start", "skill_id": "khong-ton-tai"})
        msgs = recv_until(ws, is_type("hud.state"))
        errors = [m for m in msgs if m["type"] == "hud.error"]
        assert errors and "Không tìm thấy kỹ năng" in errors[0]["message"]
        assert msgs[-1]["phase"] == "ERROR"


def test_client_cannot_inject_internal_events(client):
    with client.websocket_connect("/session/control") as ws:
        recv_until(ws, is_type("hud.state"))
        ws.send_json({"type": "session.start", "skill_id": "test-skill"})
        recv_until(ws, is_type("speech"))
        ws.send_json({"type": "vision.result", "generation": 1, "step_id": "s1",
                      "result": {"flag": True}})
        ws.send_json({"type": "debug.step", "direction": "forward"})
        msgs = recv_until(ws, is_type("speech"))
        # Had the fake result been accepted we'd be on s3 now.
        assert last_hud(msgs)["step"]["id"] == "s2"


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_frames_coalesce_to_latest_while_busy(skills_dir, monkeypatch):
    monkeypatch.setenv("SHADOW_MOCK_LATENCY_MS", "150")
    from providers import MockProvider
    from session_manager import ShadowSessionManager
    from skill_store import SkillStore

    manager = ShadowSessionManager(provider=MockProvider(), skill_store=SkillStore(skills_dir))
    fake = FakeWebSocket()
    sid = await manager.create_control_session(fake)
    await manager.handle_control_message(sid, {"type": "session.start", "skill_id": "test-skill"})
    for _ in range(50):
        if any(m.get("type") == "speech" for m in fake.sent):
            break
        await asyncio.sleep(0.01)
    for i in range(5):
        await manager.handle_control_message(
            sid, {"type": "vision.frame", "image": FRAME_DATA_URL, "ts": i}
        )
        if i == 0:
            await asyncio.sleep(0.02)  # let the worker pick up frame 0
    await asyncio.sleep(0.6)
    results = [m for m in fake.sent if m.get("type") == "vision.result"]
    # first frame processed immediately, frames 2-5 coalesce into one -> 2 detections
    assert len(results) == 2
    session = manager._sessions[sid]
    assert session.mock_frame_counts == {"s1": 2}
    await manager.close()


@pytest.mark.asyncio
async def test_min_interval_is_enforced(skills_dir, monkeypatch):
    monkeypatch.setenv("SHADOW_MOCK_LATENCY_MS", "0")
    monkeypatch.setenv("VISION_MIN_INTERVAL_MS", "300")
    from providers import MockProvider
    from session_manager import ShadowSessionManager
    from skill_store import SkillStore

    manager = ShadowSessionManager(provider=MockProvider(), skill_store=SkillStore(skills_dir))
    fake = FakeWebSocket()
    sid = await manager.create_control_session(fake)
    await manager.handle_control_message(sid, {"type": "session.start", "skill_id": "test-skill"})
    await asyncio.sleep(0.05)
    await manager.handle_control_message(sid, {"type": "vision.frame", "image": FRAME_DATA_URL})
    await asyncio.sleep(0.05)
    await manager.handle_control_message(sid, {"type": "vision.frame", "image": FRAME_DATA_URL})
    await asyncio.sleep(0.1)
    assert len([m for m in fake.sent if m.get("type") == "vision.result"]) == 1
    await asyncio.sleep(0.3)
    assert len([m for m in fake.sent if m.get("type") == "vision.result"]) == 2
    await manager.close()


class ScriptedProvider:
    """AI-mode stand-in: returns scripted dicts and records prompts."""

    mode = "ai"

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        self.calls.append({"model": model, "system": system, "content": content})
        return self.replies.pop(0)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_ai_mode_prompt_includes_reference_and_speaks_hint(skills_dir):
    from session_manager import ShadowSessionManager
    from skill_store import SkillStore

    provider = ScriptedProvider([
        {"flag": False, "hint": "Cầm vào quai ấm nhé."},
        {"flag": False, "hint": "Cầm vào quai ấm nhé."},  # same hint -> not repeated
        {"flag": True},
    ])
    manager = ShadowSessionManager(provider=provider, skill_store=SkillStore(skills_dir))
    fake = FakeWebSocket()
    sid = await manager.create_control_session(fake)
    await manager.handle_control_message(sid, {"type": "session.start", "skill_id": "test-skill"})
    await asyncio.sleep(0.05)
    for _ in range(3):
        before = len([m for m in fake.sent if m.get("type") == "vision.result"])
        await manager.handle_control_message(sid, {"type": "vision.frame", "image": FRAME_DATA_URL})
        for _ in range(100):
            if len([m for m in fake.sent if m.get("type") == "vision.result"]) > before:
                break
            await asyncio.sleep(0.01)

    first = provider.calls[0]
    assert first["model"] == "google/gemini-2.5-flash"
    texts = [p["text"] for p in first["content"] if p["type"] == "text"]
    images = [p["image_url"]["url"] for p in first["content"] if p["type"] == "image_url"]
    assert "Is the kettle held?" in texts[0] and "`flag`" in texts[0] and "`hint`" in texts[0]
    assert "Cầm ấm lên" in texts[0]
    assert images[0] == FRAME_DATA_URL
    assert len(images) == 2 and images[1].startswith("data:image/jpeg;base64,")
    assert any("ảnh mẫu của người dạy khi hoàn thành bước này" in t.lower() for t in texts)

    spoken = [(m["kind"], m["text"]) for m in fake.sent if m.get("type") == "speech"]
    assert spoken == [
        ("enter", "Cầm ấm lên nhé."),
        ("hint", "Cầm vào quai ấm nhé."),
        ("enter", "Giờ mở nắp ấm."),
    ]
    await manager.close()

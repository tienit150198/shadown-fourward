"""A step only completes after COMPLETE_CONFIRM_FRAMES consecutive 'done' frames."""

from __future__ import annotations

import pytest

import session_workflow
from test_workflow_e2e import is_type, last_hud, recv_until, send_frame


@pytest.fixture
def two_frames(monkeypatch):
    monkeypatch.setattr(session_workflow, "COMPLETE_CONFIRM_FRAMES", 2)


def _start(ws) -> None:
    recv_until(ws, is_type("hud.state"))  # session.ready + initial hud
    ws.send_json({"type": "session.start", "skill_id": "test-skill"})
    recv_until(ws, is_type("speech"))


def test_single_done_frame_does_not_advance(client, two_frames):
    with client.websocket_connect("/session/control") as ws:
        _start(ws)
        # mock: frames 0,1 = not done; frame 2 = first "done" → must NOT advance yet
        for _ in range(3):
            msgs = send_frame(ws)
        assert not [m for m in msgs if m.get("type") == "hud.state"
                    and m["step"] and m["step"]["id"] != "s1"]
        # frame 3 = second consecutive "done" → advances to s2
        msgs = send_frame(ws)
        assert last_hud(msgs)["step"]["id"] == "s2"


def test_done_streak_resets_on_not_done(client, two_frames, monkeypatch):
    import vision_runtime

    pattern = iter([True, False, True, True])

    def fake(step, detector, *, step_index, frame_number):
        return {detector.field: next(pattern)}

    monkeypatch.setattr(vision_runtime, "mock_detection", fake)
    with client.websocket_connect("/session/control") as ws:
        _start(ws)
        moved_at = None
        for i in range(4):
            msgs = send_frame(ws)
            huds = [m for m in msgs if m.get("type") == "hud.state" and m["step"]]
            if huds and huds[-1]["step"]["id"] == "s2" and moved_at is None:
                moved_at = i
        assert moved_at == 3  # True,False resets; needs True,True at frames 2–3

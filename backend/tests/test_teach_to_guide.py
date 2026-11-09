"""Integration: POST /api/teach/compile (mock) -> PUT /api/skills/{id} -> guide session."""

from __future__ import annotations

import base64

import pytest

from test_workflow_e2e import is_type, last_hud, recv_until, send_frame, speeches

pytest.importorskip("skill_compiler")

JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x00" * 64 + b"\xff\xd9"
JPEG_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()


def test_compile_save_and_guide(client, skills_dir):
    body = {
        "title": "Gấp áo phông",
        "teacher_name": "Mai",
        "transcript": [
            {"t0": 0, "t1": 3000, "text": "Đầu tiên trải áo phẳng trên bàn"},
            {"t0": 3500, "t1": 7000, "text": "Gấp hai bên tay áo vào giữa lưng áo"},
            {"t0": 7500, "t1": 11000, "text": "Cuối cùng gấp đôi áo từ dưới lên trên"},
        ],
        "frames": [{"t": i * 1500, "image": JPEG_URL} for i in range(8)],
        "notes": "",
    }
    res = client.post("/api/teach/compile", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    skill, draft_id = data["skill"], data["draft_id"]
    assert draft_id and skill["steps"]

    # draft frame preview URLs resolve before saving
    preview = next(s["reference_image_url"] for s in skill["steps"] if s.get("reference_image_url"))
    assert client.get(preview).status_code == 200

    res = client.put(f"/api/skills/{skill['id']}", json={**skill, "draft_id": draft_id})
    assert res.status_code == 200, res.text
    saved = res.json()
    assert not (skills_dir / "_drafts" / draft_id).exists()
    ref_url = next(s["reference_image_url"] for s in saved["steps"] if s.get("reference_image_url"))
    assert ref_url.startswith(f"/skills-media/{saved['id']}/frames/")
    assert client.get(ref_url).status_code == 200

    total = len(saved["steps"])
    with client.websocket_connect("/session/control") as ws:
        recv_until(ws, is_type("hud.state"))
        ws.send_json({"type": "session.start", "skill_id": saved["id"]})
        msgs = recv_until(ws, is_type("speech"))
        assert last_hud(msgs)["step"]["total"] == total
        phase = "GUIDING"
        kinds: list[str] = []
        for _ in range(total * 3 + 2):
            msgs = send_frame(ws)
            kinds += [k for k, _ in speeches(msgs)]
            huds = [m for m in msgs if m.get("type") == "hud.state"]
            if huds and huds[-1]["phase"] == "COMPLETED":
                phase = "COMPLETED"
                break
        assert phase == "COMPLETED"
        assert kinds[-1] == "complete"
        if total >= 2:
            # 2nd step's mock wrong result triggers either a hint or the step's mismatch_speech
            assert "hint" in kinds or "mismatch" in kinds

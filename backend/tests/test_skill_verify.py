"""Teach self-check (skill_verify.py), server keyframe selection, eval-set export and the
teach endpoints — all offline with fake providers."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from skill_compiler import SYSTEM_PROMPT, CompileRequest, compile_skill
from skill_verify import REWRITE_SYSTEM_PROMPT, AUTO_EVAL_PREFIX, label_frames
from teach_keyframes import RawFrame, select_keyframes
from vision_runtime import VISION_SYSTEM_PROMPT


def jpeg_url(i: int) -> str:
    # distinct (undecodable) JPEG payloads: magic bytes are all the compiler checks
    data = b"\xff\xd8\xff\xe0" + bytes([i]) * 40 + b"\xff\xd9"
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


N_FRAMES = 9
FRAME_URLS = [jpeg_url(i) for i in range(N_FRAMES)]

COMPILED = {
    "summary": "Pha trà trong 3 bước.",
    "done_speech": "Xong rồi!",
    "steps": [
        {"instruction": "Đặt cốc lên bàn", "task": "Đặt cốc", "frame": 2,
         "check": {"type": "flag", "prompt": "Return true only if a cup stands on the table."},
         "say": "Bạn đặt cốc lên bàn nhé.", "praise": "Tốt!"},
        {"instruction": "Bỏ túi trà vào cốc", "task": "Bỏ túi trà", "frame": 5,
         "check": {"type": "flag", "prompt": "LOOSE: return true if a cup is visible."},
         "say": "Bạn bỏ túi trà vào cốc nhé.", "praise": "Chuẩn!"},
        {"instruction": "Rót nước sôi", "task": "Rót nước", "frame": 8,
         "check": {"type": "flag", "prompt": "Return true only if the cup is full of tea."},
         "say": "Bạn rót nước sôi nhé.", "praise": "Tuyệt!"},
    ],
}


class FakeAI:
    """Routes by system prompt: compiler → COMPILED, judge → simulated answers, rewrite → new prompt.

    Simulated truth: step k (0-based) is done from frame 3k+2 on. The judge follows the truth,
    except: a "LOOSE" prompt says done for every frame (→ false done on step 2's negatives) and
    step 3 ("Rót nước") always says not done on its reference frame 8 (→ learner stuck).
    """

    mode = "ai"

    def __init__(self, *, rewrite_reply: Any = None, compiled: dict | None = None, vision_delay: float = 0.0) -> None:
        self.compiled = compiled or COMPILED
        self.rewrite_reply = rewrite_reply if rewrite_reply is not None else {"prompt": "STRICT: return true only if a tea bag is inside the cup.", "why": "x"}
        self.vision_delay = vision_delay
        self.calls: list[str] = []
        self.rewrite_contents: list[list[dict]] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        if system == SYSTEM_PROMPT or system.startswith(SYSTEM_PROMPT):
            self.calls.append("compile")
            return json.loads(json.dumps(self.compiled))
        if system == REWRITE_SYSTEM_PROMPT:
            self.calls.append("rewrite")
            self.rewrite_contents.append(content)
            return self.rewrite_reply
        assert system == VISION_SYSTEM_PROMPT
        self.calls.append("judge")
        if self.vision_delay:
            await asyncio.sleep(self.vision_delay)
        text = "\n".join(p["text"] for p in content if p.get("type") == "text")
        image = next(p["image_url"]["url"] for p in content if p.get("type") == "image_url")
        frame = FRAME_URLS.index(image)
        step = next(k for k, s in enumerate(self.compiled["steps"]) if s["instruction"] in text)
        if "LOOSE" in text:
            return {"flag": True}
        if step == 2 and frame >= 8:
            return {"flag": False}
        return {"flag": frame >= 3 * step + 2}

    async def aclose(self) -> None:
        return None


def make_req(**extra: Any) -> CompileRequest:
    body = {
        "title": "Pha trà",
        "teacher_name": "Lan",
        "transcript": [
            {"t0": 0, "t1": 2000, "text": "đặt cốc lên bàn"},
            {"t0": 3000, "t1": 5000, "text": "bỏ túi trà vào cốc"},
            {"t0": 6000, "t1": 8000, "text": "rót nước sôi"},
        ],
        "frames": [{"t": i * 1000, "image": FRAME_URLS[i], "reason": "interval"} for i in range(N_FRAMES)],
        **extra,
    }
    return CompileRequest.model_validate(body)


# --------------------------------------------------------------------------- labels


def test_label_frames_positive_and_negative_sets() -> None:
    labels = label_frames([2, 5, 8], 11)
    assert labels[0] == {"pos": [2, 3, 4], "neg": []}  # step 1: no negatives
    assert labels[1]["pos"] == [5, 6, 7]
    assert labels[1]["neg"][-1] == 2  # previous step's end state = hardest negative
    assert all(i < 5 for i in labels[1]["neg"])
    assert labels[2]["pos"] == [8, 9, 10]
    assert 5 in labels[2]["neg"] and 2 in labels[2]["neg"]
    # two steps sharing one reference frame: the earlier one only gets that frame as positive
    shared = label_frames([3, 3, 6], 8)
    assert shared[0]["pos"] == [3]
    assert shared[1]["neg"] == [0, 2] or all(i < 3 for i in shared[1]["neg"])
    assert label_frames([None, 4], 6)[0] == {"pos": [], "neg": []}


# --------------------------------------------------------------------------- verification flow


def test_compile_runs_self_check_and_rewrites_failing_detector(tmp_path: Path) -> None:
    provider = FakeAI()
    phases: list[str] = []
    out = asyncio.run(compile_skill(
        make_req(), provider=provider, model="compiler", skills_root=tmp_path, verify=True,
        vision_model_name="vision", on_phase=lambda p, **_: phases.append(p),
    ))
    skill = out["skill"]
    v1, v2, v3 = (s["verify"] for s in skill["steps"])
    assert v1["status"] == "ok" and v1["agree"] == v1["checked"] == 3
    # step 2: the LOOSE prompt said "done" on step 1's end state → rewritten once → fixed
    assert v2["status"] == "fixed" and v2["rewritten"] is True
    assert v2["before"]["agree"] < v2["before"]["checked"]
    assert v2["agree"] == v2["checked"]
    prompt = skill["detectors"]["step_2"]["prompt"]
    assert "STRICT" in prompt and prompt.startswith("Return JSON with a single boolean `flag` field.")
    assert 2 in v2["neg"]
    # step 3: positives after the reference judged "not done" → stays weak, plain-Vietnamese reason
    assert v3["status"] == "weak" and v3["missed"]
    assert "không nhận ra" in v3["reason"]
    q = skill["quality"]
    assert q["checked"] == sum(v["checked"] for v in (v1, v2, v3))
    assert 0 < q["score"] < 1 and q["steps_ok"] == 1 and q["steps_fixed"] == 1 and q["steps_weak"] == 1
    assert provider.calls[0] == "compile" and provider.calls.count("rewrite") == 2  # steps 2 and 3
    assert phases[0] == "learning" and "verifying" in phases and phases[-1] == "done"
    # rewrite request carries the failure evidence: frame indexes, truth, judge answer and images
    evidence = "\n".join(p.get("text", "") for p in provider.rewrite_contents[0] if p["type"] == "text")
    assert re.search(r"Frame 2: truth = step NOT done.*judge answered flag=true.*SKIP", evidence)
    assert any(p["type"] == "image_url" for p in provider.rewrite_contents[0])
    # teach metadata + response extras
    meta = json.loads((tmp_path / "_drafts" / out["draft_id"] / "teach.json").read_text())
    assert len(meta["frames"]) == N_FRAMES
    assert len(out["frames"]) == N_FRAMES and out["frames"][0]["url"].startswith("/skills-media/_drafts/")
    assert out["timings"]["verify_s"] is not None


def test_rewrite_garbage_keeps_original_prompt(tmp_path: Path) -> None:
    provider = FakeAI(rewrite_reply={"nope": 1})
    out = asyncio.run(compile_skill(make_req(), provider=provider, model="c", skills_root=tmp_path, verify=True))
    step2 = out["skill"]["steps"][1]
    assert step2["verify"]["status"] == "weak" and step2["verify"]["false_done"]
    assert "bỏ qua bước" in step2["verify"]["reason"]
    assert "LOOSE" in out["skill"]["detectors"]["step_2"]["prompt"]


def test_self_check_respects_time_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOW_VERIFY_BUDGET_S", "0.3")
    provider = FakeAI(vision_delay=1.0)
    out = asyncio.run(compile_skill(make_req(), provider=provider, model="c", skills_root=tmp_path, verify=True))
    statuses = {s["verify"]["status"] for s in out["skill"]["steps"]}
    assert statuses == {"unchecked"}
    assert "rewrite" not in provider.calls


def test_self_check_off_by_default_in_tests_and_never_in_mock(tmp_path: Path) -> None:
    provider = FakeAI()
    out = asyncio.run(compile_skill(make_req(), provider=provider, model="c", skills_root=tmp_path))
    assert provider.calls == ["compile"]  # SHADOW_TEACH_VERIFY=off (conftest)
    assert "quality" not in out["skill"]
    mock = FakeAI()
    mock.mode = "mock"
    out = asyncio.run(compile_skill(make_req(), provider=mock, model="c", skills_root=tmp_path, verify=True))
    assert mock.calls == [] and "quality" not in out["skill"]


# --------------------------------------------------------------------------- keyframes


def _png(color: tuple[int, int, int], box: tuple[int, int, int, int] | None = None) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 48), color)
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(250, 250, 250))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def test_select_keyframes_keeps_speech_scene_and_ends() -> None:
    # 40 raw frames @ 1 s: the scene changes at t=10 s (a white block appears) and at t=25 s
    frames = []
    for i in range(40):
        box = None if i < 10 else ((5, 5, 30, 30) if i < 25 else (30, 10, 60, 40))
        frames.append(RawFrame(t=i * 1000, payload=i, data=_png((40, 80, 120), box)))
    out = select_keyframes(frames, cap=12, speech_ends_ms=[3500, 17200])
    picked = [f.payload for f in out]
    assert len(out) == 12 and picked == sorted(picked)
    assert picked[0] == 0 and picked[-1] == 39
    assert 4 in picked and 18 in picked  # first frame after each speech segment end
    by = {f.payload: f.reason for f in out}
    assert by[4] == "speech" and by[18] == "speech"
    assert by.get(10) == "scene" or by.get(11) == "scene"
    assert by.get(25) == "scene" or by.get(26) == "scene"
    # small inputs are returned whole
    assert len(select_keyframes(frames[:5], cap=24)) == 5


def test_compile_accepts_more_raw_frames_than_cap(tmp_path: Path) -> None:
    req = make_req(frames=[{"t": i * 500, "image": jpeg_url(i % 200)} for i in range(60)])
    out = asyncio.run(compile_skill(req, provider=None, model="x", skills_root=tmp_path))
    assert len(out["frames"]) == 24
    reasons = {f["reason"] for f in out["frames"]}
    assert "speech" in reasons and "start" in reasons


# --------------------------------------------------------------------------- API


def _compile_and_save(client) -> tuple[dict, str]:
    body = {
        "title": "Pha trà",
        "teacher_name": "Lan",
        "transcript": make_req().model_dump()["transcript"],
        "frames": [{"t": i * 1000, "image": FRAME_URLS[i]} for i in range(N_FRAMES)],
        "job_id": "job-abc123",
    }
    res = client.post("/api/teach/compile", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    return data, data["draft_id"]


def test_eval_set_exported_on_save(client, skills_dir, tmp_path, monkeypatch) -> None:
    evals = tmp_path / "evals"
    monkeypatch.setenv("SHADOW_EVALS_DIR", str(evals))
    data, draft_id = _compile_and_save(client)
    skill = data["skill"]
    res = client.put(f"/api/skills/{skill['id']}", json={**skill, "draft_id": draft_id})
    assert res.status_code == 200, res.text
    assert (skills_dir / skill["id"] / "teach.json").is_file()
    autos = sorted(evals.glob(f"{skill['id']}/*/*/{AUTO_EVAL_PREFIX}*"))
    assert autos, "auto-labelled eval frames expected"
    counts = client.get(f"/api/eval/{skill['id']}").json()["counts"]
    first = skill["steps"][0]["id"]
    assert counts[first]["done"] >= 1
    assert any(c["not_done"] >= 1 for c in counts.values())
    # a phone capture is kept and "undo last" never deletes an auto frame
    client.post("/api/eval/capture", json={"skill_id": skill["id"], "step_id": first, "label": "done", "image": FRAME_URLS[0]})
    res = client.delete(f"/api/eval/{skill['id']}/{first}/done/last")
    assert res.json()["removed"] is True
    assert sorted(evals.glob(f"{skill['id']}/*/*/{AUTO_EVAL_PREFIX}*")) == autos
    # re-saving replaces the auto set instead of duplicating it
    client.put(f"/api/skills/{skill['id']}", json=client.get(f"/api/skills/{skill['id']}").json())
    assert len(sorted(evals.glob(f"{skill['id']}/*/*/{AUTO_EVAL_PREFIX}*"))) == len(autos)
    # frames listing for a saved skill
    frames = client.get("/api/teach/frames", params={"skill_id": skill["id"]}).json()["frames"]
    assert len(frames) == N_FRAMES and frames[0]["url"] == f"/skills-media/{skill['id']}/frames/f00.jpg"
    assert client.get(frames[0]["url"]).status_code == 200


def test_progress_endpoint_unknown_after_completion(client) -> None:
    _compile_and_save(client)
    assert client.get("/api/teach/progress/job-abc123").json() == {"phase": "unknown"}


def test_verify_step_mock_mode_changes_reference_frame(client) -> None:
    data, draft_id = _compile_and_save(client)
    res = client.post("/api/teach/verify-step", json={
        "draft_id": draft_id, "skill": data["skill"], "step": "step_1", "reference_frame": 3,
    })
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["verify"]["status"] == "unchecked"
    assert out["step"]["reference_image"] == "frames/f03.jpg"
    assert out["step"]["reference_image_url"] == f"/skills-media/_drafts/{draft_id}/frames/f03.jpg"
    bad = client.post("/api/teach/verify-step", json={"draft_id": draft_id, "skill": data["skill"], "step": 99})
    assert bad.status_code == 404
    bad = client.post("/api/teach/verify-step", json={"draft_id": draft_id, "skill": data["skill"], "step": 0, "reference_frame": 50})
    assert bad.status_code == 422
    assert client.post("/api/teach/verify-step", json={"step": 0}).status_code == 422


def test_verify_step_ai_rewrites_and_saves(client, skills_dir, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SHADOW_EVALS_DIR", str(tmp_path / "evals"))
    client.app.state.provider = FakeAI()  # AI compile (self-check off in tests): refs f02/f05/f08
    data, draft_id = _compile_and_save(client)
    skill = data["skill"]
    assert data["compiled_by"] == "ai" and len(skill["steps"]) == 3
    res = client.put(f"/api/skills/{skill['id']}", json={**skill, "draft_id": draft_id})
    assert res.status_code == 200, res.text

    fake = FakeAI()
    client.app.state.provider = fake
    res = client.post("/api/teach/verify-step", json={"skill_id": skill["id"], "step": "step_2", "save": True})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["verify"]["status"] == "fixed" and out["saved"] is True
    assert "STRICT" in out["detector"]["prompt"]
    assert fake.calls.count("rewrite") == 1
    saved = client.get(f"/api/skills/{skill['id']}").json()
    assert saved["steps"][1]["verify"]["status"] == "fixed"
    assert "STRICT" in saved["detectors"]["step_2"]["prompt"]
    assert saved["quality"]["checked"] >= 1

    # no rewrite requested → only judge calls
    fake2 = FakeAI()
    client.app.state.provider = fake2
    res = client.post("/api/teach/verify-step", json={"skill_id": skill["id"], "step": 2, "rewrite": False, "reference_frame": 8})
    assert res.status_code == 200
    assert "rewrite" not in fake2.calls and res.json()["verify"]["status"] in {"ok", "weak"}

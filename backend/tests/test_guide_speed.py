"""Guide speed/accuracy mechanisms: fast confirmation, image budget, reference-image policy,
logprob decision (+ JSON fallback, lazy hints) and the eval config matrix. Offline only."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

import eval_detectors
import session_workflow
import vision_runtime
from conftest import FRAME_DATA_URL
from providers import (
    LogprobsUnsupported,
    OpenAICompatibleProvider,
    aggregate_choice_probs,
    vision_image_max_side,
    vision_reference_mode,
)
from recipe_catalog import RecipeDetector, RecipeStep
from session_types import StepRuntimeState
from test_workflow_e2e import FakeWebSocket


def jpeg_data_url(w: int, h: int) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 120, 40)).save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def data_url_size(url: str) -> tuple[int, int]:
    raw = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as img:
        return img.size


class FakeAI:
    """AI-mode provider stand-in with both JSON and logprob calls."""

    mode = "ai"

    def __init__(self, json_replies=None, probs=None, logprobs: bool = True) -> None:
        self.json_replies = list(json_replies or [])
        self.probs = list(probs or [])
        self.logprobs = logprobs
        self.json_calls: list[dict] = []
        self.prob_calls: list[dict] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        self.json_calls.append({"content": content, "max_tokens": max_tokens, "system": system})
        return self.json_replies.pop(0) if self.json_replies else {"flag": False}

    async def chat_choice_probs(self, *, model, system, content, choices, top_logprobs=10):
        if not self.logprobs:
            raise LogprobsUnsupported("gateway has no logprobs")
        self.prob_calls.append({"content": content, "choices": choices})
        return self.probs.pop(0) if self.probs else {"yes": 0.02, "no": 0.98}

    async def aclose(self) -> None:
        return None


def n_images(content: list[dict]) -> int:
    return sum(1 for part in content if part.get("type") == "image_url")


async def _manager(skills_dir, provider):
    from session_manager import ShadowSessionManager
    from skill_store import SkillStore

    manager = ShadowSessionManager(provider=provider, skill_store=SkillStore(skills_dir))
    fake = FakeWebSocket()
    sid = await manager.create_control_session(fake)
    await manager.handle_control_message(sid, {"type": "session.start", "skill_id": "test-skill"})
    for _ in range(100):
        if any(m.get("type") == "speech" for m in fake.sent):
            break
        await asyncio.sleep(0.01)
    return manager, fake, sid


def _results(fake) -> list[dict]:
    return [m for m in fake.sent if m.get("type") == "vision.result"]


async def _frame(manager, sid, fake, image: str = FRAME_DATA_URL, timeout: float = 3.0) -> tuple[dict, float]:
    before = len(_results(fake))
    started = time.perf_counter()
    await manager.handle_control_message(sid, {"type": "vision.frame", "image": image})
    deadline = started + timeout
    while len(_results(fake)) <= before:
        assert time.perf_counter() < deadline, "no vision.result"
        await asyncio.sleep(0.005)
    return _results(fake)[-1], time.perf_counter() - started


def _step_ids(fake) -> list[str]:
    return [m["step"]["id"] for m in fake.sent if m.get("type") == "hud.state" and m.get("step")]


def _speeches(fake) -> list[tuple[str, str]]:
    return [(m["kind"], m["text"]) for m in fake.sent if m.get("type") == "speech"]


# ---- 1. fast confirmation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_confirm_skips_min_interval_once(skills_dir, monkeypatch):
    from providers import MockProvider

    monkeypatch.setattr(session_workflow, "COMPLETE_CONFIRM_FRAMES", 2)
    monkeypatch.setenv("SHADOW_MOCK_LATENCY_MS", "0")
    monkeypatch.setenv("VISION_MIN_INTERVAL_MS", "400")
    monkeypatch.delenv("VISION_FAST_CONFIRM", raising=False)
    manager, fake, sid = await _manager(skills_dir, MockProvider())
    # mock: frames 0,1 = not done; 2 = first 'done'; 3 = confirmation
    r0, _ = await _frame(manager, sid, fake)
    r1, t1 = await _frame(manager, sid, fake)
    r2, t2 = await _frame(manager, sid, fake)
    assert [r0["confirming"], r1["confirming"], r2["confirming"]] == [False, False, True]
    assert t1 >= 0.3 and t2 >= 0.3  # normal gap enforced
    assert manager._sessions[sid].fast_confirm is True
    r3, t3 = await _frame(manager, sid, fake)
    assert t3 < 0.2  # confirmation frame judged immediately
    assert r3["confirming"] is False
    assert _step_ids(fake)[-1] == "s2"
    assert manager._sessions[sid].fast_confirm is False
    # back to the normal gap on the new step
    _, t4 = await _frame(manager, sid, fake)
    assert t4 >= 0.3
    await manager.close()


@pytest.mark.asyncio
async def test_fast_confirm_can_be_disabled(skills_dir, monkeypatch):
    from providers import MockProvider

    monkeypatch.setattr(session_workflow, "COMPLETE_CONFIRM_FRAMES", 2)
    monkeypatch.setenv("SHADOW_MOCK_LATENCY_MS", "0")
    monkeypatch.setenv("VISION_MIN_INTERVAL_MS", "300")
    monkeypatch.setenv("VISION_FAST_CONFIRM", "off")
    manager, fake, sid = await _manager(skills_dir, MockProvider())
    for _ in range(3):
        r, _ = await _frame(manager, sid, fake)
    assert r["confirming"] is False
    _, t3 = await _frame(manager, sid, fake)
    assert t3 >= 0.25
    assert _step_ids(fake)[-1] == "s2"
    await manager.close()


@pytest.mark.asyncio
async def test_not_done_after_done_cancels_fast_confirm(skills_dir, monkeypatch):
    monkeypatch.setattr(session_workflow, "COMPLETE_CONFIRM_FRAMES", 2)
    monkeypatch.setenv("VISION_MIN_INTERVAL_MS", "0")
    provider = FakeAI(json_replies=[{"flag": True}, {"flag": False}])
    manager, fake, sid = await _manager(skills_dir, provider)
    r0, _ = await _frame(manager, sid, fake)
    assert r0["confirming"] is True
    r1, _ = await _frame(manager, sid, fake)
    assert r1["confirming"] is False and manager._sessions[sid].fast_confirm is False
    assert _step_ids(fake)[-1] == "s1"
    await manager.close()


# ---- 2. image budget ----------------------------------------------------------------


def test_downscale_data_url():
    big = jpeg_data_url(1024, 768)
    small = vision_runtime.downscale_data_url(big, 448)
    assert data_url_size(small) == (448, 336)
    assert len(small) < len(big)
    already = jpeg_data_url(400, 300)
    assert vision_runtime.downscale_data_url(already, 448) is already
    assert vision_runtime.downscale_data_url(FRAME_DATA_URL, 448) == FRAME_DATA_URL  # undecodable
    assert vision_runtime.downscale_data_url("not a data url", 448) == "not a data url"


def test_image_side_env(monkeypatch):
    monkeypatch.delenv("VISION_IMAGE_MAX_SIDE", raising=False)
    assert vision_image_max_side() == 448
    monkeypatch.setenv("VISION_IMAGE_MAX_SIDE", "2000")
    assert vision_image_max_side() == 512  # phone never sends more
    monkeypatch.setenv("VISION_IMAGE_MAX_SIDE", "384")
    assert vision_image_max_side() == 384


@pytest.mark.asyncio
async def test_frames_are_downscaled_and_side_announced(skills_dir, monkeypatch):
    monkeypatch.setenv("VISION_IMAGE_MAX_SIDE", "384")
    monkeypatch.setenv("VISION_REFERENCE_MODE", "never")
    provider = FakeAI(json_replies=[{"flag": False}])
    manager, fake, sid = await _manager(skills_dir, provider)
    ready = next(m for m in fake.sent if m.get("type") == "session.ready")
    assert ready["vision"] == {"image_max_side": 384}
    await _frame(manager, sid, fake, image=jpeg_data_url(1024, 768))
    sent = [p["image_url"]["url"] for p in provider.json_calls[0]["content"] if p["type"] == "image_url"]
    assert len(sent) == 1 and max(data_url_size(sent[0])) == 384
    await manager.close()


# ---- reference image policy --------------------------------------------------------


def test_reference_policy():
    fresh = StepRuntimeState()
    later = StepRuntimeState(detections_started=3)
    after_hint = StepRuntimeState(detections_started=3, last_result_hint=True)
    include = vision_runtime.should_include_reference
    assert [include("always", s) for s in (fresh, later, after_hint)] == [True, True, True]
    assert [include("never", s) for s in (fresh, later, after_hint)] == [False, False, False]
    assert [include("first", s) for s in (fresh, later, after_hint)] == [True, False, False]
    assert [include("auto", s) for s in (fresh, later, after_hint)] == [True, False, True]


def test_reference_mode_env(monkeypatch):
    monkeypatch.setenv("VISION_REFERENCE_MODE", "bogus")
    assert vision_reference_mode() == "auto"
    monkeypatch.setenv("VISION_REFERENCE_MODE", "NEVER")
    assert vision_reference_mode() == "never"


@pytest.mark.asyncio
async def test_auto_reference_first_frame_and_after_hint(skills_dir, monkeypatch):
    monkeypatch.setenv("VISION_REFERENCE_MODE", "auto")
    provider = FakeAI(json_replies=[
        {"flag": False},
        {"flag": False, "hint": "Cầm vào quai ấm nhé."},
        {"flag": False},
        {"flag": False},
    ])
    manager, fake, sid = await _manager(skills_dir, provider)
    for _ in range(4):
        await _frame(manager, sid, fake)
    assert [n_images(c["content"]) for c in provider.json_calls] == [2, 1, 2, 1]
    await manager.close()


@pytest.mark.asyncio
async def test_reference_always_and_new_step_resets(skills_dir, monkeypatch):
    monkeypatch.setenv("VISION_REFERENCE_MODE", "first")
    provider = FakeAI(json_replies=[{"flag": False}, {"flag": True}, {"state": "closed"}])
    manager, fake, sid = await _manager(skills_dir, provider)
    for _ in range(3):
        await _frame(manager, sid, fake)
    # s1: first call has the reference, second not; s2 (new step): first call again
    assert [n_images(c["content"]) for c in provider.json_calls] == [2, 1, 2]
    await manager.close()


# ---- 3. logprob decision ------------------------------------------------------------


def test_aggregate_choice_probs_merges_token_variants():
    top = [
        {"token": "yes", "logprob": -0.5},
        {"token": " Yes", "logprob": -1.5},
        {"token": "YES", "logprob": -3.0},
        {"token": "No", "logprob": -2.0},
        {"token": "Maybe", "logprob": -1.0},
    ]
    probs = aggregate_choice_probs(top, ["yes", "no"])
    import math

    yes = math.exp(-0.5) + math.exp(-1.5) + math.exp(-3.0)
    no = math.exp(-2.0)
    assert probs["yes"] == pytest.approx(yes / (yes + no))
    assert probs["no"] == pytest.approx(no / (yes + no))
    assert aggregate_choice_probs([{"token": "hmm", "logprob": 0.0}], ["yes", "no"]) == {}


@pytest.mark.asyncio
async def test_provider_chat_choice_probs_request_and_unsupported():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Yes"}, "logprobs": {"content": [{
                "token": "Yes", "logprob": -0.1,
                "top_logprobs": [{"token": "Yes", "logprob": -0.1}, {"token": "No", "logprob": -2.4}],
            }]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "yes"}}]})  # no logprobs

    provider = OpenAICompatibleProvider(
        base_url="https://example.com/v1", api_key="k", transport=httpx.MockTransport(handler),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    probs = await provider.chat_choice_probs(model="m", system="s", content=[], choices=["yes", "no"])
    assert probs["yes"] > 0.85 and probs["yes"] + probs["no"] == pytest.approx(1.0)
    assert bodies[0]["max_tokens"] == 1 and bodies[0]["logprobs"] is True and bodies[0]["top_logprobs"] == 10
    assert "response_format" not in bodies[0]
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
    with pytest.raises(LogprobsUnsupported):
        await provider.chat_choice_probs(model="m", system="s", content=[], choices=["yes", "no"])
    await provider.aclose()


def _step(**kw) -> RecipeStep:
    base = {"id": "s", "task_id": "t", "detector_key": "d"}
    base.update(kw)
    return RecipeStep(**base)


def test_logprob_result_flag_threshold():
    det = RecipeDetector(prompt="p", field="flag")
    step = _step(evaluation_mode="momentary_true_complete", complete_on=True)
    assert vision_runtime.logprob_result(step, det, {"yes": 0.95, "no": 0.05}, threshold=0.9) == {
        "flag": True, "confidence": 0.95}
    assert vision_runtime.logprob_result(step, det, {"yes": 0.85, "no": 0.15}, threshold=0.9) == {
        "flag": False, "confidence": 0.85}
    assert vision_runtime.logprob_result(step, det, {}, threshold=0.9) == {"flag": None}
    inverted = _step(evaluation_mode="momentary_true_complete", complete_on=False)
    assert vision_runtime.logprob_result(inverted, det, {"yes": 0.03, "no": 0.97}, threshold=0.9) == {
        "flag": False, "confidence": 0.97}


def test_state_options_and_logprob_result():
    det = RecipeDetector(
        field="state",
        prompt='Return JSON with a single string `state` field, one of: "locked", "loose", "unknown". '
        'Return "locked" if the lever is down. Return "loose" if it is up.',
    )
    step = _step(evaluation_mode="match_value", expected_value="locked", ignored_values=["unknown"])
    options = vision_runtime.state_options(step, det)
    assert options == [("locked", False), ("loose", False), ("unknown", False)]
    rewritten = vision_runtime._rewrite_state_prompt(det.prompt, options)
    assert "JSON" not in rewritten and "answer A if the lever is down" in rewritten
    res = vision_runtime.logprob_result(step, det, {"A": 0.95, "B": 0.04, "C": 0.01}, threshold=0.9, options=options)
    assert res == {"state": "locked", "confidence": 0.95}
    unsure = vision_runtime.logprob_result(step, det, {"A": 0.7, "B": 0.3, "C": 0.0}, threshold=0.9, options=options)
    assert unsure == {"state": "unknown", "confidence": 0.7}  # below threshold → ignored label
    loose = vision_runtime.logprob_result(step, det, {"A": 0.1, "B": 0.9, "C": 0.0}, threshold=0.9, options=options)
    assert loose["state"] == "loose"
    # a pipe-listed prompt without an unknown option gets a synthetic one (→ None)
    det2 = RecipeDetector(field="state", prompt="Lid state: open|closed. Return `state`.")
    step2 = _step(evaluation_mode="match_value", expected_value="open")
    options2 = vision_runtime.state_options(step2, det2)
    assert options2 == [("open", False), ("closed", False), ("unknown", True)]
    assert vision_runtime.logprob_result(step2, det2, {"A": 0, "B": 0, "C": 1}, threshold=0.9, options=options2)[
        "state"] is None


def test_flag_prompt_rewrite():
    text = vision_runtime._rewrite_flag_prompt(
        "Return JSON with a single boolean `flag` field. Return true only if the cup is upright. "
        "If it is tipped, return false."
    )
    assert "JSON" not in text and "answer yes only if" in text and "answer no" in text


@pytest.mark.asyncio
async def test_logprob_mode_needs_threshold_to_complete(skills_dir, monkeypatch):
    monkeypatch.setattr(session_workflow, "COMPLETE_CONFIRM_FRAMES", 1)
    monkeypatch.setenv("VISION_DECISION", "logprob")
    monkeypatch.setenv("VISION_DONE_THRESHOLD", "0.9")
    provider = FakeAI(probs=[{"yes": 0.8, "no": 0.2}, {"yes": 0.97, "no": 0.03}])
    manager, fake, sid = await _manager(skills_dir, provider)
    r0, _ = await _frame(manager, sid, fake)
    assert r0["result"] == {"flag": False, "confidence": 0.8}
    assert _step_ids(fake)[-1] == "s1"
    r1, _ = await _frame(manager, sid, fake)
    assert r1["result"] == {"flag": True, "confidence": 0.97}
    assert _step_ids(fake)[-1] == "s2"
    assert provider.json_calls == []
    call = provider.prob_calls[0]
    assert call["choices"] == ["yes", "no"]
    assert "yes or no" in call["content"][0]["text"]
    # s2 is a state detector → letter question
    provider.probs.append({"A": 0.95, "B": 0.05})
    r2, _ = await _frame(manager, sid, fake)
    assert provider.prob_calls[-1]["choices"][0] == "A" and r2["result"]["state"] == "open"
    await manager.close()


@pytest.mark.asyncio
async def test_logprob_falls_back_to_json_when_unsupported(skills_dir, monkeypatch):
    monkeypatch.setenv("VISION_DECISION", "logprob")
    provider = FakeAI(json_replies=[{"flag": False}, {"flag": False}], logprobs=False)
    manager, fake, sid = await _manager(skills_dir, provider)
    r0, _ = await _frame(manager, sid, fake)
    assert r0["result"] == {"flag": False} and not r0.get("error")
    assert manager._logprobs_unsupported is True
    provider.logprobs = True  # must not even be tried again
    await _frame(manager, sid, fake)
    assert provider.prob_calls == [] and len(provider.json_calls) == 2
    await manager.close()


@pytest.mark.asyncio
async def test_logprob_lazy_hint_when_stuck(skills_dir, monkeypatch):
    monkeypatch.setenv("VISION_DECISION", "logprob")
    provider = FakeAI(json_replies=[{"hint": "Cầm vào quai ấm nhé."}])
    manager, fake, sid = await _manager(skills_dir, provider)
    # TIP_AFTER_FRAMES (5) not-done frames → tip; then HINT_STUCK_FRAMES (3) more → one hint call
    for _ in range(7):
        await _frame(manager, sid, fake)
    assert provider.json_calls == []
    assert [k for k, _ in _speeches(fake)] == ["enter", "tip"]
    await _frame(manager, sid, fake)
    for _ in range(100):
        if any(k == "hint" for k, _ in _speeches(fake)):
            break
        await asyncio.sleep(0.01)
    assert _speeches(fake)[-1] == ("hint", "Cầm vào quai ấm nhé.")
    assert len(provider.json_calls) == 1
    hint_call = provider.json_calls[0]
    assert hint_call["max_tokens"] <= 100
    assert vision_runtime.HINT_ONLY_INSTRUCTION in hint_call["content"][0]["text"]
    # rate limited: more stuck frames right away do not trigger another hint call
    for _ in range(4):
        await _frame(manager, sid, fake)
    await asyncio.sleep(0.05)
    assert len(provider.json_calls) == 1
    await manager.close()


# ---- 4. eval config matrix ----------------------------------------------------------


def test_eval_build_configs():
    args = eval_detectors.argparse.Namespace(
        models="m", decision="json,logprob", image_side="384,448,512", reference="always,never",
    )
    configs = eval_detectors.build_configs(args)
    assert len(configs) == 12
    assert configs[0].name == "m|json|384px|ref=always"
    with pytest.raises(SystemExit):
        eval_detectors.build_configs(eval_detectors.argparse.Namespace(
            models="m", decision="xml", image_side="", reference=""))


def test_eval_cli_config_matrix(tmp_path: Path, monkeypatch):
    from test_eval import SKILL_ID, _seed

    provider = FakeAI()

    async def chat_json(*, model, system, content, max_tokens=400, temperature=0.1):
        provider.json_calls.append({"content": content})
        return {"flag": True, "state": "locked"}

    async def chat_choice_probs(*, model, system, content, choices, top_logprobs=10):
        provider.prob_calls.append({"content": content, "choices": choices})
        return {"yes": 0.95, "no": 0.05} if choices == ["yes", "no"] else {"A": 0.95}

    provider.chat_json = chat_json
    provider.chat_choice_probs = chat_choice_probs
    monkeypatch.setattr(eval_detectors, "build_provider_from_env", lambda: provider)
    evals = _seed(tmp_path)
    code = eval_detectors.main([
        SKILL_ID, "--evals-dir", str(evals), "--models", "m",
        "--decision", "json,logprob", "--image-side", "384,448", "--reference", "always,never",
    ])
    assert code == 0
    report = json.loads(next((evals / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert len(report["models"]) == 8
    never = report["models"]["m|json|384px|ref=never"]
    assert never["overall"]["total"] == 12
    lp = report["models"]["m|logprob|448px|ref=always"]
    assert lp["config"]["decision_used"] == ["logprob"]
    assert lp["threshold_sweep"]["0.9"]["false_done"] == 6
    assert lp["threshold_sweep"]["0.99"]["false_done"] == 0
    assert len(provider.json_calls) == 48  # 12 frames × 4 json configs
    assert len(provider.prob_calls) == 48  # 12 frames × 4 logprob configs

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest

import eval_detectors
from eval_store import EvalError, EvalStore
from recipe_catalog import RecipeDetector, RecipeStep
from skill_store import SkillStore

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
JPEG_URL = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
SKILL_ID = "lap-gia-do-dien-thoai"
SKILLS_DIR = Path(eval_detectors.BACKEND_DIR) / "skills"


def _step(**kw) -> RecipeStep:
    base = {"id": "s", "task_id": "t", "detector_key": "d"}
    base.update(kw)
    return RecipeStep(**base)


def test_predicted_done_modes() -> None:
    flag = RecipeDetector(prompt="p", field="flag")
    state = RecipeDetector(prompt="p", field="state")
    momentary = _step(evaluation_mode="momentary_true_complete", complete_on=True)
    assert eval_detectors.predicted_done(momentary, flag, {"flag": True})
    assert not eval_detectors.predicted_done(momentary, flag, {"flag": False})
    match = _step(evaluation_mode="match_value", expected_value="locked", ignored_values=["unknown"])
    assert eval_detectors.predicted_done(match, state, {"state": "locked"})
    assert not eval_detectors.predicted_done(match, state, {"state": "loose"})
    assert not eval_detectors.predicted_done(match, state, {"state": "unknown"})


def test_eval_store_rejects_bad_input(tmp_path: Path) -> None:
    store = EvalStore(tmp_path)
    with pytest.raises(EvalError):
        store.save("../x", "step_1", "done", JPEG_URL)
    with pytest.raises(EvalError):
        store.save(SKILL_ID, "step_1", "maybe", JPEG_URL)
    with pytest.raises(EvalError):
        store.save(SKILL_ID, "step_1", "done", "data:image/jpeg;base64," + base64.b64encode(b"nope").decode())
    store.save(SKILL_ID, "step_1", "done", JPEG_URL)
    assert store.counts(SKILL_ID) == {"step_1": {"done": 1, "not_done": 0}}
    assert store.delete_last(SKILL_ID, "step_1", "done")
    assert not store.delete_last(SKILL_ID, "step_1", "done")


def _seed(tmp_path: Path) -> Path:
    store = EvalStore(tmp_path)
    recipe = SkillStore(SKILLS_DIR).get(SKILL_ID)
    for step in recipe.steps[:2]:
        for label in ("done", "not_done"):
            for _ in range(3):
                store.save(SKILL_ID, step.id, label, JPEG_URL)
                # unique filenames (ms timestamp)
                asyncio.run(asyncio.sleep(0.002))
    return tmp_path


def test_cli_mock_writes_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("SHADOW_MODE", "mock")
    evals = _seed(tmp_path)
    assert eval_detectors.main([SKILL_ID, "--evals-dir", str(evals)]) == 0
    out = capsys.readouterr().out
    assert "TỔNG" in out
    reports = list((evals / "reports").glob("*.json"))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    overall = next(iter(data["models"].values()))["overall"]
    assert overall["total"] == 12


class _FakeProvider:
    mode = "ai"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_json(self, *, model, system, content, max_tokens=400, temperature=0.1):
        self.calls.append({"model": model, "content": content})
        # always answers "done" → every not_done frame is a false done
        return {"flag": True, "state": "locked"}

    async def aclose(self) -> None:
        return None


def test_cli_ai_path_scores_false_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProvider()
    monkeypatch.setattr(eval_detectors, "build_provider_from_env", lambda: fake)
    evals = _seed(tmp_path)
    assert eval_detectors.main([SKILL_ID, "--evals-dir", str(evals), "--models", "m1,m2"]) == 0
    assert len(fake.calls) == 24  # 12 frames × 2 models
    first = fake.calls[0]["content"]
    assert any(part.get("type") == "image_url" for part in first)
    report = json.loads(next((evals / "reports").glob("*.json")).read_text(encoding="utf-8"))
    for model in ("m1", "m2"):
        overall = report["models"][model]["overall"]
        assert overall["false_done"] == 6
        assert overall["missed_done"] == 0
        assert overall["accuracy"] == 0.5


def test_cli_no_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHADOW_MODE", "mock")
    assert eval_detectors.main([SKILL_ID, "--evals-dir", str(tmp_path)]) == 1

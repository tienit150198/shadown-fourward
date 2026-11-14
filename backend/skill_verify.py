"""Teach self-check: "AI tự kiểm tra cái nó vừa học".

The teacher's own recording is free labelled data. With the compiler's step → reference-frame
assignment (frame r_k = step k just completed):

* positives of step k ("done"): r_k and up to two frames after it, before step k+1 completes
  (r_{k+1}). Frames after r_{k+1} are not used — for non-cumulative tasks (phone settings: the
  screen changes) step k's end state may be gone by then.
* negatives of step k ("not done"): frames up to the previous step's reference r_{k-1} — that
  end state is the hardest negative (it is exactly what the learner's camera shows while
  step k is in progress). Step 1 has no negatives (the recording may start with step 1's end
  state already visible, e.g. the cup is on the table).

`verify_skill` runs the live vision judge (same system prompt, same content builder, same
result normalisation as vision_runtime.py) on those frames, computes per-step agreement and,
for steps that fail, asks the compiler model ONCE to rewrite the detector criterion from the
failure evidence, re-checks and keeps the better prompt. Everything is time-boxed; partial
results are kept. `export_eval_set` writes the same labels as an eval set (eval_store layout)
so `eval_detectors.py <skill_id>` works right after the skill is saved.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from recipe_catalog import Recipe, RecipeDetector, RecipeStep

logger = logging.getLogger("uvicorn.error")

TEACH_META_FILENAME = "teach.json"
AUTO_EVAL_PREFIX = "0auto_"  # sorts before phone captures (ms timestamps) → "Hoàn tác" never hits it
MAX_POSITIVES = 3
MAX_NEGATIVES = 2
VISION_CONCURRENCY = 8
REWRITE_CONCURRENCY = 4
REWRITE_MAX_TOKENS = 600
MIN_REWRITE_BUDGET_S = 6.0

PhaseCallback = Callable[..., Any]


def verify_enabled() -> bool:
    return (os.getenv("SHADOW_TEACH_VERIFY") or "on").strip().lower() not in ("0", "off", "false", "no")


def verify_budget_s() -> float:
    try:
        value = float((os.getenv("SHADOW_VERIFY_BUDGET_S") or "25").strip())
    except ValueError:
        return 25.0
    return value if value > 0 else 25.0


# --------------------------------------------------------------------------- teach metadata


@dataclass
class TeachFrame:
    index: int
    t: float
    file: str  # "fNN.jpg" inside <media_root>/frames/
    reason: str = ""


def write_teach_meta(media_root: Path, frames: list[TeachFrame], extra: dict[str, Any] | None = None) -> None:
    media_root.mkdir(parents=True, exist_ok=True)
    data = {"frames": [f.__dict__ for f in frames], **(extra or {})}
    (media_root / TEACH_META_FILENAME).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_teach_frames(media_root: Path) -> list[TeachFrame]:
    """Teach frames of a draft / skill media dir (falls back to listing frames/ when no meta)."""
    meta = media_root / TEACH_META_FILENAME
    frames: list[TeachFrame] = []
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            for i, item in enumerate(data.get("frames") or []):
                name = PurePosixPath(str(item.get("file") or "")).name
                if name and (media_root / "frames" / name).is_file():
                    frames.append(TeachFrame(int(item.get("index", i)), float(item.get("t") or 0), name, str(item.get("reason") or "")))
        except (OSError, ValueError, TypeError, AttributeError):
            frames = []
    if not frames:
        folder = media_root / "frames"
        if folder.is_dir():
            names = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
            frames = [TeachFrame(i, float(i), name) for i, name in enumerate(names)]
    frames.sort(key=lambda f: f.index)
    # re-index densely (indexes are positions in this list from now on)
    return [TeachFrame(i, f.t, f.file, f.reason) for i, f in enumerate(frames)]


def frame_index_of(reference_image: str | None, frames: list[TeachFrame]) -> int | None:
    if not reference_image:
        return None
    name = PurePosixPath(reference_image).name
    return next((f.index for f in frames if f.file == name), None)


# --------------------------------------------------------------------------- auto labels


def _spread(items: list[int], k: int) -> list[int]:
    if k <= 0 or not items:
        return []
    if len(items) <= k:
        return list(items)
    if k == 1:
        return [items[0]]
    return sorted({items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)})


def label_frames(ref_idx: list[int | None], n_frames: int) -> list[dict[str, list[int]]]:
    """Per step {"pos": [...], "neg": [...]} frame indexes (see module docstring)."""
    out: list[dict[str, list[int]]] = []
    for k, r in enumerate(ref_idx):
        pos: list[int] = []
        neg: list[int] = []
        if r is not None and 0 <= r < n_frames:
            nxt = next((x for x in ref_idx[k + 1:] if x is not None), None)
            end = nxt if nxt is not None and nxt > r else (r + 1 if nxt is not None else n_frames)
            after = list(range(r + 1, min(end, n_frames)))
            pos = [r, *_spread(after, MAX_POSITIVES - 1)]
            if k > 0:
                prev = next((x for x in reversed(ref_idx[:k]) if x is not None), None)
                if prev is not None:
                    cands = [i for i in range(n_frames) if i <= prev and i < r]
                    if cands:
                        neg = [cands[-1]]  # previous step's end state: the hardest negative
                        older_refs = [x for x in ref_idx[: k - 1] if x is not None and x in cands and x != cands[-1]]
                        if older_refs:
                            neg.append(older_refs[-1])
                        elif len(cands) > 1:
                            neg.append(cands[len(cands) // 2 - 1 if len(cands) > 2 else 0])
                        neg = sorted(set(neg))[-MAX_NEGATIVES:]
        out.append({"pos": pos, "neg": neg})
    return out


def skill_labels(recipe: Recipe, frames: list[TeachFrame]) -> list[dict[str, list[int]]]:
    refs = [frame_index_of(s.reference_image, frames) for s in recipe.steps]
    return label_frames(refs, len(frames))


def export_eval_set(media_root: Path, recipe: Recipe, evals_root: Path) -> dict[str, dict[str, int]]:
    """Write the auto-labelled teacher frames as <evals_root>/<skill>/<step>/{done,not_done}/0auto_*.

    Previous auto files of this skill are replaced; phone-captured samples are kept.
    """
    frames = read_teach_frames(media_root)
    base = Path(evals_root) / recipe.id
    if base.is_dir():
        for old in base.glob(f"*/*/{AUTO_EVAL_PREFIX}*"):
            old.unlink(missing_ok=True)
    counts: dict[str, dict[str, int]] = {}
    if not frames:
        return counts
    for step, labels in zip(recipe.steps, skill_labels(recipe, frames)):
        for key, label in (("pos", "done"), ("neg", "not_done")):
            for idx in labels[key]:
                src = media_root / "frames" / frames[idx].file
                folder = base / step.id / label
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, folder / f"{AUTO_EVAL_PREFIX}{frames[idx].file}")
                counts.setdefault(step.id, {"done": 0, "not_done": 0})[label] += 1
    return counts


# --------------------------------------------------------------------------- judging


def judged_done(step: RecipeStep, detector: RecipeDetector, result: dict[str, Any]) -> bool | None:
    """Would the live workflow count this single (normalised) result as 'step done'? None = unusable."""
    value = result.get(detector.field)
    if value is None:
        return None
    if value in step.ignored_values_list():
        return False
    mode = step.evaluation_mode
    if mode == "momentary_true_complete":
        return bool(value) is (step.complete_on is not False)
    if mode == "match_value":
        return value == step.expected_value
    if mode == "enum_progress_once_then_complete":
        return value == step.complete_value
    return bool(value)


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class _MediaShim:
    """Stands in for SkillStore.media_path: reference images come from the draft/skill dir."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def media_path(self, skill_id: str, rel: str | None) -> Path | None:
        if not rel:
            return None
        path = self.root / rel
        try:
            path.resolve().relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None


def _make_builder(media_root: Path) -> Any:
    """The live runtime's prompt builder, pointed at the draft/skill media dir."""
    from vision_runtime import VisionRuntimeMixin

    class _Builder(VisionRuntimeMixin):
        def __init__(self) -> None:
            self._skills = _MediaShim(media_root)  # type: ignore[assignment]
            self._ref_image_cache = {}

    return _Builder()


@dataclass
class Sample:
    frame: int
    truth: bool  # True = step done in this frame


@dataclass
class Outcome:
    frame: int
    truth: bool
    done: bool | None
    answer: Any = None
    hint: str | None = None


@dataclass
class StepCheck:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def checked(self) -> int:
        return sum(1 for o in self.outcomes if o.done is not None)

    @property
    def agree(self) -> int:
        return sum(1 for o in self.outcomes if o.done is not None and o.done == o.truth)

    @property
    def false_done(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.done is True and not o.truth]

    @property
    def missed(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.done is False and o.truth]

    def key(self) -> tuple[int, int, int]:
        """Lower is better: skipping a step (false 'done') is worse than making the learner wait."""
        return (len(self.false_done), len(self.missed), -self.checked)


class SkillVerifier:
    def __init__(
        self,
        *,
        provider: Any,
        media_root: Path,
        vision_model: str,
        compiler_model: str,
        deadline: float,
        on_progress: PhaseCallback | None = None,
    ) -> None:
        self.provider = provider
        self.media_root = Path(media_root)
        self.vision_model = vision_model
        self.compiler_model = compiler_model
        self.deadline = deadline
        self.on_progress = on_progress
        self.builder = _make_builder(self.media_root)
        self.vision_sem = asyncio.Semaphore(VISION_CONCURRENCY)
        self.rewrite_sem = asyncio.Semaphore(REWRITE_CONCURRENCY)
        self.frames = read_teach_frames(self.media_root)
        self._urls: dict[int, str] = {}
        self.judged = 0
        self.total = 0
        self.vision_calls = 0
        self.rewrite_calls = 0
        self.timings: dict[str, float] = {}

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def frame_url(self, idx: int) -> str:
        if idx not in self._urls:
            url = _data_url(self.media_root / "frames" / self.frames[idx].file)
            try:  # same pixel budget as the live judge, when the runtime has one
                from providers import vision_image_max_side
                from vision_runtime import downscale_data_url

                url = downscale_data_url(url, vision_image_max_side())
            except ImportError:
                pass
            self._urls[idx] = url
        return self._urls[idx]

    def _progress(self, **info: Any) -> None:
        if self.on_progress is not None:
            try:
                self.on_progress("verifying", **info)
            except Exception:  # noqa: BLE001 - progress is cosmetic
                pass

    async def judge(self, recipe: Recipe, step: RecipeStep, detector: RecipeDetector, sample: Sample) -> Outcome:
        from providers import (
            vision_decision,
            vision_done_threshold,
            vision_image_max_side,
            vision_reference_mode,
            vision_timeout_s,
        )

        async with self.vision_sem:
            timeout = min(vision_timeout_s(), self.remaining())
            if timeout <= 0.3:
                return Outcome(sample.frame, sample.truth, None)
            try:
                self.vision_calls += 1
                # exactly the live Guide judge: VISION_DECISION, VISION_IMAGE_MAX_SIDE and the
                # reference image (sent unless VISION_REFERENCE_MODE=never)
                self.builder._provider = self.provider
                result = await asyncio.wait_for(
                    self.builder._judge_frame(
                        recipe,
                        step,
                        detector,
                        self.frame_url(sample.frame),
                        model=self.vision_model,
                        decision=vision_decision(),
                        image_side=vision_image_max_side(),
                        include_reference=vision_reference_mode() != "never",
                        threshold=vision_done_threshold(),
                    ),
                    timeout=timeout,
                )
                for private in ("_images", "_decision", "_raw"):
                    result.pop(private, None)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - timeouts / provider errors → unchecked sample
                logger.info("verify: judge failed step=%s frame=%s: %s", step.id, sample.frame, str(error)[:200])
                return Outcome(sample.frame, sample.truth, None)
            finally:
                self.judged += 1
                self._progress(judged=self.judged, total=self.total)
        return Outcome(
            sample.frame,
            sample.truth,
            judged_done(step, detector, result),
            result.get(detector.field),
            result.get("hint"),
        )

    async def check(self, recipe: Recipe, step: RecipeStep, detector: RecipeDetector, samples: list[Sample]) -> StepCheck:
        outcomes = await asyncio.gather(*(self.judge(recipe, step, detector, s) for s in samples))
        return StepCheck(list(outcomes))

    async def rewrite(
        self,
        recipe: Recipe,
        index: int,
        detector: RecipeDetector,
        result: StepCheck,
    ) -> str | None:
        """Ask the compiler model once for a better criterion. Returns the wrapped prompt or None."""
        from skill_compiler import split_detector_prompt, wrap_detector_prompt

        step = recipe.steps[index]
        criterion, options = split_detector_prompt(detector.prompt)
        if detector.field == "state" and not options:
            options = [o for o in [step.expected_value] if isinstance(o, str)]
        prev_step = recipe.steps[index - 1] if index > 0 else None
        next_step = recipe.steps[index + 1] if index + 1 < len(recipe.steps) else None
        ref_idx = frame_index_of(step.reference_image, self.frames)

        lines = [
            f"Skill: {recipe.display_name}",
            f"Step {index + 1}/{len(recipe.steps)} (Vietnamese instruction): {step.instruction}",
        ]
        if prev_step is not None:
            lines.append(f"Previous step: {prev_step.instruction}")
        if next_step is not None:
            lines.append(f"Next step: {next_step.instruction}")
        lines.append(f"Detector field: `{detector.field}`" + (f", options {options}, done value {step.expected_value!r}" if detector.field == "state" else ""))
        lines.append(f"Current criterion:\n{criterion}")
        lines.append("\nTest on frames from the teacher's own video (truth is known):")
        content: list[dict[str, Any]] = []
        for o in result.outcomes:
            if o.done is None:
                continue
            verdict = "CORRECT" if o.done == o.truth else ("WRONG — would SKIP the step" if o.done else "WRONG — learner would be STUCK")
            truth = "step DONE in this frame" if o.truth else "step NOT done yet (state before this step)"
            lines.append(f"- Frame {o.frame}: truth = {truth}; judge answered {detector.field}={json.dumps(o.answer, ensure_ascii=False)} → {verdict}")
        content.append({"type": "text", "text": "\n".join(lines)})
        if ref_idx is not None:
            content.append({"type": "text", "text": f"Teacher's reference frame for this step (Frame {ref_idx}, step done):"})
            content.append({"type": "image_url", "image_url": {"url": self.frame_url(ref_idx)}})
        shown = {ref_idx}
        for o in result.outcomes:
            if o.done is None or o.frame in shown:
                continue
            shown.add(o.frame)
            content.append({"type": "text", "text": f"Frame {o.frame} ({'DONE' if o.truth else 'NOT done'}):"})
            content.append({"type": "image_url", "image_url": {"url": self.frame_url(o.frame)}})
        content.append({"type": "text", "text": "Now output the JSON object with the rewritten criterion."})

        timeout = self.remaining() - 2.5
        if timeout < 3:
            return None
        async with self.rewrite_sem:
            try:
                self.rewrite_calls += 1
                raw = await asyncio.wait_for(
                    self.provider.chat_json(
                        model=self.compiler_model,
                        system=REWRITE_SYSTEM_PROMPT,
                        content=content,
                        max_tokens=REWRITE_MAX_TOKENS,
                        temperature=0.2,
                    ),
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                logger.info("verify: rewrite failed step=%s: %s", step.id, str(error)[:200])
                return None
        new = (raw.get("prompt") or raw.get("criterion")) if isinstance(raw, dict) else None
        if not isinstance(new, str) or len(new.strip()) < 15:
            return None
        new = " ".join(new.split())[:900]
        if new.strip() == criterion.strip():
            return None
        return wrap_detector_prompt(new, detector.field, options)


REWRITE_SYSTEM_PROMPT = """\
You fix ONE detector prompt of SKI, a real-time coach. During coaching a small vision model (the judge) sees ONE still frame from the learner's phone camera plus the teacher's reference frame, reads the detector criterion, and answers whether the current step is done (field `flag` true/false, or a `state` label). A false "done" skips the step; a false "not done" leaves the learner stuck.

The current criterion was tested on frames from the teacher's own demonstration video where the truth is known, and the judge got some of them wrong. Write a better criterion so the judge answers ALL test frames correctly — and still generalises to a different person, table, lighting and camera angle.

How to write it (English, 1–3 sentences):
- Name the single most decisive visible difference between the DONE frames and the NOT-done frames (an object present/absent, inside/outside, attached/detached, a colour or fill level, a screen element that only appears after the step). Look at the frames to find it.
- If the judge said "done" on a NOT-done frame, the criterion was too loose: require the specific evidence that is missing in that frame, and say explicitly what makes it false (e.g. "If the tea bag is still dry / outside the cup, answer false").
- If the judge said "not done" on a DONE frame, the criterion was too strict or described something not visible: drop details that are not visible in the DONE frames (hands, motion, exact positions, brand names), and accept partial occlusion.
- Never make it false because of what LATER steps add (the DONE frames may already show the next step's result).
- Describe the end state only, never motion or timing. Do not mention frame numbers, the teacher, or "the reference image".
- Keep the answer contract: for a `flag` detector describe when it is true; for a `state` detector describe how to tell each option apart (do not add or rename options).

Output ONLY one JSON object: {"prompt": "<new criterion>", "why": "<one short English sentence>"}
"""


# --------------------------------------------------------------------------- reasons (Vietnamese UI)


def _reason(status: str, check: StepCheck, *, rewritten: bool) -> str:
    n = check.checked
    if status == "unchecked":
        return "Chưa đủ khung hình (hoặc mạng chậm) để AI tự kiểm tra bước này."
    if status == "ok":
        return f"AI đã thử trên {n} ảnh từ video của bạn và chấm đúng cả {n}."
    if status == "fixed":
        return f"AI đã tự sửa cách nhận biết bước này; thử lại đúng {check.agree}/{n} ảnh."
    fd, ms = len(check.false_done), len(check.missed)
    if fd and ms:
        text = "AI còn chấm nhầm cả hai chiều: lúc chưa làm cũng tưởng xong, lúc xong lại tưởng chưa."
    elif fd:
        text = "AI có thể bỏ qua bước này: ảnh lúc chưa làm bước này cũng bị chấm là xong."
    else:
        text = "AI có thể không nhận ra khi người học làm xong: ảnh lúc đã xong bị chấm là chưa xong."
    return text + (" Đã thử tự sửa nhưng chưa đạt." if rewritten else "")


def _summary(check: StepCheck, status: str, *, labels: dict[str, list[int]], rewritten: bool, before: StepCheck | None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "checked": check.checked,
        "agree": check.agree,
        "status": status,
        "reason": _reason(status, check, rewritten=rewritten),
        "false_done": [o.frame for o in check.false_done],
        "missed": [o.frame for o in check.missed],
        "pos": labels["pos"],
        "neg": labels["neg"],
        "rewritten": rewritten,
    }
    if before is not None:
        data["before"] = {"checked": before.checked, "agree": before.agree}
    return data


def _status(check: StepCheck) -> str:
    if check.checked == 0:
        return "unchecked"
    return "ok" if check.agree == check.checked else "weak"


# --------------------------------------------------------------------------- entry points


async def _verify_steps(
    verifier: SkillVerifier,
    skill: dict[str, Any],
    step_indexes: list[int],
    *,
    allow_rewrite: bool = True,
) -> dict[int, dict[str, Any]]:
    """Check (and maybe rewrite) the given steps of `skill` in place. Returns {index: verify}."""
    recipe = Recipe.model_validate(skill)
    labels_all = skill_labels(recipe, verifier.frames)
    plans: dict[int, list[Sample]] = {}
    for i in step_indexes:
        lab = labels_all[i]
        plans[i] = [Sample(f, True) for f in lab["pos"]] + [Sample(f, False) for f in lab["neg"]]
    verifier.total += sum(len(s) for s in plans.values())

    async def first_pass(i: int) -> StepCheck:
        step = recipe.steps[i]
        return await verifier.check(recipe, step, recipe.detectors[step.detector_key], plans[i])

    t0 = time.monotonic()
    results = dict(zip(step_indexes, await asyncio.gather(*(first_pass(i) for i in step_indexes))))
    verifier.timings["judge_s"] = round(time.monotonic() - t0, 1)

    failing = [i for i in step_indexes if _status(results[i]) == "weak"]
    rewritten: dict[int, tuple[str, StepCheck]] = {}
    if allow_rewrite and failing and verifier.remaining() > MIN_REWRITE_BUDGET_S:

        async def fix(i: int) -> tuple[int, str, StepCheck] | None:
            step = recipe.steps[i]
            detector = recipe.detectors[step.detector_key]
            prompt = await verifier.rewrite(recipe, i, detector, results[i])
            if prompt is None or verifier.remaining() < 1.0:
                return None
            candidate = RecipeDetector(prompt=prompt, field=detector.field)
            verifier.total += len(plans[i])
            again = await verifier.check(recipe, step, candidate, plans[i])
            return i, prompt, again

        t1 = time.monotonic()
        for item in await asyncio.gather(*(fix(i) for i in failing)):
            if item is not None:
                rewritten[item[0]] = (item[1], item[2])
        verifier.timings["rewrite_s"] = round(time.monotonic() - t1, 1)

    out: dict[int, dict[str, Any]] = {}
    for i in step_indexes:
        step_dict = skill["steps"][i]
        first = results[i]
        status = _status(first)
        final, before, tried = first, None, i in rewritten
        if tried:
            prompt, again = rewritten[i]
            if again.checked and again.key() < first.key():
                skill["detectors"][step_dict["detector_key"]]["prompt"] = prompt
                final, before = again, first
                status = "fixed" if _status(again) == "ok" else "weak"
        summary = _summary(final, status, labels=labels_all[i], rewritten=tried, before=before)
        step_dict["verify"] = summary
        out[i] = summary
    return out


def quality_of(skill: dict[str, Any]) -> dict[str, Any] | None:
    verifies = [s.get("verify") for s in skill.get("steps", []) if isinstance(s.get("verify"), dict)]
    if not verifies:
        return None
    checked = sum(int(v.get("checked") or 0) for v in verifies)
    agree = sum(int(v.get("agree") or 0) for v in verifies)
    by = lambda name: sum(1 for v in verifies if v.get("status") == name)  # noqa: E731
    return {
        "score": round(agree / checked, 2) if checked else None,
        "checked": checked,
        "agree": agree,
        "steps_ok": by("ok"),
        "steps_fixed": by("fixed"),
        "steps_weak": by("weak"),
        "steps_unchecked": by("unchecked"),
    }


async def verify_skill(
    skill: dict[str, Any],
    *,
    provider: Any,
    media_root: Path,
    vision_model: str,
    compiler_model: str,
    budget_s: float | None = None,
    on_progress: PhaseCallback | None = None,
) -> dict[str, Any] | None:
    """Self-check every step of a freshly compiled skill (mutates `skill`). Returns `quality`."""
    started = time.monotonic()
    verifier = SkillVerifier(
        provider=provider,
        media_root=media_root,
        vision_model=vision_model,
        compiler_model=compiler_model,
        deadline=started + (budget_s if budget_s is not None else verify_budget_s()),
        on_progress=on_progress,
    )
    if len(verifier.frames) < 2 or not skill.get("steps"):
        return None
    await _verify_steps(verifier, skill, list(range(len(skill["steps"]))))
    quality = quality_of(skill)
    if quality is not None:
        quality["seconds"] = round(time.monotonic() - started, 1)
        quality["vision_calls"] = verifier.vision_calls
        quality["rewrite_calls"] = verifier.rewrite_calls
        quality.update(verifier.timings)
        skill["quality"] = quality
    logger.info("verify: %s", quality)
    return quality


async def verify_one_step(
    skill: dict[str, Any],
    index: int,
    *,
    provider: Any,
    media_root: Path,
    vision_model: str,
    compiler_model: str,
    budget_s: float = 20.0,
    allow_rewrite: bool = True,
) -> dict[str, Any]:
    """Re-run the self-check for one step (teacher picked another reference frame)."""
    verifier = SkillVerifier(
        provider=provider,
        media_root=media_root,
        vision_model=vision_model,
        compiler_model=compiler_model,
        deadline=time.monotonic() + budget_s,
    )
    if len(verifier.frames) < 2:
        summary = {"checked": 0, "agree": 0, "status": "unchecked", "reason": _reason("unchecked", StepCheck(), rewritten=False)}
        skill["steps"][index]["verify"] = summary
        return summary
    result = await _verify_steps(verifier, skill, [index], allow_rewrite=allow_rewrite)
    quality = quality_of(skill)
    if quality is not None:
        skill["quality"] = {**(skill.get("quality") or {}), **quality}
    return result[index]

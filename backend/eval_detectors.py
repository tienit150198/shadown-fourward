"""Score a skill's vision detectors on labelled frames (SHADOW eval).

Frames are collected on the phone (Guide → settings → "Thu mẫu eval") into
backend/evals/<skill_id>/<step_id>/{done,not_done}/*.jpg.

Usage (from backend/):
    ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai
    ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai --models google/gemini-2.5-flash,openai/gpt-5-mini
    ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai --steps step_2 --concurrency 2
    # compare guide configurations (cartesian product; one row per config):
    ../.venv/bin/python eval_detectors.py pha-tra-eval --decision json,logprob \
        --image-side 384,448,512 --reference always,never --concurrency 1

It sends exactly the same prompt the live coach sends (same code path: downsizing, reference
image, json/logprob decision) and reports, per config and step: accuracy, false "done" (most
harmful: skips a step), missed "done" (learner waits), errors and latency. logprob configs
also get a VISION_DONE_THRESHOLD sweep. A JSON report is written to evals/reports/.
Skills are read from --skills-dir, else SHADOW_SKILLS_DIR, else backend/skills.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent
try:  # same .env handling as main.py
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR.parent / ".env")
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:  # pragma: no cover
    pass

from eval_store import LABELS, default_evals_root, list_images  # noqa: E402
from providers import (  # noqa: E402
    VISION_DECISIONS,
    LLMProvider,
    build_provider_from_env,
    vision_decision,
    vision_done_threshold,
    vision_image_max_side,
    vision_model,
)
from recipe_catalog import Recipe, RecipeDetector, RecipeStep  # noqa: E402
from session_helpers import extract_result_value, matches_condition  # noqa: E402
from skill_store import SkillStore  # noqa: E402
from vision_runtime import VisionRuntimeMixin  # noqa: E402

THRESHOLD_SWEEP = (0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99)


def predicted_done(step: RecipeStep, detector: RecipeDetector, result: dict[str, Any]) -> bool:
    """Would the live workflow treat this single result as 'step completed'?"""
    value = extract_result_value(result, step.value_path or detector.field)
    if value in step.ignored_values_list():
        return False
    mode = step.evaluation_mode
    if mode == "momentary_true_complete":
        return bool(value) is bool(step.complete_on)
    if mode == "match_value":
        return value == step.expected_value
    if mode == "enum_progress_once_then_complete":
        return value == step.complete_value
    if mode == "numeric_threshold_with_progress_once":
        return (
            isinstance(value, (int, float))
            and step.complete_condition is not None
            and matches_condition(step.complete_condition, value)
        )
    # count_rising_edges_true: a single frame can only show the edge itself
    return bool(value)


class _PromptBuilder(VisionRuntimeMixin):
    """Reuses the live runtime's judging code (`_judge_frame`) without a session."""

    def __init__(self, store: SkillStore, provider: LLMProvider | None = None) -> None:
        self._skills = store
        self._provider = provider  # type: ignore[assignment]
        self._ref_image_cache = {}


@dataclass(frozen=True)
class Config:
    model: str
    decision: str = "json"
    image_side: int = 448
    reference: str = "always"

    @property
    def name(self) -> str:
        return f"{self.model}|{self.decision}|{self.image_side}px|ref={self.reference}"


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


@dataclass
class Sample:
    step_id: str
    label: str
    path: Path


@dataclass
class Outcome:
    model: str  # config name
    sample: Sample
    done: bool | None  # None = error
    latency_ms: int
    result: dict[str, Any] | None = None
    error: str | None = None
    decision_used: str | None = None


@dataclass
class Score:
    total: int = 0
    correct: int = 0
    false_done: int = 0
    missed_done: int = 0
    errors: int = 0
    latencies: list[int] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.total += 1
        if outcome.done is None:
            self.errors += 1
            return
        self.latencies.append(outcome.latency_ms)
        truth = outcome.sample.label == "done"
        if outcome.done == truth:
            self.correct += 1
        elif outcome.done and not truth:
            self.false_done += 1
        else:
            self.missed_done += 1

    def as_dict(self) -> dict[str, Any]:
        lat = sorted(self.latencies)
        return {
            "total": self.total,
            "accuracy": round(self.correct / self.total, 3) if self.total else None,
            "false_done": self.false_done,
            "missed_done": self.missed_done,
            "errors": self.errors,
            "latency_p50_ms": int(statistics.median(lat)) if lat else None,
            "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        }


def collect_samples(evals_dir: Path, recipe: Recipe, steps: set[str] | None) -> list[Sample]:
    samples: list[Sample] = []
    for step in recipe.steps:
        if steps and step.id not in steps:
            continue
        for label in LABELS:
            for path in list_images(evals_dir / step.id / label):
                samples.append(Sample(step.id, label, path))
    return samples


def mock_result(step: RecipeStep, detector: RecipeDetector, sample: Sample) -> dict[str, Any]:
    """Deterministic fake (≈80% right) so the pipeline can be smoke-tested offline."""
    truth = sample.label == "done"
    lucky = hashlib.md5(sample.path.name.encode()).digest()[0] < 205
    says_done = truth if lucky else not truth
    if step.evaluation_mode == "match_value":
        return {detector.field: step.expected_value if says_done else "unknown"}
    if step.evaluation_mode == "enum_progress_once_then_complete":
        return {detector.field: step.complete_value if says_done else "unknown"}
    return {detector.field: says_done if step.complete_on is not False else not says_done}


async def run_one(
    provider: LLMProvider,
    builder: _PromptBuilder,
    recipe: Recipe,
    config: Config,
    sample: Sample,
    sem: asyncio.Semaphore,
    threshold: float = 0.9,
) -> Outcome:
    step = next(s for s in recipe.steps if s.id == sample.step_id)
    detector = recipe.detectors[step.detector_key]
    async with sem:
        started = time.perf_counter()
        try:
            decision_used = config.decision
            if provider.mode == "mock":
                await asyncio.sleep(0.01)
                result = mock_result(step, detector, sample)
            else:
                result = await builder._judge_frame(
                    recipe,
                    step,
                    detector,
                    image_data_url(sample.path),
                    model=config.model,
                    decision=config.decision,
                    image_side=config.image_side,
                    include_reference=config.reference == "always",
                    threshold=threshold,
                )
                decision_used = result.pop("_decision", config.decision)
                result.pop("_images", None)
                raw = result.pop("_raw", None)
                if decision_used == "logprob" and isinstance(raw, dict):
                    result["probs"] = {k: round(v, 4) for k, v in raw.items()}
            latency = int((time.perf_counter() - started) * 1000)
            return Outcome(
                config.name, sample, predicted_done(step, detector, result), latency, result,
                decision_used=decision_used,
            )
        except Exception as error:  # noqa: BLE001 — report every failure, keep going
            latency = int((time.perf_counter() - started) * 1000)
            return Outcome(config.name, sample, None, latency, error=f"{type(error).__name__}: {error}")


def threshold_sweep(outcomes: list[Outcome]) -> dict[str, dict[str, Any]]:
    """Re-score logprob outcomes at several VISION_DONE_THRESHOLD values (from `confidence`)."""
    sweep: dict[str, dict[str, Any]] = {}
    scored = [o for o in outcomes if o.result and isinstance(o.result.get("confidence"), (int, float))]
    if not scored:
        return sweep
    for t in THRESHOLD_SWEEP:
        correct = false_done = missed = 0
        for o in scored:
            done = o.result["confidence"] >= t  # type: ignore[index]
            truth = o.sample.label == "done"
            if done == truth:
                correct += 1
            elif done:
                false_done += 1
            else:
                missed += 1
        sweep[str(t)] = {
            "accuracy": round(correct / len(scored), 3),
            "false_done": false_done,
            "missed_done": missed,
        }
    return sweep


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_configs(args: argparse.Namespace) -> list[Config]:
    models = _csv(args.models) or [vision_model()]
    decisions = _csv(args.decision) or [vision_decision()]
    for decision in decisions:
        if decision not in VISION_DECISIONS:
            raise SystemExit(f"--decision: '{decision}' không hợp lệ (json | logprob)")
    try:
        sides = [int(x) for x in _csv(args.image_side)] or [vision_image_max_side()]
    except ValueError:
        raise SystemExit("--image-side: cần số nguyên, vd 384,448,512") from None
    references = _csv(args.reference) or ["always"]
    for ref in references:
        if ref not in {"always", "never"}:
            raise SystemExit(f"--reference: '{ref}' không hợp lệ (always | never)")
    return [
        Config(model, decision, side, ref)
        for model in models
        for decision in decisions
        for side in sides
        for ref in references
    ]


def print_table(title: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"\n{title}")
    header = f"  {'':<44} {'n':>4} {'đúng':>6} {'báo xong sai':>13} {'bỏ sót':>7} {'lỗi':>4} {'p50 ms':>7} {'p95 ms':>7}"
    print(header)
    for name, s in rows:
        acc = f"{s['accuracy'] * 100:.0f}%" if s["accuracy"] is not None else "-"
        print(
            f"  {name[:44]:<44} {s['total']:>4} {acc:>6} {s['false_done']:>13} {s['missed_done']:>7} "
            f"{s['errors']:>4} {s['latency_p50_ms'] or '-':>7} {s['latency_p95_ms'] or '-':>7}"
        )


async def main_async(args: argparse.Namespace) -> int:
    skills_dir = args.skills_dir or (os.getenv("SHADOW_SKILLS_DIR") or "").strip() or str(BACKEND_DIR / "skills")
    store = SkillStore(Path(skills_dir))
    recipe = store.get(args.skill_id)
    evals_root = Path(args.evals_dir) if args.evals_dir else default_evals_root(BACKEND_DIR)
    steps = set(args.steps.split(",")) if args.steps else None
    samples = collect_samples(evals_root / recipe.id, recipe, steps)
    if args.limit:
        samples = samples[: args.limit]
    if not samples:
        print(
            f"Chưa có frame nào trong {evals_root / recipe.id}. Thu mẫu trên điện thoại: "
            "Hướng dẫn → Cài đặt → 'Thu mẫu eval'."
        )
        return 1

    configs = build_configs(args)
    threshold = args.threshold if args.threshold is not None else vision_done_threshold()
    provider = build_provider_from_env()
    if provider.mode == "mock":
        print("⚠️  Chế độ mock (chưa có LLM_API_KEY): kết quả là GIẢ, chỉ để kiểm tra pipeline.")
    print(
        f"Skill: {recipe.display_name} · {len(samples)} frame · {len(configs)} cấu hình: "
        + ", ".join(c.name for c in configs)
    )

    builder = _PromptBuilder(store, provider)
    sem = asyncio.Semaphore(max(1, args.concurrency))
    try:
        # sample-major order: configs are interleaved so gateway load affects them equally
        outcomes = await asyncio.gather(
            *(
                run_one(provider, builder, recipe, c, s, sem, threshold)
                for s in samples
                for c in configs
            )
        )
    finally:
        await provider.aclose()

    report: dict[str, Any] = {
        "skill_id": recipe.id,
        "mode": provider.mode,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "threshold": threshold,
        "models": {},  # keyed by model (single default config) or config name
    }
    overall_rows = []
    single = len(configs) == 1 or all(
        c.decision == configs[0].decision and c.image_side == configs[0].image_side
        and c.reference == configs[0].reference for c in configs
    )
    for config in configs:
        model = config.model if single else config.name
        mine = [o for o in outcomes if o.model == config.name]
        overall = Score()
        per_step: dict[str, Score] = {}
        for o in mine:
            overall.add(o)
            per_step.setdefault(o.sample.step_id, Score()).add(o)
        step_names = {s.id: (s.instruction or s.id) for s in recipe.steps}
        print_table(
            f"Cấu hình {config.name}",
            [(f"{sid} {step_names[sid][:20]}", sc.as_dict()) for sid, sc in per_step.items()],
        )
        overall_rows.append((model if single else config.name.split("|", 1)[-1], overall.as_dict()))
        used = sorted({o.decision_used for o in mine if o.decision_used})
        sweep = threshold_sweep(mine) if "logprob" in used else {}
        if sweep:
            print("  ngưỡng P(xong):  " + "  ".join(
                f"{t}→{v['accuracy'] * 100:.0f}% (sai {v['false_done']}, sót {v['missed_done']})"
                for t, v in sweep.items()
            ))
        report["models"][model] = {
            "config": {
                "model": config.model, "decision": config.decision,
                "image_side": config.image_side, "reference": config.reference,
                "decision_used": used,
            },
            "overall": overall.as_dict(),
            "threshold_sweep": sweep,
            "steps": {sid: sc.as_dict() for sid, sc in per_step.items()},
            "mistakes": [
                {
                    "step_id": o.sample.step_id,
                    "label": o.sample.label,
                    "file": str(o.sample.path),
                    "predicted_done": o.done,
                    "result": o.result,
                    "error": o.error,
                }
                for o in mine
                if o.done is None or o.done != (o.sample.label == "done")
            ],
        }
    print_table("TỔNG", overall_rows)
    print(
        "\n'báo xong sai' = frame CHƯA xong mà model nói xong → bỏ qua bước (nguy hiểm nhất khi demo)."
    )

    reports_dir = evals_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{recipe.id}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Báo cáo chi tiết (kèm các frame chấm sai): {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("skill_id")
    parser.add_argument("--models", help="danh sách model, phân cách bằng dấu phẩy (mặc định VISION_MODEL)")
    parser.add_argument("--decision", help="json,logprob (mặc định VISION_DECISION)")
    parser.add_argument("--image-side", help="cạnh dài ảnh người học, vd 384,448,512 (mặc định VISION_IMAGE_MAX_SIDE)")
    parser.add_argument("--reference", help="always,never — có gửi ảnh mẫu của người dạy không (mặc định always)")
    parser.add_argument("--threshold", type=float, help="VISION_DONE_THRESHOLD cho logprob (mặc định từ .env, 0.9)")
    parser.add_argument("--skills-dir", help="thư mục skill (mặc định SHADOW_SKILLS_DIR hoặc backend/skills)")
    parser.add_argument("--steps", help="chỉ chấm các step id này, phân cách bằng dấu phẩy")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="chỉ chấm N frame đầu (thử nhanh)")
    parser.add_argument("--evals-dir", help="thư mục evals (mặc định backend/evals hoặc SHADOW_EVALS_DIR)")
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())

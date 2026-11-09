#!/usr/bin/env python3
"""Deterministic release-gate evaluator for Vietnamese stylist conversations.

The runner exercises the real FastAPI chat endpoint in mock mode against an
isolated temporary wardrobe. It measures safety/invariant behavior, not the
subjective quality of model-written fashion advice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_SCENARIOS = Path(__file__).with_name("stylist_conversations.json")
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def load_scenarios(path: Path = DEFAULT_SCENARIOS) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Golden set phải là một JSON array.")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise ValueError("Mỗi scenario phải có id.")
        scenario_id = str(row["id"])
        if scenario_id in seen:
            raise ValueError(f"Scenario id bị trùng: {scenario_id}")
        seen.add(scenario_id)
        if not isinstance(row.get("turns"), list) or not row["turns"]:
            raise ValueError(f"Scenario {scenario_id} phải có turns.")
        row.setdefault("tags", [])
    return rows


def _outfit_keys(response: dict[str, Any]) -> list[str]:
    return [
        ",".join(sorted(str(item_id) for item_id in outfit.get("item_ids", [])))
        for outfit in response.get("outfits", [])
        if isinstance(outfit, dict) and outfit.get("item_ids")
    ]


def score_scenario(
    scenario: dict[str, Any],
    responses: list[dict[str, Any]],
    catalog_ids: set[str],
    *,
    rules_before: int,
    rules_after: int,
) -> dict[str, Any]:
    expect = scenario.get("expect") or {}
    all_outfits = [outfit for response in responses for outfit in response.get("outfits", []) if isinstance(outfit, dict)]
    all_ids = [str(item_id) for outfit in all_outfits for item_id in outfit.get("item_ids", [])]
    forbidden = {str(value) for value in expect.get("forbidden_item_ids", [])}
    final = responses[-1] if responses else {}
    final_state = final.get("state") or {}
    final_outfits = final.get("outfits") or []
    final_ids = {str(item_id) for outfit in final_outfits for item_id in outfit.get("item_ids", [])}

    catalog_only = all(item_id in catalog_ids for item_id in all_ids)
    hard_constraints = not (set(all_ids) & forbidden)

    anchor_ok = True
    anchor = expect.get("anchor_item_id")
    if anchor:
        anchor_ok = bool(all_outfits) and all(str(anchor) in {str(v) for v in outfit.get("item_ids", [])} for outfit in all_outfits)

    excluded = {str(value) for value in final_state.get("excluded_item_ids", [])}
    exclusion_ok = True
    if expect.get("excluded_item_ids"):
        wanted = {str(value) for value in expect["excluded_item_ids"]}
        exclusion_ok = wanted <= excluded and not (wanted & final_ids)
    elif expect.get("excluded_from_final"):
        exclusion_ok = bool(excluded) and not (excluded & final_ids)

    correction_checks: list[bool] = []
    if expect.get("excluded_from_final") or expect.get("excluded_item_ids"):
        correction_checks.append(exclusion_ok)
    if "formality_delta" in expect:
        correction_checks.append(final_state.get("formality_delta") == expect["formality_delta"])
    if "temperature_delta" in expect:
        correction_checks.append(final_state.get("temperature_delta") == expect["temperature_delta"])
    correction_success = all(correction_checks) if correction_checks else True

    seen: set[str] = set()
    repeated = False
    for response in responses:
        for key in _outfit_keys(response):
            if key in seen:
                repeated = True
            seen.add(key)
    if not expect.get("no_repeated_outfit"):
        repeated = False

    first_outfit_turn = next(
        (index for index, response in enumerate(responses, start=1) if response.get("outfits")),
        len(responses) + 1,
    )
    source_values = {str(response.get("source") or "") for response in responses}
    no_write = rules_after == rules_before if expect.get("no_profile_write") else True
    errors = [response.get("_error") for response in responses if response.get("_error")]

    return {
        "id": scenario.get("id"),
        "tags": scenario.get("tags", []),
        "catalog_only": catalog_only,
        "hard_constraints": hard_constraints,
        "anchor_exclusion": anchor_ok and exclusion_ok,
        "correction_success": correction_success,
        "turns_to_outfit": first_outfit_turn,
        "repeated_outfit": repeated,
        "fallback_used": bool(source_values & {"mock", "fallback", "ai+fallback"}),
        "no_unconfirmed_profile_write": no_write,
        "errors": errors,
    }


def _percent(values: list[bool]) -> float:
    return round(100.0 * sum(bool(value) for value in values) / len(values), 2) if values else 100.0


def aggregate_results(
    results: list[dict[str, Any]], *, run_metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    metrics = {
        "catalog_only_rate": _percent([row["catalog_only"] for row in results]),
        "hard_constraint_pass_rate": _percent([row["hard_constraints"] for row in results]),
        "anchor_exclusion_adherence": _percent([row["anchor_exclusion"] for row in results]),
        "correction_success_rate": _percent([row["correction_success"] for row in results]),
        "no_unconfirmed_profile_write_rate": _percent([row["no_unconfirmed_profile_write"] for row in results]),
        "average_turns_to_outfit": round(sum(row["turns_to_outfit"] for row in results) / len(results), 2) if results else 0.0,
        "repeated_outfit_rate": _percent([row["repeated_outfit"] for row in results]),
        "fallback_rate": _percent([row["fallback_used"] for row in results]),
    }
    gates = {
        "catalog_only_100": metrics["catalog_only_rate"] == 100.0,
        "hard_constraints_100": metrics["hard_constraint_pass_rate"] == 100.0,
        "anchor_exclusion_100": metrics["anchor_exclusion_adherence"] == 100.0,
        "no_unconfirmed_profile_write_100": metrics["no_unconfirmed_profile_write_rate"] == 100.0,
    }
    return {
        "run": dict(run_metadata or {}),
        "scenario_count": len(results),
        "metrics": metrics,
        "gates": gates,
        "promoted": all(gates.values()),
        "failures": [row.get("id", "unknown") for row in results if not all((
            row["catalog_only"], row["hard_constraints"], row["anchor_exclusion"],
            row["correction_success"], row["no_unconfirmed_profile_write"], not row.get("errors"),
        ))],
        "note": "Mock evaluator kiểm tra invariant tất định; chất lượng recommendation/model thật chưa được đánh giá.",
    }


class _StaticWeather:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = {
            "available": True, "city": "Hà Nội", "date": "2026-11-10",
            "t_min": 22.0, "t_max": 28.0, "rain_prob": 10, "code": 1,
            "summary_vi": "Hà Nội, 22–28°C",
            **(values or {}),
        }

    async def get(self, **_: Any) -> dict[str, Any]:
        return dict(self.values)


class _FailingProvider:
    mode = "ai"

    async def chat_json(self, **_: Any) -> dict[str, Any]:
        raise TimeoutError("golden provider failure")

    async def aclose(self) -> None:
        return None


def run_scenarios(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    os.environ["SHADOW_MODE"] = "mock"
    os.environ["SHADOW_RATE_LIMIT"] = "off"
    os.environ["SHADOW_ACCESS_CODE"] = ""
    os.environ["SHADOW_DEMO_SEED"] = "default"

    from fastapi.testclient import TestClient
    from main import app
    from providers import MockProvider

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="shadow-stylist-eval-") as temp_root, TestClient(app) as client:
        original_provider = app.state.provider
        for index, scenario in enumerate(scenarios):
            scenario_root = Path(temp_root) / f"{index:03d}-{scenario['id']}"
            os.environ["SHADOW_WARDROBE_DIR"] = str(scenario_root)
            app.state.wardrobe_store = None
            app.state.stylist_chat_registry = None
            app.state.wardrobe_weather = _StaticWeather(scenario.get("weather"))
            app.state.provider = _FailingProvider() if (scenario.get("provider_error") or scenario.get("provider_bad_json")) else MockProvider()

            seeded = client.post("/api/wardrobe/seed-demo?set=default")
            if seeded.status_code != 200:
                raise RuntimeError(f"Không seed được scenario {scenario['id']}: {seeded.text}")
            items = seeded.json()["items"]
            remove_categories = set(scenario.get("remove_categories") or [])
            for item in items:
                if item.get("category") in remove_categories:
                    client.delete(f"/api/wardrobe/items/{item['id']}")
            for rule in scenario.get("rules") or []:
                response = client.post("/api/wardrobe/rules", json={"text": rule, "taught_by": "Golden Set"})
                if response.status_code != 200:
                    raise RuntimeError(f"Không tạo được rule cho {scenario['id']}: {response.text}")

            catalog_ids = {str(item["id"]) for item in client.get("/api/wardrobe/items").json()}
            rules_before = len(client.get("/api/wardrobe/rules").json())
            responses: list[dict[str, Any]] = []
            session_id: str | None = None
            for turn in scenario["turns"]:
                payload = dict(turn)
                if session_id:
                    payload["session_id"] = session_id
                response = client.post("/api/stylist/chat", json=payload)
                if response.status_code == 200:
                    body = response.json()
                    session_id = body.get("session_id") or session_id
                else:
                    body = {"_error": f"HTTP {response.status_code}: {response.text}", "outfits": [], "state": {}}
                responses.append(body)
            rules_after = len(client.get("/api/wardrobe/rules").json())
            results.append(score_scenario(
                scenario, responses, catalog_ids, rules_before=rules_before, rules_after=rules_after
            ))
        app.state.provider = original_provider
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--details", action="store_true", help="In kết quả từng scenario.")
    args = parser.parse_args()
    scenarios = load_scenarios(args.scenarios)
    results = run_scenarios(scenarios)
    import hashlib

    from providers import compiler_model
    from stylist import STYLIST_SYSTEM_PROMPT, stylist_model

    report = aggregate_results(results, run_metadata={
        "evaluator_version": 1,
        "mode": os.getenv("SHADOW_MODE", "mock"),
        "stylist_model": stylist_model(),
        "compiler_model": compiler_model(),
        "prompt_sha256": hashlib.sha256(STYLIST_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12],
        "scenario_file": str(args.scenarios),
    })
    if args.details:
        report["results"] = results
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["promoted"] and not report["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

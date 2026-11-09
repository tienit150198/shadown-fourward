from __future__ import annotations

from pathlib import Path

from evals.run_stylist_conversations import aggregate_results, load_scenarios, score_scenario


EVAL_FILE = Path(__file__).resolve().parents[1] / "evals" / "stylist_conversations.json"


def test_golden_set_has_required_coverage():
    scenarios = load_scenarios(EVAL_FILE)
    assert len(scenarios) >= 30
    tags = {tag for scenario in scenarios for tag in scenario["tags"]}
    assert {
        "đi_làm", "đi_học", "đám_cưới", "đi_chơi", "nóng", "mưa", "lạnh",
        "anchor", "đang_giặt", "taught_rule", "provider_error", "missing_category", "prompt_injection",
    } <= tags


def test_score_scenario_checks_catalog_constraints_and_corrections():
    scenario = {
        "id": "golden",
        "tags": ["anchor", "đang_giặt"],
        "expect": {
            "anchor_item_id": "b1",
            "excluded_item_ids": ["t1"],
            "forbidden_item_ids": ["s1"],
            "no_repeated_outfit": True,
            "no_profile_write": True,
        },
    }
    responses = [
        {"phase": "recommendation", "source": "mock", "outfits": [{"item_ids": ["t2", "b1"]}],
         "state": {"anchor_item_id": "b1", "excluded_item_ids": []}},
        {"phase": "recommendation", "source": "mock", "outfits": [{"item_ids": ["t3", "b1"]}],
         "state": {"anchor_item_id": "b1", "excluded_item_ids": ["t1"]}},
    ]
    result = score_scenario(scenario, responses, {"t1", "t2", "t3", "b1", "s1"}, rules_before=2, rules_after=2)
    assert result["catalog_only"] is True
    assert result["hard_constraints"] is True
    assert result["anchor_exclusion"] is True
    assert result["correction_success"] is True
    assert result["turns_to_outfit"] == 1
    assert result["repeated_outfit"] is False
    assert result["no_unconfirmed_profile_write"] is True


def test_aggregate_fails_promotion_when_any_invariant_breaks():
    good = {
        "catalog_only": True, "hard_constraints": True, "anchor_exclusion": True,
        "correction_success": True, "turns_to_outfit": 1, "repeated_outfit": False,
        "fallback_used": True, "no_unconfirmed_profile_write": True,
    }
    bad = {**good, "catalog_only": False}
    report = aggregate_results([good, bad], run_metadata={"mode": "mock", "stylist_model": "mock-v1"})
    assert report["metrics"]["catalog_only_rate"] == 50.0
    assert report["gates"]["catalog_only_100"] is False
    assert report["promoted"] is False
    assert report["run"] == {"mode": "mock", "stylist_model": "mock-v1"}

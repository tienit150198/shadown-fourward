from __future__ import annotations

import json
from typing import Any

from recipe_catalog import RecipeCondition
from session_types import ControlSession

# SKI: Overshoot prompt matching, OpenAI tool-argument parsing and SDP helpers from
# GlassKit were removed together with the WebRTC runtimes.


def completed_task_ids_for_session(session: ControlSession) -> set[str]:
    if session.recipe is None:
        return set()
    if session.phase == "COMPLETED":
        return {task.id for task in session.recipe.tasks}
    if session.current_step_id is None:
        return set()
    current_index = session.step_index_by_id.get(session.current_step_id)
    if current_index is None:
        return set()
    completed: set[str] = set()
    for task in session.recipe.tasks:
        indices = [
            index
            for index, step in enumerate(session.recipe.steps)
            if step.task_id == task.id
        ]
        if indices and max(indices) < current_index:
            completed.add(task.id)
    return completed


def extract_result_value(result: dict[str, Any], path: str) -> Any:
    current: Any = result
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def matches_condition(condition: RecipeCondition, value: int | float) -> bool:
    if condition.eq is not None and value != condition.eq:
        return False
    if condition.gt is not None and value <= condition.gt:
        return False
    if condition.gte is not None and value < condition.gte:
        return False
    if condition.lt is not None and value >= condition.lt:
        return False
    if condition.lte is not None and value > condition.lte:
        return False
    return True


def value_meeting_condition(condition: RecipeCondition | None, fallback: int | float = 10) -> int | float:
    """Pick a number that satisfies `condition` (used by the mock vision detector)."""
    if condition is None:
        return fallback
    if isinstance(condition.eq, (int, float)) and not isinstance(condition.eq, bool):
        return condition.eq
    low = None
    if condition.gte is not None:
        low = condition.gte
    if condition.gt is not None:
        low = condition.gt + 1 if low is None else max(low, condition.gt + 1)
    high = None
    if condition.lte is not None:
        high = condition.lte
    if condition.lt is not None:
        high = condition.lt - 1 if high is None else min(high, condition.lt - 1)
    if low is not None:
        return low
    if high is not None:
        return high
    return fallback


def parse_structured_result(raw_result: Any) -> dict[str, Any] | None:
    if isinstance(raw_result, dict):
        return raw_result
    if not isinstance(raw_result, str):
        return None
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None

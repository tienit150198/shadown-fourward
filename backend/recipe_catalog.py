from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from session_constants import EvaluationMode


class RecipeTask(BaseModel):
    id: str
    text: str


class RecipeDetector(BaseModel):
    prompt: str
    field: Literal["ingredients", "color", "state", "flag", "level"]


class RecipeCondition(BaseModel):
    eq: int | float | str | bool | None = None
    gt: int | float | None = None
    gte: int | float | None = None
    lt: int | float | None = None
    lte: int | float | None = None


class RecipeMilestone(BaseModel):
    count: int
    speech: str | None = None
    next_step_id: str | None = None


class RecipeStep(BaseModel):
    id: str
    task_id: str
    evaluation_mode: EvaluationMode
    detector_key: str
    expected_value: str | int | float | bool | None = None
    ignored_values: list[Any] = Field(default_factory=list)
    ignore_values: list[Any] = Field(default_factory=list)
    value_path: str | None = None
    progress_condition: RecipeCondition | None = None
    progress_once_speech: str | None = None
    complete_condition: RecipeCondition | None = None
    complete_speech: str | None = None
    next_step_id: str | None = None
    target_count: int | None = None
    milestones: list[RecipeMilestone] = Field(default_factory=list)
    count_edge: str | None = None
    rearm_condition: str | None = None
    speak_on_observation_change_only: bool = False
    on_enter_speech: str | None = None
    mismatch_speech: str | None = None
    success_speech: str | None = None
    progress_value: str | None = None
    complete_value: str | None = None
    complete_on: bool | None = None
    # --- SKI extensions (optional, backward compatible with GlassKit recipes) ---
    instruction: str | None = None
    tip: str | None = None
    warning: str | None = None
    reference_image: str | None = None
    allow_dynamic_hint: bool = True
    # Teach self-check result (skill_verify.py): {"checked", "agree", "status", "reason", ...}
    verify: dict[str, Any] | None = None

    def ignored_values_list(self) -> list[Any]:
        return [*self.ignored_values, *self.ignore_values]


class Recipe(BaseModel):
    id: str
    display_name: str
    start_step_id: str
    tasks: list[RecipeTask]
    detectors: dict[str, RecipeDetector]
    steps: list[RecipeStep]
    # --- SKI extensions (optional) ---
    language: str = "vi"
    learned_from: str | None = None
    summary: str | None = None
    created_at: str | None = None
    cover_image: str | None = None
    # Teach self-check summary over all steps (skill_verify.py): {"score", "checked", "agree", ...}
    quality: dict[str, Any] | None = None


class RecipeCatalog:
    """SKI: thin wrapper over a skill store (see skill_store.SkillStore).

    GlassKit's file-glob catalog and inventory-based `best_match` were removed because
    SKI users pick a skill explicitly.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def list_entries(self) -> list[dict[str, str]]:
        return self._store.list_entries()

    def load(self, recipe_id: str) -> Recipe:
        try:
            return self._store.get(recipe_id)
        except KeyError as error:
            raise ValueError(f"Unknown recipe id: {recipe_id}") from error

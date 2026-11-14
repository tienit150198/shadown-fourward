"""Bounded lesson controls shared by Live and regular conversation tools."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class LessonControlArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    command: Literal['start_teaching','finish_teaching','review','rename','update_step','insert_step','remove_step','reorder_steps','save','start_guidance','next','previous','repeat','stop']
    skill_id: str | None = Field(default=None, max_length=64)
    revision: int | None = Field(default=None, ge=0, description='Current lesson revision from execution/context; required for edits and save so stale requests cannot overwrite newer changes.')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    step_number: int | None = Field(default=None, ge=1, le=31)
    instruction: str | None = Field(default=None, min_length=1, max_length=800)
    detector_prompt: str | None = Field(default=None, min_length=1, max_length=1500, description='Required when changing an instruction: describe the NEW visible completion condition; detector returns JSON state="done" or "not_done". Do not reuse an obsolete completion test.')
    tip: str | None = Field(default=None, max_length=400)
    warning: str | None = Field(default=None, max_length=400)
    order: list[int] | None = Field(default=None, min_length=1, max_length=30, description='Permutation of ALL current one-based step numbers.')


class LessonStepSnapshot(BaseModel):
    model_config = ConfigDict(extra='ignore')
    number: int = Field(ge=1,le=30)
    instruction: str = Field(max_length=800)
    tip: str = Field(default='',max_length=400)
    warning: str = Field(default='',max_length=400)


class LessonSnapshot(BaseModel):
    model_config = ConfigDict(extra='ignore')
    status: str = Field(max_length=40)
    title: str = Field(max_length=160)
    revision: int = Field(ge=0)
    step: int = Field(default=0,ge=0,le=30)
    skill_id: str | None = Field(default=None,max_length=64)
    steps: list[LessonStepSnapshot] = Field(default_factory=list,max_length=30)


DESCRIPTION = ('Distinguish user learning from user teaching AI: asking to learn/practice must use start_guidance, never start_teaching. First read list_skills to select a matching saved skill_id. A correction such as wanting to learn must change your action immediately. ' 
    'Control teaching, draft review and learned-skill practice IN THE CURRENT CAMERA. '
    'start_teaching records spoken demonstration; finish_teaching compiles a draft for review, it DOES NOT save. '
    'Use review to show steps; skill_id loads a saved lesson as a NEW editable copy. '
    'Use rename/update_step/insert_step/remove_step/reorder_steps for explicitly requested adjustments; provide current revision. '
    'New steps and instruction changes require a matching detector_prompt for the new visible completion condition. '
    'Never invent observed steps or silently delete them. save persists only after explicit user confirmation; provide revision. '
    'start_guidance uses a saved skill_id; next/previous/repeat control practice. stop cancels without saving. '
    'No manual editor or navigation required. Wait for actual client execution; review/compiling is not saved, and loading is not ready.')

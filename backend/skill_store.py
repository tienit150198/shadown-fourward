"""File-based skill storage for SHADOW (new — not part of GlassKit).

Layout (root = backend/skills):
    <root>/<id>.json                    skill JSON (GlassKit Recipe + SHADOW fields)
    <root>/<id>/frames/*.jpg            media; step.reference_image is relative to <root>/<id>/
    <root>/_drafts/<draft_id>/frames/*  frames saved by the compiler before the teacher saves
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from recipe_catalog import Recipe

logger = logging.getLogger("uvicorn.error")

DRAFTS_DIRNAME = "_drafts"
MEDIA_URL_PREFIX = "/skills-media"
_MAX_ID_LENGTH = 64
MEDIA_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


class SkillNotFound(KeyError):
    pass


class SkillValidationError(ValueError):
    pass


def slugify_id(value: str) -> str:
    """Sanitize to a slug of [a-z0-9-] (Vietnamese diacritics are folded)."""
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)[:_MAX_ID_LENGTH].strip("-")
    return text


def sanitize_draft_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "", str(value or ""))[:_MAX_ID_LENGTH]
    return text


def is_safe_relative_path(rel: str) -> bool:
    if not rel or not isinstance(rel, str):
        return False
    if "\\" in rel or rel.startswith("/"):
        return False
    parts = PurePosixPath(rel).parts
    return bool(parts) and all(part not in ("..", ".", "") for part in parts)


def media_url(skill_id: str, rel: str | None) -> str | None:
    if not rel or not is_safe_relative_path(rel):
        return None
    return f"{MEDIA_URL_PREFIX}/{skill_id}/{rel}"


def semantic_errors(recipe: Recipe) -> list[str]:
    errors: list[str] = []
    if not recipe.steps:
        errors.append("skill must have at least one step")
    step_ids = [step.id for step in recipe.steps]
    if len(set(step_ids)) != len(step_ids):
        errors.append("step ids must be unique")
    task_ids = {task.id for task in recipe.tasks}
    if recipe.start_step_id not in step_ids:
        errors.append(f"start_step_id {recipe.start_step_id!r} is not a step id")
    for step in recipe.steps:
        if step.detector_key not in recipe.detectors:
            errors.append(f"step {step.id!r}: unknown detector_key {step.detector_key!r}")
        if step.task_id not in task_ids:
            errors.append(f"step {step.id!r}: unknown task_id {step.task_id!r}")
        if step.next_step_id is not None and step.next_step_id not in step_ids:
            errors.append(f"step {step.id!r}: unknown next_step_id {step.next_step_id!r}")
        for milestone in step.milestones:
            if milestone.next_step_id is not None and milestone.next_step_id not in step_ids:
                errors.append(
                    f"step {step.id!r}: unknown milestone next_step_id {milestone.next_step_id!r}"
                )
        if step.reference_image is not None and not is_safe_relative_path(step.reference_image):
            errors.append(f"step {step.id!r}: unsafe reference_image path")
    if recipe.cover_image is not None and not is_safe_relative_path(recipe.cover_image):
        errors.append("unsafe cover_image path")
    return errors


class SkillStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.drafts_root = self.root / DRAFTS_DIRNAME

    # ---- paths -------------------------------------------------------------
    def _json_path(self, skill_id: str) -> Path:
        return self.root / f"{skill_id}.json"

    def media_dir(self, skill_id: str) -> Path:
        return self.root / slugify_id(skill_id)

    def media_path(self, skill_id: str, rel: str | None) -> Path | None:
        """Absolute path for a media file of a skill, or None if unsafe / missing."""
        if not rel or not is_safe_relative_path(rel) or "\x00" in rel:
            return None
        if PurePosixPath(rel).suffix.lower() not in MEDIA_SUFFIXES:
            return None  # only images are ever served (never skill JSON or other files)
        if skill_id == DRAFTS_DIRNAME:
            # Draft previews: /skills-media/_drafts/<draft_id>/frames/<file>
            base = self.drafts_root
        else:
            if not slugify_id(skill_id):
                return None  # "" would make the skills root itself the media dir
            base = self.media_dir(skill_id)
        path = base / rel
        try:
            # No symlink escapes: the resolved file must stay inside the resolved base dir.
            path.resolve().relative_to(base.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_file() else None

    def draft_dir(self, draft_id: str) -> Path:
        return self.drafts_root / sanitize_draft_id(draft_id)

    # ---- read --------------------------------------------------------------
    def exists(self, skill_id: str) -> bool:
        sid = slugify_id(skill_id)
        return bool(sid) and self._json_path(sid).is_file()

    def get(self, skill_id: str) -> Recipe:
        sid = slugify_id(skill_id)
        path = self._json_path(sid) if sid else None
        if path is None or not path.is_file():
            raise SkillNotFound(skill_id)
        try:
            return Recipe.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as error:
            raise SkillValidationError(f"invalid skill {sid}: {error}") from error

    # RecipeCatalog-compatible alias
    load = get

    def get_dict(self, skill_id: str) -> dict[str, Any]:
        """Full skill JSON plus `*_url` fields for images."""
        recipe = self.get(skill_id)
        data = recipe.model_dump(mode="json")
        data["cover_image_url"] = media_url(recipe.id, recipe.cover_image)
        for step in data.get("steps", []):
            step["reference_image_url"] = media_url(recipe.id, step.get("reference_image"))
        return data

    def summary(self, recipe: Recipe) -> dict[str, Any]:
        cover = recipe.cover_image
        if cover is None:
            cover = next(
                (step.reference_image for step in recipe.steps if step.reference_image),
                None,
            )
        return {
            "id": recipe.id,
            "display_name": recipe.display_name,
            "learned_from": recipe.learned_from,
            "summary": recipe.summary,
            "steps": len(recipe.steps),
            "cover_image_url": media_url(recipe.id, cover),
            "created_at": recipe.created_at,
        }

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return items
        for path in sorted(self.root.glob("*.json")):
            try:
                recipe = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
            except (ValidationError, OSError, ValueError) as error:
                logger.warning("skipping invalid skill file %s: %s", path.name, error)
                continue
            items.append(self.summary(recipe))
        items.sort(key=lambda row: (row.get("created_at") or "", row["id"]), reverse=True)
        return items

    def list_entries(self) -> list[dict[str, str]]:
        return [{"id": row["id"]} for row in self.list()]

    # ---- write -------------------------------------------------------------
    def save(self, skill: dict[str, Any]) -> Recipe:
        if not isinstance(skill, dict):
            raise SkillValidationError("skill must be a JSON object")
        data = dict(skill)
        draft_id = data.pop("draft_id", None)
        sid = slugify_id(str(data.get("id") or data.get("display_name") or ""))
        if not sid:
            raise SkillValidationError("skill id is empty after sanitizing")
        data["id"] = sid
        try:
            recipe = Recipe.model_validate(data)
        except ValidationError as error:
            raise SkillValidationError(str(error)) from error
        errors = semantic_errors(recipe)
        if errors:
            raise SkillValidationError("; ".join(errors))

        self.root.mkdir(parents=True, exist_ok=True)
        if draft_id:
            self._promote_draft(str(draft_id), sid)

        payload = recipe.model_dump(mode="json", exclude_none=True)
        tmp = self._json_path(sid).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._json_path(sid))
        return recipe

    def _promote_draft(self, draft_id: str, skill_id: str) -> None:
        clean = sanitize_draft_id(draft_id)
        if not clean:
            return
        source = self.drafts_root / clean
        if not source.is_dir():
            logger.info("draft %s not found (already promoted?)", clean)
            return
        target = self.media_dir(skill_id)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        shutil.rmtree(source, ignore_errors=True)

    def delete(self, skill_id: str) -> bool:
        sid = slugify_id(skill_id)
        if not sid:
            return False
        path = self._json_path(sid)
        existed = path.is_file()
        if existed:
            path.unlink()
        media = self.media_dir(sid)
        if media.is_dir() and media.name != DRAFTS_DIRNAME:
            shutil.rmtree(media, ignore_errors=True)
        return existed

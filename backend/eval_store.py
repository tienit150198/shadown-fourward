"""Labelled frames for measuring detector accuracy (SHADOW eval).

Layout: <evals_root>/<skill_id>/<step_id>/<label>/<timestamp>.jpg, label in {done, not_done}.
Frames are captured from the phone in Guide → "Thu mẫu eval", then scored with eval_detectors.py.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import time
from pathlib import Path

LABELS = ("done", "not_done")
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_PER_LABEL = 500  # per skill/step/label folder (bounds disk use of a public demo)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DATA_URL_RE = re.compile(r"^data:image/(jpeg|jpg|png);base64,(.+)$", re.DOTALL)


class EvalError(ValueError):
    pass


def default_evals_root(backend_dir: Path) -> Path:
    override = (os.getenv("SHADOW_EVALS_DIR") or "").strip()
    return Path(override) if override else backend_dir / "evals"


def _check_id(value: str, what: str) -> str:
    value = (value or "").strip()
    if not _ID_RE.match(value):
        raise EvalError(f"{what} không hợp lệ: {value!r}")
    return value


def decode_image(data_url: str) -> tuple[bytes, str]:
    match = _DATA_URL_RE.match((data_url or "").strip())
    if not match:
        raise EvalError("Ảnh phải là data URL JPEG/PNG.")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError) as error:
        raise EvalError("Ảnh base64 hỏng.") from error
    if len(raw) > MAX_IMAGE_BYTES:
        raise EvalError("Ảnh quá lớn.")
    if raw[:3] == b"\xff\xd8\xff":
        return raw, ".jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return raw, ".png"
    raise EvalError("Ảnh không phải JPEG/PNG.")


class EvalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, skill_id: str, step_id: str, label: str, image: str) -> Path:
        skill_id = _check_id(skill_id, "skill_id")
        step_id = _check_id(step_id, "step_id")
        if label not in LABELS:
            raise EvalError(f"label phải là một trong {LABELS}")
        if len(image or "") > MAX_IMAGE_BYTES * 4 // 3 + 64:
            raise EvalError("Ảnh quá lớn.")  # before base64-decoding it
        raw, ext = decode_image(image)
        folder = self.root / skill_id / step_id / label
        if len(list_images(folder)) >= MAX_PER_LABEL:
            raise EvalError(f"Đã đủ {MAX_PER_LABEL} mẫu cho nhãn này.")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{int(time.time() * 1000)}{ext}"
        path.write_bytes(raw)
        return path

    def counts(self, skill_id: str) -> dict[str, dict[str, int]]:
        skill_id = _check_id(skill_id, "skill_id")
        result: dict[str, dict[str, int]] = {}
        base = self.root / skill_id
        if not base.is_dir():
            return result
        for step_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            result[step_dir.name] = {
                label: len(list_images(step_dir / label)) for label in LABELS
            }
        return result

    def delete_last(self, skill_id: str, step_id: str, label: str) -> bool:
        skill_id = _check_id(skill_id, "skill_id")
        step_id = _check_id(step_id, "step_id")
        if label not in LABELS:
            raise EvalError(f"label phải là một trong {LABELS}")
        images = list_images(self.root / skill_id / step_id / label)
        if not images:
            return False
        images[-1].unlink()
        return True


def list_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )

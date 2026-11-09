"""File-based wardrobe storage for the built-in "Trợ lý tủ đồ" skill (new in SHADOW).

Layout (root = backend/wardrobe, override with SHADOW_WARDROBE_DIR):
    <root>/items.json          list of wardrobe items
    <root>/rules.json          styling rules taught by a person ({id, text, taught_by, created_at})
    <root>/images/<id>.jpg     item photos (always re-encoded to JPEG)
    <root>/tryon/<uuid>.jpg    photo try-on results

Media is served at /wardrobe-media/<rel> (see wardrobe_api.py) and only for safe relative
paths that resolve inside <root>.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

BACKEND_DIR = Path(__file__).resolve().parent
MEDIA_URL_PREFIX = "/wardrobe-media"

CATEGORIES = ("top", "bottom", "dress", "outerwear", "shoes", "accessory")
CATEGORY_LABELS_VI = {
    "top": "Áo",
    "bottom": "Quần/chân váy",
    "dress": "Váy liền/đầm",
    "outerwear": "Áo khoác",
    "shoes": "Giày dép",
    "accessory": "Phụ kiện",
}
# Fixed occasion vocabulary (the tagger maps to these; free extras are kept after them).
OCCASIONS = (
    "đi làm",
    "đi học",
    "đi chơi",
    "hẹn hò",
    "dự tiệc",
    "đám cưới",
    "phỏng vấn",
    "thể thao",
    "du lịch",
    "đi biển",
    "ở nhà",
    "lễ tết",
)
STYLES = ("công sở", "thường ngày", "thể thao", "dự tiệc", "thanh lịch", "năng động", "cá tính", "nữ tính")
SEASONS = ("xuân", "hạ", "thu", "đông")

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # decoded upload cap
MAX_IMAGE_DATA_URL_CHARS = (MAX_IMAGE_BYTES * 4) // 3 + 64
MAX_IMAGE_SIDE = 1024
JPEG_QUALITY = 85
MAX_TRYON_RESULTS = 100
MAX_ITEMS = 500  # disk/LLM-prompt bound for a public demo
MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard (checked before decoding pixels)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_DATA_URL_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


class WardrobeError(ValueError):
    """Invalid input (maps to HTTP 422)."""


class ItemNotFound(KeyError):
    pass


def wardrobe_root() -> Path:
    override = (os.getenv("SHADOW_WARDROBE_DIR") or "").strip()
    return Path(override) if override else BACKEND_DIR / "wardrobe"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_safe_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def fold_vi(text: Any) -> str:
    """Lowercase + strip Vietnamese diacritics (for keyword matching only)."""
    value = str(text or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


def is_safe_relative_path(rel: Any) -> bool:
    if not rel or not isinstance(rel, str):
        return False
    if "\\" in rel or rel.startswith("/") or "\x00" in rel:
        return False
    parts = PurePosixPath(rel).parts
    return bool(parts) and all(part not in ("..", ".", "") for part in parts)


def media_url(rel: str | None) -> str | None:
    if not rel or not is_safe_relative_path(rel):
        return None
    return f"{MEDIA_URL_PREFIX}/{rel}"


# ---- images -------------------------------------------------------------------


def decode_image_data_url(data_url: Any, *, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Decode a JPEG/PNG data URL, checking the declared type, size and magic bytes."""
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise WardrobeError("Ảnh phải là data URL (data:image/jpeg;base64,...).")
    if len(data_url) > (max_bytes * 4) // 3 + 64:
        raise WardrobeError("Ảnh quá lớn.")
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise WardrobeError("Data URL ảnh không hợp lệ.")
    mime = match.group(1).lower()
    if mime not in ("image/jpeg", "image/jpg", "image/png"):
        raise WardrobeError("Chỉ nhận ảnh JPEG hoặc PNG.")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError):
        raise WardrobeError("Ảnh base64 bị lỗi.") from None
    if not raw:
        raise WardrobeError("Ảnh trống.")
    if len(raw) > max_bytes:
        raise WardrobeError("Ảnh quá lớn.")
    is_jpeg = raw[:3] == b"\xff\xd8\xff"
    is_png = raw[:8] == b"\x89PNG\r\n\x1a\n"
    if not (is_jpeg or is_png):
        raise WardrobeError("Nội dung không phải ảnh JPEG/PNG.")
    return raw


def to_jpeg(raw: bytes, *, max_side: int = MAX_IMAGE_SIDE, quality: int = JPEG_QUALITY) -> bytes:
    """Re-encode any Pillow-readable image to an RGB JPEG no larger than max_side."""
    try:
        from PIL import Image, ImageOps
    except ImportError as error:  # pragma: no cover - Pillow is in requirements.txt
        raise WardrobeError(f"Thiếu thư viện xử lý ảnh (Pillow): {error}") from error
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise WardrobeError("Ảnh quá lớn (quá nhiều điểm ảnh).")
            if image.format == "JPEG":
                image.draft("RGB", (max_side * 2, max_side * 2))  # decode downscaled: less RAM/CPU
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except WardrobeError:
        raise
    except Exception as error:
        raise WardrobeError(f"Không đọc được ảnh: {error}") from error


def jpeg_data_url(data: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


# ---- field normalization ------------------------------------------------------

_CATEGORY_SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    # Order matters: more specific phrases first ("áo khoác" before "áo", "chân váy" before "váy").
    ("outerwear", ("outerwear", "ao khoac", "khoac", "jacket", "coat", "blazer", "jackets", "vest", "cardigan",
                   "hoodie", "ao mang to", "mang to", "ao gio", "ao phao", "ao len cardigan")),
    ("bottom", ("bottom", "chan vay", "skirt", "quan", "pants", "trousers", "jeans", "jean", "shorts", "legging", "leggings")),
    ("dress", ("dress", "dam", "vay lien", "vay", "jumpsuit", "ao dai", "gown")),
    ("shoes", ("shoes", "shoe", "giay", "dep", "sandal", "sandals", "sneaker", "sneakers", "boot", "boots", "guoc", "heels")),
    ("accessory", ("accessory", "accessories", "phu kien", "tui", "bag", "mu", "non", "hat", "kinh",
                   "khan", "scarf", "that lung", "belt", "dong ho", "watch", "trang suc", "vong", "jewelry")),
    ("top", ("top", "ao", "shirt", "t-shirt", "tshirt", "blouse", "polo", "sweater", "tank", "so mi",
             "ao thun", "ao phong", "croptop", "crop top")),
]


def normalize_category(value: Any, default: str | None = None) -> str | None:
    if isinstance(value, str):
        folded = fold_vi(value)
        if folded in CATEGORIES:
            return folded
        words = f" {re.sub(r'[^a-z0-9 -]+', ' ', folded)} "
        for category, synonyms in _CATEGORY_SYNONYMS:
            for synonym in synonyms:
                if f" {synonym} " in words:
                    return category
    return default


def _clean_text(value: Any, max_len: int) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:max_len]


def _clean_list(value: Any, *, max_items: int, max_len: int = 40, lower: bool = True) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;/]| và ", value)
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for entry in value:
        text = _clean_text(entry, max_len)
        if lower:
            text = text.lower()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


_OCCASION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "đi làm": ("di lam", "cong so", "van phong", "office", "work", "hop", "meeting"),
    "đi học": ("di hoc", "truong", "school", "lop hoc"),
    "đi chơi": ("di choi", "dao pho", "cafe", "ca phe", "casual", "thuong ngay", "hang ngay", "cuoi tuan"),
    "hẹn hò": ("hen ho", "date", "lang man"),
    "dự tiệc": ("du tiec", "tiec", "party", "su kien", "event", "sinh nhat"),
    "đám cưới": ("dam cuoi", "dam hoi", "wedding", "an cuoi", "tiec cuoi", "le cuoi"),
    "phỏng vấn": ("phong van", "interview"),
    "thể thao": ("the thao", "gym", "chay bo", "sport", "yoga", "tap"),
    "du lịch": ("du lich", "travel", "di xa", "da ngoai", "picnic", "phuot"),
    "đi biển": ("di bien", "bien", "beach"),
    "ở nhà": ("o nha", "home", "mac nha"),
    "lễ tết": ("le tet", "tet", "le hoi", "chua", "le"),
}


def match_occasions(text: Any) -> list[str]:
    """Fixed-list occasions whose keywords appear in free text (e.g. an event description)."""
    folded = f" {fold_vi(text)} "
    found = []
    for occasion, keywords in _OCCASION_KEYWORDS.items():
        if any(f" {kw} " in folded or (len(kw) > 4 and kw in folded) for kw in keywords):
            found.append(occasion)
    return found


def normalize_occasions(value: Any) -> list[str]:
    raw = _clean_list(value, max_items=10)
    fixed: list[str] = []
    extras: list[str] = []
    for entry in raw:
        if entry in OCCASIONS:
            target = [entry]
        else:
            target = match_occasions(entry)
        if target:
            for occasion in target:
                if occasion not in fixed:
                    fixed.append(occasion)
        elif entry not in extras:
            extras.append(entry)
    ordered = [occ for occ in OCCASIONS if occ in fixed]
    return (ordered + extras)[:8]


def normalize_seasons(value: Any) -> list[str]:
    mapping = {"xuan": "xuân", "spring": "xuân", "ha": "hạ", "he": "hạ", "summer": "hạ",
               "thu": "thu", "autumn": "thu", "fall": "thu", "dong": "đông", "winter": "đông"}
    out: list[str] = []
    for entry in _clean_list(value, max_items=8):
        folded = fold_vi(entry).removeprefix("mua ").strip()
        if folded in ("quanh nam", "bon mua", "all", "all season", "ca nam"):
            return list(SEASONS)
        season = mapping.get(folded) or next((v for k, v in mapping.items() if folded.startswith(k)), None)
        if season and season not in out:
            out.append(season)
    return [season for season in SEASONS if season in out]


def normalize_warmth(value: Any, default: int = 2) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(1, min(5, number))


def normalize_item_fields(raw: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Validate/clean editable item fields. With partial=True only present keys are returned."""
    if not isinstance(raw, dict):
        raise WardrobeError("Dữ liệu món đồ phải là object JSON.")
    out: dict[str, Any] = {}

    def has(key: str) -> bool:
        return key in raw and raw[key] is not None

    if has("name") or not partial:
        out["name"] = _clean_text(raw.get("name"), 80) or "Món đồ"
    if has("category") or not partial:
        category = normalize_category(raw.get("category"))
        if category is None:
            if partial:
                raise WardrobeError(f"Loại đồ không hợp lệ: {raw.get('category')!r}. Chọn: {', '.join(CATEGORIES)}.")
            category = "top"
        out["category"] = category
    if has("colors") or not partial:
        out["colors"] = _clean_list(raw.get("colors"), max_items=4, max_len=24)
    if has("style") or not partial:
        out["style"] = _clean_text(raw.get("style"), 40).lower()
    if has("warmth") or not partial:
        out["warmth"] = normalize_warmth(raw.get("warmth"))
    if has("occasions") or not partial:
        out["occasions"] = normalize_occasions(raw.get("occasions"))
    if has("seasons") or not partial:
        out["seasons"] = normalize_seasons(raw.get("seasons"))
    for key, max_len in (("pattern", 30), ("material", 40), ("notes", 300), ("description_en", 160)):
        if has(key) or not partial:
            text = _clean_text(raw.get(key), max_len)
            out[key] = text.lower() if key in ("pattern", "material") else text
    # garment_attrs hook: richer attributes (color_hex, formality, fit, sleeve, length, fabric, …) when present
    from garment_attrs import normalize_attr_fields

    out.update(normalize_attr_fields(raw))
    return out


# ---- store --------------------------------------------------------------------


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


class WardrobeStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.items_path = self.root / "items.json"
        self.rules_path = self.root / "rules.json"
        self.images_dir = self.root / "images"
        self.tryon_dir = self.root / "tryon"
        self._lock = threading.RLock()

    # ---- paths ---------------------------------------------------------------
    def media_path(self, rel: str | None) -> Path | None:
        """Absolute path of a media file under root, or None if unsafe / missing."""
        if not is_safe_relative_path(rel):
            return None
        path = (self.root / str(rel)).resolve()
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return None
        return path if path.is_file() else None

    def item_image_path(self, item: dict[str, Any]) -> Path | None:
        return self.media_path(item.get("image"))

    # ---- items ---------------------------------------------------------------
    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        data = dict(item)
        data["image_url"] = media_url(item.get("image"))
        from garment_attrs import public_attrs_vi  # garment_attrs hook: Vietnamese labels of attribute codes

        data["attrs_vi"] = public_attrs_vi(item)
        return data

    def _load_items(self) -> list[dict[str, Any]]:
        rows = [row for row in _read_json_list(self.items_path) if is_safe_id(row.get("id"))]
        return self._migrate_attrs(rows)

    def _migrate_attrs(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """garment_attrs hook: items saved before the attribute schema get derived attributes once (persisted)."""
        from garment_attrs import fill_attrs, needs_migration

        if not any(needs_migration(row) for row in rows):
            return rows
        with self._lock:
            migrated = []
            for row in rows:
                if needs_migration(row):
                    path = self.media_path(row.get("image"))
                    try:
                        row = fill_attrs(row, path.read_bytes() if path else None)
                    except Exception:  # never block loading the wardrobe on one bad row/photo
                        pass
                migrated.append(row)
            try:
                _atomic_write_json(self.items_path, migrated)
            except OSError:
                pass
            return migrated

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.public_item(item) for item in self._load_items()]

    def get_item(self, item_id: str) -> dict[str, Any]:
        if not is_safe_id(item_id):
            raise ItemNotFound(item_id)
        with self._lock:
            for item in self._load_items():
                if item["id"] == item_id:
                    return self.public_item(item)
        raise ItemNotFound(item_id)

    def create_item(
        self,
        fields: dict[str, Any],
        image_jpeg: bytes,
        *,
        item_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a new item. `image_jpeg` must already be JPEG bytes (see to_jpeg)."""
        item_id = item_id or new_id("it")
        if not is_safe_id(item_id):
            raise WardrobeError("id món đồ không hợp lệ.")
        if image_jpeg[:3] != b"\xff\xd8\xff":
            raise WardrobeError("Ảnh lưu phải là JPEG.")
        clean = normalize_item_fields(fields)
        rel = f"images/{item_id}.jpg"
        item = {"id": item_id, **clean, "image": rel, "created_at": now_iso()}
        if extra:
            item.update({k: v for k, v in extra.items() if k not in item or k == "created_at"})
        from garment_attrs import fill_attrs  # garment_attrs hook: derive missing attributes (+ photo colour)

        item = fill_attrs(item, image_jpeg)
        with self._lock:
            items = self._load_items()
            if any(row["id"] == item_id for row in items):
                raise WardrobeError(f"Món đồ {item_id} đã tồn tại.")
            if len(items) >= MAX_ITEMS:
                raise WardrobeError(f"Tủ đồ đã đầy (tối đa {MAX_ITEMS} món).")
            self.images_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.images_dir / f".{item_id}.{uuid.uuid4().hex[:6]}.tmp"
            tmp.write_bytes(image_jpeg)
            os.replace(tmp, self.root / rel)
            items.append(item)
            _atomic_write_json(self.items_path, items)
        return self.public_item(item)

    def update_item(self, item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not is_safe_id(item_id):
            raise ItemNotFound(item_id)
        clean = normalize_item_fields(patch, partial=True)
        with self._lock:
            items = self._load_items()
            for index, item in enumerate(items):
                if item["id"] == item_id:
                    updated = {**item, **clean, "updated_at": now_iso()}
                    if clean.get("category") not in (None, item.get("category")):
                        # garment_attrs hook: category changed → re-derive category-dependent attributes
                        from garment_attrs import fill_attrs

                        for key in ("subtype", "sleeve", "length", "fit"):
                            if key not in clean:
                                updated.pop(key, None)
                        updated = fill_attrs({**updated, "attrs_v": None})
                    items[index] = updated
                    _atomic_write_json(self.items_path, items)
                    return self.public_item(updated)
        raise ItemNotFound(item_id)

    def delete_item(self, item_id: str) -> bool:
        if not is_safe_id(item_id):
            return False
        with self._lock:
            items = self._load_items()
            remaining = [item for item in items if item["id"] != item_id]
            if len(remaining) == len(items):
                return False
            _atomic_write_json(self.items_path, remaining)
            image = self.images_dir / f"{item_id}.jpg"
            if image.is_file():
                image.unlink()
        return True

    def has_items(self, ids: Iterable[str]) -> set[str]:
        wanted = set(ids)
        return {item["id"] for item in self._load_items() if item["id"] in wanted}

    # ---- rules ---------------------------------------------------------------
    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in _read_json_list(self.rules_path) if is_safe_id(r.get("id"))]

    def add_rule(self, text: Any, taught_by: Any = None) -> dict[str, Any]:
        clean_text = _clean_text(text, 300)
        if not clean_text:
            raise WardrobeError("Quy tắc đang trống.")
        rule = {
            "id": new_id("r"),
            "text": clean_text,
            "taught_by": _clean_text(taught_by, 60) or "Bạn",
            "created_at": now_iso(),
        }
        with self._lock:
            rules = self.list_rules()
            if len(rules) >= 200:
                raise WardrobeError("Đã có quá nhiều quy tắc (tối đa 200).")
            rules.append(rule)
            _atomic_write_json(self.rules_path, rules)
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        if not is_safe_id(rule_id):
            return False
        with self._lock:
            rules = self.list_rules()
            remaining = [rule for rule in rules if rule["id"] != rule_id]
            if len(remaining) == len(rules):
                return False
            _atomic_write_json(self.rules_path, remaining)
        return True

    # ---- try-on results ------------------------------------------------------
    def save_tryon(self, image_jpeg: bytes) -> str:
        """Save a try-on result; returns its relative path (tryon/<uuid>.jpg)."""
        self.tryon_dir.mkdir(parents=True, exist_ok=True)
        rel = f"tryon/{uuid.uuid4().hex}.jpg"
        tmp = self.tryon_dir / f".{uuid.uuid4().hex[:8]}.tmp"
        tmp.write_bytes(image_jpeg)
        os.replace(tmp, self.root / rel)
        self._prune_tryon()
        return rel

    def _prune_tryon(self) -> None:
        files = sorted(self.tryon_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[MAX_TRYON_RESULTS:]:
            try:
                old.unlink()
            except OSError:
                pass

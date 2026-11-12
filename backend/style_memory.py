"""Teachable + learning memory of "Trợ lý tủ đồ" (new in SHADOW).

Three ways the stylist learns, all persisted as JSON in the wardrobe dir (atomic writes):

1. **Taught rules** (rules.json, owned by wardrobe_store): free Vietnamese text ("Đi đám cưới không mặc
   quần short và dép") is parsed ONCE into a structured *constraint* (compiler model; deterministic
   Vietnamese pattern parser in mock mode / as fallback), validated against a strict schema and stored
   next to the original text. "must" constraints filter candidates before the LLM and are re-checked
   (and repaired) after it; "should" constraints adjust ranking.
2. **Feedback** (style_profile.json): 👍/👎 (+ reason chips) and "Mặc bộ này" become time-decayed
   affinities per item and per attribute (color family, formality, category, style, kind, fit); the
   profile is summarised into the stylist prompt and adjusts ranking. "worn" history stops the exact
   same outfit being suggested two days in a row.
3. **Examples** (examples.json): "Đi đám cưới thì mặc như này" — a teacher-picked outfit per event,
   used as few-shot references and as a similarity bonus in ranking.

Constraint schema (normalized form; see validate_constraint):

    {
      "when":   {"events": ["đám cưới", ...],                       # OCCASIONS vocabulary; [] = any event
                 "weather": {"rain": true, "t_max_gte": 30, "t_max_lte": .., "t_min_gte": .., "t_min_lte": 18}},
      "avoid":   [selector, ...],   # an item matching ANY selector is avoided
      "prefer":  [selector, ...],   # each selector met by some item → bonus (always soft)
      "require": [selector, ...],   # EVERY selector must be met by some item of the outfit
      "strength": "must" | "should",   # applies to avoid/require
      "explanation_vi": "…"
    }
    selector (all given fields must hold — AND; values inside a list — OR):
      {"categories": ["top"|"bottom"|"dress"|"outerwear"|"shoes"|"accessory"], "kinds": ["quần short", ...],
       "colors": ["trắng"], "items": ["<item id>"], "styles": ["thể thao"], "keywords": ["áo dài"],
       "attributes": {"formality_lt|lte|gt|gte": 1-5, "warmth_lt|lte|gt|gte": 1-5,
                      "length": "short"|"long", "tone": "light"|"dark"|"loud", "fit": "...", "pattern": "...",
                      "material": "..."}}

Ranking hook for any scorer: ``rule_adjustments(outfit, context) -> [(delta, source_id, reason_vi)]`` where
``context = build_context(items, rules, memory, event=..., weather=...)``. source_id is a rule id
("r_…"), an example id ("ex_…"), "pref:<feature>" (learned taste) or "hist:worn".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import threading
import time
import unicodedata
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any, Iterable

from wardrobe_store import (
    CATEGORIES,
    CATEGORY_LABELS_VI,
    OCCASIONS,
    WardrobeError,
    WardrobeStore,
    _atomic_write_json,
    _clean_text,
    _read_json_list,
    fold_vi,
    is_safe_id,
    match_occasions,
    new_id,
    now_iso,
)
from weather import is_rainy

logger = logging.getLogger("uvicorn.error")

RULE_PARSE_TIMEOUT_S = 25.0
RULE_PARSE_MAX_TOKENS = 600
PROFILE_FILE = "style_profile.json"
EXAMPLES_FILE = "examples.json"
MAX_EVENTS = 400
MAX_WORN = 120
MAX_EXAMPLES = 60
HALF_LIFE_DAYS = 21.0
STRENGTHS = ("must", "should")
VOTES = ("up", "down", "wear")
REASONS = {
    "color": "Màu không hợp",
    "too_formal": "Quá trang trọng",
    "too_hot": "Quá nóng",
    "dislike_item": "Không thích món này",
}

# ---- garment attributes ------------------------------------------------------------------------

# canonical kind → (folded keywords, category the item must have or None)
KINDS: dict[str, tuple[tuple[str, ...], str | None]] = {
    "quần short": (("quan short", "short", "quan dui", "quan ngan", "quan sooc"), "bottom"),
    "quần jean": (("quan jean", "jean", "quan bo"), "bottom"),
    "quần tây": (("quan tay", "quan au"), "bottom"),
    "quần kaki": (("quan kaki", "kaki"), "bottom"),
    "chân váy": (("chan vay",), "bottom"),
    "sơ mi": (("so mi", "somi", "shirt"), "top"),
    "áo thun": (("ao thun", "ao phong", "t-shirt", "tshirt", "ao pull"), "top"),
    "polo": (("polo",), "top"),
    "áo hai dây": (("hai day", "ao 2 day"), "top"),
    "áo len": (("ao len", "sweater"), None),
    "áo khoác gió": (("ao khoac gio", "khoac gio", "ao gio"), "outerwear"),
    "áo chống nắng": (("chong nang",), "outerwear"),
    "blazer": (("blazer", "vest", "ao vest"), "outerwear"),
    "áo khoác": (("ao khoac", "khoac", "jacket", "hoodie", "cardigan"), "outerwear"),
    "dép": (("dep", "sandal", "dep quai", "tong"), "shoes"),
    "giày thể thao": (("giay the thao", "sneaker", "giay chay"), "shoes"),
    "giày da": (("giay da", "loafer", "giay tay", "oxford", "derby"), "shoes"),
    "giày slip-on": (("slip-on", "slip on"), "shoes"),
    "váy": (("vay lien", "vay", "dam"), "dress"),
    "túi": (("tui", "balo"), "accessory"),
    "mũ": (("mu", "non"), "accessory"),
    "kính": (("kinh",), "accessory"),
}
_KIND_KEYWORDS = sorted(((kw, kind) for kind, (kws, _) in KINDS.items() for kw in kws), key=lambda r: -len(r[0]))

_STYLE_FORMALITY = {"the thao": 1, "thuong ngay": 2, "nang dong": 2, "ca tinh": 2, "o nha": 1,
                    "thanh lich": 3, "nu tinh": 3, "toi gian": 3, "co dien": 3, "cong so": 4, "du tiec": 5}
_CASUAL_KINDS = {"quần short", "dép", "áo thun", "áo hai dây"}
_FORMAL_KINDS = {"quần tây", "giày da", "blazer"}
FAMILY_LABELS = ("trắng", "đen", "xám", "be/nâu", "xanh dương", "xanh lá", "đỏ/hồng", "cam/vàng", "tím")


def _words(folded: str) -> str:
    return f" {re.sub(r'[^a-z0-9-]+', ' ', folded)} "


def kinds_of_text(text: Any, *, category: str | None = None, exclusive: bool = False) -> list[str]:
    """Canonical garment kinds named in text. exclusive=True: longest match wins and is consumed
    ("áo khoác gió" → only "áo khoác gió"), used when parsing rules; items use inclusive matching."""
    phrase = _words(fold_vi(text))
    found: list[str] = []
    for kw, kind in _KIND_KEYWORDS:
        need = KINDS[kind][1]
        if category is not None and need is not None and need != category:
            continue
        token = f" {kw} "
        if token in phrase:
            if kind not in found:
                found.append(kind)
            if exclusive:
                phrase = phrase.replace(token, " | ")
    return found


def color_family(color: Any) -> str:
    f = fold_vi(color)
    if not f:
        return ""
    if f.startswith(("xanh navy", "xanh than", "xanh duong", "xanh da troi", "navy", "xanh nhat", "xanh dam")):
        return "xanh dương"
    if f.startswith(("xanh la", "xanh reu", "xanh o liu", "xanh bac ha", "xanh co vit", "xanh ngoc", "xanh luc")):
        return "xanh lá"
    if f.startswith(("trang", "kem")):
        return "trắng"
    if f.startswith("den"):
        return "đen"
    if f.startswith(("xam", "ghi", "bac")):
        return "xám"
    if f.startswith(("be", "nau", "kaki", "bo", "vang dat")):
        return "be/nâu"
    if f.startswith(("do", "hong")):
        return "đỏ/hồng"
    if f.startswith(("cam", "vang", "dao")):
        return "cam/vàng"
    if f.startswith("tim"):
        return "tím"
    return str(color).strip().lower()


def _main_color(item: dict[str, Any]) -> str:
    colors = item.get("colors") or []
    return str(colors[0]).strip().lower() if colors else ""


def color_tone(color: Any) -> str:
    f = fold_vi(color)
    fam = color_family(color)
    if not f:
        return ""
    if "nhat" in f or fam == "trắng" or f.startswith(("be", "kem", "hong", "vang", "xanh bac ha")):
        return "light"
    if "dam" in f or fam in ("đen", "xanh dương") and not f.startswith("xanh duong") or f.startswith(("nau", "xam")):
        return "dark"
    if fam in ("đỏ/hồng", "cam/vàng", "tím", "xanh lá", "xanh dương"):
        return "loud"
    return "dark"


def item_attrs(item: dict[str, Any]) -> dict[str, Any]:
    """Derived attributes used by selectors and the taste profile. Explicit item fields (e.g. from
    garment_attrs: formality/length/fit) win over the heuristics."""
    category = item.get("category") or "top"
    text = " ".join(str(item.get(k) or "") for k in ("name", "product_name"))
    kinds = kinds_of_text(text, category=category)
    style = fold_vi(item.get("style"))
    formality = item.get("formality")
    if not isinstance(formality, (int, float)) or isinstance(formality, bool):
        formality = _STYLE_FORMALITY.get(style, 2)
        if set(kinds) & _CASUAL_KINDS:
            formality = min(formality, 2)
        if set(kinds) & _FORMAL_KINDS:
            formality = max(formality, 4)
        elif "sơ mi" in kinds:
            formality = max(formality, 3)
    formality = int(max(1, min(5, round(formality))))
    length = item.get("length") if item.get("length") in ("short", "long") else None
    if length is None and category in ("bottom", "dress"):
        folded = _words(fold_vi(text))
        length = "short" if ("quần short" in kinds or " ngan " in folded or " mini " in folded) else "long"
    fit = str(item.get("fit") or "").strip().lower()
    if not fit:
        folded = _words(fold_vi(text))
        phom = re.search(r" (?:phom|form|dang) (om vua|om|rong|vua|suong|regular|slim|oversize) ", folded)
        if phom:
            fit = {"om vua": "ôm vừa", "om": "ôm", "rong": "rộng", "vua": "vừa", "suong": "suông", "regular": "vừa",
                   "slim": "ôm", "oversize": "rộng"}[phom.group(1)]
    if not fit:
        for key, label in ((" suong ", "suông"), (" om ", "ôm"), (" slim ", "ôm"), (" oversize ", "rộng"),
                           (" rong ", "rộng"), (" regular ", "vừa")):
            if key in folded:
                fit = label
                break
    main = _main_color(item)
    warmth = item.get("warmth") if isinstance(item.get("warmth"), int) else 2
    return {
        "category": category, "kinds": kinds, "style": str(item.get("style") or "").lower(),
        "formality": formality, "length": length, "fit": fit, "warmth": warmth,
        "color": main, "family": color_family(main), "tone": color_tone(main),
        "pattern": fold_vi(item.get("pattern")), "material": fold_vi(item.get("material")),
    }


def formality_bucket(value: int) -> str:
    return "thoải mái" if value <= 2 else ("lịch sự" if value == 3 else "trang trọng")


# ---- constraint schema ---------------------------------------------------------------------------


class ConstraintError(ValueError):
    pass


_SELECTOR_KEYS = {"categories", "kinds", "colors", "items", "styles", "keywords", "attributes"}
_ATTR_INT_KEYS = {f"{a}_{op}" for a in ("formality", "warmth") for op in ("lt", "lte", "gt", "gte")}
_ATTR_ENUMS = {"length": ("short", "long"), "tone": ("light", "dark", "loud")}
_ATTR_TEXT_KEYS = {"fit", "pattern", "material"}
_WEATHER_KEYS = {"rain", "t_max_gte", "t_max_lte", "t_min_gte", "t_min_lte"}


def _str_list(value: Any, field: str, *, max_items: int = 8, max_len: int = 40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ConstraintError(f"{field} phải là danh sách")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ConstraintError(f"{field} chỉ chứa chuỗi")
        text = re.sub(r"\s+", " ", entry).strip().lower()[:max_len]
        if text and text not in out:
            out.append(text)
    return out[:max_items]


def _int_1_5(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConstraintError(f"{field} phải là số 1-5")
    number = int(round(value))
    if not 1 <= number <= 5:
        raise ConstraintError(f"{field} phải trong khoảng 1-5")
    return number


def validate_selector(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConstraintError("selector phải là object")
    unknown = set(raw) - _SELECTOR_KEYS
    if unknown:
        raise ConstraintError(f"khoá selector không hợp lệ: {sorted(unknown)}")
    out: dict[str, Any] = {}
    cats = _str_list(raw.get("categories"), "categories")
    for cat in cats:
        if cat not in CATEGORIES:
            raise ConstraintError(f"category không hợp lệ: {cat}")
    if cats:
        out["categories"] = cats
    kinds: list[str] = []
    keywords = _str_list(raw.get("keywords"), "keywords", max_items=6, max_len=30)
    for kind in _str_list(raw.get("kinds"), "kinds"):
        if kind in KINDS:
            kinds.append(kind)
        else:
            mapped = kinds_of_text(kind, exclusive=True)
            if mapped:
                kinds.extend(k for k in mapped if k not in kinds)
            elif kind not in keywords:
                keywords.append(kind)  # free garment word ("áo dài"): matched against item names
    if kinds:
        out["kinds"] = kinds
    if keywords:
        out["keywords"] = keywords[:6]
    for key in ("colors", "styles"):
        values = _str_list(raw.get(key), key, max_len=24)
        if values:
            out[key] = values
    ids = _str_list(raw.get("items"), "items", max_items=12, max_len=64)
    for item_id in ids:
        if not is_safe_id(item_id):
            raise ConstraintError(f"id món đồ không hợp lệ: {item_id}")
    if ids:
        out["items"] = ids
    attrs_raw = raw.get("attributes")
    if attrs_raw is not None:
        if not isinstance(attrs_raw, dict):
            raise ConstraintError("attributes phải là object")
        unknown = set(attrs_raw) - _ATTR_INT_KEYS - set(_ATTR_ENUMS) - _ATTR_TEXT_KEYS
        if unknown:
            raise ConstraintError(f"thuộc tính không hợp lệ: {sorted(unknown)}")
        attrs: dict[str, Any] = {}
        for key, value in attrs_raw.items():
            if value is None:
                continue
            if key in _ATTR_INT_KEYS:
                attrs[key] = _int_1_5(value, key)
            elif key in _ATTR_ENUMS:
                if value not in _ATTR_ENUMS[key]:
                    raise ConstraintError(f"{key} phải là một trong {_ATTR_ENUMS[key]}")
                attrs[key] = value
            else:
                if not isinstance(value, str):
                    raise ConstraintError(f"{key} phải là chuỗi")
                if value.strip():
                    attrs[key] = value.strip().lower()[:30]
        if attrs:
            out["attributes"] = attrs
    return out


def _selector_list(raw: Any, field: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        # Object form {colors:[..], categories:[..]} means "any of these": split into one selector per field.
        if not raw:
            return []
        parts = []
        for key, value in raw.items():
            if key == "attributes" and isinstance(value, dict):
                parts.append({"attributes": value})
            elif key in _SELECTOR_KEYS:
                parts.append({key: value})
            else:
                raise ConstraintError(f"khoá {field} không hợp lệ: {key}")
        raw = parts
    if not isinstance(raw, list):
        raise ConstraintError(f"{field} phải là danh sách selector")
    out = []
    for entry in raw[:8]:
        selector = validate_selector(entry)
        if selector and selector not in out:
            out.append(selector)
    return out


def validate_constraint(raw: Any) -> dict[str, Any]:
    """Strictly validate + normalize a constraint (raises ConstraintError)."""
    if not isinstance(raw, dict):
        raise ConstraintError("constraint phải là object")
    if isinstance(raw.get("constraint"), dict) and len(raw) == 1:
        raw = raw["constraint"]
    unknown = set(raw) - {"when", "avoid", "prefer", "require", "strength", "explanation_vi"}
    if unknown:
        raise ConstraintError(f"khoá không hợp lệ: {sorted(unknown)}")
    when_raw = raw.get("when") or {}
    if not isinstance(when_raw, dict):
        raise ConstraintError("when phải là object")
    if set(when_raw) - {"events", "weather"}:
        raise ConstraintError(f"khoá when không hợp lệ: {sorted(set(when_raw) - {'events', 'weather'})}")
    events: list[str] = []
    for entry in _str_list(when_raw.get("events"), "events"):
        mapped = [entry] if entry in OCCASIONS else match_occasions(entry)
        if not mapped:
            raise ConstraintError(f"sự kiện không rõ: {entry}")
        events.extend(m for m in mapped if m not in events)
    weather_raw = when_raw.get("weather") or {}
    if not isinstance(weather_raw, dict):
        raise ConstraintError("when.weather phải là object")
    if set(weather_raw) - _WEATHER_KEYS:
        raise ConstraintError(f"khoá weather không hợp lệ: {sorted(set(weather_raw) - _WEATHER_KEYS)}")
    weather: dict[str, Any] = {}
    for key, value in weather_raw.items():
        if value is None or (isinstance(value, str) and value.strip().lower() in ("", "null", "none", "số")):
            continue  # models fill unused keys with placeholders
        if key != "rain" and isinstance(value, str) and re.fullmatch(r"-?\d{1,2}(\.\d+)?", value.strip()):
            value = float(value)
        if key == "rain":
            if not isinstance(value, bool):
                raise ConstraintError("weather.rain phải là true/false")
            weather[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not -20 <= value <= 50:
                raise ConstraintError(f"weather.{key} phải là nhiệt độ °C")
            weather[key] = round(float(value), 1)
    strength = raw.get("strength") or "should"
    if strength not in STRENGTHS:
        raise ConstraintError("strength phải là must|should")
    explanation = raw.get("explanation_vi") or ""
    if not isinstance(explanation, str):
        raise ConstraintError("explanation_vi phải là chuỗi")
    out = {
        "when": {"events": events, "weather": weather},
        "avoid": _selector_list(raw.get("avoid"), "avoid"),
        "prefer": _selector_list(raw.get("prefer"), "prefer"),
        "require": _selector_list(raw.get("require"), "require"),
        "strength": strength,
        "explanation_vi": re.sub(r"\s+", " ", explanation).strip()[:240],
    }
    return out


def is_actionable(constraint: dict[str, Any] | None) -> bool:
    return bool(constraint) and any(constraint.get(k) for k in ("avoid", "prefer", "require"))


# ---- rendering ("Trợ lý hiểu là: …") ----------------------------------------------------------


def _fmt_temp(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def describe_when(constraint: dict[str, Any]) -> str:
    when = constraint.get("when") or {}
    parts: list[str] = []
    if when.get("events"):
        parts.append("đi " + " hoặc ".join(e.removeprefix("đi ") for e in when["events"]))
    weather = when.get("weather") or {}
    if weather.get("rain") is True:
        parts.append("trời mưa")
    elif weather.get("rain") is False:
        parts.append("trời không mưa")
    if "t_max_gte" in weather:
        parts.append(f"nóng từ {_fmt_temp(weather['t_max_gte'])}°C")
    if "t_max_lte" in weather:
        parts.append(f"cao nhất ≤ {_fmt_temp(weather['t_max_lte'])}°C")
    if "t_min_lte" in weather:
        parts.append(f"lạnh dưới {_fmt_temp(weather['t_min_lte'])}°C")
    if "t_min_gte" in weather:
        parts.append(f"thấp nhất ≥ {_fmt_temp(weather['t_min_gte'])}°C")
    return "Khi " + ", ".join(parts) if parts else "Luôn luôn"


_ATTR_TEXT = {
    "formality_lt": lambda v: f"độ trang trọng dưới {v}/5", "formality_lte": lambda v: f"độ trang trọng ≤ {v}/5",
    "formality_gt": lambda v: f"độ trang trọng trên {v}/5", "formality_gte": lambda v: f"độ trang trọng từ {v}/5",
    "warmth_lt": lambda v: f"độ ấm dưới {v}/5", "warmth_lte": lambda v: "mỏng/thoáng" if v <= 2 else f"độ ấm ≤ {v}/5",
    "warmth_gt": lambda v: f"độ ấm trên {v}/5", "warmth_gte": lambda v: "ấm/dày" if v >= 3 else f"độ ấm từ {v}/5",
    "length": lambda v: "ngắn" if v == "short" else "dài",
    "tone": lambda v: {"light": "sáng màu", "dark": "tối màu", "loud": "màu nổi"}[v],
    "fit": lambda v: f"dáng {v}", "pattern": lambda v: f"họa tiết {v}", "material": lambda v: f"chất {v}",
}


def describe_selector(selector: dict[str, Any], items_by_id: dict[str, dict[str, Any]] | None = None) -> str:
    nouns: list[str] = []
    kinds = selector.get("kinds") or []
    nouns += kinds
    implied = {KINDS[k][1] for k in kinds if k in KINDS}
    nouns += [CATEGORY_LABELS_VI.get(c, c).lower() for c in selector.get("categories") or [] if c not in implied]
    nouns += selector.get("keywords") or []
    if selector.get("items"):
        names = [str((items_by_id or {}).get(i, {}).get("name") or i) for i in selector["items"]]
        nouns += names
    noun = " / ".join(nouns) if nouns else "đồ"
    mods: list[str] = []
    if selector.get("colors"):
        mods.append("màu " + " hoặc ".join(selector["colors"]))
    if selector.get("styles"):
        mods.append("phong cách " + " / ".join(selector["styles"]))
    for key, value in (selector.get("attributes") or {}).items():
        render = _ATTR_TEXT.get(key)
        if render:
            mods.append(render(value))
    return " ".join([noun, *mods]).strip()


def describe_constraint(constraint: dict[str, Any] | None, items_by_id: dict[str, dict[str, Any]] | None = None) -> str:
    """Deterministic Vietnamese rendering shown to the teacher ("Trợ lý hiểu là: …")."""
    if not is_actionable(constraint):
        return "Trợ lý chưa hiểu rõ quy tắc này thành điều kiện cụ thể — sẽ để AI tự cân nhắc khi phối."
    must = constraint.get("strength") == "must"
    parts: list[str] = []
    if constraint.get("avoid"):
        verb = "KHÔNG chọn" if must else "hạn chế"
        parts.append(f"{verb} " + ", ".join(describe_selector(s, items_by_id) for s in constraint["avoid"]))
    if constraint.get("require"):
        verb = "PHẢI có" if must else "nên có"
        parts.append(f"{verb} " + " và ".join(describe_selector(s, items_by_id) for s in constraint["require"]))
    if constraint.get("prefer"):
        parts.append("ưu tiên " + ", ".join(describe_selector(s, items_by_id) for s in constraint["prefer"]))
    tag = "bắt buộc" if must else "nên"
    return f"{describe_when(constraint)}: {'; '.join(parts)} ({tag})."


# ---- deterministic Vietnamese pattern parser (mock mode + fallback) -------------------------------

_TRIGGERS: list[tuple[tuple[str, ...], str, str]] = [
    # (words, section, strength) — longest first
    (("không", "được"), "avoid", "must"), (("không", "bao", "giờ"), "avoid", "must"),
    (("không", "nên"), "avoid", "should"), (("không", "thích"), "avoid", "should"),
    (("không", "muốn"), "avoid", "should"), (("không", "hợp"), "avoid", "should"), (("hạn", "chế"), "avoid", "should"), (("ít", "khi"), "avoid", "should"),
    (("bắt", "buộc"), "require", "must"), (("luôn", "luôn"), "require", "must"), (("ưu", "tiên"), "prefer", "should"),
    (("không",), "avoid", "must"), (("đừng",), "avoid", "must"), (("cấm",), "avoid", "must"),
    (("kiêng",), "avoid", "must"), (("chớ",), "avoid", "must"), (("tránh",), "avoid", "must"),
    (("bớt",), "avoid", "should"), (("phải",), "require", "must"), (("luôn",), "require", "must"),
    (("nhớ",), "require", "must"), (("nên",), "prefer", "should"), (("thích",), "prefer", "should"),
    (("hãy",), "prefer", "should"),
]
_FILLER = {"mặc", "đi", "mang", "dùng", "chọn", "có", "thêm", "đeo", "diện", "cầm", "theo", "một", "cái", "chiếc",
           "đôi", "bộ", "những", "các", "phối", "với", "là", "được", "nên", "lên", "vào", "món"}
_STOP = {"khi", "nếu", "lúc", "thì", "vì", "để", "mà", "nhưng", "cho", "hôm", "ngày", "kẻo", "tránh", "nhé", "nha",
         "trời"}
_RULE_COLORS = (
    "xanh dương đậm", "xanh dương nhạt", "xanh navy", "xanh dương", "xanh lá", "xanh rêu", "xanh bạc hà",
    "xanh cổ vịt", "xanh than", "xanh nhạt", "xanh đậm", "đỏ đô", "cam đào", "nâu nhạt", "nâu đậm", "xám đậm",
    "trắng", "đen", "đỏ", "hồng", "cam", "vàng", "tím", "xám", "be", "kem", "nâu", "bạc",
)
_GENERIC_CATEGORY = ((" ao ", "top"), (" quan ", "bottom"), (" giay ", "shoes"), (" phu kien ", "accessory"))


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+|[,.;!?]", unicodedata.normalize("NFC", str(text or "")).lower())


def _parse_target_segment(words: list[str]) -> dict[str, Any]:
    phrase = " " + " ".join(words) + " "
    folded = _words(fold_vi(phrase))
    selector: dict[str, Any] = {}
    attrs: dict[str, Any] = {}
    # tones before colors so "sáng màu" is not read as a color
    if any(p in phrase for p in (" sáng màu ", " màu sáng ", " tươi sáng ")):
        attrs["tone"] = "light"
    elif any(p in phrase for p in (" tối màu ", " màu tối ", " sẫm màu ", " màu sẫm ")):
        attrs["tone"] = "dark"
    elif any(p in phrase for p in (" sặc sỡ ", " màu nổi ", " lòe loẹt ", " loè loẹt ", " chói ")):
        attrs["tone"] = "loud"
    colors: list[str] = []
    rest = phrase
    for color in _RULE_COLORS:
        if f" {color} " in rest:
            colors.append(color)
            rest = rest.replace(f" {color} ", " ")
    if colors:
        selector["colors"] = colors
    kinds = kinds_of_text(phrase, exclusive=True)
    for kind in kinds:  # attribute words are read from what is left ("hai dây" must not read as "dày")
        for kw in KINDS[kind][0]:
            folded = folded.replace(f" {kw} ", " | ")
    if kinds:
        selector["kinds"] = kinds
    else:
        for key, category in _GENERIC_CATEGORY:
            if key in folded:
                selector["categories"] = [category]
                break
    if " mong " in folded or " thoang " in folded:
        attrs["warmth_lte"] = 2
    elif " day " in folded or " am " in folded:
        attrs["warmth_gte"] = 3
    if any(p in folded for p in (" trang trong ", " lich su ", " chin chu ", " lich lam ")):
        attrs["formality_gte"] = 3
    elif any(p in folded for p in (" xue xoa ", " luom thuom ", " qua thoai mai ", " tuy tien ")):
        attrs["formality_lte"] = 1
    if " the thao " in folded and "giày thể thao" not in kinds and "quần short" not in kinds:
        selector["styles"] = ["thể thao"]
    if any(p in folded for p in (" quan dai ", " vay dai ")):
        attrs["length"] = "long"
    elif any(p in folded for p in (" vay ngan ", " do ngan ", " ho hang ", " do ho ", " mini ")):
        attrs["length"] = "short"
    for key, label in ((" tron ", "trơn"), (" ke soc ", "kẻ"), (" ke ", "kẻ"), (" caro ", "caro"),
                       (" hoa ", "hoa"), (" cham bi ", "chấm bi")):
        if key in folded:
            attrs["pattern"] = label
            break
    if attrs:
        selector["attributes"] = attrs
    return selector


def _split_segments(words: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for word in words:
        if word in ("và", "hoặc", "hay", ","):
            segments.append([])
        else:
            segments[-1].append(word)
    return [s for s in segments if s]


def pattern_parse(text: str) -> dict[str, Any]:
    """Small deterministic parser for common Vietnamese phrasings → validated constraint.

    Covers: "không mặc/tránh <màu|loại> khi <sự kiện|trời mưa|trời nóng>", "nên/ưu tiên …",
    "đi <sự kiện> phải …", "trời dưới N độ thì luôn thêm …".
    """
    words = _tokens(text)
    sections: dict[str, list[dict[str, Any]]] = {"avoid": [], "prefer": [], "require": []}
    strengths: list[str] = []
    in_target = [False] * len(words)
    index = 0
    while index < len(words):
        match = None
        for trig, section, strength in _TRIGGERS:
            if tuple(words[index:index + len(trig)]) == trig:
                match = (trig, section, strength)
                break
        if not match:
            index += 1
            continue
        trig, section, strength = match
        for k in range(index, index + len(trig)):
            in_target[k] = True
        j = index + len(trig)
        while j < len(words) and words[j] in _FILLER:
            in_target[j] = True
            j += 1
        target: list[str] = []
        while j < len(words):
            word = words[j]
            if word in ".;!?" or word in _STOP:
                break
            if any(tuple(words[j:j + len(t)]) == t for t, _, _ in _TRIGGERS if t[0] not in ("không",)):
                break
            if word == "," and j + 1 < len(words) and words[j + 1] in _STOP | {"tránh"}:
                break
            target.append(word)
            in_target[j] = True
            j += 1
        selectors = [_parse_target_segment(seg) for seg in _split_segments(target)]
        selectors = [s for s in selectors if s]
        for prev, cur in zip(selectors, selectors[1:]):
            # "áo xanh lá hoặc đỏ": a color-only segment keeps the garment noun of the previous one.
            if not any(k in cur for k in ("kinds", "categories", "keywords")):
                for key in ("kinds", "categories", "keywords"):
                    if key in prev:
                        cur[key] = list(prev[key])
        # "áo trắng và quần đen" = two selectors; "đồ trắng" = colors only; drop empty ones.
        if selectors:
            sections[section].extend(selectors)
            strengths.append(strength)
        index = j if j > index else index + 1
    condition = " ".join(w for w, used in zip(words, in_target) if not used)
    events = match_occasions(condition)
    weather: dict[str, Any] = {}
    cond_f = _words(fold_vi(condition))
    if " mua " in cond_f:
        weather["rain"] = True
    number = re.search(r"(tren|hon|tu|duoi|qua)\s+(\d{1,2})\s*(do|c)\b", fold_vi(condition))
    if number:
        value = float(number.group(2))
        if number.group(1) == "duoi":
            weather["t_min_lte"] = value
        else:
            weather["t_max_gte"] = value
    elif " se lanh " in cond_f or " troi mat " in cond_f:
        weather["t_min_lte"] = 22.0
    elif " lanh " in cond_f or " ret " in cond_f:
        weather["t_min_lte"] = 18.0
    elif " nong " in cond_f or " nang gat " in cond_f:
        weather["t_max_gte"] = 30.0
    strength = "must" if "must" in strengths else "should"
    raw = {
        "when": {"events": events, "weather": weather},
        "avoid": sections["avoid"], "prefer": sections["prefer"], "require": sections["require"],
        "strength": strength,
    }
    if strength == "should" and raw["require"]:
        raw["prefer"] = raw["prefer"] + raw["require"]
        raw["require"] = []
    constraint = validate_constraint(raw)
    constraint["explanation_vi"] = describe_constraint(constraint)
    return constraint


# ---- LLM rule parsing ----------------------------------------------------------------------------

RULE_SYSTEM_PROMPT = (
    "Bạn chuyển một quy tắc phối đồ tiếng Việt (do người dùng dạy) thành JSON ràng buộc có cấu trúc. "
    "Chỉ trả về MỘT object JSON đúng schema, không giải thích ngoài JSON."
)


def _rule_prompt(text: str, items: list[dict[str, Any]] | None) -> str:
    wardrobe = ""
    if items:
        rows = [f'{it["id"]}: {it.get("name")}' for it in items[:60]]
        wardrobe = "\nTủ đồ (chỉ dùng id khi quy tắc nhắc ĐÚNG một món cụ thể):\n" + "\n".join(rows) + "\n"
    return (
        f"QUY TẮC: “{text}”\n{wardrobe}\n"
        "Schema:\n"
        '{"when": {"events": [sự kiện], "weather": {"rain": true|false, "t_max_gte": số, "t_max_lte": số, '
        '"t_min_gte": số, "t_min_lte": số}},\n'
        ' "avoid": [selector], "prefer": [selector], "require": [selector],\n'
        ' "strength": "must"|"should", "explanation_vi": "1 câu diễn giải"}\n'
        'selector = {"categories": [...], "kinds": [...], "colors": [...], "items": [id], "styles": [...], '
        '"keywords": [...], "attributes": {"formality_lt|formality_lte|formality_gt|formality_gte": 1-5, '
        '"warmth_lte|warmth_gte": 1-5, "length": "short|long", "tone": "light|dark|loud", "pattern": "trơn|kẻ|hoa", '
        '"fit": "...", "material": "..."}}\n'
        "Trong MỘT selector mọi trường phải cùng đúng (AND): \"áo trắng\" = {\"categories\":[\"top\"],\"colors\":[\"trắng\"]}. "
        "Nhiều món khác nhau = nhiều selector: \"quần short và dép\" = [{\"kinds\":[\"quần short\"]},{\"kinds\":[\"dép\"]}].\n"
        f"- events chỉ chọn từ: {', '.join(OCCASIONS)}; [] nếu áp dụng mọi dịp.\n"
        f"- categories: {', '.join(CATEGORIES)}.\n"
        f"- kinds chỉ chọn từ: {', '.join(KINDS)}; từ khác (vd: áo dài) đưa vào keywords.\n"
        "- colors: màu tiếng Việt (trắng, đen, xám, be, nâu, đỏ, hồng, cam, vàng, xanh lá, xanh dương, xanh navy, tím...).\n"
        "- formality: 1=thể thao/ở nhà, 2=thường ngày, 3=lịch sự, 4=công sở, 5=dự tiệc. warmth: 1=rất mát … 5=rất ấm.\n"
        "- Thời tiết: \"trời mưa\" → rain true; \"trời nóng\" → t_max_gte 30; \"trên N độ\" → t_max_gte N; "
        "\"lạnh\" → t_min_lte 18; \"dưới N độ\" → t_min_lte N.\n"
        "- \"không/đừng/cấm/tránh\" → avoid, strength must; \"không nên/hạn chế\" → avoid, should; "
        "\"phải/luôn/bắt buộc\" → require, must; \"nên/ưu tiên/thích\" → prefer, should.\n"
        "- Phần lý do (vd: \"tránh lấn át cô dâu\") KHÔNG phải ràng buộc, chỉ dùng cho explanation_vi.\n"
        "- CHỈ ghi khoá weather khi câu nói rõ về thời tiết; câu không nhắc thời tiết → \"weather\": {} "
        "(không điền rain=false hay 0/100). Bỏ các khoá selector rỗng.\n"
        "- Không bịa điều kiện không có trong câu. Không chắc → để danh sách rỗng."
    )


def ground_weather(raw: Any, text: str) -> Any:
    """Drop weather conditions the rule text never mentions (models fill the schema with defaults such as
    rain=false, 0…100 °C). Applied to the raw reply before strict validation."""
    if not isinstance(raw, dict):
        return raw
    target = raw["constraint"] if isinstance(raw.get("constraint"), dict) and len(raw) == 1 else raw
    when = target.get("when")
    weather = when.get("weather") if isinstance(when, dict) else None
    if not isinstance(weather, dict):
        return raw
    folded = _words(fold_vi(text))
    says_rain = any(w in folded for w in (" mua ", " uot ", " tanh ", " kho rao "))
    says_temp = bool(re.search(r"\d", text)) or any(w in folded for w in (
        " nong ", " lanh ", " ret ", " mat ", " oi ", " nang ", " do ", " am ", " se "))
    cleaned = {}
    for key, value in weather.items():
        if key == "rain" and not says_rain:
            continue
        if key != "rain":
            if not says_temp or value is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (
                    (key.endswith("_gte") and value <= 0) or (key.endswith("_lte") and value >= 45)):
                continue  # "no bound" placeholder
        cleaned[key] = value
    when["weather"] = cleaned
    return raw


async def parse_rule(provider: Any, text: str, items: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], str]:
    """→ (validated constraint, parser) where parser is "ai" | "pattern" | "pattern_fallback"."""
    if getattr(provider, "mode", "mock") == "mock":
        return pattern_parse(text), "pattern"
    from providers import compiler_model

    try:
        raw = await asyncio.wait_for(
            provider.chat_json(
                model=compiler_model(),
                system=RULE_SYSTEM_PROMPT,
                content=[{"type": "text", "text": _rule_prompt(text, items)}],
                max_tokens=RULE_PARSE_MAX_TOKENS,
                temperature=0.0,
            ),
            timeout=RULE_PARSE_TIMEOUT_S,
        )
        constraint = validate_constraint(ground_weather(raw, text))
        if items is not None:
            known = {it["id"] for it in items}
            for section in ("avoid", "prefer", "require"):
                for selector in constraint[section]:
                    if selector.get("items"):
                        selector["items"] = [i for i in selector["items"] if i in known]
                        if not selector["items"]:
                            selector.pop("items")
                constraint[section] = [s for s in constraint[section] if s]
        if not is_actionable(constraint):
            pattern = pattern_parse(text)
            if is_actionable(pattern):
                return pattern, "pattern_fallback"
        if not constraint["explanation_vi"]:
            constraint["explanation_vi"] = describe_constraint(constraint)
        return constraint, "ai"
    except Exception as error:
        logger.warning("rule parse via model failed, using pattern parser: %r (reply: %.300s)", error,
                       json.dumps(locals().get("raw"), ensure_ascii=False))
        return pattern_parse(text), "pattern_fallback"


_PATTERN_CACHE: dict[str, dict[str, Any]] = {}


def constraint_of(rule: dict[str, Any]) -> dict[str, Any]:
    """Stored constraint of a rule (validated) or the pattern parse of its text (legacy rules)."""
    stored = rule.get("constraint")
    if isinstance(stored, dict):
        try:
            return validate_constraint(stored)
        except ConstraintError:
            pass
    text = str(rule.get("text") or "")
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if key not in _PATTERN_CACHE:
        if len(_PATTERN_CACHE) > 500:
            _PATTERN_CACHE.clear()
        try:
            _PATTERN_CACHE[key] = pattern_parse(text)
        except ConstraintError:
            _PATTERN_CACHE[key] = validate_constraint({})
    return json.loads(json.dumps(_PATTERN_CACHE[key]))


def public_rule(rule: dict[str, Any], items_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    constraint = constraint_of(rule)
    out = dict(rule)
    out["constraint"] = constraint
    out.setdefault("parser", "pattern")
    out.setdefault("confirmed", False)
    out["strength"] = constraint["strength"]
    out["strength_vi"] = "Bắt buộc" if constraint["strength"] == "must" else "Nên"
    out["parser_vi"] = PARSER_LABELS.get(out["parser"], out["parser"])
    out["interpretation_vi"] = describe_constraint(constraint, items_by_id)
    out["display_vi"] = f"Trợ lý hiểu là: {out['interpretation_vi']}"
    out["actionable"] = is_actionable(constraint)
    return out


PARSER_LABELS = {
    "ai": "AI hiểu quy tắc", "pattern": "Bộ hiểu câu có sẵn (offline)",
    "pattern_fallback": "Bộ hiểu câu có sẵn (AI lỗi)", "teacher": "Người dạy chỉnh tay",
}


# ---- rule storage (rules.json is owned by WardrobeStore; we add fields under its lock) -------------


def _write_rule_fields(store: WardrobeStore, rule_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    with store._lock:
        rules = store.list_rules()
        for index, rule in enumerate(rules):
            if rule["id"] == rule_id:
                rules[index] = {**rule, **fields}
                _atomic_write_json(store.rules_path, rules)
                return rules[index]
    raise KeyError(rule_id)


async def teach_rule(
    store: WardrobeStore, provider: Any, text: Any, taught_by: Any, items: list[dict[str, Any]], *,
    taught_via: str = "text",
) -> dict[str, Any]:
    clean = _clean_text(text, 300)
    if not clean:
        raise WardrobeError("Quy tắc đang trống.")
    constraint, parser = await parse_rule(provider, clean, items)
    rule = store.add_rule(clean, taught_by)
    saved = _write_rule_fields(store, rule["id"], {
        "constraint": constraint, "parser": parser, "confirmed": False,
        "taught_via": taught_via if taught_via in ("text", "voice") else "text",
    })
    return public_rule(saved, {it["id"]: it for it in items})


async def edit_rule(
    store: WardrobeStore, provider: Any, rule_id: str, patch: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """PUT: {text?} re-parses; {constraint?} is validated strictly; {strength?, confirmed?, taught_by?}."""
    current = next((r for r in store.list_rules() if r["id"] == rule_id), None)
    if current is None:
        raise KeyError(rule_id)
    fields: dict[str, Any] = {"updated_at": now_iso()}
    text = patch.get("text")
    if isinstance(text, str) and _clean_text(text, 300) and _clean_text(text, 300) != current.get("text"):
        fields["text"] = _clean_text(text, 300)
        constraint, parser = await parse_rule(provider, fields["text"], items)
        fields.update({"constraint": constraint, "parser": parser, "confirmed": False})
    if patch.get("constraint") is not None:
        try:
            constraint = validate_constraint(patch["constraint"])
        except ConstraintError as error:
            raise WardrobeError(f"Ràng buộc không hợp lệ: {error}") from None
        constraint["explanation_vi"] = constraint["explanation_vi"] or describe_constraint(constraint)
        fields.update({"constraint": constraint, "parser": "teacher"})
    if patch.get("strength") is not None:
        if patch["strength"] not in STRENGTHS:
            raise WardrobeError("strength phải là must hoặc should.")
        constraint = fields.get("constraint") or constraint_of(current)
        constraint["strength"] = patch["strength"]
        if constraint["strength"] == "should" and constraint.get("require"):
            pass  # soft require = strong preference
        fields["constraint"] = constraint
    if patch.get("taught_by") is not None:
        fields["taught_by"] = _clean_text(patch["taught_by"], 60) or current.get("taught_by") or "Bạn"
    if patch.get("confirmed") is not None:
        fields["confirmed"] = bool(patch["confirmed"])
    saved = _write_rule_fields(store, rule_id, fields)
    return public_rule(saved, {it["id"]: it for it in items})


# ---- memory storage (profile + examples) -----------------------------------------------------------

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path) -> threading.RLock:
    key = str(Path(root).resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _empty_profile() -> dict[str, Any]:
    return {"version": 1, "events": [], "worn": []}


def load_profile(root: Path) -> dict[str, Any]:
    data = _read_json_dict(Path(root) / PROFILE_FILE)
    profile = _empty_profile()
    if isinstance(data.get("events"), list):
        profile["events"] = [e for e in data["events"] if isinstance(e, dict)][-MAX_EVENTS:]
    if isinstance(data.get("worn"), list):
        profile["worn"] = [w for w in data["worn"] if isinstance(w, dict)][-MAX_WORN:]
    if data.get("reset_at"):
        profile["reset_at"] = data["reset_at"]
    return profile


def list_examples(root: Path) -> list[dict[str, Any]]:
    return [e for e in _read_json_list(Path(root) / EXAMPLES_FILE) if is_safe_id(e.get("id"))]


def load_memory(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    with _lock_for(root):
        return {"profile": load_profile(root), "examples": list_examples(root)}


def _local_today() -> str:
    return date_cls.today().isoformat()


def _valid_date(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date_cls.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def record_feedback(
    root: Path | str,
    *,
    item_ids: list[str],
    vote: str,
    event: str = "",
    weather: dict[str, Any] | None = None,
    reason: str | None = None,
    disliked_item_ids: list[str] | None = None,
    day: str | None = None,
    known_ids: Iterable[str] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Append one 👍/👎/"Mặc bộ này" event (and a worn entry for "wear")."""
    if vote not in VOTES:
        raise WardrobeError("vote phải là up, down hoặc wear.")
    if reason is not None and reason not in REASONS:
        raise WardrobeError(f"reason phải là một trong: {', '.join(REASONS)}.")
    known = set(known_ids) if known_ids is not None else None
    ids = [i for i in dict.fromkeys(item_ids or []) if is_safe_id(i) and (known is None or i in known)]
    if not ids:
        raise WardrobeError("Bộ đồ không có món nào trong tủ.")
    disliked = [i for i in (disliked_item_ids or []) if i in ids]
    ts = time.time() if now is None else float(now)
    weather_small = None
    if isinstance(weather, dict):
        weather_small = {k: weather.get(k) for k in ("t_min", "t_max", "rain_prob", "summary_vi")
                         if isinstance(weather.get(k), (int, float, str))}
    entry = {
        "ts": ts, "at": now_iso(), "vote": vote, "item_ids": ids[:8], "event": _clean_text(event, 120),
        "occasions": match_occasions(event), "weather": weather_small, "reason": reason,
        "disliked_item_ids": disliked,
    }
    root = Path(root)
    with _lock_for(root):
        profile = load_profile(root)
        profile["events"] = (profile["events"] + [entry])[-MAX_EVENTS:]
        if vote == "wear":
            worn_day = _valid_date(day) or _local_today()
            profile["worn"] = (profile["worn"] + [{"date": worn_day, "item_ids": ids[:8], "event": entry["event"]}])[-MAX_WORN:]
        _atomic_write_json(root / PROFILE_FILE, profile)
    return entry


def reset_profile(root: Path | str) -> None:
    root = Path(root)
    with _lock_for(root):
        _atomic_write_json(root / PROFILE_FILE, {**_empty_profile(), "reset_at": now_iso()})


def add_example(
    root: Path | str, *, item_ids: list[str], event: str, taught_by: Any = None, note: Any = None,
    known_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    known = set(known_ids) if known_ids is not None else None
    ids = [i for i in dict.fromkeys(item_ids or []) if is_safe_id(i) and (known is None or i in known)]
    if len(ids) < 2:
        raise WardrobeError("Chọn ít nhất 2 món có trong tủ đồ cho bộ mẫu.")
    event_clean = _clean_text(event, 80)
    if not event_clean:
        raise WardrobeError("Bộ mẫu này dùng cho dịp nào?")
    example = {
        "id": new_id("ex"), "event": event_clean, "occasions": match_occasions(event_clean), "item_ids": ids[:8],
        "note": _clean_text(note, 200), "taught_by": _clean_text(taught_by, 60) or "Bạn", "created_at": now_iso(),
    }
    root = Path(root)
    with _lock_for(root):
        examples = list_examples(root)
        if len(examples) >= MAX_EXAMPLES:
            raise WardrobeError(f"Đã có quá nhiều bộ mẫu (tối đa {MAX_EXAMPLES}).")
        examples.append(example)
        _atomic_write_json(root / EXAMPLES_FILE, examples)
    return example


def delete_example(root: Path | str, example_id: str) -> bool:
    if not is_safe_id(example_id):
        return False
    root = Path(root)
    with _lock_for(root):
        examples = list_examples(root)
        remaining = [e for e in examples if e["id"] != example_id]
        if len(remaining) == len(examples):
            return False
        _atomic_write_json(root / EXAMPLES_FILE, remaining)
    return True


# ---- taste profile ----------------------------------------------------------------------------


def item_features(item: dict[str, Any]) -> dict[str, list[str]]:
    a = item_attrs(item)
    attrs = [f"color:{a['family']}" if a["family"] else None, f"formality:{formality_bucket(a['formality'])}",
             f"cat:{a['category']}", f"style:{a['style']}" if a["style"] else None,
             f"fit:{a['fit']}" if a["fit"] else None, *[f"kind:{k}" for k in a["kinds"][:2]]]
    return {"item": [f"item:{item['id']}"], "attrs": [x for x in attrs if x]}


def _decay(age_s: float) -> float:
    return 0.5 ** (max(0.0, age_s) / (HALF_LIFE_DAYS * 86400.0))


def compute_affinities(profile: dict[str, Any], items_by_id: dict[str, dict[str, Any]], *, now: float | None = None) -> dict[str, float]:
    """feature → affinity in (-1, 1): tanh of the time-decayed, reason-aware vote sum."""
    now = time.time() if now is None else now
    raw: dict[str, float] = {}

    def add(feature: str, weight: float) -> None:
        raw[feature] = raw.get(feature, 0.0) + weight

    for event in profile.get("events") or []:
        decay = _decay(now - float(event.get("ts") or now))
        vote, reason = event.get("vote"), event.get("reason")
        ids = [i for i in event.get("item_ids") or [] if i in items_by_id]
        if not ids:
            continue
        sign = 1.0 if vote in ("up", "wear") else -1.0
        strength = 2.0 if vote == "wear" else 1.0
        occasions = event.get("occasions") or []
        disliked = set(event.get("disliked_item_ids") or [])
        for item_id in ids:
            item = items_by_id[item_id]
            feats = item_features(item)
            a = item_attrs(item)
            if sign > 0:
                w_item, w_attr = strength, 0.5 * strength
            elif reason == "color":
                w_item, w_attr = -0.2, 0.0
                add(f"color:{a['family']}", -1.0 * decay)
            elif reason == "too_formal":
                w_item, w_attr = -0.3, 0.0
                if a["formality"] >= 3:
                    add(f"formality:{formality_bucket(a['formality'])}", -1.0 * decay)
            elif reason == "too_hot":
                w_item, w_attr = -0.3, 0.0
                if a["warmth"] >= 2 or a["category"] == "outerwear":
                    add(f"warm:{a['warmth']}", -1.0 * decay)
            elif reason == "dislike_item":
                w_item, w_attr = (-1.5 if (not disliked or item_id in disliked) else 0.0), 0.0
            else:
                w_item, w_attr = -1.0, -0.4
            for feature in feats["item"]:
                add(feature, w_item * decay)
                for occ in occasions:
                    add(f"ev:{occ}|{feature}", w_item * decay)
            for feature in feats["attrs"]:
                if w_attr:
                    add(feature, w_attr * decay)
    return {k: math.tanh(v / 2.5) for k, v in raw.items() if abs(v) > 1e-6}


_FEATURE_LABEL = {
    "color": lambda v: f"màu {v}", "formality": lambda v: f"đồ {v}", "style": lambda v: f"phong cách {v}",
    "cat": lambda v: CATEGORY_LABELS_VI.get(v, v).lower(), "fit": lambda v: f"dáng {v}", "kind": lambda v: v,
    "warm": lambda v: f"đồ độ ấm {v}/5",
}


def feature_label(feature: str, items_by_id: dict[str, dict[str, Any]]) -> str:
    kind, _, value = feature.rpartition("|")[2].partition(":")
    if kind == "item":
        return str(items_by_id.get(value, {}).get("name") or value)
    render = _FEATURE_LABEL.get(kind)
    return render(value) if render else value


def profile_summary(memory: dict[str, Any] | None, items: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
    """Facts for the "Gu của bạn" card and the stylist prompt."""
    profile = (memory or {}).get("profile") or _empty_profile()
    items_by_id = {it["id"]: it for it in items}
    aff = compute_affinities(profile, items_by_id, now=now)
    events = profile.get("events") or []

    def top(prefixes: tuple[str, ...], positive: bool, n: int = 3) -> list[dict[str, Any]]:
        rows = [(k, v) for k, v in aff.items() if k.startswith(prefixes) and (v >= 0.12 if positive else v <= -0.12)]
        rows.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
        return [{"feature": k, "label": feature_label(k, items_by_id), "score": round(v, 3)} for k, v in rows[:n]]

    liked_attrs = top(("color:", "style:", "kind:", "formality:", "fit:"), True, 4)
    disliked_attrs = top(("color:", "style:", "kind:", "formality:", "warm:"), False, 3)
    liked_items = [r | {"item_id": r["feature"].split(":", 1)[1]} for r in top(("item:",), True, 3)]
    disliked_items = [r | {"item_id": r["feature"].split(":", 1)[1]} for r in top(("item:",), False, 3)]
    worn_ids = {i for w in profile.get("worn") or [] for i in w.get("item_ids") or []}
    liked_ids = {i for e in events if e.get("vote") in ("up", "wear") for i in e.get("item_ids") or []}
    never_worn = [
        {"item_id": it["id"], "name": it.get("name")} for it in items
        if it["id"] not in worn_ids and it["id"] not in liked_ids and it.get("category") not in ("accessory",)
    ]
    facts: list[str] = []
    if liked_attrs:
        facts.append("Bạn hay thích: " + ", ".join(r["label"] for r in liked_attrs) + ".")
    if liked_items:
        facts.append("Món bạn ưng: " + ", ".join(r["label"] for r in liked_items) + ".")
    if disliked_attrs or disliked_items:
        facts.append("Bạn ít thích: " + ", ".join(r["label"] for r in (disliked_attrs + disliked_items)[:4]) + ".")
    if profile.get("worn") and never_worn:
        facts.append("Chưa mặc lần nào: " + ", ".join(str(r["name"]) for r in never_worn[:3]) + ".")
    votes = sum(1 for e in events if e.get("vote") in ("up", "down"))
    return {
        "facts": facts,
        "liked": liked_attrs, "disliked": disliked_attrs,
        "liked_items": liked_items, "disliked_items": disliked_items,
        "never_worn": never_worn[:6] if profile.get("worn") else [],
        "worn_recent": list(reversed((profile.get("worn") or [])[-5:])),
        "stats": {"votes": votes, "worn": len(profile.get("worn") or []),
                  "likes": sum(1 for e in events if e.get("vote") == "up"),
                  "dislikes": sum(1 for e in events if e.get("vote") == "down")},
        "reset_at": profile.get("reset_at"),
    }


# ---- context + matching ---------------------------------------------------------------------------


def build_context(
    items: list[dict[str, Any]],
    rules: list[dict[str, Any]] | None,
    memory: dict[str, Any] | None,
    *,
    event: str = "",
    weather: dict[str, Any] | None = None,
    anchor_id: str | None = None,
    day: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    items_by_id = {it["id"]: it for it in items}
    memory = memory or {}
    profile = memory.get("profile") or _empty_profile()
    target_day = _valid_date(day) or _valid_date((weather or {}).get("date")) or _local_today()
    occasions = match_occasions(event)
    parsed = []
    for rule in rules or []:
        parsed.append({"rule": rule, "constraint": constraint_of(rule)})
    examples = []
    for ex in memory.get("examples") or []:
        ids = [i for i in ex.get("item_ids") or [] if i in items_by_id]
        if len(ids) >= 2:
            examples.append({**ex, "item_ids": ids})
    return {
        "event": event, "occasions": occasions, "weather": weather, "anchor_id": anchor_id, "day": target_day,
        "items_by_id": items_by_id, "rules": parsed,
        "affinity": compute_affinities(profile, items_by_id, now=now) if profile.get("events") else {},
        "profile": profile, "examples": examples,
        "worn": profile.get("worn") or [],
    }


def _temp_ok(weather: dict[str, Any] | None, cond: dict[str, Any]) -> bool:
    if not cond:
        return True
    if not weather or not weather.get("available"):
        return False
    if "rain" in cond and is_rainy(weather) != cond["rain"]:
        return False
    t_min, t_max = weather.get("t_min"), weather.get("t_max")
    for key, value in cond.items():
        if key == "rain":
            continue
        actual = t_max if key.startswith("t_max") else t_min
        if not isinstance(actual, (int, float)):
            return False
        if key.endswith("_gte") and not actual >= value:
            return False
        if key.endswith("_lte") and not actual <= value:
            return False
    return True


def rule_is_applicable(constraint: dict[str, Any], context: dict[str, Any]) -> bool:
    when = constraint.get("when") or {}
    events = when.get("events") or []
    if events and not set(events) & set(context.get("occasions") or []):
        return False
    return _temp_ok(context.get("weather"), when.get("weather") or {})


def item_matches(item: dict[str, Any], selector: dict[str, Any]) -> bool:
    if not selector:
        return False
    a = item_attrs(item)
    if selector.get("items") and item.get("id") not in selector["items"]:
        return False
    if selector.get("categories") and a["category"] not in selector["categories"]:
        return False
    if selector.get("kinds") and not set(a["kinds"]) & set(selector["kinds"]):
        return False
    if selector.get("keywords"):
        name = _words(fold_vi(" ".join(str(item.get(k) or "") for k in ("name", "product_name"))))
        if not any(f" {fold_vi(kw)} " in name for kw in selector["keywords"]):
            return False
    if selector.get("colors") and not any(color_matches(a["color"], c) for c in selector["colors"]):
        return False
    if selector.get("styles") and fold_vi(a["style"]) not in {fold_vi(s) for s in selector["styles"]}:
        return False
    for key, value in (selector.get("attributes") or {}).items():
        if key.startswith(("formality_", "warmth_")):
            attr, op = key.rsplit("_", 1)
            actual = a[attr]
            if not {"lt": actual < value, "lte": actual <= value, "gt": actual > value, "gte": actual >= value}[op]:
                return False
        elif key in ("length", "tone"):
            if a[key] != value:
                return False
        elif key == "fit":
            if fold_vi(value) not in fold_vi(a["fit"]):
                return False
        elif key in ("pattern", "material"):
            if fold_vi(value) not in a[key]:
                return False
    return True


def color_matches(item_color: str, rule_color: str) -> bool:
    """Main color vs a rule color: exact, prefix ("đỏ" ~ "đỏ đô") or all words ("xanh nhạt" ~ "xanh dương nhạt")."""
    a, b = fold_vi(item_color), fold_vi(rule_color)
    if not a or not b:
        return False
    return a == b or a.startswith(b + " ") or set(b.split()) <= set(a.split())


def _outfit_items(outfit: Any, context: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = context.get("items_by_id") or {}
    if isinstance(outfit, dict):
        if isinstance(outfit.get("items"), list) and outfit["items"] and isinstance(outfit["items"][0], dict) \
                and outfit["items"][0].get("category"):
            return [by_id.get(it.get("id"), it) for it in outfit["items"]]
        return [by_id[i] for i in outfit.get("item_ids") or [] if i in by_id]
    out = []
    for entry in outfit or []:
        if isinstance(entry, dict):
            out.append(by_id.get(entry.get("id"), entry))
        elif entry in by_id:
            out.append(by_id[entry])
    return out


def _is_wearable(categories: set[str]) -> bool:
    return "dress" in categories or {"top", "bottom"} <= categories


def _teacher(rule: dict[str, Any]) -> str:
    return str(rule.get("taught_by") or "người dạy")


# ---- deterministic enforcement -----------------------------------------------------------------


def enforce_must(items: list[dict[str, Any]], context: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    """Drop items an applicable "must avoid" rule forbids → (allowed items, {rule_id: banned ids}).

    The anchor is never dropped; if the bans leave nothing wearable they are relaxed (and recorded in
    context["relaxed"]) — better a rule-breaking suggestion than none.
    """
    anchor_id = context.get("anchor_id")
    banned_by_rule: dict[str, set[str]] = {}
    for entry in context.get("rules") or []:
        constraint, rule = entry["constraint"], entry["rule"]
        if constraint["strength"] != "must" or not constraint["avoid"] or not rule_is_applicable(constraint, context):
            continue
        ids = {it["id"] for it in items if it["id"] != anchor_id and any(item_matches(it, s) for s in constraint["avoid"])}
        banned_by_rule[rule["id"]] = ids
    banned = set().union(*banned_by_rule.values()) if banned_by_rule else set()
    allowed = [it for it in items if it["id"] not in banned]
    if banned and not _is_wearable({it.get("category") for it in allowed}):
        context["relaxed"] = sorted(banned_by_rule)
        context["banned"] = {}
        return items, {}
    context["banned"] = banned_by_rule
    context["allowed_ids"] = {it["id"] for it in allowed}
    return allowed, banned_by_rule


def _selector_met(items: list[dict[str, Any]], selector: dict[str, Any]) -> bool:
    return any(item_matches(it, selector) for it in items)


def rule_adjustments(outfit: Any, context: dict[str, Any]) -> list[tuple[float, str, str]]:
    """Ranking hook: [(delta, source_id, reason_vi)] for one outfit (list of items / ids, or an outfit dict).

    Combines applicable taught rules, learned taste (affinities), teacher examples and worn history.
    Positive delta = rank higher. Deterministic for a given context.
    """
    items = _outfit_items(outfit, context)
    if not items:
        return []
    out: list[tuple[float, str, str]] = []
    by_id = context.get("items_by_id") or {}
    # 1) taught rules
    for entry in context.get("rules") or []:
        constraint, rule = entry["constraint"], entry["rule"]
        if not is_actionable(constraint) or not rule_is_applicable(constraint, context):
            continue
        must = constraint["strength"] == "must"
        who = _teacher(rule)
        for selector in constraint["avoid"]:
            hits = [it for it in items if item_matches(it, selector)]
            if hits:
                out.append((-6.0 if must else -1.5, rule["id"],
                            f"Có {hits[0].get('name')} — trái quy tắc của {who}"))
        for selector in constraint["require"]:
            if _selector_met(items, selector):
                out.append((0.6, rule["id"], f"Có {describe_selector(selector, by_id)} — học từ {who}"))
            else:
                out.append((-6.0 if must else -1.2, rule["id"], f"Thiếu {describe_selector(selector, by_id)} (quy tắc của {who})"))
        for selector in constraint["prefer"]:
            if _selector_met(items, selector):
                out.append((1.2, rule["id"], f"Ưu tiên {describe_selector(selector, by_id)} — học từ {who}"))
    # 2) learned taste
    aff = context.get("affinity") or {}
    if aff:
        contrib: dict[str, float] = {}
        occasions = context.get("occasions") or []
        n = max(1, len(items))
        for item in items:
            feats = item_features(item)
            for feature in feats["item"]:
                value = aff.get(feature, 0.0) * 1.0 + sum(aff.get(f"ev:{o}|{feature}", 0.0) for o in occasions) * 0.8
                if value:
                    contrib[feature] = contrib.get(feature, 0.0) + value * 1.6 / n
            for feature in feats["attrs"]:
                value = aff.get(feature, 0.0) * 0.6
                if value:
                    contrib[feature] = contrib.get(feature, 0.0) + value * 1.6 / n
            warm = aff.get(f"warm:{item_attrs(item)['warmth']}", 0.0)
            if warm and (context.get("weather") or {}).get("available"):
                t_max = (context.get("weather") or {}).get("t_max")
                if isinstance(t_max, (int, float)) and t_max >= 27:
                    key = f"warm:{item_attrs(item)['warmth']}"
                    contrib[key] = contrib.get(key, 0.0) + warm * 1.6 / n
        for feature, value in sorted(contrib.items(), key=lambda kv: (-abs(kv[1]), kv[0])):
            if abs(value) < 0.08:
                continue
            label = feature_label(feature, by_id)
            reason = (f"Bạn từng thích {label}" if feature.startswith("item:") else f"Bạn hay thích {label}") \
                if value > 0 else (f"Bạn không thích {label}" if feature.startswith("item:") else f"Bạn ít thích {label}")
            out.append((round(value, 4), f"pref:{feature}", reason))
    # 3) teacher examples for this event
    occasions = set(context.get("occasions") or [])
    best: tuple[float, dict[str, Any]] | None = None
    ids = {it["id"] for it in items}
    for ex in context.get("examples") or []:
        if ex.get("occasions") and not set(ex["occasions"]) & occasions:
            continue
        if not ex.get("occasions") and fold_vi(ex.get("event")) != fold_vi(context.get("event")):
            continue
        ex_items = [by_id[i] for i in ex["item_ids"] if i in by_id]
        jacc = len(ids & set(ex["item_ids"])) / max(1, len(ids | set(ex["item_ids"])))
        ex_sig = {(item_attrs(i)["category"], item_attrs(i)["family"]) for i in ex_items}
        my_sig = {(item_attrs(i)["category"], item_attrs(i)["family"]) for i in items}
        attr_sim = len(ex_sig & my_sig) / max(1, max(len(ex_sig), len(my_sig)))
        sim = 0.7 * jacc + 0.3 * attr_sim
        if best is None or sim > best[0]:
            best = (sim, ex)
    if best and best[0] >= 0.2:
        sim, ex = best
        out.append((round(2.5 * sim, 4), ex["id"], f"Giống bộ mẫu “{ex.get('event')}” của {ex.get('taught_by') or 'người dạy'}"))
    # 4) worn history: never the exact same outfit two days in a row
    day = context.get("day")
    if day:
        try:
            yesterday = (date_cls.fromisoformat(day) - timedelta(days=1)).isoformat()
        except ValueError:
            yesterday = None
        for worn in context.get("worn") or []:
            if worn.get("date") in (day, yesterday) and set(worn.get("item_ids") or []) == ids:
                when = "hôm nay" if worn.get("date") == day else "hôm qua"
                out.append((-4.0, "hist:worn", f"Bạn đã mặc y hệt bộ này {when}"))
                break
    return out


# ---- prompt addendum ---------------------------------------------------------------------------


def prompt_addendum(context: dict[str, Any], *, max_examples: int = 3) -> str:
    by_id = context.get("items_by_id") or {}
    lines: list[str] = []
    applicable = [e for e in context.get("rules") or []
                  if is_actionable(e["constraint"]) and rule_is_applicable(e["constraint"], context)]
    if applicable:
        lines.append("QUY TẮC ÁP DỤNG HÔM NAY (trợ lý đã hiểu; phần BẮT BUỘC đã được lọc khỏi tủ đồ, phần NÊN hãy cố theo):")
        for e in applicable[:12]:
            lines.append(f'- [{e["rule"]["id"]}] {describe_constraint(e["constraint"], by_id)} '
                         f'(dạy bởi {_teacher(e["rule"])})')
    examples = [ex for ex in context.get("examples") or []
                if (ex.get("occasions") and set(ex["occasions"]) & set(context.get("occasions") or []))
                or (not ex.get("occasions") and fold_vi(ex.get("event")) == fold_vi(context.get("event")))]
    if examples:
        lines.append("BỘ MẪU DO NGƯỜI DẠY CHO DỊP NÀY (phối theo tinh thần này, có thể dùng lại các món):")
        for ex in examples[-max_examples:]:
            names = ", ".join(str(by_id[i].get("name")) for i in ex["item_ids"] if i in by_id)
            note = f' — “{ex["note"]}”' if ex.get("note") else ""
            lines.append(f'- [{ex["id"]}] {ex.get("event")} ({ex.get("taught_by")}): {json.dumps(ex["item_ids"])} = {names}{note}')
    summary = profile_summary({"profile": context.get("profile")}, list(by_id.values())) \
        if (context.get("profile") or {}).get("events") else None
    taste = [f for f in (summary or {}).get("facts", []) if not f.startswith("Chưa mặc")]
    if taste:
        lines.append("GU CỦA NGƯỜI DÙNG (học từ phản hồi 👍/👎 và đồ đã mặc): " + " ".join(taste))
    day = context.get("day")
    recent = [w for w in context.get("worn") or [] if w.get("date") and day and w["date"] <= day][-2:]
    if recent:
        lines.append("ĐÃ MẶC GẦN ĐÂY (đừng gợi ý lại y hệt): " + "; ".join(
            f'{w["date"]}: {json.dumps(w.get("item_ids"))}' for w in recent))
    return "\n".join(lines)


def augment_prompt(prompt: str, context: dict[str, Any]) -> str:
    extra = prompt_addendum(context)
    if not extra:
        return prompt
    marker = "\nYÊU CẦU:"
    if marker in prompt:
        head, tail = prompt.split(marker, 1)
        return f"{head}{extra}\n{marker}{tail}"
    return f"{prompt}\n{extra}"


# ---- post-processing: repair, rank, credit ------------------------------------------------------


_REPAIR_SLOTS = {"outerwear": 1, "shoes": 1, "accessory": 2}


def _repair(outfit: dict[str, Any], context: dict[str, Any], pool: list[dict[str, Any]]) -> list[str]:
    """Satisfy applicable require (must: add/replace; should/prefer: only fill an empty optional slot)."""
    by_id = context.get("items_by_id") or {}
    anchor_id = context.get("anchor_id")
    notes: list[str] = []
    for entry in context.get("rules") or []:
        constraint, rule = entry["constraint"], entry["rule"]
        if not is_actionable(constraint) or not rule_is_applicable(constraint, context):
            continue
        must = constraint["strength"] == "must"
        wanted = [(s, must) for s in constraint["require"]] + [(s, False) for s in constraint["prefer"]]
        for selector, hard in wanted:
            items = [by_id[i] for i in outfit["item_ids"] if i in by_id]
            if _selector_met(items, selector):
                continue
            cands = sorted((it for it in pool if item_matches(it, selector) and it["id"] not in outfit["item_ids"]),
                           key=lambda it: (-(context.get("affinity") or {}).get(f"item:{it['id']}", 0.0), it["id"]))
            for cand in cands:
                cat = cand.get("category")
                same = [it for it in items if it.get("category") == cat]
                if cat in _REPAIR_SLOTS and len(same) < _REPAIR_SLOTS[cat]:
                    outfit["item_ids"].append(cand["id"])
                elif hard and same and all(it["id"] != anchor_id for it in same) and cat != "dress" \
                        and not (cat in ("top", "bottom") and any(it.get("category") == "dress" for it in items)):
                    outfit["item_ids"] = [cand["id"] if i == same[0]["id"] else i for i in outfit["item_ids"]]
                else:
                    continue
                notes.append(f"Thêm {cand.get('name')} theo quy tắc của {_teacher(rule)}.")
                break
    return notes


def _credit_rules(outfit_items: list[dict[str, Any]], claimed: list[str], context: dict[str, Any],
                  pool: list[dict[str, Any]]) -> list[str]:
    """Rule ids that really shaped (and are honoured by) this outfit."""
    banned = context.get("banned") or {}
    credited: list[str] = []
    for entry in context.get("rules") or []:
        constraint, rule = entry["constraint"], entry["rule"]
        rid = rule["id"]
        applicable = rule_is_applicable(constraint, context)
        if not applicable:
            continue
        if not is_actionable(constraint):
            if rid in claimed:
                credited.append(rid)  # free-text rule the model says it followed
            continue
        violated = any(item_matches(it, s) for s in constraint["avoid"] for it in outfit_items) or \
            (constraint["strength"] == "must" and not all(_selector_met(outfit_items, s) for s in constraint["require"]))
        if violated:
            continue
        effect = False
        if banned.get(rid):
            effect = True  # it removed real items from the wardrobe before picking
        elif constraint["avoid"] and any(item_matches(it, s) for s in constraint["avoid"] for it in pool):
            effect = True  # soft avoid: there were matching items and the outfit has none
        if any(_selector_met(outfit_items, s) for s in constraint["require"] + constraint["prefer"]):
            effect = True
        if effect:
            credited.append(rid)
    return credited


def finalize(
    outfits: list[dict[str, Any]],
    items: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    max_outfits: int = 3,
    preserve_selection: bool = False,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Repair → score (prior from the picker/model order + rule_adjustments) → rank → credit.

    `items` = the allowed pool (after enforce_must). Adds per outfit: rules_used (accurate rule ids),
    learned [{kind, id, label_vi, taught_by?}], adjustments [{delta, source, reason_vi}].
    """
    by_id = context.get("items_by_id") or {}
    pool = [it for it in items if it["id"] in by_id] or items
    scored = []
    seen: set[frozenset[str]] = set()
    for index, outfit in enumerate(outfits):
        outfit = dict(outfit)
        outfit["item_ids"] = [i for i in outfit.get("item_ids") or [] if i in by_id]
        notes = [] if preserve_selection else _repair(outfit, context, pool)
        key = frozenset(outfit["item_ids"])
        if not outfit["item_ids"] or key in seen:
            continue
        seen.add(key)
        if notes:
            tips = str(outfit.get("tips") or "").strip()
            outfit["tips"] = (tips + " " + " ".join(notes)).strip()
        adjustments = rule_adjustments(outfit, context)
        delta = sum(a[0] for a in adjustments)
        hard_violation = any(a[0] <= -6.0 for a in adjustments)
        prior = -0.35 * index
        scored.append((hard_violation, -(prior + delta), index, outfit, adjustments))
    if not preserve_selection:
        scored.sort(key=lambda row: (row[0], row[1], row[2]))
    # Drop rule-breaking / worn-yesterday outfits when compliant alternatives exist.
    compliant = [row for row in scored if not row[0] and not any(a[1] == "hist:worn" for a in row[4])]
    if not preserve_selection and len(compliant) >= min(2, len(scored)):
        scored = compliant + [row for row in scored if row not in compliant][: max(0, 2 - len(compliant))]
    used: set[str] = set()
    result = []
    rules_by_id = {e["rule"]["id"]: e["rule"] for e in context.get("rules") or []}
    examples_by_id = {e["id"]: e for e in context.get("examples") or []}
    for _, _, _, outfit, adjustments in scored[:max_outfits]:
        outfit_items = [by_id[i] for i in outfit["item_ids"]]
        claimed = [str(r) for r in outfit.get("rules_used") or []]
        credited = _credit_rules(outfit_items, claimed, context, pool)
        outfit["rules_used"] = credited
        used.update(credited)
        learned: list[dict[str, Any]] = []
        for rid in credited:
            rule = rules_by_id[rid]
            learned.append({"kind": "rule", "id": rid, "taught_by": rule.get("taught_by"),
                            "label_vi": f"Học từ {_teacher(rule)}: {rule.get('text')}"})
        for delta, source, reason in adjustments:
            if source in examples_by_id:
                ex = examples_by_id[source]
                learned.append({"kind": "example", "id": source, "taught_by": ex.get("taught_by"), "label_vi": reason})
            elif source.startswith("pref:") and delta > 0 and len([x for x in learned if x["kind"] == "profile"]) < 2:
                learned.append({"kind": "profile", "id": source, "label_vi": f"Theo gu của bạn: {reason[0].lower()}{reason[1:]}"})
        outfit["learned"] = learned
        outfit["adjustments"] = [{"delta": round(d, 3), "source": s, "reason_vi": r} for d, s, r in adjustments]
        outfit["score_delta"] = round(sum(d for d, _, _ in adjustments), 3)
        result.append(outfit)
    return result, used


def rank_candidates(outfits: list[dict[str, Any]], items: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback candidates best-first by rules/taste/examples (used to top up a short model answer)."""
    ranked, _ = finalize([dict(o, item_ids=list(o.get("item_ids") or [])) for o in outfits], items, context,
                         max_outfits=len(outfits))
    return ranked


def stylist_payload(context: dict[str, Any]) -> dict[str, Any]:
    """Small summary the API returns next to suggestions (what was learned/enforced)."""
    return {
        "relaxed_rules": context.get("relaxed") or [],
        "banned": {rid: sorted(ids) for rid, ids in (context.get("banned") or {}).items()},
        "examples_considered": [e["id"] for e in context.get("examples") or []
                                if set(e.get("occasions") or []) & set(context.get("occasions") or [])],
        "profile_votes": len((context.get("profile") or {}).get("events") or []),
    }


# ---- legacy helpers (were in stylist.py; kept for callers/tests) ------------------------------------


def banned_colors(text: str) -> list[str]:
    """Colors a rule forbids: "Đi đám cưới không mặc đồ trắng" → ["trắng"] (via the pattern parser)."""
    try:
        constraint = pattern_parse(text)
    except ConstraintError:
        return []
    if constraint["strength"] != "must":
        return []
    found: list[str] = []
    for selector in constraint["avoid"]:
        for color in selector.get("colors") or []:
            if color not in found:
                found.append(color)
    return found


def rule_occasions(rule: dict[str, Any]) -> list[str]:
    return list(constraint_of(rule)["when"]["events"])


def rule_applies(rule: dict[str, Any], event_occasions: list[str]) -> bool:
    events = constraint_of(rule)["when"]["events"]
    return not events or bool(set(events) & set(event_occasions))


def apply_rule_bans(
    items: list[dict[str, Any]], rules: list[dict[str, Any]], event: str, anchor_id: str | None,
    weather: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    context = build_context(items, rules, None, event=event, weather=weather, anchor_id=anchor_id)
    return enforce_must(items, context)


def attribute_rules(outfits: list[dict[str, Any]], rules: list[dict[str, Any]], event: str, enforced: set[str]) -> set[str]:
    """Legacy: credit enforced rules and occasion rules of this event to every outfit."""
    occasions = set(match_occasions(event))
    auto = [r["id"] for r in rules if r["id"] in enforced or (set(rule_occasions(r)) & occasions)]
    used: set[str] = set()
    for outfit in outfits:
        merged = list(dict.fromkeys([*(outfit.get("rules_used") or []), *auto]))
        outfit["rules_used"] = merged
        used.update(merged)
    return used

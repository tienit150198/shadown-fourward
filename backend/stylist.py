"""AI parts of "Trợ lý tủ đồ" (new in SHADOW): auto-tagging, "what am I wearing", outfit picking.

- tag_item():       Qwen-VL (VISION_MODEL) → item fields (Vietnamese), strictly normalized.
- describe_wearing(): Qwen-VL describes the person's current outfit, optionally matching a wardrobe item.
- suggest_outfits(): one chat_json call to STYLIST_MODEL (or COMPILER_MODEL) → 2–3 outfits that only use
  existing item ids; validated and repaired, with a deterministic rule-based picker as fallback
  (also used in mock mode).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import itertools
import json
import logging
import os
from typing import Any

from providers import LLMProvider, compiler_model, vision_model
from wardrobe_store import (
    CATEGORIES,
    CATEGORY_LABELS_VI,
    OCCASIONS,
    STYLES,
    fold_vi,
    jpeg_data_url,
    match_occasions,
    normalize_category,
    normalize_item_fields,
)
from weather import is_rainy

import outfit_scoring  # explainable scoring: review LLM outfits, fallback picker, score/why per outfit
from garment_attrs import TAG_PROMPT_EXTRA, prompt_fields

logger = logging.getLogger("uvicorn.error")

TAG_TIMEOUT_S = 30.0
WEARING_TIMEOUT_S = 20.0
STYLIST_TIMEOUT_S = 45.0
STYLIST_MAX_TOKENS = 1200
MAX_WARDROBE_FOR_PROMPT = 80

NEUTRAL_COLORS = {"trang", "den", "xam", "be", "kem", "nau", "navy", "xanh navy", "xanh than", "bac", "ghi"}


def stylist_model() -> str:
    return (os.getenv("STYLIST_MODEL") or "").strip() or compiler_model()


# ---- color helpers -----------------------------------------------------------

_NAMED_COLORS: list[tuple[str, tuple[int, int, int]]] = [
    ("trắng", (240, 240, 240)),
    ("đen", (25, 25, 25)),
    ("xám", (128, 128, 128)),
    ("be", (215, 195, 160)),
    ("nâu", (120, 80, 50)),
    ("đỏ", (200, 30, 40)),
    ("hồng", (235, 150, 170)),
    ("cam", (240, 140, 50)),
    ("vàng", (235, 210, 60)),
    ("xanh lá", (60, 150, 80)),
    ("xanh rêu", (110, 125, 80)),
    ("xanh dương", (50, 100, 200)),
    ("xanh navy", (30, 40, 80)),
    ("tím", (130, 70, 160)),
]


def dominant_color_vi(jpeg: bytes) -> str | None:
    """Nearest Vietnamese color name of the image centre (ignores near-white background)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as image:
            image = image.convert("RGB")
            w, h = image.size
            crop = image.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4)).resize((32, 32))
            data = crop.tobytes()
            pixels = [tuple(data[i:i + 3]) for i in range(0, len(data), 3)]
    except Exception:
        return None
    fg = [p for p in pixels if not all(c > 225 for c in p)] or pixels
    r = sum(p[0] for p in fg) / len(fg)
    g = sum(p[1] for p in fg) / len(fg)
    b = sum(p[2] for p in fg) / len(fg)
    return min(_NAMED_COLORS, key=lambda nc: sum((a - c) ** 2 for a, c in zip(nc[1], (r, g, b))))[0]


def _color_keys(item: dict[str, Any]) -> list[str]:
    return [fold_vi(c) for c in item.get("colors") or [] if c]


def _is_neutral(color: str) -> bool:
    return color in NEUTRAL_COLORS or any(color.startswith(n) for n in ("trang", "den", "xam", "be", "kem"))


def color_harmony(items: list[dict[str, Any]]) -> float:
    """Heuristic: neutrals go with anything; >1 distinct loud color is penalized."""
    loud: list[str] = []
    for item in items:
        if item.get("category") == "accessory":
            continue
        colors = _color_keys(item)
        main = colors[0] if colors else ""
        if main and not _is_neutral(main):
            loud.append(main)
    distinct = set(loud)
    if len(distinct) <= 1:
        return 1.0 if loud else 0.6
    if len(distinct) == 2:
        return -0.3
    return -1.2


# ---- 1) tagging ----------------------------------------------------------------

TAG_SYSTEM_PROMPT = (
    "Bạn là chuyên gia thời trang, gắn nhãn ảnh MỘT món đồ trong tủ đồ của người Việt. "
    "Chỉ trả về MỘT object JSON, không giải thích. Mọi chữ (trừ category và description_en) bằng tiếng Việt có dấu."
)


TAG_COLORS = (
    "trắng", "đen", "xám", "be", "kem", "nâu", "đỏ", "đỏ đô", "hồng", "cam", "cam đào", "vàng", "xanh lá",
    "xanh rêu", "xanh bạc hà", "xanh dương", "xanh dương đậm", "xanh navy", "xanh cổ vịt", "tím", "bạc",
)

# English words Qwen-VL sometimes leaks into Vietnamese tags ("Áo velvet xanh cobalt"). Longest first.
_EN_TO_VI: tuple[tuple[str, str], ...] = (
    ("xanh cobalt", "xanh dương đậm"), ("xanh royal", "xanh dương đậm"), ("royal blue", "xanh dương đậm"),
    ("navy blue", "xanh navy"), ("light blue", "xanh dương nhạt"), ("dark blue", "xanh dương đậm"),
    ("xanh teal", "xanh cổ vịt"), ("xanh mint", "xanh bạc hà"), ("xanh olive", "xanh ô liu"),
    ("cobalt", "xanh dương đậm"), ("teal", "xanh cổ vịt"), ("burgundy", "đỏ đô"), ("maroon", "đỏ đô"),
    ("beige", "be"), ("khaki", "kaki"), ("olive", "xanh ô liu"), ("mint", "xanh bạc hà"), ("navy", "xanh navy"),
    ("white", "trắng"), ("black", "đen"), ("grey", "xám"), ("gray", "xám"), ("blue", "xanh dương"),
    ("red", "đỏ"), ("pink", "hồng"), ("green", "xanh lá"), ("brown", "nâu"), ("yellow", "vàng"),
    ("purple", "tím"), ("orange", "cam"), ("cream", "kem"),
    ("velvet", "nhung"), ("corduroy", "nhung tăm"), ("silk", "lụa"), ("linen", "lanh"), ("wool", "len"),
    ("knit", "len"), ("suede", "da lộn"), ("leather", "da"), ("chiffon", "voan"), ("lace", "ren"),
    ("denim", "jean"),
)


def vietnamize(text: Any) -> Any:
    """Replace common English garment/color words in a Vietnamese tag; keeps "xanh navy" intact."""
    if not isinstance(text, str) or not text:
        return text
    import re

    out = text
    for en, vi in _EN_TO_VI:
        if en == "navy":
            out = re.sub(r"(?<!xanh )\bnavy\b", vi, out, flags=re.IGNORECASE)
        else:
            out = re.sub(rf"\b{re.escape(en)}\b", vi, out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    # "Áo nhung xanh dương đậm" – drop words repeated back-to-back after replacement ("xanh xanh dương").
    out = re.sub(r"\b(\w+) \1\b", r"\1", out)
    return out[:1].upper() + out[1:] if text[:1].isupper() else out


def _tag_prompt(name_hint: str | None, category_hint: str | None) -> str:
    hints = []
    if name_hint:
        hints.append(f"Người dùng đặt tên: “{name_hint}”.")
    if category_hint:
        hints.append(f"Người dùng chọn loại: {category_hint}.")
    return (
        "Nhìn ảnh và mô tả món đồ chính (bỏ qua người mẫu, móc treo, nền).\n"
        + ("\n".join(hints) + "\n" if hints else "")
        + "Trả về JSON đúng các khóa sau:\n"
        "{\n"
        '  "name": "tên ngắn tiếng Việt, 2-6 từ, có màu, vd: Áo sơ mi trắng tay dài. KHÔNG dùng từ tiếng Anh '
        '(velvet→nhung, denim→jean, lace→ren, cobalt→xanh dương đậm)",\n'
        f'  "category": "một trong: {"|".join(CATEGORIES)} (top=áo, bottom=quần/chân váy, dress=váy liền/đầm, '
        'outerwear=áo khoác/blazer/cardigan, shoes=giày dép, accessory=túi/mũ/kính/khăn/trang sức)",\n'
        f'  "colors": ["1-3 màu chính, màu nhiều nhất đứng đầu, chọn từ: {", ".join(TAG_COLORS)}"],\n'
        f'  "style": "một phong cách, ưu tiên: {", ".join(STYLES)}",\n'
        '  "warmth": số nguyên 1-5 (1=rất mát: áo hai dây/quần short; 2=mỏng: áo thun, sơ mi cộc; '
        "3=vừa: sơ mi dài tay, quần jean; 4=ấm: áo len, blazer dày; 5=rất ấm: áo phao, măng tô),\n"
        f'  "occasions": ["2-4 dịp, chọn từ: {", ".join(OCCASIONS)}; có thể thêm 1 dịp khác"],\n'
        '  "seasons": ["các mùa hợp: xuân, hạ, thu, đông"],\n'
        '  "pattern": "trơn|kẻ sọc|kẻ caro|hoa|chấm bi|họa tiết|in chữ/hình",\n'
        '  "material": "chất liệu đoán được (cotton, jean, len, lụa, da, kaki, voan, nhung...) hoặc \\"\\"",\n'
        '  "notes": "1 câu gợi ý phối ngắn bằng tiếng Việt",\n'
        '  "description_en": "short English garment description for a try-on model, e.g. '
        'white long-sleeve cotton shirt",\n'
        + TAG_PROMPT_EXTRA  # garment_attrs hook: formality/fit/sleeve/length/fabric/breathability/rain_ok
        + "}"
    )


def mock_tags(jpeg: bytes, *, name: str | None = None, category: str | None = None) -> dict[str, Any]:
    """Deterministic placeholder tags (offline demo): dominant color + category defaults."""
    digest = int(hashlib.sha1(jpeg).hexdigest(), 16)
    cat = normalize_category(category) or "top"
    color = dominant_color_vi(jpeg) or "chưa rõ"
    label, warmth, occasions, style = {
        "top": ("Áo", 2, ["đi làm", "đi chơi"], "thường ngày"),
        "bottom": ("Quần", 3, ["đi làm", "đi chơi"], "thường ngày"),
        "dress": ("Váy", 2, ["hẹn hò", "dự tiệc"], "nữ tính"),
        "outerwear": ("Áo khoác", 4, ["đi làm", "đi chơi"], "thường ngày"),
        "shoes": ("Giày", 3, ["đi làm", "đi chơi"], "thường ngày"),
        "accessory": ("Phụ kiện", 1, ["đi chơi"], "thường ngày"),
    }[cat]
    english_noun = _NOUN_EN[cat]
    return {
        "name": name or f"{label} {color}",
        "category": cat,
        "colors": [color],
        "style": style,
        "warmth": warmth,
        "occasions": occasions,
        "seasons": ["xuân", "hạ", "thu"] if warmth <= 3 else ["thu", "đông"],
        "pattern": "trơn",
        "material": "",
        "notes": f"Nhãn tạm (chế độ demo, mã {digest % 1000:03d}) — bạn có thể sửa.",
        "description_en": f"{color_en(color)} {english_noun}".strip(),
    }


_NOUN_EN = {"top": "top", "bottom": "pants", "dress": "dress", "outerwear": "jacket",
            "shoes": "shoes", "accessory": "accessory"}

_COLOR_EN = {
    "trang": "white", "den": "black", "xam": "grey", "be": "beige", "kem": "cream", "nau": "brown",
    "do": "red", "hong": "pink", "cam": "orange", "vang": "yellow", "xanh la": "green",
    "xanh reu": "olive green", "xanh duong": "blue", "xanh navy": "navy", "xanh than": "navy",
    "tim": "purple", "xanh": "blue", "xanh duong dam": "dark blue", "xanh duong nhat": "light blue",
    "do do": "burgundy", "cam dao": "peach", "xanh co vit": "teal", "xanh o liu": "olive green", "xanh ngoc": "teal", "xanh bac ha": "mint green", "bac": "silver",
    "ghi": "grey", "dao": "peach", "hong dao": "peach", "cam dat": "terracotta",
}


def color_en(color_vi: str) -> str:
    return _COLOR_EN.get(fold_vi(color_vi), "")


async def tag_item(
    provider: LLMProvider,
    jpeg: bytes,
    *,
    name: str | None = None,
    category: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Return (normalized fields, source) where source is "ai" | "mock" | "fallback".

    User-provided name/category always override the model.
    """
    user_category = normalize_category(category) if category else None
    if provider.mode == "mock":
        return normalize_item_fields(mock_tags(jpeg, name=name, category=user_category)), "mock"
    content = [
        {"type": "text", "text": _tag_prompt(name, user_category)},
        {"type": "image_url", "image_url": {"url": jpeg_data_url(jpeg)}},
    ]
    try:
        raw = await asyncio.wait_for(
            provider.chat_json(
                model=vision_model(),
                system=TAG_SYSTEM_PROMPT,
                content=content,
                max_tokens=650,
                temperature=0.1,
            ),
            timeout=TAG_TIMEOUT_S,
        )
        if not isinstance(raw, dict):
            raise ValueError("tag reply is not an object")
        model_category = normalize_category(raw.get("category"))
        if model_category is None:
            # Strict: unknown enum → infer from the name, else reject the reply.
            model_category = normalize_category(raw.get("name"))
        if model_category is None and user_category is None:
            raise ValueError(f"unknown category {raw.get('category')!r}")
        raw["category"] = user_category or model_category
        for key in ("name", "style", "pattern", "material"):
            raw[key] = vietnamize(raw.get(key))
        if isinstance(raw.get("colors"), list):
            raw["colors"] = list(dict.fromkeys(vietnamize(c) for c in raw["colors"] if isinstance(c, str)))
        if name:
            raw["name"] = name
        fields = normalize_item_fields(raw)
        if not fields["colors"]:
            fields["colors"] = [dominant_color_vi(jpeg) or "chưa rõ"]
        if not fields.get("description_en"):
            fields["description_en"] = fallback_description_en(fields)
        return fields, "ai"
    except Exception as error:
        logger.warning("wardrobe tagging failed, using fallback tags: %r", error)
        return normalize_item_fields(mock_tags(jpeg, name=name, category=user_category)), "fallback"


def fallback_description_en(item: dict[str, Any]) -> str:
    noun = _NOUN_EN.get(item.get("category") or "", "garment")
    name = fold_vi(item.get("name"))
    if item.get("category") == "bottom" and ("vay" in name or "skirt" in name):
        noun = "skirt"
    elif item.get("category") == "bottom" and "jean" in name:
        noun = "jeans"
    colors = [color_en(c) for c in item.get("colors") or []]
    colors = [c for c in colors if c]
    return " ".join(([" and ".join(colors[:2])] if colors else []) + [noun])


# ---- 2) what is the person wearing --------------------------------------------

WEARING_SYSTEM_PROMPT = (
    "Bạn là trợ lý thời trang. Nhìn ảnh một người và mô tả trang phục họ đang mặc. "
    "Chỉ trả về MỘT object JSON. Viết tiếng Việt có dấu."
)


def _compact_for_matching(items: list[dict[str, Any]]) -> str:
    rows = [
        {"id": it["id"], "name": it.get("name"), "category": it.get("category"), "colors": it.get("colors")}
        for it in items[:MAX_WARDROBE_FOR_PROMPT]
    ]
    return json.dumps(rows, ensure_ascii=False)


_PRIMARY_ORDER = ("dress", "top", "outerwear", "bottom")


def _colors_overlap(a: list[str], b: list[str]) -> bool:
    fa, fb = [fold_vi(x) for x in a if x], [fold_vi(x) for x in b if x]
    return any(x == y or x in y or y in x for x in fa for y in fb)


def verify_match(match_id: Any, garments: list[dict[str, Any]], items: list[dict[str, Any]]) -> str | None:
    """Accept the model's wardrobe match only if a worn garment has the same category and a shared color."""
    if not isinstance(match_id, str):
        return None
    item = next((it for it in items if it["id"] == match_id), None)
    if item is None:
        return None
    for garment in garments:
        if garment["category"] == item.get("category") and _colors_overlap(garment["colors"], item.get("colors") or []):
            return match_id
    return None


def primary_garment(garments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The worn garment to keep when building outfits (dress > top > outerwear > bottom)."""
    for category in _PRIMARY_ORDER:
        for garment in garments:
            if garment["category"] == category:
                return garment
    return None


async def describe_wearing(
    provider: LLMProvider, image_data_url: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """→ {"wearing", "garments": [{"category","colors","description"}], "primary", "match_item_id", "source"}.

    The description is written from the image first (wardrobe names would bias it); the optional
    wardrobe match is then verified deterministically (same category + overlapping color).
    """
    if provider.mode == "mock":
        return {
            "wearing": "Chế độ demo: chưa phân tích ảnh đồ đang mặc.",
            "garments": [],
            "primary": None,
            "match_item_id": None,
            "source": "mock",
        }
    text = (
        "BƯỚC 1 — chỉ nhìn ẢNH: liệt kê từng món người trong ảnh đang mặc (bỏ qua nền, đồ vật). "
        "Mô tả đúng những gì thấy: loại, màu, tay áo (dài/ngắn/không tay), cổ áo, dáng. "
        "KHÔNG dùng tên món trong tủ đồ để mô tả.\n"
        "BƯỚC 2 — so với tủ đồ bên dưới: chỉ ghép nếu một món trong tủ GIỐNG HỆT món đang mặc "
        "(cùng loại, cùng màu, cùng tay áo/cổ áo/dáng). Khác bất kỳ chi tiết nào → null.\n"
        "Tủ đồ (JSON):\n"
        + _compact_for_matching(items)
        + "\n\nTrả về JSON:\n"
        '{"garments": [{"category": "' + "|".join(CATEGORIES) + '", "colors": ["màu tiếng Việt"], '
        '"description": "mô tả ngắn theo ảnh, vd: áo sơ mi không tay màu hồng nhạt có hai túi ngực"}],\n'
        ' "wearing": "1 câu ngắn liệt kê các món + màu, vd: áo sơ mi trắng, quần jean xanh",\n'
        ' "match_item_id": "id món trong tủ giống hệt món đang mặc, hoặc null"}'
    )
    content = [
        {"type": "image_url", "image_url": {"url": image_data_url}},
        {"type": "text", "text": text},
    ]
    try:
        raw = await asyncio.wait_for(
            provider.chat_json(
                model=vision_model(),
                system=WEARING_SYSTEM_PROMPT,
                content=content,
                max_tokens=400,
                temperature=0.0,
            ),
            timeout=WEARING_TIMEOUT_S,
        )
    except Exception as error:
        logger.warning("describe_wearing failed: %r", error)
        return {"wearing": "", "garments": [], "primary": None, "match_item_id": None, "source": "error"}
    garments = []
    for garment in raw.get("garments") or []:
        if not isinstance(garment, dict):
            continue
        category = normalize_category(garment.get("category")) or normalize_category(garment.get("description"))
        if category is None:
            continue
        colors = [str(c).strip().lower() for c in garment.get("colors") or [] if str(c).strip()][:3]
        garments.append({"category": category, "colors": colors, "description": str(garment.get("description") or "")[:120]})
    garments = garments[:6]
    match = verify_match(raw.get("match_item_id"), garments, items)
    wearing = str(raw.get("wearing") or "").strip()[:300]
    if not wearing and garments:
        wearing = ", ".join(g["description"] or CATEGORY_LABELS_VI[g["category"]] for g in garments)
    return {
        "wearing": wearing,
        "garments": garments,
        "primary": primary_garment(garments),
        "match_item_id": match,
        "source": "ai",
    }


# ---- 3) outfit suggestion -------------------------------------------------------


def target_warmth(weather: dict[str, Any] | None) -> float:
    """Ideal per-garment warmth for the day's feel-temperature (default ~25°C)."""
    if weather and weather.get("available"):
        t_min, t_max = weather.get("t_min"), weather.get("t_max")
        if isinstance(t_min, (int, float)) and isinstance(t_max, (int, float)):
            temp = t_min * 0.4 + t_max * 0.6
        elif isinstance(t_max, (int, float)):
            temp = t_max
        else:
            temp = 25.0
    else:
        temp = 25.0
    if temp >= 30:
        return 1.5
    if temp >= 26:
        return 2.0
    if temp >= 22:
        return 2.5
    if temp >= 18:
        return 3.2
    if temp >= 14:
        return 4.0
    return 4.7


def needs_outerwear(weather: dict[str, Any] | None) -> bool:
    if not weather or not weather.get("available"):
        return False
    t_min = weather.get("t_min")
    return isinstance(t_min, (int, float)) and t_min < 20


def too_hot_for_outerwear(weather: dict[str, Any] | None) -> bool:
    if not weather or not weather.get("available"):
        return False
    t_min, t_max = weather.get("t_min"), weather.get("t_max")
    return isinstance(t_min, (int, float)) and isinstance(t_max, (int, float)) and t_min >= 26 and t_max >= 31


def _occasion_score(item: dict[str, Any], event_occasions: list[str], event_text: str) -> float:
    occ = set(item.get("occasions") or [])
    score = 0.0
    if event_occasions:
        score += 1.5 if occ & set(event_occasions) else -0.5
    style = fold_vi(item.get("style"))
    folded_event = fold_vi(event_text)
    if style and style in folded_event:
        score += 0.5
    formal = {"đi làm", "phỏng vấn", "đám cưới", "dự tiệc"}
    if set(event_occasions) & formal and fold_vi(item.get("style")) in ("the thao",):
        score -= 1.0
    return score


def _item_score(item: dict[str, Any], weather: dict[str, Any] | None, event_occasions: list[str], event: str) -> float:
    warmth = item.get("warmth") or 2
    target = target_warmth(weather)
    category = item.get("category")
    if category == "outerwear":
        # Outerwear is judged by itself: only the cold end matters.
        w_score = -abs(warmth - max(3.0, target + 0.5)) * 0.6
    elif category in ("shoes", "accessory"):
        w_score = 0.0
    else:
        w_score = -abs(warmth - target) * 0.8
    rain_penalty = 0.0
    if is_rainy(weather) and category == "shoes":
        material = fold_vi(item.get("material"))
        if any(m in material for m in ("vai", "canvas", "da lon", "suede")):
            rain_penalty -= 0.8
    return w_score + _occasion_score(item, event_occasions, event) + rain_penalty


def _items_by_cat(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
    for item in items:
        grouped.setdefault(item.get("category") or "top", []).append(item)
    return grouped


def is_wearable(categories: set[str]) -> bool:
    return "dress" in categories or {"top", "bottom"} <= categories


def _names(items: list[dict[str, Any]]) -> str:
    return ", ".join(str(it.get("name") or it["id"]).lower() for it in items)


def _template_reason(outfit: list[dict[str, Any]], weather: dict[str, Any] | None, event: str) -> str:
    parts = [f"Bộ {_names(outfit)}"]
    if weather and weather.get("available") and weather.get("t_min") is not None:
        parts.append(f"độ ấm hợp với {round(weather['t_min'])}–{round(weather['t_max'])}°C")
    colors = [c for it in outfit for c in (it.get("colors") or [])[:1]]
    if colors:
        neutral = all(_is_neutral(fold_vi(c)) for c in colors)
        parts.append("tông trung tính dễ phối" if neutral else f"màu {', '.join(dict.fromkeys(colors))} hài hoà")
    if event:
        parts.append(f"phù hợp cho {event.strip().rstrip('.')}")
    return "; ".join(parts) + "."


def _template_tips(outfit: list[dict[str, Any]], weather: dict[str, Any] | None) -> str:
    tips = []
    cats = {it.get("category") for it in outfit}
    if is_rainy(weather):
        tips.append("Trời có thể mưa — mang theo ô, tránh giày vải sáng màu.")
    if needs_outerwear(weather) and "outerwear" not in cats:
        tips.append("Sáng tối se lạnh — nên mang thêm một áo khoác mỏng.")
    if too_hot_for_outerwear(weather):
        tips.append("Trời nóng — chọn chất liệu thoáng, mang nước theo.")
    if not tips:
        tips.append("Thêm một phụ kiện nhỏ (đồng hồ, túi) để bộ đồ có điểm nhấn.")
    return " ".join(tips)


def rule_based_outfits(
    items: list[dict[str, Any]],
    *,
    weather: dict[str, Any] | None,
    event: str = "",
    anchor_id: str | None = None,
    worn_categories: set[str] | None = None,
    max_outfits: int = 3,
) -> list[dict[str, Any]]:
    """Deterministic picker: warmth vs temperature, occasion match, color harmony."""
    worn = set(worn_categories or set()) - {"shoes", "accessory"}
    event_occasions = match_occasions(event)
    by_id = {it["id"]: it for it in items}
    anchor = by_id.get(anchor_id) if anchor_id else None
    grouped = _items_by_cat(items)
    score = {it["id"]: _item_score(it, weather, event_occasions, event) for it in items}

    bases: list[list[dict[str, Any]]] = []
    tops = grouped["top"] if "top" not in worn else [None]
    bottoms = grouped["bottom"] if "bottom" not in worn else [None]
    if "dress" in worn:
        bases.append([])
    else:
        for top, bottom in itertools.product(tops, bottoms):
            bases.append([x for x in (top, bottom) if x is not None])
        if not ({"top", "bottom"} & worn):
            bases.extend([[dress] for dress in grouped["dress"]])
    if anchor and anchor.get("category") in ("top", "bottom", "dress"):
        bases = [b for b in bases if anchor in b]

    def total(combo: list[dict[str, Any]]) -> float:
        return sum(score[it["id"]] for it in combo) + color_harmony(combo)

    candidates = []
    for base in bases:
        cats = {it["category"] for it in base} | worn
        if not is_wearable(cats):
            continue
        candidates.append((total(base) / max(1, len(base)), base))
    candidates.sort(key=lambda row: (-row[0], [it["id"] for it in row[1]]))

    chosen: list[list[dict[str, Any]]] = []
    used: dict[str, int] = {}
    pool = list(candidates)
    while pool and len(chosen) < max_outfits:
        # Diversity: penalize re-using garments already picked.
        pool.sort(key=lambda row: (-(row[0] - 0.7 * sum(used.get(it["id"], 0) for it in row[1])),
                                   [it["id"] for it in row[1]]))
        _, base = pool.pop(0)
        outfit = list(base)
        extras_wanted = []
        if (needs_outerwear(weather) or (is_rainy(weather) and not too_hot_for_outerwear(weather))) \
                and "outerwear" not in worn:
            extras_wanted.append("outerwear")
        extras_wanted.append("shoes")
        for category in extras_wanted:
            options = grouped.get(category) or []
            if options:
                best = max(options, key=lambda it: (score[it["id"]] + color_harmony(outfit + [it]), it["id"]))
                outfit.append(best)
        if anchor and anchor not in outfit:
            outfit = [it for it in outfit if it["category"] != anchor["category"]] + [anchor]
        for it in outfit:
            used[it["id"]] = used.get(it["id"], 0) + 1
        chosen.append(outfit)

    results = []
    for index, outfit in enumerate(chosen):
        results.append({
            "title": _template_title(outfit, event, index),
            "item_ids": [it["id"] for it in outfit],
            "reason": _template_reason(outfit, weather, event),
            "tips": _template_tips(outfit, weather),
        })
    return results


def _template_title(outfit: list[dict[str, Any]], event: str, index: int) -> str:
    styles = [it.get("style") for it in outfit if it.get("style")]
    style = styles[0] if styles else "gọn gàng"
    labels = ["Lựa chọn an toàn", "Phương án thứ hai", "Thử chút mới lạ"]
    return f"{labels[min(index, 2)]}: {style}"


def _compact_wardrobe(items: list[dict[str, Any]]) -> str:
    lines = []
    for it in items[:MAX_WARDROBE_FOR_PROMPT]:
        lines.append(json.dumps({
            "id": it["id"],
            "name": it.get("name"),
            "cat": it.get("category"),
            "colors": it.get("colors"),
            "style": it.get("style"),
            "warmth": it.get("warmth"),
            "occ": it.get("occasions"),
            **prompt_fields(it),  # garment_attrs hook
        }, ensure_ascii=False))
    return "\n".join(lines)


STYLIST_SYSTEM_PROMPT = (
    "Bạn là stylist cá nhân người Việt, phối đồ CHỈ từ tủ đồ được cho. "
    "Chỉ trả về MỘT object JSON hợp lệ, không markdown, không giải thích ngoài JSON. Viết tiếng Việt có dấu, ngắn gọn, thân thiện."
)


def _weather_line(weather: dict[str, Any] | None) -> str:
    if not weather or not weather.get("available"):
        return "Không có dữ liệu thời tiết (giả định 24–30°C, không mưa)."
    return str(weather.get("summary_vi") or "")


def build_stylist_prompt(
    items: list[dict[str, Any]],
    *,
    weather: dict[str, Any] | None,
    event: str,
    notes: str,
    rules: list[dict[str, Any]],
    anchor: dict[str, Any] | None,
    wearing: dict[str, Any] | None,
) -> str:
    rules_text = "\n".join(
        f'- [{r["id"]}] {r["text"]} (dạy bởi {r.get("taught_by") or "?"})' for r in rules[:40]
    ) or "(chưa có)"
    lines = [
        "TỦ ĐỒ (mỗi dòng một món; cat: top=áo, bottom=quần/chân váy, dress=váy liền, outerwear=áo khoác, "
        "shoes=giày, accessory=phụ kiện; warmth 1=rất mát … 5=rất ấm):",
        _compact_wardrobe(items),
        "",
        f"THỜI TIẾT: {_weather_line(weather)}",
        f"SỰ KIỆN: {event or 'đi chơi thường ngày'}",
    ]
    if notes:
        lines.append(f"GHI CHÚ CỦA NGƯỜI DÙNG: {notes}")
    if wearing and wearing.get("wearing"):
        lines.append(f"NGƯỜI DÙNG ĐANG MẶC: {wearing['wearing']}.")
        primary = wearing.get("primary")
        if primary and not anchor:
            lines.append(
                f"GIỮ NGUYÊN món đang mặc: {primary.get('description') or primary['category']} "
                f"(loại {primary['category']}, không có trong tủ đồ). KHÔNG chọn thêm món loại {primary['category']}"
                + ("" if primary["category"] in ("dress", "outerwear") else " và KHÔNG chọn dress")
                + "; "
                "bộ phối được coi là đã có món này, chỉ chọn các món còn lại từ tủ đồ."
            )
    if anchor:
        lines.append(
            f'MÓN PHẢI GIỮ: id "{anchor["id"]}" ({anchor.get("name")}) — BẮT BUỘC có trong MỌI bộ.'
        )
    lines += [
        "",
        "QUY TẮC PHỐI ĐỒ DO NGƯỜI DÙNG DẠY (ưu tiên áp dụng khi liên quan):",
        rules_text,
        "",
        "YÊU CẦU:",
        "- Đưa ra 2-3 bộ khác nhau, chỉ dùng id có trong TỦ ĐỒ (không bịa id).",
        "- Mỗi bộ mặc được: (1 top + 1 bottom) HOẶC (1 dress); thêm tùy chọn tối đa 1 outerwear, 1 shoes, 1-2 accessory.",
        "- Độ ấm hợp nhiệt độ: ≥30°C chọn warmth 1-2, không áo khoác; 22-29°C warmth 2-3; 18-21°C warmth 3 + có thể áo khoác mỏng; "
        "<18°C warmth 3-5 + áo khoác. Nếu mưa: nhắc mang ô, tránh giày vải sáng màu.",
        "- Hợp sự kiện (công sở/dự tiệc lịch sự hơn, đi chơi thoải mái), màu sắc hài hoà (tối đa 2 màu nổi, còn lại trung tính).",
        "- Nếu tủ đồ thiếu món hợp sự kiện (vd: thể thao mà không có đồ thể thao), chọn bộ gần nhất, thoải mái nhất "
        "(không chọn đầm/chân váy cho thể thao) và nói rõ trong tips nên bổ sung món gì.",
        "- reason: 1 câu (tối đa 30 từ) vì sao bộ này hợp (thời tiết, sự kiện, màu). tips: 1 câu ngắn (tối đa 20 từ).",
        "- title: 2-4 từ, gợi đúng SỰ KIỆN và phong cách bộ đồ (không gọi \"công sở\"/\"dự tiệc\" nếu sự kiện không phải vậy).",
        "- shoes/outerwear/accessory là tùy chọn: nếu món duy nhất có sẵn không hợp (thời tiết, sự kiện, quy tắc) thì BỎ RA "
        "khỏi item_ids và gợi ý thay thế trong tips.",
        "- Không tự mâu thuẫn: nếu tips khuyên đổi/thay/bỏ một món thì món đó KHÔNG được có trong item_ids.",
        "- BẮT BUỘC: bộ nào tuân theo một quy tắc liên quan tới sự kiện/thời tiết hôm nay thì ghi id quy tắc đó "
        "vào rules_used của bộ (vd: [\"r_ab12\"]); quy tắc không liên quan thì bỏ qua.",
        "",
        "Trả về đúng JSON:",
        '{"outfits": [{"title": "tên bộ ngắn", "item_ids": ["id", "..."], "reason": "...", "tips": "...", '
        '"rules_used": ["id quy tắc"]}]}',
    ]
    return "\n".join(lines)


def validate_outfits(
    raw: Any,
    items: list[dict[str, Any]],
    *,
    weather: dict[str, Any] | None,
    anchor_id: str | None,
    worn_categories: set[str],
    rule_ids: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Keep only wearable outfits made of known ids; repair what can be repaired."""
    by_id = {it["id"]: it for it in items}
    anchor = by_id.get(anchor_id) if anchor_id else None
    worn = set(worn_categories) - {"shoes", "accessory"}
    outfits_raw = raw.get("outfits") if isinstance(raw, dict) else None
    if not isinstance(outfits_raw, list):
        return [], set()
    valid: list[dict[str, Any]] = []
    seen_sets: set[frozenset[str]] = set()
    used_rules: set[str] = set()
    for entry in outfits_raw[:5]:
        if not isinstance(entry, dict):
            continue
        ids = entry.get("item_ids")
        if not isinstance(ids, list):
            continue
        picked: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        limits = {"top": 1, "bottom": 1, "dress": 1, "outerwear": 1, "shoes": 1, "accessory": 2}
        for item_id in ids:
            item = by_id.get(str(item_id))
            if item is None or item in picked:
                continue  # drop unknown / duplicate ids
            category = item.get("category") or "top"
            if category in worn and (anchor is None or item is not anchor):
                continue  # user already wears this kind of garment
            if counts.get(category, 0) >= limits.get(category, 1):
                continue
            counts[category] = counts.get(category, 0) + 1
            picked.append(item)
        if anchor and anchor not in picked:
            picked = [it for it in picked if it.get("category") != anchor.get("category")] + [anchor]
        cats = {it.get("category") for it in picked}
        if "dress" in cats:
            keeps_separates = bool({"top", "bottom"} & worn) or (
                anchor is not None and anchor.get("category") in ("top", "bottom")
            )
            if keeps_separates:
                # A kept top/bottom (worn or "Giữ món này") can't go under a dress: drop the dress.
                picked = [it for it in picked if it.get("category") != "dress"]
            else:
                picked = [it for it in picked if it.get("category") not in ("top", "bottom")]
            cats = {it.get("category") for it in picked}
        if too_hot_for_outerwear(weather):
            picked = [it for it in picked if not (it.get("category") == "outerwear" and (it.get("warmth") or 0) >= 4)]
            cats = {it.get("category") for it in picked}
        if not is_wearable(cats | worn) or not picked:
            continue
        key = frozenset(it["id"] for it in picked)
        if key in seen_sets:
            continue
        seen_sets.add(key)
        rules_here = [str(r) for r in entry.get("rules_used") or [] if str(r) in rule_ids]
        used_rules.update(rules_here)
        valid.append({
            "title": str(entry.get("title") or "Bộ phối").strip()[:80],
            "item_ids": [it["id"] for it in picked],
            "reason": str(entry.get("reason") or "").strip()[:400],
            "tips": str(entry.get("tips") or "").strip()[:300],
            "rules_used": rules_here,
        })
    for outfit in valid:
        if not outfit["reason"]:
            outfit["reason"] = _template_reason([by_id[i] for i in outfit["item_ids"]], weather, "")
    return valid[:3], used_rules


# ---- taught rules + learned taste: see style_memory.py ------------------------------------------
# (the simple colour-ban code that lived here moved into style_memory; names re-exported for callers)
import style_memory  # noqa: E402
from style_memory import (  # noqa: E402,F401
    apply_rule_bans,
    attribute_rules,
    banned_colors,
    rule_applies,
    rule_occasions,
)


async def suggest_outfits(
    provider: LLMProvider,
    items: list[dict[str, Any]],
    *,
    weather: dict[str, Any] | None,
    event: str = "",
    notes: str = "",
    rules: list[dict[str, Any]] | None = None,
    anchor_id: str | None = None,
    wearing: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """→ {"outfits": [...], "rules_used": [rule objects], "source": "ai"|"ai+fallback"|"fallback"|"mock"}.

    `memory` = style_memory.load_memory(root) (feedback profile + teacher examples), optional.
    """
    rules = rules or []
    rule_by_id = {r["id"]: r for r in rules}
    primary = (wearing or {}).get("primary")
    worn_categories: set[str] = set()
    if primary and not anchor_id:
        worn_categories = {primary["category"]}
    by_id = {it["id"]: it for it in items}
    anchor = by_id.get(anchor_id) if anchor_id else None
    if not items:
        return {"outfits": [], "rules_used": [], "source": "empty"}
    # ---- style_memory hook 1/3: "must" rules filter the wardrobe before any picking ----
    style_ctx = style_memory.build_context(items, rules, memory, event=event, weather=weather, anchor_id=anchor_id)
    items, banned_by_rule = style_memory.enforce_must(items, style_ctx)

    def fallback(n: int = 3) -> list[dict[str, Any]]:
        # extra candidates so style_memory.finalize can re-rank them by rules/taste/examples
        # ---- outfit_scoring hook 1/3: deterministic picker = combination search on score_outfit ----
        return outfit_scoring.pick_outfits(
            items, weather=weather, event=event, anchor_id=anchor_id,
            worn_categories=worn_categories, max_outfits=max(n, 6),
        )

    score_ctx = outfit_scoring.build_context(event, weather, worn_categories=worn_categories, anchor_id=anchor_id)
    dropped: list[dict[str, Any]] = []

    if provider.mode == "mock":
        outfits = fallback()
        for outfit in outfits:
            outfit["rules_used"] = []
        # ---- style_memory hook 3/3 (mock): repair + rank + accurate credits ----
        outfits, used = style_memory.finalize(outfits, items, style_ctx)
        outfit_scoring.annotate_outfits(outfits, items, score_ctx)  # ---- outfit_scoring hook 3/3 (mock) ----
        return {"outfits": outfits, "rules_used": [r for r in rules if r["id"] in used], "source": "mock",
                "memory": style_memory.stylist_payload(style_ctx), "dropped": []}

    prompt = build_stylist_prompt(
        items, weather=weather, event=event, notes=notes, rules=rules, anchor=anchor, wearing=wearing
    )
    prompt = style_memory.augment_prompt(prompt, style_ctx)  # ---- style_memory hook 2/3: rules/taste/examples ----
    outfits: list[dict[str, Any]] = []
    used: set[str] = set()
    try:
        raw = await asyncio.wait_for(
            provider.chat_json(
                model=stylist_model(),
                system=STYLIST_SYSTEM_PROMPT,
                content=[{"type": "text", "text": prompt}],
                max_tokens=STYLIST_MAX_TOKENS,
                temperature=0.4,
            ),
            timeout=STYLIST_TIMEOUT_S,
        )
        outfits, used = validate_outfits(
            raw, items, weather=weather, anchor_id=anchor_id,
            worn_categories=worn_categories, rule_ids=set(rule_by_id),
        )
        # ---- outfit_scoring hook 2/3: drop hard violations (shorts at a wedding, jacket at 34°C…), re-rank ----
        outfits, dropped = outfit_scoring.review_outfits(outfits, items, score_ctx)
        if dropped:
            logger.info("stylist: dropped %d model outfit(s): %s", len(dropped), [d["reason_vi"] for d in dropped])
        if not outfits:
            logger.warning("stylist reply unusable, using rule-based picker: %s", str(raw)[:300])
    except Exception as error:
        logger.warning("stylist model failed, using rule-based picker: %r", error)

    source = "ai"
    if not outfits:
        outfits = fallback()
        source = "fallback"
    elif len(outfits) < (3 if dropped else 2):  # outfit_scoring: refill to 3 when outfits were dropped
        target = 3 if dropped else 2
        have = {frozenset(o["item_ids"]) for o in outfits}
        for extra in style_memory.rank_candidates(fallback(6), items, style_ctx):  # style_memory: best-first top-up
            if len(outfits) >= target:
                break
            if frozenset(extra["item_ids"]) not in have:
                have.add(frozenset(extra["item_ids"]))
                outfits.append(extra)
                source = "ai+fallback"
    for outfit in outfits:
        outfit.setdefault("rules_used", [])
    outfits = outfit_scoring.rank_outfits(outfits, items, score_ctx)  # outfit_scoring: best score first
    # ---- style_memory hook 3/3: repair "must require", re-rank by rules/taste/examples, accurate credits ----
    outfits, used = style_memory.finalize(outfits, items, style_ctx)
    outfit_scoring.annotate_outfits(outfits, items, score_ctx)  # ---- outfit_scoring hook 3/3 ----
    return {
        "outfits": outfits,
        "rules_used": [rule_by_id[r] for r in rule_by_id if r in used],
        "source": source,
        "memory": style_memory.stylist_payload(style_ctx),
        "dropped": dropped,
    }

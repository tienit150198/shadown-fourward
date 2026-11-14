"""Richer garment attributes for wardrobe items ("Trợ lý tủ đồ", new in SKI).

Schema additions per item (all optional on input, filled by `fill_attrs`; machine-readable English codes):

    color_hex      "#rrggbb"  dominant garment colour from the photo (Pillow quantize, background ignored)
    color_family   neutral | earth | denim | cool | warm
    subtype        tee | polo | shirt | oxford_shirt | dress_shirt | linen_shirt | cuban_shirt | tank | sweater |
                   blouse | shorts | sport_shorts | jeans | chinos | trousers | linen_pants | joggers | skirt |
                   pants | uv_jacket | windbreaker | overshirt | blazer | denim_jacket | hoodie | cardigan |
                   coat | puffer | jacket | sneakers | slip_on | loafers | dress_shoes | sandals | slides |
                   boots | heels | cap | hat | belt | bag | watch | glasses | scarf | jewelry | dress | other
    formality      1-5  (1 sport/beach/slides … 3 smart casual … 5 blazer/suit)
    fit            slim | regular | relaxed | oversized | null (shoes/accessories)
    sleeve         short | long | none | null (only top/outerwear/dress)
    length         short | long | null (only bottom/dress)
    fabric         cotton | linen | denim | poly | nylon | rayon | wool | silk | leather | suede | canvas | rubber | blend
    breathability  1-5  (5 = linen/mesh)
    rain_ok        bool (water-repellent outer, closed shoes that survive rain, quick-dry fabric)
    uv_protect     bool (UPF/sun jacket, cap/hat)
    attrs_v        schema version (items without it are migrated on load, see wardrobe_store)

`seasons` and `description_en` (existing fields) are filled when empty.

Derivation order per field: explicit value (user / hand-checked seed) > deterministic rules from the item's
own text (name, shop product name/type, material, style, English description: "Phom Rộng F4", "tay ngắn",
"trượt nước" …) > Qwen-VL tag (`TAG_PROMPT_EXTRA`) > category defaults. Right after AI tagging
(`tag_item_with_attrs`) strong rules beat the model ("rules_first"); everywhere else existing values win.

CLI (backfill the demo seed sets, offline):
    cd backend && ../.venv/bin/python garment_attrs.py backfill wardrobe_seed wardrobe_seed_shop
"""

from __future__ import annotations

import colorsys
import io
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ATTRS_VERSION = 1

FITS = ("slim", "regular", "relaxed", "oversized")
SLEEVES = ("short", "long", "none")
LENGTHS = ("short", "long")
FABRICS = ("cotton", "linen", "denim", "poly", "nylon", "rayon", "wool", "silk", "leather", "suede", "canvas",
           "rubber", "blend")
COLOR_FAMILIES = ("neutral", "earth", "denim", "cool", "warm")
SUBTYPES: dict[str, tuple[str, ...]] = {
    "top": ("tee", "polo", "shirt", "oxford_shirt", "dress_shirt", "linen_shirt", "cuban_shirt", "tank", "sweater",
            "blouse"),
    "bottom": ("shorts", "sport_shorts", "jeans", "chinos", "trousers", "linen_pants", "joggers", "skirt", "pants"),
    "outerwear": ("uv_jacket", "windbreaker", "overshirt", "blazer", "denim_jacket", "hoodie", "cardigan", "coat",
                  "puffer", "jacket"),
    "shoes": ("sneakers", "slip_on", "loafers", "dress_shoes", "sandals", "slides", "boots", "heels"),
    "accessory": ("cap", "hat", "belt", "bag", "watch", "glasses", "scarf", "jewelry"),
    "dress": ("dress",),
}
ALL_SUBTYPES = tuple(dict.fromkeys(s for group in SUBTYPES.values() for s in group)) + ("other",)
ATTR_FIELDS = ("color_hex", "color_family", "subtype", "formality", "fit", "sleeve", "length", "fabric",
               "breathability", "rain_ok", "uv_protect")
# Vietnamese labels for third-party UIs (codes stay English).
LABELS_VI: dict[str, dict[str, str]] = {
    "fit": {"slim": "ôm", "regular": "vừa", "relaxed": "rộng", "oversized": "oversize"},
    "sleeve": {"short": "tay ngắn", "long": "tay dài", "none": "không tay"},
    "length": {"short": "ngắn", "long": "dài"},
    "fabric": {"cotton": "cotton", "linen": "linen/đũi", "denim": "jean", "poly": "polyester", "nylon": "vải dù",
               "rayon": "rayon", "wool": "len", "silk": "lụa", "leather": "da", "suede": "da lộn",
               "canvas": "vải canvas", "rubber": "cao su/EVA", "blend": "vải pha"},
    "color_family": {"neutral": "trung tính", "earth": "tông đất", "denim": "jean", "cool": "tông lạnh",
                     "warm": "tông nóng"},
    "formality": {"1": "rất thoải mái", "2": "thường ngày", "3": "lịch sự", "4": "công sở", "5": "trang trọng"},
}


def _fold(text: Any) -> str:
    value = str(text or "").replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower()).strip()


# ---- normalisation (used by wardrobe_store.normalize_item_fields) ---------------------------------

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_ENUM_SYNONYMS: dict[str, dict[str, str]] = {
    "fit": {"om": "slim", "om vua": "slim", "slim fit": "slim", "skinny": "slim", "vua": "regular",
            "regular fit": "regular", "chuan": "regular", "rong": "relaxed", "suong": "relaxed", "loose": "relaxed",
            "relaxed fit": "relaxed", "oversize": "oversized", "boxy": "oversized", "rong thung": "oversized"},
    "sleeve": {"ngan": "short", "tay ngan": "short", "coc": "short", "coc tay": "short", "short sleeve": "short",
               "dai": "long", "tay dai": "long", "long sleeve": "long", "khong tay": "none", "sat nach": "none",
               "sleeveless": "none", "hai day": "none"},
    "length": {"ngan": "short", "dai": "long", "lung": "short", "mini": "short", "maxi": "long", "midi": "long"},
    "fabric": {"lanh": "linen", "dui": "linen", "jean": "denim", "kaki": "cotton", "khaki": "cotton",
               "thun": "cotton", "polyester": "poly", "du": "nylon", "vai du": "nylon", "da": "leather",
               "da that": "leather", "da lon": "suede", "len": "wool", "lua": "silk", "voan": "poly",
               "chiffon": "poly", "vai": "canvas", "cao su": "rubber", "eva": "rubber", "pha": "blend",
               "spandex": "poly", "mesh": "poly"},
    "color_family": {"trung tinh": "neutral", "dat": "earth", "tong dat": "earth", "jean": "denim",
                     "lanh": "cool", "nong": "warm"},
}
_ALLOWED: dict[str, tuple[str, ...]] = {
    "fit": FITS, "sleeve": SLEEVES, "length": LENGTHS, "fabric": FABRICS, "color_family": COLOR_FAMILIES,
    "subtype": ALL_SUBTYPES,
}


def _enum(field: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    folded = _fold(value).replace("_", " ").replace("-", " ").strip()
    allowed = _ALLOWED[field]
    code = folded.replace(" ", "_")
    if code in allowed:
        return code
    mapped = _ENUM_SYNONYMS.get(field, {}).get(folded)
    return mapped if mapped in allowed else None


def _int_1_5(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(1, min(5, number))


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        folded = _fold(value)
        if folded in ("true", "yes", "1", "co", "duoc", "ok"):
            return True
        if folded in ("false", "no", "0", "khong", "khong duoc"):
            return False
    return None


def normalize_hex(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _HEX_RE.match(value.strip())
    return f"#{match.group(1).lower()}" if match else None


def normalize_attr_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Clean the attribute fields present in `raw`; invalid values are dropped (never raise), null clears."""
    out: dict[str, Any] = {}
    for key in ATTR_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            if key in ("sleeve", "length", "fit"):
                out[key] = None  # explicit "not applicable"
            continue
        if key == "color_hex":
            clean = normalize_hex(value)
        elif key in ("formality", "breathability"):
            clean = _int_1_5(value)
        elif key in ("rain_ok", "uv_protect"):
            clean = _bool(value)
        else:
            clean = _enum(key, value)
        if clean is not None:
            out[key] = clean
    if isinstance(raw.get("attrs_v"), int) and not isinstance(raw.get("attrs_v"), bool):
        out["attrs_v"] = raw["attrs_v"]
    return out


# ---- colour ---------------------------------------------------------------------------------------

NAMED_HEX: dict[str, str] = {
    "trang": "#f2f2f0", "den": "#1c1c1c", "xam": "#8a8a8a", "xam nhat": "#c8c8c8", "xam dam": "#4a4a4a",
    "ghi": "#8a8a8a", "bac": "#c0c0c0", "be": "#d8c7a6", "kem": "#ede3cc", "nau": "#7a5233",
    "nau nhat": "#b89470", "nau dam": "#4e3324", "kaki": "#bfa77f", "do": "#c62828", "do do": "#7b1f2a",
    "hong": "#eba0b4", "cam": "#ef8a32", "cam dao": "#f4b393", "vang": "#e9cf45", "xanh la": "#3f9654",
    "xanh la dam": "#1f4d34", "xanh reu": "#5e6b3c", "xanh o liu": "#6b6b3a", "xanh bac ha": "#a8dcc6",
    "xanh co vit": "#1d6f72", "xanh ngoc": "#1d7f7a", "xanh duong": "#3a6fc4", "xanh duong nhat": "#a9c8e8",
    "xanh duong dam": "#1f3a6b", "xanh navy": "#1f2a48", "xanh than": "#1f2a48", "navy": "#1f2a48",
    "tim": "#7d4a9e", "xanh": "#3a6fc4", "xanh jean": "#4a6a96",
}
_NEUTRAL_NAMES = ("trang", "den", "xam", "ghi", "bac", "kem", "xanh navy", "xanh than", "navy")
_EARTH_NAMES = ("be", "nau", "kaki", "xanh reu", "xanh o liu", "bo", "vang dat")


def name_hex(color: Any) -> str | None:
    folded = _fold(color)
    if not folded:
        return None
    if folded in NAMED_HEX:
        return NAMED_HEX[folded]
    for name in sorted(NAMED_HEX, key=len, reverse=True):
        if folded.startswith(name):
            return NAMED_HEX[name]
    return None


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def hex_to_hsv(value: str) -> tuple[float, float, float]:
    """→ (hue degrees 0-360, saturation 0-1, value 0-1)."""
    r, g, b = hex_to_rgb(value)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return h * 360.0, s, v


def _rgb_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb)


def dominant_color_hex(jpeg: bytes, hint_hex: str | None = None) -> str | None:
    """Dominant garment colour of a product photo.

    Background = median of the border pixels (works for white and light-grey studio backdrops); pixels
    close to it are ignored, the rest is quantized (median cut, 5 colours) and the biggest cluster wins.
    A garment that is (almost) the backdrop colour — white tee on white — is detected by the small
    foreground and returned as the light pixels' mean.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as image:
            image = image.convert("RGB")
            image.thumbnail((96, 96))
            w, h = image.size
            raw = image.tobytes()
            px = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    except Exception:
        return None
    if w < 4 or h < 4:
        return _rgb_hex(tuple(sum(p[i] for p in px) / len(px) for i in range(3)))
    border = [px[x] for x in range(w)] + [px[(h - 1) * w + x] for x in range(w)]
    border += [px[y * w] for y in range(h)] + [px[y * w + w - 1] for y in range(h)]
    bg = tuple(sorted(p[i] for p in border)[len(border) // 2] for i in range(3))

    def dist(p: tuple[int, ...]) -> float:
        return sum((a - b) ** 2 for a, b in zip(p, bg)) ** 0.5

    x0, x1, y0, y1 = int(w * 0.15), int(w * 0.85), int(h * 0.1), int(h * 0.9)
    centre = [px[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]
    bg_sum = sum(bg)
    # foreground = clearly different from the backdrop, or lighter than a grey backdrop (white item)
    fg = [p for p in centre if dist(p) > 28 or (bg_sum < 735 and sum(p) > bg_sum + 24 and max(p) - min(p) < 40)]
    if len(fg) < 0.12 * len(centre):
        # Garment ≈ backdrop (white/very light item): mean of the pixels that differ at least a bit.
        subtle = [p for p in centre if dist(p) > 5] or centre
        light = [p for p in subtle if sum(p) >= sum(bg) - 30] or subtle
        return _rgb_hex(tuple(sum(p[i] for p in light) / len(light) for i in range(3)))
    from PIL import Image

    strip = Image.new("RGB", (len(fg), 1))
    strip.putdata(fg)
    quant = strip.quantize(colors=5, method=Image.Quantize.MEDIANCUT)
    palette = quant.getpalette() or []
    counts = sorted(quant.getcolors() or [], reverse=True)
    if not counts:
        return None
    clusters = [(n, tuple(palette[i * 3:i * 3 + 3])) for n, i in counts]
    rgb = clusters[0][1]
    if hint_hex:
        # The item's own colour name ("trắng") disagrees with the biggest cluster (black sole/insole of a
        # white shoe)? Take the sizeable cluster closest to the name instead.
        hint = hex_to_rgb(hint_hex)

        def gap(c: tuple[int, ...]) -> float:
            return sum((a - b) ** 2 for a, b in zip(c, hint)) ** 0.5

        if gap(rgb) > 140:
            sizeable = [c for n, c in clusters if n >= 0.15 * len(fg)]
            rgb = min(sizeable or [rgb], key=gap)
    return _rgb_hex(rgb)


def family_from_hex(value: str) -> str:
    hue, sat, val = hex_to_hsv(value)
    if val < 0.2 or sat < 0.12 or (val > 0.88 and sat < 0.16):
        return "neutral"
    if 195 <= hue <= 255 and val < 0.42:
        return "neutral"  # navy
    if 18 <= hue <= 55 and (sat < 0.55 or val < 0.6):
        return "earth"  # beige, khaki, tan, brown
    if 55 < hue <= 95 and sat < 0.6 and val < 0.65:
        return "earth"  # olive
    if hue < 50 or hue >= 330:
        return "warm"
    if hue < 70:
        return "warm"  # yellow
    return "cool"


def color_family(item: dict[str, Any], color_hex: str | None, fabric: str | None) -> str:
    colors = item.get("colors") or []
    main = _fold(colors[0]) if colors else ""
    hex_family = family_from_hex(color_hex) if color_hex else None
    if fabric == "denim" and (hex_family in ("cool", "neutral", None)) and not main.startswith(("den", "trang", "xam")):
        return "denim"
    if main:
        if any(main == n or main.startswith(n + " ") for n in _NEUTRAL_NAMES):
            return "neutral"
        if any(main == n or main.startswith(n + " ") for n in _EARTH_NAMES):
            return "earth"
    if hex_family:
        return hex_family
    named = name_hex(main)
    return family_from_hex(named) if named else "neutral"


# ---- rule-based derivation ------------------------------------------------------------------------


def item_text(item: dict[str, Any]) -> str:
    """Folded text the rules look at (never `notes`: they mention *other* garments)."""
    parts = [item.get(k) for k in ("name", "product_name", "product_type", "material", "style", "description_en")]
    return f" {re.sub(r'[^a-z0-9#.+-]+', ' ', _fold(' '.join(str(p) for p in parts if p)))} "


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(p, text) for p in patterns)


_LINEN = r"(\blinen\b|(?<!mat )\blanh\b|(?<!quan )\bdui\b)"
_SHORTS = r"\b(quan short|short|shorts|quan dui|quan sooc|quan ngan|bermuda)\b(?![ -]?sleeve)"
_SPORT = r"\b(the thao|mesh|gym|running|chay bo|sport|athletic|trainer|training)\b"


def _subtype(category: str, t: str) -> str | None:
    if category == "top":
        rules = [
            ("tank", r"\b(tank|ba lo|sat nach|khong tay|sleeveless|hai day)\b"),
            ("polo", r"\bpolo\b"),
            ("cuban_shirt", r"\b(cuban|camp collar|camp-collar|hawaii|hawaiian)\b"),
            ("oxford_shirt", r"\boxford\b"),
            ("linen_shirt", rf"\b(so mi|shirt)\b.*{_LINEN}|{_LINEN}.*\b(so mi|shirt)\b"),
            ("dress_shirt", r"\b(so mi|shirt)\b.*\b(cong so|ceo|dress|co tru)\b|\bdress shirt\b"),
            ("shirt", r"\bso mi\b|(?<!t-)(?<!t )\bshirt\b|\bbutton[- ]up\b"),
            ("tee", r"\b(ao thun|t-shirt|tshirt|tee|ao phong|crew-neck|crew neck)\b"),
            ("sweater", r"\b(ao len|sweater|knit|ao ni)\b"),
            ("blouse", r"\bblouse\b"),
        ]
    elif category == "bottom":
        rules = [
            ("sport_shorts", rf"(?=.*{_SHORTS})(?=.*{_SPORT})"),
            ("shorts", _SHORTS),
            ("skirt", r"\b(chan vay|skirt)\b"),
            ("joggers", r"\bjogger\b.*\bthe thao\b|\bthe thao\b.*\bjogger\b|\b(sweatpants|track pants)\b"),
            ("jeans", r"\b(jean|jeans|denim|quan bo)\b"),
            ("linen_pants", _LINEN),
            ("joggers", r"\bjogger"),
            ("trousers", r"\b(quan tay|quan au|tailored|dress pants|dress trousers|slacks)\b"),
            ("chinos", r"\b(kaki|khaki|chino|chinos)\b"),
            ("trousers", r"\btrousers\b"),
        ]
    elif category == "outerwear":
        rules = [
            ("uv_jacket", r"\b(chong nang|uv|upf|sun[- ]protection|anti-uv)\b"),
            ("blazer", r"\b(blazer|vest|suit jacket)\b"),
            ("windbreaker", r"\b(ao gio|khoac gio|khoac du|ao khoac du|windbreaker|ripstop|truot nuoc|rainshield|rain)\b"),
            ("denim_jacket", r"\b(jean|denim)\b"),
            ("puffer", r"\b(phao|puffer|down jacket)\b"),
            ("coat", r"\b(mang to|trench|overcoat|coat)\b"),
            ("hoodie", r"\bhoodie\b"),
            ("cardigan", r"\bcardigan\b"),
            ("overshirt", r"\b(so mi|overshirt|shirt jacket|shacket|shirt)\b"),
        ]
    elif category == "shoes":
        rules = [
            ("loafers", r"\b(loafer|loafers|moc|monk)\b"),
            ("slip_on", r"\bslip[- ]?on\b"),
            ("sneakers", r"\b(sneaker|sneakers|giay the thao|running shoe|trainers|giay chay)\b"),
            ("boots", r"\b(boot|boots|bot)\b"),
            ("heels", r"\b(cao got|heel|heels|pump)\b"),
            ("dress_shoes", r"\b(giay tay|giay da|oxford|derby|dress shoe)\b"),
            ("slides", r"\b(slide|slides|dep quai ngang|dep le|slipper|mules)\b"),
            ("sandals", r"\b(sandal|sandals|dep quai hau|dep|tong)\b"),
        ]
    elif category == "accessory":
        rules = [
            ("cap", r"\b(cap|mu luoi trai|non luoi trai|baseball|dad hat|trucker)\b"),
            ("hat", r"\b(hat|bucket|mu|non rong vanh)\b"),
            ("belt", r"\b(that lung|belt|day nit)\b"),
            ("bag", r"\b(tui|balo|bag|backpack|tote)\b"),
            ("watch", r"\b(dong ho|watch)\b"),
            ("glasses", r"\b(kinh|glasses|sunglasses)\b"),
            ("scarf", r"\b(khan|scarf)\b"),
            ("jewelry", r"\b(vong|nhan|day chuyen|trang suc|jewelry|necklace|bracelet)\b"),
        ]
    elif category == "dress":
        return "dress"
    else:
        return None
    for name, pattern in rules:
        if re.search(pattern, t):
            return name
    return None


_DEFAULT_SUBTYPE = {"top": "tee", "bottom": "pants", "outerwear": "jacket", "shoes": "sneakers",
                    "accessory": "other", "dress": "dress"}


def _fit(t: str) -> str | None:
    if _has(t, r"\b(f5|oversize|oversized|sieu rong|hop rong|boxy)\b"):
        return "oversized"
    if _has(t, r"\b(phom rong|f4|relaxed|loose|ong rong|baggy|wide[- ]leg|rong rai)\b"):
        return "relaxed"
    if _has(t, r"\b(om vua|phom om|f2|f1|slim|skinny|phom the thao)\b"):
        return "slim"
    if _has(t, r"\b(phom vua|f3|regular fit|regular)\b"):
        return "regular"
    return None


def _sleeve(t: str, subtype: str | None) -> str | None:
    if subtype == "tank" or _has(t, r"\b(khong tay|sat nach|sleeveless|ba lo|hai day)\b"):
        return "none"
    if _has(t, r"\b(tay ngan|coc tay|tay coc|short[- ]sleeve|short sleeves?)\b"):
        return "short"
    if _has(t, r"\b(tay dai|long[- ]sleeve|long sleeves?)\b"):
        return "long"
    return None


def _length(t: str, category: str, subtype: str | None) -> str | None:
    if subtype in ("shorts", "sport_shorts"):
        return "short"
    if category == "bottom" and subtype in ("jeans", "chinos", "trousers", "linen_pants", "joggers"):
        return "long"
    if _has(t, r"\b(mini|ngan|short)\b(?![ -]?sleeve)") and category in ("dress", "bottom"):
        return "short"
    if _has(t, r"\b(maxi|midi|dai|long)\b(?![ -]?sleeve)") and category in ("dress", "bottom"):
        return "long"
    return None


def _fabric(t: str) -> str | None:
    rules = [
        ("denim", r"\b(jean|jeans|denim)\b"),
        ("suede", r"\b(da lon|suede)\b"),
        ("leather", r"\b(da that|real leather|da bo|da trau|da y|leather|giay da|that lung da|da bong|patent)\b"),
        ("linen", _LINEN),
        ("nylon", r"\b(vai du|khoac du|ao khoac du|du nhan|short du|the thao du|nylon|ripstop|windbreaker|rainshield)\b"),
        ("wool", r"\b(ao len|vai len|wool|cashmere|merino)\b"),
        ("silk", r"\b(lua|silk|satin)\b"),
        ("rayon", r"\b(rayon|viscose|modal)\b"),
        ("canvas", r"\b(canvas|vai bo|giay vai)\b"),
        ("poly", r"\b(mesh|luoi|poly|polyester|spandex|airdry|icevibes|cool touch|coolmax|dri-fit|pindot|thun chong uv|anti-uv|nhung|velvet)\b"),
        ("cotton", r"\b(cotton|supima|kaki|khaki|chino|chinos|twill|oxford|pique|ca sau|jersey)\b"),
        ("rubber", r"\b(cao su|rubber|eva|tpr)\b"),
    ]
    for name, pattern in rules:
        if re.search(pattern, t):
            return name
    return None


_DEFAULT_FABRIC = {
    "tee": "cotton", "polo": "cotton", "shirt": "cotton", "oxford_shirt": "cotton", "dress_shirt": "cotton",
    "linen_shirt": "linen", "cuban_shirt": "poly", "tank": "cotton", "sweater": "wool", "blouse": "poly",
    "shorts": "cotton", "sport_shorts": "poly", "jeans": "denim", "chinos": "cotton", "trousers": "poly",
    "linen_pants": "linen", "joggers": "poly", "skirt": "poly", "pants": "cotton",
    "uv_jacket": "poly", "windbreaker": "nylon", "overshirt": "cotton", "blazer": "poly",
    "denim_jacket": "denim", "hoodie": "cotton", "cardigan": "wool", "coat": "wool", "puffer": "nylon",
    "jacket": "poly", "sneakers": "canvas", "slip_on": "canvas", "loafers": "leather", "dress_shoes": "leather",
    "sandals": "rubber", "slides": "rubber", "boots": "leather", "heels": "leather", "cap": "cotton",
    "hat": "cotton", "belt": "leather", "bag": "canvas", "dress": "poly",
}

_BASE_FORMALITY = {
    "tee": 2, "polo": 3, "shirt": 3, "oxford_shirt": 3, "dress_shirt": 4, "linen_shirt": 3, "cuban_shirt": 2,
    "tank": 1, "sweater": 3, "blouse": 3,
    "shorts": 2, "sport_shorts": 1, "jeans": 2, "chinos": 3, "trousers": 4, "linen_pants": 3, "joggers": 1,
    "skirt": 3, "pants": 3,
    "uv_jacket": 2, "windbreaker": 2, "overshirt": 3, "blazer": 5, "denim_jacket": 2, "hoodie": 1,
    "cardigan": 3, "coat": 4, "puffer": 2, "jacket": 3,
    "sneakers": 2, "slip_on": 2, "loafers": 4, "dress_shoes": 5, "sandals": 1, "slides": 1, "boots": 3,
    "heels": 4,
    "cap": 1, "hat": 2, "belt": 3, "bag": 2, "watch": 3, "glasses": 2, "scarf": 3, "jewelry": 3, "dress": 3,
    "other": 2,
}

_FABRIC_BREATH = {"linen": 5, "cotton": 4, "rayon": 4, "silk": 4, "poly": 3, "blend": 3, "canvas": 3,
                  "suede": 3, "rubber": 3, "nylon": 2, "denim": 2, "leather": 2, "wool": 1}


def _formality(subtype: str, t: str, item: dict[str, Any], sleeve: str | None, fabric: str | None,
               color_hex: str | None) -> int:
    value = _BASE_FORMALITY.get(subtype, 2)
    if subtype in ("shirt", "oxford_shirt") and sleeve == "long":
        value = 4
    if subtype == "jeans" and color_hex:
        _, _, val = hex_to_hsv(color_hex)
        value = 3 if val < 0.4 else 2  # dark wash is smarter
    if subtype == "belt" and fabric == "leather":
        value = 4
    style = _fold(item.get("style"))
    if subtype in ("tee", "pants", "jacket", "other"):  # generic: let the style hint move it
        if style in ("cong so", "du tiec"):
            value = max(value, 3)
        if style == "the thao":
            value = min(value, 1 if subtype == "pants" else 2)
    if _has(t, _SPORT) and subtype in ("tee", "pants", "shorts"):
        value = 1
    return max(1, min(5, value))


def _breathability(subtype: str, category: str, t: str, fabric: str, sleeve: str | None,
                   length: str | None) -> int:
    value = _FABRIC_BREATH.get(fabric, 3)
    if _has(t, r"\b(mesh|luoi|mat chim)\b"):
        value = 5
    if subtype in ("sandals", "slides"):
        value = 5
    if sleeve == "none" or length == "short":
        value += 1
    if category == "outerwear" and subtype not in ("uv_jacket",):
        value = max(2, value - 1)
    if _has(t, r"\b(day dan|dày|11[.]8oz|2[89]0gsm|3[01]0gsm|300gsm|heavy|thick)\b"):
        value -= 1
    if value <= 3 and _has(t, r"\b(thoang|thoat nhiet|giai nhiet|mat lanh|nhanh kho|breathable|quick[- ]dry)\b"):
        value += 1
    return max(1, min(5, value))


def _rain_ok(category: str, subtype: str, t: str, fabric: str) -> bool:
    if _has(t, r"\b(truot nuoc|chong nuoc|rainshield|water[- ]?repellent|waterproof|khang nuoc)\b"):
        return True
    if category == "outerwear":
        return subtype == "windbreaker" or fabric == "nylon"
    if category == "shoes":
        if subtype in ("sandals", "slides"):
            return False  # rain → closed shoes
        return fabric in ("leather", "rubber", "poly", "nylon")
    return fabric in ("poly", "nylon") and _has(t, r"\b(nhanh kho|quick[- ]dry|vai du|nylon)\b")


def _uv_protect(subtype: str, t: str) -> bool:
    return subtype in ("uv_jacket", "cap", "hat") or _has(t, r"\b(upf|anti-uv|chong uv|chong nang|uv protection)\b")


_DEFAULT_SLEEVE = {"tee": "short", "polo": "short", "cuban_shirt": "short", "linen_shirt": "short",
                   "tank": "none", "sweater": "long", "blazer": "long", "dress": "short"}


def _seasons(warmth: int, breath: int) -> list[str]:
    if warmth <= 2:
        return ["xuân", "hạ"] if breath < 5 else ["hạ"]
    if warmth == 3:
        return ["xuân", "thu"]
    return ["thu", "đông"]


_NOUN_EN = {
    "tee": "T-shirt", "polo": "polo shirt", "shirt": "button-up shirt", "oxford_shirt": "oxford shirt",
    "dress_shirt": "dress shirt", "linen_shirt": "linen shirt", "cuban_shirt": "Cuban-collar shirt",
    "tank": "tank top", "sweater": "sweater", "blouse": "blouse", "shorts": "shorts",
    "sport_shorts": "athletic shorts", "jeans": "jeans", "chinos": "chino trousers", "trousers": "tailored trousers",
    "linen_pants": "linen trousers", "joggers": "joggers", "skirt": "skirt", "pants": "trousers",
    "uv_jacket": "hooded sun-protection jacket", "windbreaker": "hooded windbreaker", "overshirt": "overshirt",
    "blazer": "blazer", "denim_jacket": "denim jacket", "hoodie": "hoodie", "cardigan": "cardigan",
    "coat": "coat", "puffer": "puffer jacket", "jacket": "jacket", "sneakers": "sneakers",
    "slip_on": "slip-on shoes", "loafers": "loafers", "dress_shoes": "leather dress shoes", "sandals": "sandals",
    "slides": "slide sandals", "boots": "boots", "heels": "heels", "cap": "baseball cap", "hat": "hat",
    "belt": "belt", "bag": "bag", "watch": "watch", "glasses": "glasses", "scarf": "scarf",
    "jewelry": "jewelry", "dress": "dress", "other": "accessory",
}


def description_en(item: dict[str, Any], attrs: dict[str, Any]) -> str:
    from stylist import color_en  # lazy: stylist imports wardrobe_store which imports us lazily

    colors = [color_en(c) for c in (item.get("colors") or [])[:2]]
    colors = [c for c in colors if c]
    words = [" and ".join(colors)] if colors else []
    if attrs.get("fit") in ("relaxed", "oversized", "slim"):
        words.append({"relaxed": "relaxed-fit", "oversized": "oversized", "slim": "slim-fit"}[attrs["fit"]])
    if attrs.get("sleeve") in ("short", "long") and item.get("category") in ("top", "dress"):
        words.append(f"{attrs['sleeve']}-sleeve")
    if attrs.get("fabric") in ("linen", "denim", "leather", "suede", "cotton") and attrs.get("subtype") not in (
            "jeans", "denim_jacket", "linen_shirt", "linen_pants"):
        words.append({"denim": "denim", "leather": "leather", "suede": "suede", "linen": "linen",
                      "cotton": "cotton"}[attrs["fabric"]])
    words.append(_NOUN_EN.get(attrs.get("subtype") or "other", "garment"))
    return " ".join(w for w in words if w)[:160]


def derive_attrs(
    item: dict[str, Any],
    jpeg: bytes | None = None,
    *,
    mode: str = "fill",
) -> dict[str, Any]:
    """Full attribute dict for `item` (does not mutate it).

    mode="fill": valid existing values win (user edits, hand-checked seeds, migrations).
    mode="rules_first": strong text rules beat existing values (right after AI tagging); colour comes from
    the photo when one is given.
    """
    category = item.get("category") if item.get("category") in SUBTYPES else "top"
    t = item_text(item)
    existing = normalize_attr_fields(item)

    def pick(field: str, strong: Any, default: Any) -> Any:
        have = existing.get(field) if field in existing else None
        if mode == "fill" and field in existing and (have is not None or field in ("sleeve", "length", "fit")):
            return have
        if strong is not None:
            return strong
        if have is not None:
            return have
        return default

    out: dict[str, Any] = {}
    subtype = pick("subtype", _subtype(category, t), _DEFAULT_SUBTYPE[category])
    if subtype not in SUBTYPES[category] and subtype != "other":
        subtype = _subtype(category, t) or _DEFAULT_SUBTYPE[category]
    out["subtype"] = subtype

    colors = item.get("colors") or []
    fallback_hex = name_hex(colors[0]) if colors else None
    image_hex = dominant_color_hex(jpeg, fallback_hex) if jpeg else None
    if mode == "rules_first":
        out["color_hex"] = image_hex or existing.get("color_hex") or fallback_hex or "#888888"
    else:
        out["color_hex"] = existing.get("color_hex") or image_hex or fallback_hex or "#888888"

    if category in ("top", "outerwear", "dress"):
        out["sleeve"] = pick("sleeve", _sleeve(t, subtype), _DEFAULT_SLEEVE.get(subtype, "long"))
    else:
        out["sleeve"] = None
    if category in ("bottom", "dress"):
        out["length"] = pick("length", _length(t, category, subtype), "long" if category == "bottom" else None)
    else:
        out["length"] = None
    if category in ("top", "bottom", "outerwear", "dress"):
        out["fit"] = pick("fit", _fit(t), "regular")
    else:
        out["fit"] = None
    fabric = pick("fabric", _fabric(t), _DEFAULT_FABRIC.get(subtype, "blend"))
    out["fabric"] = fabric
    out["color_family"] = pick("color_family", None, None) if mode == "fill" and "color_family" in existing \
        else color_family(item, out["color_hex"], fabric)
    out["formality"] = pick("formality",
                            _formality(subtype, t, item, out["sleeve"], fabric, out["color_hex"])
                            if _subtype(category, t) else None,
                            _formality(subtype, t, item, out["sleeve"], fabric, out["color_hex"]))
    out["breathability"] = pick("breathability",
                                _breathability(subtype, category, t, fabric, out["sleeve"], out["length"])
                                if _fabric(t) or subtype in ("sandals", "slides") else None,
                                _breathability(subtype, category, t, fabric, out["sleeve"], out["length"]))
    rain = _rain_ok(category, subtype, t, fabric)
    strong_rain = True if rain and _has(t, r"\b(truot nuoc|chong nuoc|rainshield|waterproof)\b") else None
    if subtype in ("sandals", "slides"):
        strong_rain = False  # rain → closed shoes
    out["rain_ok"] = pick("rain_ok", strong_rain, rain)
    uv = _uv_protect(subtype, t)
    out["uv_protect"] = pick("uv_protect", True if uv else None, uv)
    out["attrs_v"] = ATTRS_VERSION
    return out


def fill_attrs(item: dict[str, Any], jpeg: bytes | None = None, *, mode: str = "fill") -> dict[str, Any]:
    """Item + derived attributes; also fills empty `seasons` / `description_en`."""
    attrs = derive_attrs(item, jpeg, mode=mode)
    merged = {**item, **attrs}
    if not merged.get("seasons"):
        warmth = item.get("warmth") if isinstance(item.get("warmth"), int) else 2
        merged["seasons"] = _seasons(warmth, attrs["breathability"])
    if not str(merged.get("description_en") or "").strip():
        merged["description_en"] = description_en(merged, attrs)
    return merged


def needs_migration(item: dict[str, Any]) -> bool:
    return item.get("attrs_v") != ATTRS_VERSION


def attrs_of(item: dict[str, Any]) -> dict[str, Any]:
    """Attributes for scoring: the stored ones, or derived on the fly (no photo) for legacy/test items."""
    if not needs_migration(item) and all(k in item for k in ATTR_FIELDS):
        return {k: item.get(k) for k in ATTR_FIELDS}
    return {k: v for k, v in derive_attrs(item).items() if k in ATTR_FIELDS}


def public_attrs_vi(item: dict[str, Any]) -> dict[str, str]:
    """Human-readable Vietnamese labels of the attribute codes (for third-party UIs)."""
    out: dict[str, str] = {}
    for field in ("fit", "sleeve", "length", "fabric", "color_family"):
        value = item.get(field)
        if isinstance(value, str) and value in LABELS_VI[field]:
            out[field] = LABELS_VI[field][value]
    if isinstance(item.get("formality"), int):
        out["formality"] = LABELS_VI["formality"].get(str(item["formality"]), "")
    return out


# ---- AI tagging integration -------------------------------------------------------------------------

TAG_PROMPT_EXTRA = (
    '  "formality": số nguyên 1-5 (1=đồ thể thao/đi biển/dép lê, 2=thường ngày: áo thun, quần short, sneaker, '
    "3=lịch sự thoải mái: polo, sơ mi, quần kaki dài, 4=công sở: sơ mi trắng, quần tây, giày da, 5=trang trọng: "
    "blazer/vest),\n"
    '  "fit": "slim|regular|relaxed|oversized (null nếu là giày/phụ kiện)",\n'
    '  "sleeve": "short|long|none (chỉ áo/váy/áo khoác; món khác null)",\n'
    '  "length": "short|long (chỉ quần/chân váy/váy; món khác null)",\n'
    f'  "fabric": "một trong: {"|".join(FABRICS)}",\n'
    '  "breathability": số nguyên 1-5 (5=rất thoáng: linen, vải lưới; 1=bí: len, da),\n'
    '  "rain_ok": true/false (mặc/đi mưa được: vải dù trượt nước, giày kín không phải vải/da lộn)\n'
)


async def tag_item_with_attrs(provider: Any, jpeg: bytes, *, name: str | None = None,
                              category: str | None = None, extra_text: dict[str, Any] | None = None
                              ) -> tuple[dict[str, Any], str]:
    """stylist.tag_item + deterministic attributes (rules beat the model; colour from the photo).

    `extra_text` (e.g. shop product_name/product_type) is used for the rules but not stored here.
    """
    from stylist import tag_item

    fields, source = await tag_item(provider, jpeg, name=name, category=category)
    probe = {**fields, **{k: v for k, v in (extra_text or {}).items() if isinstance(v, str)}}
    enriched = fill_attrs(probe, jpeg, mode="rules_first")
    for key in (extra_text or {}):
        if key not in fields:
            enriched.pop(key, None)
    return enriched, source


# ---- backfill CLI -----------------------------------------------------------------------------------


def backfill_seed_dir(seed_dir: Path, *, verbose: bool = True) -> int:
    """Add/refresh attributes in <seed_dir>/items.json using the images in <seed_dir>/images/.

    Existing attribute values are kept (hand fixes), except color_hex which is recomputed from the photo
    when present. Returns the number of rows written.
    """
    path = seed_dir / "items.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        image = seed_dir / "images" / f"{row['id']}.jpg"
        jpeg = image.read_bytes() if image.is_file() else None
        base = dict(row)
        if jpeg:
            base.pop("color_hex", None)
            base.pop("color_family", None)
        filled = fill_attrs(base, jpeg)
        out.append(filled)
        if verbose:
            print(f"{row['id']:<36} {filled['subtype']:<13} f{filled['formality']} {str(filled['fit']):<9} "
                  f"{str(filled['sleeve']):<5} {str(filled['length']):<5} {filled['fabric']:<7} "
                  f"b{filled['breathability']} rain={int(filled['rain_ok'])} uv={int(filled['uv_protect'])} "
                  f"{filled['color_hex']} {filled['color_family']:<7} {row.get('colors')}")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(out)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "backfill":
        print("usage: garment_attrs.py backfill <seed_dir> [<seed_dir> ...]")
        return 2
    here = Path(__file__).resolve().parent
    for name in argv[1:]:
        seed_dir = (here / name).resolve()
        print(f"== {seed_dir.name}: {backfill_seed_dir(seed_dir)} món")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main(sys.argv[1:]))


def prompt_fields(item: dict[str, Any]) -> dict[str, Any]:
    """Compact attribute keys for the stylist LLM prompt (only those known)."""
    attrs = attrs_of(item)
    out: dict[str, Any] = {"formality": attrs.get("formality")}
    for key in ("sleeve", "length", "fabric"):
        if attrs.get(key):
            out[key] = attrs[key]
    out["breath"] = attrs.get("breathability")
    if attrs.get("rain_ok"):
        out["rain_ok"] = True
    if attrs.get("uv_protect"):
        out["uv"] = True
    return out

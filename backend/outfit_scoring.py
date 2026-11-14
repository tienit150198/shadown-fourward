"""Explainable outfit scoring for "Trợ lý tủ đồ" (new in SHADOW).

score_outfit(items, ctx) → {"score": 0-100, "ok": bool, "score_parts": {...}, "why": [...], "why_line": "...",
"violations": [...]} with five components (weights):

    occasion_fit   0.30  formality 1-5 of every piece vs the event's target range (EVENT_FORMALITY) + hard
                         rules: formal events → no shorts / slides / sandals / tank tops / sport pieces;
                         sport → no trousers, dress shoes, blazer, jeans
    weather_fit    0.30  feel-temperature vs warmth/breathability/sleeve/length; hot (≥29°C feel or ≥32°C max)
                         → no jacket unless it is a UV/sun layer (or a rain shell when it rains; a blazer at a
                         formal event is only penalised); rain → a rain_ok outer layer + closed, rain_ok shoes;
                         strong sun → UV layer / cap recommended; cold → no shorts, add a layer
    color_harmony  0.20  colour_hex in HSV: neutrals/earth/denim are the base; 0 accents = calm, 1 accent =
                         best, 2 accents must be analogous (≤45°) or complementary (150-210°), 3+ = too
                         loud; ≤3 colour families; denim-on-denim needs contrasting washes
    completeness   0.10  top+bottom or a dress (hard), shoes recommended
    variety        0.10  pieces not repeated from the previous suggestions

Any hard violation caps the score at 39 and makes ok=False. Used by stylist.suggest_outfits to (a) drop
and re-rank the LLM's outfits (`review_outfits`), (b) pick outfits deterministically (`pick_outfits`,
fallback + mock mode) and (c) annotate every returned outfit (`annotate_outfits`).

`why` entries are stable for third-party UIs: {"code", "ok", "score", "text_vi", "detail_vi"}; codes:
color_harmony | occasion_fit | weather_fit | completeness | variety. Violations: {"code", "text_vi",
"item_id"} with codes too_casual | too_formal | too_hot | too_cold | missing_piece.
"""

from __future__ import annotations

import itertools
from typing import Any, Iterable

from garment_attrs import attrs_of, hex_to_hsv, name_hex
from wardrobe_store import fold_vi, match_occasions
from weather import is_rainy

WEIGHTS = {"occasion_fit": 0.30, "weather_fit": 0.30, "color_harmony": 0.20, "completeness": 0.10,
           "variety": 0.10}
HARD_CAP = 39
OK_THRESHOLD = 0.7

# occasion → (min formality, max formality, kind). First match in this order wins ("đám cưới ở biển" = wedding).
EVENT_FORMALITY: list[tuple[str, int, int, str]] = [
    ("phỏng vấn", 4, 5, "formal"),
    ("đám cưới", 3, 5, "formal"),
    ("dự tiệc", 3, 5, "formal"),
    ("đi làm", 3, 4, "work"),
    ("lễ tết", 3, 4, "formal"),
    ("thể thao", 1, 2, "sport"),
    ("đi biển", 1, 2, "beach"),
    ("hẹn hò", 2, 4, "date"),
    ("đi học", 2, 3, "casual"),
    ("du lịch", 1, 3, "travel"),
    ("đi chơi", 1, 3, "casual"),
    ("ở nhà", 1, 2, "home"),
]
DEFAULT_EVENT = ("đi chơi", 1, 3, "casual")
CORE = ("top", "bottom", "dress", "outerwear", "shoes")
OUTDOOR_KINDS = ("beach", "travel", "casual", "sport")

TITLES = {
    "phỏng vấn": ("Chỉn chu phỏng vấn", "Gọn gàng tự tin", "Lịch sự tối giản"),
    "đám cưới": ("Lịch lãm dự cưới", "Thanh lịch nhẹ nhàng", "Trẻ trung lịch sự"),
    "dự tiệc": ("Lịch sự dự tiệc", "Thanh lịch buổi tối", "Nổi bật vừa đủ"),
    "đi làm": ("Công sở gọn gàng", "Công sở thoải mái", "Smart casual"),
    "lễ tết": ("Lịch sự ngày lễ", "Ấm áp chỉn chu", "Nhẹ nhàng ngày lễ"),
    "thể thao": ("Thể thao năng động", "Vận động thoáng mát", "Tập luyện gọn nhẹ"),
    "đi biển": ("Biển mát mẻ", "Dạo biển thoải mái", "Nắng biển năng động"),
    "hẹn hò": ("Hẹn hò lịch sự", "Hẹn hò trẻ trung", "Thử chút mới lạ"),
    "đi học": ("Đi học gọn gàng", "Năng động đến lớp", "Thoải mái cả ngày"),
    "du lịch": ("Du lịch thoải mái", "Khám phá năng động", "Gọn nhẹ đi xa"),
    "đi chơi": ("Dạo phố thoải mái", "Năng động cuối tuần", "Thử chút mới lạ"),
    "ở nhà": ("Ở nhà thoải mái", "Thư giãn", "Mặc nhà gọn gàng"),
}


# ---- context ------------------------------------------------------------------------------------------


def event_profile(event: str) -> dict[str, Any]:
    occasions = match_occasions(event or "")
    for occasion, lo, hi, kind in EVENT_FORMALITY:
        if occasion in occasions:
            return {"occasion": occasion, "lo": lo, "hi": hi, "kind": kind, "label": event.strip() or occasion}
    occasion, lo, hi, kind = DEFAULT_EVENT
    return {"occasion": occasion, "lo": lo, "hi": hi, "kind": kind, "label": (event or "").strip() or "đi chơi"}


def weather_profile(weather: dict[str, Any] | None) -> dict[str, Any]:
    available = bool(weather and weather.get("available"))
    t_min = weather.get("t_min") if available else None
    t_max = weather.get("t_max") if available else None
    nums = isinstance(t_min, (int, float)) and isinstance(t_max, (int, float))
    if nums:
        feel = t_min * 0.4 + t_max * 0.6
    elif isinstance(t_max, (int, float)):
        feel = float(t_max)
    else:
        feel, t_max = 27.0, None
    rain = available and is_rainy(weather)
    code = weather.get("code") if available else None
    sunny = bool(available and not rain and (code in (0, 1, 2) or code is None)
                 and isinstance(t_max, (int, float)) and t_max >= 30)
    hot = feel >= 29 or (isinstance(t_max, (int, float)) and t_max >= 32)
    heat = (weather or {}).get("conditions", {}).get("heat")
    cold, cool, warm = feel < 18, 18 <= feel < 22, 24 <= feel < 29 and not hot
    if heat:
        hot, cold, cool, warm = heat == "hot", heat == "cold", heat == "cool", heat == "mild"
    return {
        "available": available, "feel": feel, "t_min": t_min, "t_max": t_max, "rain": rain, "sunny": sunny,
        "hot": hot, "warm": warm, "cool": cool, "cold": cold,
        "chilly_morning": isinstance(t_min, (int, float)) and t_min < 20,
        "temp_text": f"{round(t_min)}–{round(t_max)}°C" if nums else ({"hot": "trời nóng", "cold": "trời lạnh", "cool": "trời mát", "mild": "thời tiết ôn hòa"}.get(heat, "")),
    }


def build_context(
    event: str = "",
    weather: dict[str, Any] | None = None,
    *,
    worn_categories: Iterable[str] = (),
    anchor_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event": event_profile(event),
        "weather": weather_profile(weather),
        "worn": set(worn_categories) - {"shoes", "accessory"},
        "anchor_id": anchor_id,
    }


# ---- per-piece helpers ----------------------------------------------------------------------------------


def _piece(item: dict[str, Any]) -> dict[str, Any]:
    attrs = attrs_of(item)
    hex_value = attrs.get("color_hex")
    if not hex_value:
        colors = item.get("colors") or []
        hex_value = name_hex(colors[0]) if colors else None
    hsv = hex_to_hsv(hex_value) if hex_value else (0.0, 0.0, 0.5)
    warmth = item.get("warmth")
    return {
        **attrs, "id": item.get("id"), "category": item.get("category") or "top",
        "name": str(item.get("name") or item.get("id") or "món đồ"), "hsv": hsv,
        "warmth": warmth if isinstance(warmth, int) and not isinstance(warmth, bool) else 2,
        "color_name": str((item.get("colors") or [""])[0] or ""),
    }


def _lower(name: str) -> str:
    return name[:1].lower() + name[1:] if name else name


def _hue_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ---- components -------------------------------------------------------------------------------------------


def _color(pieces: list[dict[str, Any]]) -> tuple[float, str, str]:
    """→ (score, short label, detail)."""
    core = [p for p in pieces if p["category"] in CORE]
    if not core:
        return 0.8, "Màu hợp", "Chưa đủ món để chấm màu."
    accents = [p for p in core if p.get("color_family") in ("warm", "cool")]
    groups: list[list[dict[str, Any]]] = []
    for p in accents:
        for group in groups:
            if _hue_diff(group[0]["hsv"][0], p["hsv"][0]) <= 25:
                group.append(p)
                break
        else:
            groups.append([p])
    families = {p.get("color_family") for p in core}
    if not groups:
        score, detail = 0.85, "Tông trung tính, dễ phối"
    elif len(groups) == 1:
        score, detail = 1.0, f"Nền trung tính + 1 điểm nhấn {groups[0][0]['color_name'] or 'màu'}"
    elif len(groups) == 2:
        d = _hue_diff(groups[0][0]["hsv"][0], groups[1][0]["hsv"][0])
        if d <= 45:
            score, detail = 0.85, "Hai màu tương đồng, hài hoà"
        elif 150 <= d <= 210:
            score, detail = 0.8, "Hai màu bổ túc, nổi bật có chủ ý"
        else:
            score, detail = 0.45, "Hai màu nổi khó đi cùng nhau"
    else:
        score, detail = 0.2, f"Quá nhiều màu nổi ({len(groups)} màu)"
    if len(families) > 3:
        score -= 0.25
        detail += "; hơn 3 nhóm màu"
    denim = [p for p in core if p.get("color_family") == "denim" and p["category"] in ("top", "bottom", "outerwear")]
    if len(denim) >= 2:
        if abs(denim[0]["hsv"][2] - denim[1]["hsv"][2]) < 0.15:
            score -= 0.3
            detail += "; jean phối jean cùng tông"
        else:
            detail += "; jean phối jean khác tông — ổn"
    elif denim and len(groups) <= 1:
        detail += "; jean làm nền"
    score = max(0.0, min(1.0, score))
    return score, ("Màu hợp" if score >= OK_THRESHOLD else "Màu hơi rối"), detail


def _occasion(pieces: list[dict[str, Any]], ctx: dict[str, Any]) -> tuple[float, str, list[dict[str, Any]]]:
    ev = ctx["event"]
    lo, hi, kind, label = ev["lo"], ev["hi"], ev["kind"], ev["occasion"]
    violations: list[dict[str, Any]] = []
    core = [p for p in pieces if p["category"] in CORE]
    devs = []
    for p in core:
        f = p.get("formality") or 2
        weight = 0.5 if p["category"] == "outerwear" else 1.0
        devs.append(((lo - f) if f < lo else (f - hi) if f > hi else 0) * weight)
    penalty = 0.0
    if devs:
        mean_f = sum(p.get("formality") or 2 for p in core) / len(core)
        penalty += 0.25 * (sum(devs) / len(devs)) + 0.15 * max(devs) + 0.04 * abs(mean_f - (lo + hi) / 2)
    formal = lo >= 3
    if kind == "formal":  # clothes under the dress code weigh more than shoes/outer at ceremonies
        penalty += 0.1 * sum(1 for p in core if p["category"] in ("top", "bottom", "dress")
                             and (p.get("formality") or 2) < lo)
    for p in core:
        st, name = p.get("subtype"), _lower(p["name"])
        if formal:
            if p["category"] == "bottom" and p.get("length") == "short":
                violations.append({"code": "too_casual", "item_id": p["id"], "text_vi": f"Quần short không hợp {label}"})
            elif st in ("slides", "sandals"):
                violations.append({"code": "too_casual", "item_id": p["id"], "text_vi": f"Dép không hợp {label}"})
            elif st in ("tank", "sport_shorts", "joggers", "hoodie") or (p.get("formality") or 2) <= 1:
                violations.append({"code": "too_casual", "item_id": p["id"], "text_vi": f"{name} quá xuề xoà cho {label}"})
        if kind == "sport" and (st in ("trousers", "dress_shoes", "loafers", "blazer", "dress_shirt", "jeans")
                                or (p["category"] == "bottom" and p.get("fabric") == "denim")):
            violations.append({"code": "too_formal", "item_id": p["id"], "text_vi": f"{name} không hợp vận động"})
        if kind == "sport" and st in ("slides", "sandals", "heels"):
            violations.append({"code": "too_casual", "item_id": p["id"], "text_vi": f"{name} không hợp vận động"})
        if kind == "sport" and p["category"] == "dress" or (kind == "sport" and st == "skirt"):
            violations.append({"code": "too_formal", "item_id": p["id"], "text_vi": f"{name} không hợp vận động"})
        if kind == "beach" and st in ("blazer", "dress_shoes", "loafers", "trousers"):
            penalty += 0.25
    for p in pieces:
        if p["category"] == "accessory" and formal and p.get("subtype") == "cap":
            penalty += 0.15
    score = max(0.0, min(1.0, 1.0 - penalty))
    if violations:
        score = min(score, 0.3)
    if score >= OK_THRESHOLD:
        detail = f"đúng độ {'lịch sự' if formal else 'thoải mái'} cho {label}"
    elif devs and sum(1 for p in core if (p.get('formality') or 2) > hi) > sum(1 for p in core if (p.get('formality') or 2) < lo):
        detail = f"hơi trang trọng so với {label}"
    else:
        detail = f"hơi xuề xoà so với {label}"
    return score, detail, violations


def _weather(pieces: list[dict[str, Any]], ctx: dict[str, Any]) -> tuple[float, str, str, list[dict[str, Any]], list[str]]:
    """→ (score, short label, detail, violations, tips)."""
    wp, ev = ctx["weather"], ctx["event"]
    if not wp["available"]:
        return 1.0, "Chưa có dự báo", "chưa đánh giá được độ phù hợp với thời tiết thực tế", [], []
    formal = ev["lo"] >= 3
    penalties: list[tuple[float, str, str]] = []  # (amount, short label, detail)
    violations: list[dict[str, Any]] = []
    tips: list[str] = []
    cats = {p["category"] for p in pieces}
    outers = [p for p in pieces if p["category"] == "outerwear"]
    shoes = [p for p in pieces if p["category"] == "shoes"]
    body = [p for p in pieces if p["category"] in ("top", "bottom", "dress")]
    t = wp["temp_text"] or ("trời mưa" if wp["rain"] else "điều kiện đã chọn")
    if wp["hot"]:
        for p in outers:
            if p.get("uv_protect") and not wp["rain"]:
                continue
            if wp["rain"] and p.get("rain_ok") and p["warmth"] <= 3:
                continue
            if formal and p.get("subtype") == "blazer":
                penalties.append((0.25, "Hơi nóng", "blazer chỉ nên khoác trong phòng máy lạnh"))
                continue
            violations.append({"code": "too_hot", "item_id": p["id"], "text_vi": f"{t} mà mặc {_lower(p['name'])}"})
        for p in body:
            if p["warmth"] >= 4:
                violations.append({"code": "too_hot", "item_id": p["id"], "text_vi": f"{_lower(p['name'])} quá ấm cho {t}"})
            elif p["warmth"] == 3:
                penalties.append((0.1, "Hơi nóng", f"{_lower(p['name'])} hơi dày"))
            if p["category"] == "top" and p.get("sleeve") == "long":
                penalties.append((0.04 if formal else 0.12, "Hơi nóng", "tay dài hơi nóng"))
            if p["category"] == "bottom" and p.get("length") == "long":
                fabric = p.get("fabric")
                amount = 0.15 if fabric == "denim" else (0.0 if formal else 0.03 if fabric == "linen" else 0.08)
                if amount:
                    penalties.append((amount, "Hơi nóng", "quần jean dày" if fabric == "denim" else "quần dài hơi nóng"))
        if body:
            breath = sum(p.get("breathability") or 3 for p in body) / len(body)
            if breath < 3:
                penalties.append((0.15, "Hơi bí", "vải chưa thoáng"))
    if not wp["hot"]:
        needs_layer = wp["rain"] or wp["cold"] or wp["cool"] or wp["chilly_morning"]
        for p in outers:
            if p["warmth"] >= 4 and not (wp["cold"] or wp["cool"]) and wp["feel"] >= 22:
                penalties.append((0.4 if wp["feel"] >= 25 else 0.2, "Hơi nóng", f"{_lower(p['name'])} quá ấm"))
            elif not needs_layer and not (p.get("uv_protect") and wp["sunny"]) and p.get("subtype") != "blazer":
                penalties.append((0.05, "Hợp thời tiết", "không cần áo khoác"))
    if wp["hot"]:
        pass
    elif wp["cold"] or wp["cool"]:
        for p in body:
            if p["category"] == "bottom" and p.get("length") == "short":
                penalties.append((0.35 if wp["cold"] else 0.15, "Hơi lạnh", "quần short hơi lạnh"))
        if "outerwear" not in cats:
            if wp["cold"]:
                penalties.append((0.3, "Hơi lạnh", "nên có áo khoác"))
            elif wp["chilly_morning"]:
                penalties.append((0.08, "Hơi lạnh", "sáng tối se lạnh"))
                tips.append("Sáng tối se lạnh — mang thêm áo khoác mỏng.")
        if wp["cold"] and body and sum(p["warmth"] for p in body) / len(body) < 2.5:
            penalties.append((0.2, "Hơi lạnh", "đồ hơi mỏng"))
    if wp["rain"]:
        if any(p.get("rain_ok") for p in outers):
            pass
        elif ev["kind"] == "formal":  # nobody wears a windbreaker to a wedding: an umbrella will do
            penalties.append((0.1, "Hợp thời tiết", "trời mưa — nhớ mang ô"))
            tips.append("Trời có thể mưa — mang ô, đi giày da kín mũi.")
        else:
            penalties.append((0.25, "Thiếu áo mưa", "trời mưa — nên có áo khoác gió/áo mưa"))
            tips.append("Trời có thể mưa — mang ô hoặc áo khoác gió.")
        for p in shoes:
            if p.get("subtype") in ("sandals", "slides"):
                penalties.append((0.3, "Giày dễ ướt", "mưa nên đi giày kín"))
            elif not p.get("rain_ok"):
                penalties.append((0.2, "Giày dễ ướt", f"{_lower(p['name'])} dễ ướt/bẩn khi mưa"))
    if wp["sunny"] and wp["hot"] and ev["kind"] in OUTDOOR_KINDS:
        if any(p.get("uv_protect") for p in pieces):
            pass
        else:
            penalties.append((0.05, "Nắng gắt", "nắng gắt — nên có mũ/áo chống nắng"))
            tips.append("Nắng gắt — mang mũ, áo chống nắng và kem chống nắng.")
    score = max(0.0, 1.0 - sum(a for a, _, _ in penalties))
    if violations:
        score = min(score, 0.3)
    if violations:
        label, detail = "Quá nóng", violations[0]["text_vi"]
    elif penalties and score < OK_THRESHOLD:
        worst = max(penalties, key=lambda row: row[0])
        label, detail = worst[1], "; ".join(dict.fromkeys(d for _, _, d in penalties))
    else:
        label = "Hợp thời tiết"
        if wp["available"]:
            extra = []
            if wp["rain"] and any(p.get("rain_ok") for p in outers):
                extra.append("có áo khoác chống mưa")
            if wp["hot"]:
                extra.append("vải thoáng" if body and min(p.get("breathability") or 3 for p in body) >= 4 else "đủ mát")
            if wp["sunny"] and any(p.get("uv_protect") for p in pieces):
                extra.append("có lớp chống nắng")
            detail = f"hợp {t}" + (f", {', '.join(extra)}" if extra else "")
        else:
            label, detail = "Chưa có dự báo", "chưa đánh giá được độ phù hợp với thời tiết thực tế"
        if penalties:
            detail += "; " + "; ".join(dict.fromkeys(d for _, _, d in penalties))
    return score, label, detail, violations, tips


def _completeness(pieces: list[dict[str, Any]], ctx: dict[str, Any]) -> tuple[float, str, str, list[dict[str, Any]]]:
    cats = {p["category"] for p in pieces} | ctx["worn"]
    if not ("dress" in cats or {"top", "bottom"} <= cats):
        missing = "áo" if "top" not in cats else "quần"
        return 0.0, "Thiếu món", f"Thiếu {missing}", [
            {"code": "missing_piece", "item_id": None, "text_vi": f"Bộ đồ thiếu {missing}"}]
    if "shoes" not in cats:
        return 0.6, "Thiếu giày", "Chưa có giày dép", []
    return 1.0, "Đủ bộ", "Đủ áo, quần và giày", []


def _variety(pieces: list[dict[str, Any]], previous: list[set[str]]) -> tuple[float, str, str]:
    if not previous:
        return 1.0, "Khác bộ trước", "Bộ đầu tiên"
    seen = set().union(*previous)
    core_shared = [p for p in pieces if p["id"] in seen and p["category"] in ("top", "bottom", "dress", "outerwear")]
    shoes_shared = [p for p in pieces if p["id"] in seen and p["category"] == "shoes"]
    score = max(0.0, 1.0 - 0.35 * len(core_shared) - 0.1 * len(shoes_shared))
    if not core_shared:
        return score, "Khác bộ trước", "Không lặp áo/quần với các bộ trước"
    return score, "Trùng món", f"Lặp lại {len(core_shared)} món với bộ trước"


# ---- public API ----------------------------------------------------------------------------------------------


def score_outfit(
    items: list[dict[str, Any]],
    ctx: dict[str, Any],
    *,
    previous: list[set[str]] | None = None,
) -> dict[str, Any]:
    pieces = [_piece(it) for it in items]
    previous = previous or []
    c_score, c_label, c_detail = _color(pieces)
    o_score, o_detail, o_viol = _occasion(pieces, ctx)
    w_score, w_label, w_detail, w_viol, tips = _weather(pieces, ctx)
    k_score, k_label, k_detail, k_viol = _completeness(pieces, ctx)
    v_score, v_label, v_detail = _variety(pieces, previous)
    anchor = ctx.get("anchor_id")
    violations = o_viol + w_viol + k_viol
    hard = [v for v in violations if not (anchor and v.get("item_id") == anchor)]
    parts = {"color_harmony": c_score, "occasion_fit": o_score, "weather_fit": w_score,
             "completeness": k_score, "variety": v_score}
    total = 100.0 * sum(WEIGHTS[k] * v for k, v in parts.items())
    if hard:
        total = min(total, HARD_CAP)
    o_label = "Đúng dịp" if o_score >= OK_THRESHOLD else "Chưa hợp dịp"
    why = [
        {"code": "color_harmony", "ok": c_score >= OK_THRESHOLD, "score": round(c_score, 2), "text_vi": c_label,
         "detail_vi": c_detail},
        {"code": "occasion_fit", "ok": o_score >= OK_THRESHOLD, "score": round(o_score, 2), "text_vi": o_label,
         "detail_vi": o_detail[:1].upper() + o_detail[1:]},
        {"code": "weather_fit", "ok": w_score >= OK_THRESHOLD, "score": round(w_score, 2), "text_vi": w_label,
         "detail_vi": w_detail[:1].upper() + w_detail[1:]},
        {"code": "completeness", "ok": k_score >= OK_THRESHOLD, "score": round(k_score, 2), "text_vi": k_label,
         "detail_vi": k_detail},
        {"code": "variety", "ok": v_score >= OK_THRESHOLD, "score": round(v_score, 2), "text_vi": v_label,
         "detail_vi": v_detail},
    ]
    shown = [w for w in why if w["code"] in ("color_harmony", "occasion_fit", "weather_fit")]
    shown += [w for w in why if w["code"] in ("completeness", "variety") and not w["ok"]]
    why_line = " · ".join(f"{w['text_vi']} {'✓' if w['ok'] else '⚠'}" for w in shown)
    return {
        "score": int(round(total)),
        "ok": not hard,
        "score_parts": {k: round(v, 2) for k, v in parts.items()},
        "why": why,
        "why_line": why_line,
        "violations": violations,
        "tips": tips,
    }


def _items_of(outfit: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [by_id[i] for i in outfit.get("item_ids") or [] if i in by_id]


def annotate_outfits(
    outfits: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add score/score_parts/why/why_line/score_ok (+violations) to each outfit, in place (variety vs earlier)."""
    by_id = {it["id"]: it for it in items}
    previous: list[set[str]] = []
    for outfit in outfits:
        result = score_outfit(_items_of(outfit, by_id), ctx, previous=previous)
        outfit["score"] = result["score"]
        outfit["score_ok"] = result["ok"]
        outfit["score_parts"] = result["score_parts"]
        outfit["why"] = result["why"]
        outfit["why_line"] = result["why_line"]
        outfit["violations"] = result["violations"]
        previous.append(set(outfit.get("item_ids") or []))
    return outfits


def rank_outfits(
    outfits: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> list[dict[str, Any]]:
    """Move clearly weaker outfits (≥12 points below the best) after the others, so style_memory.finalize's
    "earlier = better" prior starts from the scoring order; otherwise the model's order is kept."""
    by_id = {it["id"]: it for it in items}
    scored = [(score_outfit(_items_of(outfit, by_id), ctx), index, outfit) for index, outfit in enumerate(outfits)]
    if not scored:
        return outfits
    best = max(r["score"] for r, _, _ in scored)
    scored.sort(key=lambda row: (not row[0]["ok"], row[0]["score"] < best - 12,
                                 -row[0]["score"] if row[0]["score"] < best - 12 else 0, row[1]))
    return [row[2] for row in scored]


def review_outfits(
    outfits: list[dict[str, Any]],
    items: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate the LLM's outfits: drop hard violations (with a Vietnamese reason), move weak ones last.

    → (kept outfits in order, dropped [{"title", "item_ids", "reason_vi", "violations"}]). Violations caused
    by the anchor item ("Giữ món này") never drop an outfit.
    """
    by_id = {it["id"]: it for it in items}
    kept: list[tuple[int, int, dict[str, Any]]] = []
    dropped: list[dict[str, Any]] = []
    previous: list[set[str]] = []
    for index, outfit in enumerate(outfits):
        result = score_outfit(_items_of(outfit, by_id), ctx, previous=previous)
        if not result["ok"]:
            reasons = "; ".join(dict.fromkeys(v["text_vi"] for v in result["violations"]))
            dropped.append({"title": outfit.get("title"), "item_ids": list(outfit.get("item_ids") or []),
                            "reason_vi": f"Bỏ bộ “{outfit.get('title') or 'Bộ phối'}”: {reasons}.",
                            "violations": result["violations"]})
            continue
        previous.append(set(outfit.get("item_ids") or []))
        kept.append((0 if result["score"] >= 55 else 1, index, outfit))
    kept.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in kept], dropped


# ---- deterministic picker -----------------------------------------------------------------------------------


def _item_prior(item: dict[str, Any], ctx: dict[str, Any]) -> float:
    """Cheap single-piece fit used to prune the search (higher = better)."""
    p = _piece(item)
    ev, wp = ctx["event"], ctx["weather"]
    f = p.get("formality") or 2
    score = -abs(f - (ev["lo"] + ev["hi"]) / 2) * 0.6 - (max(0, ev["lo"] - f) + max(0, f - ev["hi"])) * 1.2
    if p["category"] in ("top", "bottom", "dress"):
        if wp["hot"]:
            score -= max(0, p["warmth"] - 2) * 0.8 - (p.get("breathability") or 3) * 0.15
        elif wp["cold"]:
            score -= max(0, 3 - p["warmth"]) * 0.6
    occasions = set(item.get("occasions") or [])
    if ev["occasion"] in occasions:
        score += 0.6
    return score


def _top_k(items: list[dict[str, Any]], ctx: dict[str, Any], k: int, keep: dict[str, Any] | None) -> list[dict[str, Any]]:
    ranked = sorted(items, key=lambda it: (-_item_prior(it, ctx), it["id"]))[:k]
    if keep is not None and keep in items and keep not in ranked:
        ranked = ranked[:-1] + [keep] if ranked else [keep]
    return ranked


def _template_reason(items: list[dict[str, Any]], result: dict[str, Any]) -> str:
    names = ", ".join(_lower(str(it.get("name") or it["id"])) for it in items[:4])
    why = {w["code"]: w for w in result["why"]}
    parts = [why["occasion_fit"]["detail_vi"], why["color_harmony"]["detail_vi"], why["weather_fit"]["detail_vi"]]
    text = f"{names[:1].upper() + names[1:]}: " + "; ".join(_lower(p) for p in parts if p)
    return text[:400].rstrip(" ;") + "."


def _template_tips(items: list[dict[str, Any]], result: dict[str, Any], ctx: dict[str, Any]) -> str:
    tips = list(dict.fromkeys(result.get("tips") or []))
    cats = {it.get("category") for it in items}
    if "shoes" not in cats:
        tips.append("Tủ đồ chưa có giày hợp — nên bổ sung một đôi.")
    if not tips:
        if ctx["event"]["lo"] >= 3:
            tips.append("Sơ vin gọn gàng, thêm thắt lưng da và đồng hồ cho chỉn chu.")
        else:
            tips.append("Thêm một phụ kiện nhỏ (mũ, đồng hồ) để bộ đồ có điểm nhấn.")
    return " ".join(tips[:2])


def _accessories(outfit: list[dict[str, Any]], accessories: list[dict[str, Any]], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    ev, wp = ctx["event"], ctx["weather"]
    extras = []
    bottom = next((p for p in outfit if p.get("category") == "bottom"), None)
    pieces = {it["id"]: _piece(it) for it in accessories}
    if ev["lo"] >= 3 and bottom is not None and attrs_of(bottom).get("length") == "long":
        belts = [it for it in accessories if pieces[it["id"]].get("subtype") == "belt"]
        if belts:
            extras.append(belts[0])
    if ev["lo"] < 3 and (wp["sunny"] or ev["kind"] in ("beach", "sport", "travel")):
        caps = [it for it in accessories if pieces[it["id"]].get("subtype") in ("cap", "hat")]
        if caps:
            extras.append(caps[0])
    return extras[:2]


def _strong(result: dict[str, Any]) -> bool:
    parts = result["score_parts"]
    return result["ok"] and all(parts[k] >= OK_THRESHOLD for k in ("occasion_fit", "weather_fit", "color_harmony"))


def pick_outfits(
    items: list[dict[str, Any]],
    *,
    weather: dict[str, Any] | None,
    event: str = "",
    anchor_id: str | None = None,
    worn_categories: set[str] | None = None,
    max_outfits: int = 3,
) -> list[dict[str, Any]]:
    """Search top/bottom(/dress) × outerwear × shoes combinations with score_outfit, then pick the top-k
    diverse ones. Same output shape as stylist.rule_based_outfits (title, item_ids, reason, tips)."""
    ctx = build_context(event, weather, worn_categories=worn_categories or (), anchor_id=anchor_id)
    worn = ctx["worn"]
    by_id = {it["id"]: it for it in items}
    anchor = by_id.get(anchor_id) if anchor_id else None
    grouped: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        grouped.setdefault(it.get("category") or "top", []).append(it)
    limits = {"top": 7, "bottom": 6, "dress": 4, "outerwear": 3, "shoes": 3}
    pool = {cat: _top_k(grouped.get(cat, []), ctx, n, anchor) for cat, n in limits.items()}

    bases: list[list[dict[str, Any]]] = []
    if "dress" in worn:
        bases.append([])
    else:
        tops = pool["top"] if "top" not in worn else [None]
        bottoms = pool["bottom"] if "bottom" not in worn else [None]
        for top, bottom in itertools.product(tops, bottoms):
            base = [x for x in (top, bottom) if x is not None]
            if base or worn:
                bases.append(base)
        if not ({"top", "bottom"} & worn):
            bases.extend([[dress] for dress in pool["dress"]])
    if anchor and anchor.get("category") in ("top", "bottom", "dress"):
        bases = [b for b in bases if anchor in b]
    outer_opts: list[dict[str, Any] | None] = [None] + ([] if "outerwear" in worn else pool["outerwear"])
    shoe_opts: list[dict[str, Any] | None] = pool["shoes"] or [None]
    if anchor and anchor.get("category") == "outerwear":
        outer_opts = [anchor]
    if anchor and anchor.get("category") == "shoes":
        shoe_opts = [anchor]

    candidates: list[tuple[float, bool, list[dict[str, Any]], dict[str, Any]]] = []
    for base in bases:
        for outer, shoes in itertools.product(outer_opts, shoe_opts):
            combo = base + [x for x in (outer, shoes) if x is not None]
            if not combo:
                continue
            result = score_outfit(combo, ctx)
            if result["score_parts"]["completeness"] == 0.0:
                continue
            candidates.append((result["score"] - 0.01 * len(combo), result["ok"], combo, result))
    if not candidates:
        return []
    candidates.sort(key=lambda row: (not row[1], -row[0], [it["id"] for it in row[2]]))
    if any(row[1] for row in candidates):
        candidates = [row for row in candidates if row[1]] or candidates

    chosen: list[list[dict[str, Any]]] = []
    seen_core: set[frozenset[str]] = set()
    used: dict[str, int] = {}
    while candidates and len(chosen) < max_outfits:
        def adjusted(row):
            combo = row[2]
            penalty = sum((12 if it.get("category") in ("top", "bottom", "dress") else 5
                           if it.get("category") == "outerwear" else 2) * used.get(it["id"], 0) for it in combo)
            return row[0] - penalty + (30 if _strong(row[3]) else 0)  # all-green outfits first, then variety

        best = max(candidates, key=lambda row: (adjusted(row), -len(row[2]), tuple(it["id"] for it in row[2])))
        candidates.remove(best)
        if len(chosen) >= 2 and not _strong(best[3]) and max_outfits <= 3:
            # 2 good outfits beat a third that misses the dress code / weather / colours. With more slots
            # (candidates for style_memory re-ranking) weaker-but-valid ones are still returned, last.
            break
        combo = best[2]
        core = frozenset(it["id"] for it in combo if it.get("category") in ("top", "bottom", "dress"))
        if core in seen_core and len(candidates) >= max_outfits - len(chosen):
            continue
        seen_core.add(core)
        for it in combo:
            used[it["id"]] = used.get(it["id"], 0) + 1
        chosen.append(combo)

    titles = TITLES.get(ctx["event"]["occasion"], TITLES["đi chơi"])
    accessories = [it for it in grouped.get("accessory", [])]
    results = []
    previous: list[set[str]] = []
    for index, combo in enumerate(chosen):
        outfit_items = combo + [a for a in _accessories(combo, accessories, ctx) if a not in combo]
        result = score_outfit(outfit_items, ctx, previous=previous)
        previous.append({it["id"] for it in outfit_items})
        results.append({
            "title": titles[min(index, len(titles) - 1)],
            "item_ids": [it["id"] for it in outfit_items],
            "reason": _template_reason(outfit_items, result),
            "tips": _template_tips(outfit_items, result, ctx),
        })
    return results

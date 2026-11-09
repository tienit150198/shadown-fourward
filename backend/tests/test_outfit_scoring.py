"""outfit_scoring: concrete cases (wedding, 34°C, rain, sport) + LLM review + picker + API fields."""

from __future__ import annotations

import asyncio

from wardrobe_testlib import FakeProvider, item, sample_wardrobe, wardrobe_dir, wclient, weather  # noqa: F401

from outfit_scoring import build_context, pick_outfits, review_outfits, score_outfit


def men_summer() -> list[dict]:
    """Small men's summer wardrobe with explicit attributes (like the YaMe seed)."""
    return [
        item("tee", "top", warmth=1, colors=["trắng"], name="Áo thun trắng", subtype="tee", formality=2,
             sleeve="short", fabric="cotton", breathability=4),
        item("polo", "top", warmth=1, colors=["xanh dương"], name="Áo polo xanh dương", subtype="polo", formality=3,
             sleeve="short", fabric="cotton", breathability=4),
        item("dshirt", "top", warmth=2, colors=["trắng"], name="Sơ mi trắng dài tay", subtype="dress_shirt",
             formality=4, sleeve="long", fabric="cotton", breathability=4, style="công sở"),
        item("shorts", "bottom", warmth=1, colors=["be"], name="Quần short kaki be", subtype="shorts", formality=2,
             length="short", fabric="cotton", breathability=5),
        item("sport", "bottom", warmth=1, colors=["đen"], name="Quần short thể thao đen", subtype="sport_shorts",
             formality=1, length="short", fabric="poly", breathability=5, style="thể thao"),
        item("trousers", "bottom", warmth=2, colors=["đen"], name="Quần tây đen", subtype="trousers", formality=4,
             length="long", fabric="poly", breathability=3, style="công sở"),
        item("jacket", "outerwear", warmth=3, colors=["xanh rêu"], name="Áo khoác kaki", subtype="jacket",
             formality=3, sleeve="long", fabric="cotton", breathability=2),
        item("uv", "outerwear", warmth=2, colors=["be"], name="Áo khoác chống nắng be", subtype="uv_jacket",
             formality=2, sleeve="long", fabric="poly", breathability=3, uv_protect=True),
        item("wind", "outerwear", warmth=2, colors=["xám đậm"], name="Áo khoác gió xám", subtype="windbreaker",
             formality=2, sleeve="long", fabric="nylon", breathability=2, rain_ok=True),
        item("slides", "shoes", colors=["nâu"], name="Dép quai ngang nâu", subtype="slides", formality=1,
             fabric="suede", rain_ok=False),
        item("sneakers", "shoes", colors=["trắng"], name="Sneaker trắng", subtype="sneakers", formality=2,
             fabric="leather", rain_ok=True),
        item("loafers", "shoes", colors=["đen"], name="Giày loafer da đen", subtype="loafers", formality=4,
             fabric="leather", rain_ok=True),
    ]


def by_id():
    return {it["id"]: it for it in men_summer()}


def score(ids, event, w, **kw):
    items = by_id()
    return score_outfit([items[i] for i in ids], build_context(event, w, **kw))


def test_wedding_rejects_shorts_and_sandals():
    bad = score(["tee", "shorts", "slides"], "Đám cưới", weather(26, 32))
    assert not bad["ok"] and bad["score"] <= 39
    texts = " ".join(v["text_vi"] for v in bad["violations"])
    assert "Quần short" in texts and "Dép" in texts
    good = score(["dshirt", "trousers", "loafers"], "Đám cưới", weather(26, 32))
    assert good["ok"] and good["score"] >= 75
    assert {w["code"]: w["ok"] for w in good["why"]}["occasion_fit"] is True


def test_hot_day_no_jacket_unless_uv():
    hot = weather(28, 34, rain=5, code=1)
    jacket = score(["tee", "shorts", "jacket", "sneakers"], "đi chơi", hot)
    assert not jacket["ok"] and any(v["code"] == "too_hot" for v in jacket["violations"])
    uv = score(["tee", "shorts", "uv", "sneakers"], "đi chơi", hot)
    assert uv["ok"] and {w["code"]: w for w in uv["why"]}["weather_fit"]["ok"]
    plain = score(["tee", "shorts", "sneakers"], "đi chơi", hot)
    assert plain["ok"]


def test_rain_wants_windbreaker_and_closed_shoes():
    rain = weather(25, 29, rain=80, code=63)
    best = score(["tee", "shorts", "wind", "sneakers"], "đi chơi", rain)
    wet = score(["tee", "shorts", "slides"], "đi chơi", rain)
    assert best["score_parts"]["weather_fit"] > wet["score_parts"]["weather_fit"]
    assert best["score"] > wet["score"]
    weather_why = {w["code"]: w for w in wet["why"]}["weather_fit"]
    assert not weather_why["ok"] and ("giày kín" in weather_why["detail_vi"] or "áo khoác gió" in weather_why["detail_vi"])
    picked = pick_outfits(men_summer(), weather=rain, event="đi chơi")
    assert "wind" in picked[0]["item_ids"] and "slides" not in picked[0]["item_ids"]


def test_sport_rejects_trousers_and_loafers():
    bad = score(["polo", "trousers", "loafers"], "Thể thao", None)
    assert not bad["ok"]
    good = score(["tee", "sport", "sneakers"], "Thể thao", None)
    assert good["ok"] and good["score"] >= 75


def test_color_harmony_rules():
    items = [
        item("red", "top", colors=["đỏ"], color_hex="#c62828", color_family="warm"),
        item("green", "bottom", colors=["xanh lá"], color_hex="#3f9654", color_family="cool"),
        item("purple", "shoes", colors=["tím"], color_hex="#7d4a9e", color_family="cool"),
        item("navy", "bottom", colors=["xanh navy"], color_hex="#1f2a48", color_family="neutral"),
        item("jean1", "bottom", colors=["xanh dương"], color_hex="#4a6a96", color_family="denim", fabric="denim"),
        item("jean2", "outerwear", colors=["xanh dương"], color_hex="#4d6c99", color_family="denim", fabric="denim"),
    ]
    by = {it["id"]: it for it in items}
    ctx = build_context("đi chơi", None)
    loud = score_outfit([by["red"], by["green"], by["purple"]], ctx)
    calm = score_outfit([by["red"], by["navy"]], ctx)
    assert calm["score_parts"]["color_harmony"] == 1.0  # neutrals + one accent
    assert loud["score_parts"]["color_harmony"] < 0.5
    denim = score_outfit([by["red"], by["jean1"], by["jean2"]], ctx)
    assert denim["score_parts"]["color_harmony"] < calm["score_parts"]["color_harmony"]
    assert "jean phối jean" in {w["code"]: w for w in denim["why"]}["color_harmony"]["detail_vi"]


def test_why_is_stable_for_third_party_ui():
    result = score(["dshirt", "trousers", "loafers"], "đi làm", weather(24, 30))
    assert [w["code"] for w in result["why"]] == ["color_harmony", "occasion_fit", "weather_fit", "completeness", "variety"]
    for entry in result["why"]:
        assert set(entry) == {"code", "ok", "score", "text_vi", "detail_vi"} and entry["text_vi"]
    assert result["why_line"] == "Màu hợp ✓ · Đúng dịp ✓ · Hợp thời tiết ✓"
    no_shoes = score(["dshirt", "trousers"], "đi làm", weather(24, 30))
    assert "Thiếu giày ⚠" in no_shoes["why_line"]


def test_review_drops_bad_llm_outfits_with_reason_and_anchor_exception():
    items = men_summer()
    outfits = [
        {"title": "Biển", "item_ids": ["tee", "shorts", "slides"]},
        {"title": "Lịch sự", "item_ids": ["dshirt", "trousers", "loafers"]},
    ]
    ctx = build_context("đám cưới", weather(26, 32))
    kept, dropped = review_outfits(outfits, items, ctx)
    assert [o["title"] for o in kept] == ["Lịch sự"]
    assert dropped[0]["item_ids"] == ["tee", "shorts", "slides"] and "Bỏ bộ “Biển”" in dropped[0]["reason_vi"]
    # the user insists on keeping the shorts: no drop because of the anchor item itself
    anchored = build_context("đám cưới", weather(26, 32), anchor_id="shorts")
    kept, dropped = review_outfits([{"title": "A", "item_ids": ["dshirt", "shorts", "loafers"]}], items, anchored)
    assert len(kept) == 1 and not dropped


def test_picker_is_diverse_and_respects_event():
    wedding = pick_outfits(men_summer(), weather=weather(26, 32), event="Đám cưới")
    assert len(wedding) >= 2
    for outfit in wedding:
        assert not {"shorts", "sport", "slides", "tee"} & set(outfit["item_ids"])
        assert outfit["reason"] and outfit["tips"] and outfit["title"]
    beach = pick_outfits(men_summer(), weather=weather(27, 33, rain=5, code=0), event="Đi biển (Vũng Tàu)")
    assert "loafers" not in beach[0]["item_ids"] and "trousers" not in beach[0]["item_ids"]
    cores = [frozenset(i for i in o["item_ids"] if i in ("tee", "polo", "dshirt", "shorts", "sport", "trousers"))
             for o in beach]
    assert len(set(cores)) == len(cores)


def test_suggest_drops_and_refills_to_three():
    from stylist import suggest_outfits

    provider = FakeProvider({"outfits": [
        {"title": "Sai dịp", "item_ids": ["tee", "shorts", "slides"], "reason": "r"},
        {"title": "Đúng", "item_ids": ["dshirt", "trousers", "loafers"], "reason": "r"},
    ]})
    result = asyncio.run(suggest_outfits(provider, men_summer(), weather=weather(26, 32), event="Đám cưới"))
    ids = [o["item_ids"] for o in result["outfits"]]
    assert 2 <= len(ids) <= 3 and ["tee", "shorts", "slides"] not in ids
    assert result["dropped"] and result["dropped"][0]["title"] == "Sai dịp"
    for outfit in result["outfits"]:
        assert isinstance(outfit["score"], int) and outfit["why_line"] and outfit["why"]


def test_suggest_route_returns_scores(wclient):
    wclient.post("/api/wardrobe/seed-demo")
    body = wclient.post("/api/stylist/suggest", json={"event": "đi làm", "city": "Hà Nội"}).json()
    assert body["dropped_outfits"] == []
    for outfit in body["outfits"]:
        assert 0 <= outfit["score"] <= 100 and outfit["why_line"].count("·") >= 2
        assert set(outfit["score_parts"]) == {"color_harmony", "occasion_fit", "weather_fit", "completeness", "variety"}
        assert all(it["color_hex"].startswith("#") for it in outfit["items"])


def test_legacy_items_without_attributes_still_score():
    result = score_outfit(sample_wardrobe()[:2] + [sample_wardrobe()[4]], build_context("đi làm", weather(20, 26)))
    assert 0 <= result["score"] <= 100

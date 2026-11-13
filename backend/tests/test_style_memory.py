"""Teachable + learning stylist (style_memory.py): rule parsing → enforcement, feedback, examples, credits."""

from __future__ import annotations

from wardrobe_testlib import seed_test_wardrobe

import asyncio
import time

import pytest
from wardrobe_testlib import FakeProvider, item, wardrobe_dir, wclient, weather  # noqa: F401

import style_memory as sm
from providers import MockProvider
from stylist import suggest_outfits


def run(coro):
    return asyncio.run(coro)


def closet() -> list[dict]:
    """Small men's summer wardrobe (like the shop seed) with shorts, sandals, shirts and a windbreaker."""
    return [
        item("tee-w", "top", warmth=1, colors=["trắng"], occasions=["đi chơi", "đám cưới"], name="Áo thun trắng"),
        item("tee-b", "top", warmth=1, colors=["đen"], occasions=["đi chơi"], name="Áo thun đen"),
        item("shirt-w", "top", warmth=1, colors=["trắng"], occasions=["đi làm", "đám cưới"], name="Sơ mi trắng công sở", style="công sở"),
        item("shirt-n", "top", warmth=2, colors=["xanh navy"], occasions=["đi làm", "đám cưới"], name="Sơ mi navy", style="thanh lịch"),
        item("short", "bottom", warmth=1, colors=["be"], occasions=["đi chơi", "đám cưới"], name="Quần short kaki be"),
        item("trouser", "bottom", warmth=2, colors=["đen"], occasions=["đi làm", "đám cưới"], name="Quần tây đen", style="công sở"),
        item("jean", "bottom", warmth=3, colors=["xanh dương nhạt"], occasions=["đi chơi", "đi làm"], name="Quần jean xanh nhạt"),
        item("wind", "outerwear", warmth=2, colors=["xám đậm"], occasions=["đi làm", "đi chơi"], name="Áo khoác gió xám"),
        item("sandal", "shoes", warmth=1, colors=["nâu"], occasions=["đi chơi", "đám cưới"], name="Dép quai ngang nâu"),
        item("loafer", "shoes", warmth=2, colors=["đen"], occasions=["đi làm", "đám cưới"], name="Giày loafer da đen", style="công sở"),
    ]


def rule(rid, text, who="Chị Lan"):
    return {"id": rid, "text": text, "taught_by": who, "constraint": sm.pattern_parse(text)}


HOT_RAIN = weather(27, 31, rain=80, code=61)
MILD = weather(22, 28)


# ---- parsing ------------------------------------------------------------------------------------


def test_pattern_parser_common_phrasings():
    c = sm.pattern_parse("Đi đám cưới không mặc quần short và dép")
    assert c["when"] == {"events": ["đám cưới"], "weather": {}}
    assert c["avoid"] == [{"kinds": ["quần short"]}, {"kinds": ["dép"]}] and c["strength"] == "must"
    c = sm.pattern_parse("Trời mưa thì nên mặc áo khoác gió")
    assert c["when"]["weather"] == {"rain": True} and c["prefer"] == [{"kinds": ["áo khoác gió"]}]
    assert c["strength"] == "should"
    c = sm.pattern_parse("Đi làm ưu tiên sơ mi")
    assert c["when"]["events"] == ["đi làm"] and c["prefer"] == [{"kinds": ["sơ mi"]}]
    c = sm.pattern_parse("Trời dưới 20 độ thì luôn thêm một áo khoác mỏng.")
    assert c["when"]["weather"] == {"t_min_lte": 20.0} and c["strength"] == "must"
    assert c["require"] == [{"kinds": ["áo khoác"], "attributes": {"warmth_lte": 2}}]
    c = sm.pattern_parse("Không mặc áo trắng khi trời nóng")
    assert c["avoid"] == [{"colors": ["trắng"], "categories": ["top"]}] and c["when"]["weather"] == {"t_max_gte": 30.0}
    c = sm.pattern_parse("Đi làm không mặc áo hai dây")  # "hai dây" is a garment, not "dày"
    assert c["avoid"] == [{"kinds": ["áo hai dây"]}]
    assert "KHÔNG chọn quần short, dép" in sm.describe_constraint(sm.pattern_parse("Đi đám cưới không mặc quần short và dép"))


def test_validate_constraint_is_strict_and_accepts_object_form():
    c = sm.validate_constraint({"when": {"events": ["tiệc cưới"]}, "avoid": {"colors": ["Trắng"], "categories": ["shoes"]},
                                "strength": "must"})
    assert c["when"]["events"] == ["dự tiệc", "đám cưới"]
    assert c["avoid"] == [{"colors": ["trắng"]}, {"categories": ["shoes"]}]  # object form = any of
    for bad in ({"foo": 1}, {"strength": "maybe"}, {"avoid": [{"categories": ["hat"]}]},
                {"avoid": [{"attributes": {"formality_lt": 9}}]}, {"when": {"events": ["sao hoả"]}},
                {"when": {"weather": {"rain": "yes"}}}, {"avoid": [{"items": ["../x"]}]}):
        with pytest.raises(sm.ConstraintError):
            sm.validate_constraint(bad)
    # unknown garment words survive as keywords
    assert sm.validate_constraint({"avoid": [{"kinds": ["áo dài"]}]})["avoid"] == [{"keywords": ["áo dài"]}]


def test_parse_rule_ai_validates_and_falls_back():
    reply = {"when": {"events": ["đám cưới"], "weather": {}}, "avoid": [{"kinds": ["quần short"]}, {"kinds": ["dép"]}],
             "prefer": [], "require": [], "strength": "must", "explanation_vi": "Đám cưới cần lịch sự."}
    provider = FakeProvider(reply)
    c, parser = run(sm.parse_rule(provider, "Đi đám cưới không mặc quần short và dép", closet()))
    assert parser == "ai" and c["avoid"][1] == {"kinds": ["dép"]} and c["explanation_vi"] == "Đám cưới cần lịch sự."
    assert "quần short" in provider.calls[0]["content"][0]["text"]
    c, parser = run(sm.parse_rule(FakeProvider({"avoid": "whatever", "zzz": 1}), "Đi làm ưu tiên sơ mi"))
    assert parser == "pattern_fallback" and c["prefer"] == [{"kinds": ["sơ mi"]}]
    c, parser = run(sm.parse_rule(MockProvider(), "Đi làm ưu tiên sơ mi"))
    assert parser == "pattern"


# ---- enforcement: must vs should ------------------------------------------------------------------


def test_must_avoid_filters_and_should_avoid_only_demotes():
    must = [rule("r_m", "Đi đám cưới không mặc quần short và dép")]
    res = run(suggest_outfits(MockProvider(), closet(), weather=MILD, event="Đám cưới", rules=must))
    ids = {i for o in res["outfits"] for i in o["item_ids"]}
    assert res["outfits"] and not ids & {"short", "sandal"}
    assert all(o["rules_used"] == ["r_m"] for o in res["outfits"]) and res["rules_used"] == must
    assert res["memory"]["banned"] == {"r_m": ["sandal", "short"]}

    should = [rule("r_s", "Đi đám cưới không nên mặc quần short")]
    ctx = sm.build_context(closet(), should, None, event="Đám cưới", weather=MILD)
    allowed, banned = sm.enforce_must(closet(), ctx)
    assert banned == {} and len(allowed) == len(closet())  # not filtered …
    adj = sm.rule_adjustments(["tee-w", "short"], ctx)
    assert adj and adj[0][0] < 0 and adj[0][1] == "r_s"  # … but ranked down
    assert sm.rule_adjustments(["tee-w", "trouser"], ctx) == []


def test_must_require_repairs_outfit_and_rules_apply_only_when_condition_holds():
    rain = [rule("r_rain", "Trời mưa thì phải mặc áo khoác gió")]
    res = run(suggest_outfits(MockProvider(), closet(), weather=HOT_RAIN, event="đi chơi", rules=rain))
    assert all("wind" in o["item_ids"] for o in res["outfits"])
    assert all("r_rain" in o["rules_used"] for o in res["outfits"])
    dry = run(suggest_outfits(MockProvider(), closet(), weather=MILD, event="đi chơi", rules=rain))
    assert dry["rules_used"] == [] and all(o["rules_used"] == [] for o in dry["outfits"])


def test_should_prefer_ranks_and_credits_only_matching_outfits():
    rules = [rule("r_shirt", "Đi làm ưu tiên sơ mi", who="Mẹ")]
    res = run(suggest_outfits(MockProvider(), closet(), weather=MILD, event="đi làm", rules=rules))
    first = res["outfits"][0]
    assert {"shirt-w", "shirt-n"} & set(first["item_ids"])
    for outfit in res["outfits"]:
        has_shirt = bool({"shirt-w", "shirt-n"} & set(outfit["item_ids"]))
        assert ("r_shirt" in outfit["rules_used"]) == has_shirt
        if has_shirt:
            assert any(l["kind"] == "rule" and l["taught_by"] == "Mẹ" for l in outfit["learned"])


def test_ai_claims_are_checked_and_prompt_has_interpretations():
    rules = [rule("r_m", "Đi đám cưới không mặc quần short và dép"), rule("r_x", "Đi biển nên mặc đồ sáng màu")]
    provider = FakeProvider({"outfits": [
        {"title": "A", "item_ids": ["shirt-n", "trouser", "loafer"], "reason": "r", "rules_used": ["r_m", "r_x"]},
        {"title": "B", "item_ids": ["shirt-w", "jean", "loafer"], "reason": "r", "rules_used": []},
    ]})
    res = run(suggest_outfits(provider, closet(), weather=MILD, event="Đám cưới", rules=rules))
    prompt = provider.calls[0]["content"][0]["text"]
    assert "QUY TẮC ÁP DỤNG HÔM NAY" in prompt and "KHÔNG chọn quần short, dép" in prompt
    assert '"id": "short"' not in prompt  # must-avoid items hidden from the model
    assert all(o["rules_used"] == ["r_m"] for o in res["outfits"])  # r_x (beach) is not credited


# ---- learning from feedback --------------------------------------------------------------------


def test_feedback_updates_profile_and_changes_ranking(tmp_path):
    items = closet()
    base = run(suggest_outfits(MockProvider(), items, weather=MILD, event="đi chơi"))
    top = base["outfits"][0]["item_ids"]
    now = time.time()
    for _ in range(2):
        sm.record_feedback(tmp_path, item_ids=top, vote="down", event="đi chơi", now=now)
    liked = base["outfits"][-1]["item_ids"]
    sm.record_feedback(tmp_path, item_ids=liked, vote="up", event="đi chơi", now=now)
    memory = sm.load_memory(tmp_path)
    after = run(suggest_outfits(MockProvider(), items, weather=MILD, event="đi chơi", memory=memory))
    assert after["outfits"][0]["item_ids"] != top
    again = run(suggest_outfits(MockProvider(), items, weather=MILD, event="đi chơi", memory=sm.load_memory(tmp_path)))
    assert [o["item_ids"] for o in again["outfits"]] == [o["item_ids"] for o in after["outfits"]]  # deterministic
    summary = sm.profile_summary(memory, items, now=now)
    assert summary["stats"] == {"votes": 3, "worn": 0, "likes": 1, "dislikes": 2}
    assert summary["facts"] and any(f.startswith("Bạn") for f in summary["facts"])


def test_reason_chips_target_the_right_feature(tmp_path):
    items = closet()
    by_id = {it["id"]: it for it in items}
    sm.record_feedback(tmp_path, item_ids=["tee-b", "jean"], vote="down", reason="color")
    aff = sm.compute_affinities(sm.load_profile(tmp_path), by_id)
    assert aff["color:đen"] < 0 and aff["item:tee-b"] > -0.2  # mostly the color learns
    sm.reset_profile(tmp_path)
    sm.record_feedback(tmp_path, item_ids=["tee-b", "jean"], vote="down", reason="dislike_item", disliked_item_ids=["jean"])
    aff = sm.compute_affinities(sm.load_profile(tmp_path), by_id)
    assert aff["item:jean"] < -0.3 and "item:tee-b" not in aff
    with pytest.raises(Exception):
        sm.record_feedback(tmp_path, item_ids=["tee-b"], vote="down", reason="nope")


def test_time_decay_halves_old_feedback(tmp_path):
    by_id = {it["id"]: it for it in closet()}
    now = time.time()
    sm.record_feedback(tmp_path, item_ids=["tee-b", "jean"], vote="up", now=now - 42 * 86400)
    old = sm.compute_affinities(sm.load_profile(tmp_path), by_id, now=now)["item:tee-b"]
    sm.reset_profile(tmp_path)
    sm.record_feedback(tmp_path, item_ids=["tee-b", "jean"], vote="up", now=now)
    fresh = sm.compute_affinities(sm.load_profile(tmp_path), by_id, now=now)["item:tee-b"]
    assert 0 < old < fresh / 2


def test_worn_yesterday_is_not_suggested_again(tmp_path):
    items = closet()
    w = dict(MILD, date="2026-11-10")
    first = run(suggest_outfits(MockProvider(), items, weather=w, event="đi làm"))["outfits"][0]["item_ids"]
    sm.record_feedback(tmp_path, item_ids=first, vote="wear", event="đi làm", day="2026-11-09")
    res = run(suggest_outfits(MockProvider(), items, weather=w, event="đi làm", memory=sm.load_memory(tmp_path)))
    assert all(set(o["item_ids"]) != set(first) for o in res["outfits"])
    later = dict(MILD, date="2026-11-14")  # a few days later it may come back
    res = run(suggest_outfits(MockProvider(), items, weather=later, event="đi làm", memory=sm.load_memory(tmp_path)))
    assert any(set(o["item_ids"]) == set(first) for o in res["outfits"])


# ---- teach by example ---------------------------------------------------------------------------


def test_examples_influence_ranking_and_prompt(tmp_path):
    items = closet()
    ex = sm.add_example(tmp_path, item_ids=["shirt-n", "jean", "loafer"], event="Đi làm", taught_by="Chị Lan",
                        note="Đi làm thì mặc như này", known_ids=[it["id"] for it in items])
    assert ex["occasions"] == ["đi làm"]
    res = run(suggest_outfits(MockProvider(), items, weather=MILD, event="đi làm", memory=sm.load_memory(tmp_path)))
    first = res["outfits"][0]
    assert set(first["item_ids"]) >= {"shirt-n", "jean"}
    assert any(l["kind"] == "example" and l["id"] == ex["id"] for l in first["learned"])
    other = run(suggest_outfits(MockProvider(), items, weather=MILD, event="đi chơi", memory=sm.load_memory(tmp_path)))
    assert not any(l["kind"] == "example" for o in other["outfits"] for l in o["learned"])
    provider = FakeProvider({"outfits": [{"title": "A", "item_ids": ["shirt-w", "trouser"], "reason": "r"},
                                         {"title": "B", "item_ids": ["tee-b", "jean"], "reason": "r"}]})
    run(suggest_outfits(provider, items, weather=MILD, event="đi làm", memory=sm.load_memory(tmp_path)))
    assert "BỘ MẪU DO NGƯỜI DẠY" in provider.calls[0]["content"][0]["text"] and ex["id"] in provider.calls[0]["content"][0]["text"]
    assert sm.delete_example(tmp_path, ex["id"]) and sm.list_examples(tmp_path) == []
    with pytest.raises(Exception):
        sm.add_example(tmp_path, item_ids=["shirt-n"], event="Đi làm")


def test_rule_adjustments_hook_shape():
    ctx = sm.build_context(closet(), [rule("r_shirt", "Đi làm ưu tiên sơ mi")], None, event="đi làm", weather=MILD)
    adj = sm.rule_adjustments({"item_ids": ["shirt-w", "trouser"]}, ctx)
    assert adj == [(1.2, "r_shirt", "Ưu tiên sơ mi — học từ Chị Lan")]
    assert sm.rule_adjustments([closet()[2], closet()[5]], ctx) == adj


# ---- API ----------------------------------------------------------------------------------------


def test_rules_api_returns_interpretation_and_put_edits(wclient):
    seed_test_wardrobe()
    rule_ = wclient.post("/api/wardrobe/rules", json={"text": "Đi đám cưới không mặc quần short và dép",
                                                      "taught_by": "Chị Lan", "taught_via": "voice"}).json()
    assert rule_["constraint"]["avoid"] == [{"kinds": ["quần short"]}, {"kinds": ["dép"]}]
    assert rule_["display_vi"].startswith("Trợ lý hiểu là: Khi đi đám cưới") and rule_["strength"] == "must"
    assert rule_["parser"] == "pattern" and rule_["confirmed"] is False and rule_["taught_via"] == "voice"
    assert wclient.get("/api/wardrobe/rules").json() == [rule_]
    soft = wclient.put(f"/api/wardrobe/rules/{rule_['id']}", json={"strength": "should", "confirmed": True}).json()
    assert soft["strength"] == "should" and soft["confirmed"] is True and "hạn chế" in soft["interpretation_vi"]
    edited = wclient.put(f"/api/wardrobe/rules/{rule_['id']}", json={"text": "Đi làm ưu tiên sơ mi"}).json()
    assert edited["constraint"]["prefer"] == [{"kinds": ["sơ mi"]}] and edited["confirmed"] is False
    bad = wclient.put(f"/api/wardrobe/rules/{rule_['id']}", json={"constraint": {"avoid": [{"categories": ["hat"]}]}})
    assert bad.status_code == 422
    manual = wclient.put(f"/api/wardrobe/rules/{rule_['id']}", json={"constraint": {
        "when": {"events": ["đi làm"]}, "avoid": [{"attributes": {"formality_lt": 3}}], "strength": "must"}}).json()
    assert manual["parser"] == "teacher" and "độ trang trọng dưới 3/5" in manual["interpretation_vi"]
    assert wclient.put("/api/wardrobe/rules/r_nope", json={"confirmed": True}).status_code == 404


def test_feedback_profile_examples_api_and_suggest_credits(wclient):
    items = seed_test_wardrobe()["items"]
    ids = [it["id"] for it in items]
    tops = [it["id"] for it in items if it["category"] == "top"]
    bottoms = [it["id"] for it in items if it["category"] == "bottom"]
    assert wclient.get("/api/stylist/profile").json()["facts"] == []
    r = wclient.post("/api/stylist/feedback", json={"item_ids": [tops[0], bottoms[0]], "vote": "down",
                                                    "reason": "Màu không hợp", "event": "Đi làm"})
    body = r.json()
    assert r.status_code == 200 and body["recorded"]["reason"] == "color" and body["message_vi"].startswith("Đã ghi nhận")
    assert body["profile"]["stats"]["dislikes"] == 1
    assert wclient.post("/api/stylist/feedback", json={"item_ids": [tops[0]], "vote": "meh"}).status_code == 422
    assert wclient.post("/api/stylist/feedback", json={"item_ids": ["nope"], "vote": "up"}).status_code == 404
    wclient.post("/api/stylist/feedback", json={"item_ids": [tops[1], bottoms[1]], "vote": "wear", "date": "2026-11-09"})
    prof = wclient.get("/api/stylist/profile").json()
    assert prof["stats"]["worn"] == 1 and prof["worn_recent"][0]["date"] == "2026-11-09"
    assert {c["code"] for c in prof["reason_codes"]} == set(sm.REASONS)

    ex = wclient.post("/api/wardrobe/examples", json={"event": "Đi làm", "item_ids": [tops[1], bottoms[1]],
                                                      "taught_by": "Chị Lan", "note": "Đi làm mặc thế này"}).json()
    assert ex["id"].startswith("ex_") and len(ex["items"]) == 2 and "Chị Lan" in ex["label_vi"]
    assert wclient.post("/api/wardrobe/examples", json={"event": "x", "item_ids": [tops[0]]}).status_code == 422
    assert [e["id"] for e in wclient.get("/api/wardrobe/examples").json()] == [ex["id"]]

    wclient.post("/api/wardrobe/rules", json={"text": "Đi làm ưu tiên sơ mi", "taught_by": "Mẹ"})
    res = wclient.post("/api/stylist/suggest", json={"event": "Đi làm", "city": "Hà Nội"}).json()
    assert res["feedback_context"]["event"] == "Đi làm" and "learning" in res
    for outfit in res["outfits"]:
        assert {"learned", "adjustments", "score_delta", "rules_used"} <= set(outfit)
    assert any(l["kind"] in ("rule", "example") for o in res["outfits"] for l in o["learned"])

    assert wclient.delete("/api/stylist/profile").json()["profile"]["stats"]["votes"] == 0
    assert wclient.delete(f"/api/wardrobe/examples/{ex['id']}").json() == {"ok": True}
    assert wclient.delete(f"/api/wardrobe/examples/{ex['id']}").status_code == 404
    assert set(ids) >= set(tops)


def test_model_placeholder_weather_is_dropped_and_dislike_phrasing():
    # Real Qwen reply shape: unused weather keys filled with rain=false / 0…100 °C.
    reply = {"when": {"events": ["đi làm"], "weather": {"rain": False, "t_max_gte": 0, "t_max_lte": 100,
                                                         "t_min_gte": 0, "t_min_lte": 100}},
             "avoid": [], "prefer": [{"categories": ["top"], "kinds": ["sơ mi"], "colors": [], "items": [],
                                      "styles": [], "keywords": [], "attributes": {}}],
             "require": [], "strength": "should", "explanation_vi": "Đi làm nên mặc sơ mi."}
    c, parser = run(sm.parse_rule(FakeProvider(reply), "Đi làm ưu tiên sơ mi"))
    assert parser == "ai" and c["when"]["weather"] == {} and c["prefer"] == [{"categories": ["top"], "kinds": ["sơ mi"]}]
    assert sm.describe_constraint(c) == "Khi đi làm: ưu tiên sơ mi (nên)."
    rainy = {**reply, "when": {"events": [], "weather": {"rain": True, "t_max_lte": 100}}}
    c, _ = run(sm.parse_rule(FakeProvider(rainy), "Trời mưa thì nên mặc sơ mi"))
    assert c["when"]["weather"] == {"rain": True}
    c = sm.pattern_parse("Mình không thích quần jean xanh nhạt")
    assert c["avoid"] == [{"colors": ["xanh nhạt"], "kinds": ["quần jean"]}] and c["strength"] == "should"
    jean = item("j", "bottom", colors=["xanh dương nhạt"], name="Quần jean ống suông xanh nhạt")
    assert sm.item_matches(jean, c["avoid"][0])

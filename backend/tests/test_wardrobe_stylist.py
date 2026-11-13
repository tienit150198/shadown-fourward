from __future__ import annotations

from wardrobe_testlib import seed_test_wardrobe

import asyncio

from wardrobe_testlib import (  # noqa: F401
    FakeProvider,
    data_url,
    make_jpeg,
    sample_wardrobe,
    wardrobe_dir,
    wclient,
    weather,
)

from providers import MockProvider
from stylist import (
    describe_wearing,
    is_wearable,
    rule_based_outfits,
    suggest_outfits,
    tag_item,
    validate_outfits,
    verify_match,
    vietnamize,
)
from wardrobe_store import to_jpeg


def run(coro):
    return asyncio.run(coro)


def by_id():
    return {it["id"]: it for it in sample_wardrobe()}


def cats(outfit):
    items = by_id()
    return {items[i]["category"] for i in outfit["item_ids"]}


# ---- tagging -------------------------------------------------------------------------


def test_tag_mock_is_deterministic_and_uses_dominant_color():
    jpeg = to_jpeg(make_jpeg(color=(200, 30, 40)))
    fields, source = run(tag_item(MockProvider(), jpeg))
    again, _ = run(tag_item(MockProvider(), jpeg))
    assert source == "mock" and fields == again
    assert fields["colors"] == ["đỏ"] and fields["category"] == "top"
    named, _ = run(tag_item(MockProvider(), jpeg, name="Áo của mẹ", category="Quần"))
    assert named["name"] == "Áo của mẹ" and named["category"] == "bottom"


def test_tag_ai_reply_is_normalized_and_user_overrides_win():
    provider = FakeProvider({
        "name": "Áo khoác jean xanh", "category": "Áo khoác", "colors": ["Xanh dương", "trắng"],
        "warmth": 9, "occasions": ["đi chơi", "tiệc cưới", "chụp ảnh"], "seasons": ["thu", "đông"],
        "style": "Năng động", "description_en": "blue denim jacket",
    })
    jpeg = to_jpeg(make_jpeg())
    fields, source = run(tag_item(provider, jpeg))
    assert source == "ai"
    assert fields["category"] == "outerwear" and fields["warmth"] == 5
    assert fields["colors"] == ["xanh dương", "trắng"]
    assert fields["occasions"] == ["đi chơi", "dự tiệc", "đám cưới", "chụp ảnh"]
    assert fields["style"] == "năng động" and fields["description_en"] == "blue denim jacket"
    call = provider.calls[0]
    assert call["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    provider = FakeProvider({"name": "Áo", "category": "top", "colors": ["đen"], "warmth": 2})
    fields, _ = run(tag_item(provider, jpeg, name="Quần yêu thích", category="bottom"))
    assert fields["name"] == "Quần yêu thích" and fields["category"] == "bottom"
    assert "Người dùng chọn loại: bottom" in provider.calls[0]["content"][0]["text"]


def test_tag_bad_reply_falls_back():
    jpeg = to_jpeg(make_jpeg())
    fields, source = run(tag_item(FakeProvider({"category": "spaceship", "name": "???"}), jpeg))
    assert source == "fallback" and fields["category"] == "top"
    fields, source = run(tag_item(FakeProvider(TimeoutError("slow")), jpeg, category="dress"))
    assert source == "fallback" and fields["category"] == "dress"


# ---- deterministic picker ---------------------------------------------------------------


def test_rule_based_hot_day_prefers_light_items_without_outerwear():
    outfits = rule_based_outfits(sample_wardrobe(), weather=weather(27, 34), event="đi chơi")
    assert 2 <= len(outfits) <= 3
    for outfit in outfits:
        assert is_wearable(cats(outfit))
        assert "outerwear" not in cats(outfit)
        assert outfit["reason"] and outfit["tips"] and outfit["title"]
    first = outfits[0]["item_ids"]
    assert "t3" not in first  # warm sweater is not the first pick at 34°C
    assert len({tuple(o["item_ids"]) for o in outfits}) == len(outfits)


def test_rule_based_cold_rainy_work_day_adds_outerwear():
    outfits = rule_based_outfits(sample_wardrobe(), weather=weather(12, 16, rain=80, code=63), event="đi làm")
    first = outfits[0]
    assert "outerwear" in cats(first)
    assert "b1" not in first["item_ids"]  # no shorts in the cold
    assert "mưa" in first["tips"]


def test_rule_based_anchor_and_worn_categories():
    outfits = rule_based_outfits(sample_wardrobe(), weather=weather(20, 26), anchor_id="b2")
    assert outfits and all("b2" in o["item_ids"] for o in outfits)
    worn = rule_based_outfits(sample_wardrobe(), weather=weather(20, 26), worn_categories={"top"})
    assert worn and all("top" not in cats(o) and "dress" not in cats(o) for o in worn)
    assert all("bottom" in cats(o) for o in worn)


# ---- validation of model output -----------------------------------------------------------


def test_validate_outfits_drops_unknown_ids_and_preserves_layers():
    raw = {"outfits": [
        {"title": "A", "item_ids": ["t2", "b2", "ghost", "t2", "s1"], "reason": "ok", "tips": "x", "rules_used": ["r_1", "r_x"]},
        {"title": "B", "item_ids": ["t1", "s1"], "reason": "no bottom"},
        {"title": "C", "item_ids": ["d1", "t1", "b1"], "reason": "dress wins"},
        {"title": "dup", "item_ids": ["b2", "t2", "s1"]},
        "garbage",
    ]}
    valid, used = validate_outfits(raw, sample_wardrobe(), weather=weather(20, 26), anchor_id=None,
                                   worn_categories=set(), rule_ids={"r_1"})
    assert [o["item_ids"] for o in valid] == [["t2", "b2", "s1"], ["t1", "s1"], ["d1", "t1", "b1"]]
    assert valid[0]["rules_used"] == ["r_1"] and used == {"r_1"}


def test_validate_outfits_anchor_heat_and_worn():
    raw = {"outfits": [{"title": "A", "item_ids": ["t1", "b1", "o1"], "reason": "r"}]}
    valid, _ = validate_outfits(raw, sample_wardrobe(), weather=weather(27, 35), anchor_id="b2",
                                worn_categories=set(), rule_ids=set())
    assert valid[0]["item_ids"] == ["t1", "b1", "o1", "b2"]  # add explicit anchor without rewriting layers
    worn, _ = validate_outfits({"outfits": [{"item_ids": ["t2", "b2"]}]}, sample_wardrobe(),
                               weather=None, anchor_id=None, worn_categories={"top"}, rule_ids=set())
    assert worn[0]["item_ids"] == ["t2", "b2"]
    assert validate_outfits({"nope": 1}, sample_wardrobe(), weather=None, anchor_id=None,
                            worn_categories=set(), rule_ids=set()) == ([], set())


def test_validate_outfits_keeps_dress_layers_and_anchor():
    raw = {"outfits": [{"title": "A", "item_ids": ["d1", "s1"], "reason": "r"},
                       {"title": "B", "item_ids": ["b1", "s1"], "reason": "r"}]}
    worn, _ = validate_outfits(raw, sample_wardrobe(), weather=None, anchor_id=None,
                               worn_categories={"top"}, rule_ids=set())
    assert [o["item_ids"] for o in worn] == [["d1", "s1"], ["b1", "s1"]]
    anchored, _ = validate_outfits({"outfits": [{"item_ids": ["d1", "t1"]}]}, sample_wardrobe(),
                                   weather=None, anchor_id="b2", worn_categories=set(), rule_ids=set())
    assert anchored[0]["item_ids"] == ["d1", "t1", "b2"]


def test_vietnamize_replaces_english_tag_words():
    assert vietnamize("Áo velvet xanh cobalt") == "Áo nhung xanh dương đậm"
    assert vietnamize("xanh navy") == "xanh navy"
    assert vietnamize("Quần denim xanh blue") == "Quần jean xanh dương"
    assert vietnamize("áo sơ mi trắng") == "áo sơ mi trắng"


# ---- suggest_outfits ---------------------------------------------------------------------------


RULES = [{"id": "r_1", "text": "Đi làm không mặc áo hai dây", "taught_by": "Chị Mai"}]


def test_suggest_ai_success():
    provider = FakeProvider({"outfits": [
        {"title": "Công sở", "item_ids": ["t2", "b2", "s1"], "reason": "Lịch sự.", "tips": "Đeo đồng hồ.", "rules_used": ["r_1"]},
        {"title": "Váy", "item_ids": ["d1"], "reason": "Nữ tính.", "tips": ""},
    ]})
    result = run(suggest_outfits(provider, sample_wardrobe(), weather=weather(20, 26), event="đi làm", rules=RULES))
    assert result["source"] == "ai" and len(result["outfits"]) == 2
    assert result["rules_used"] == RULES
    prompt = provider.calls[0]["content"][0]["text"]
    assert '"id": "t2"' in prompt and "Chị Mai" in prompt and "[r_1]" in prompt and "đi làm" in prompt
    assert provider.calls[0]["max_tokens"] == 1200


def test_suggest_preserves_single_ai_outfit_and_falls_back_on_garbage():
    provider = FakeProvider({"outfits": [{"title": "A", "item_ids": ["t2", "b2"], "reason": "r"}]})
    result = run(suggest_outfits(provider, sample_wardrobe(), weather=weather(20, 26)))
    assert result["source"] == "ai" and len(result["outfits"]) == 1
    result = run(suggest_outfits(FakeProvider({"outfits": [{"item_ids": ["ghost"]}]}), sample_wardrobe(), weather=None))
    assert result["source"] == "fallback" and len(result["outfits"]) >= 2
    result = run(suggest_outfits(FakeProvider(RuntimeError("down")), sample_wardrobe(), weather=None))
    assert result["source"] == "fallback"
    result = run(suggest_outfits(MockProvider(), sample_wardrobe(), weather=None))
    assert result["source"] == "mock" and result["rules_used"] == []


def test_describe_wearing_verifies_match():
    items = sample_wardrobe()
    garments = [{"category": "top", "colors": ["hồng nhạt"], "description": "áo sơ mi không tay hồng"}]
    assert verify_match("t2", garments, items) is None  # navy shirt ≠ pink shirt
    assert verify_match("ghost", garments, items) is None
    assert verify_match("t2", [{"category": "top", "colors": ["navy"], "description": ""}], items) == "t2"

    provider = FakeProvider({
        "wearing": "áo sơ mi hồng, quần đen",
        "garments": [{"category": "Áo", "colors": ["hồng"], "description": "áo sơ mi hồng"},
                     {"category": "quần", "colors": ["đen"], "description": "quần tây đen"}],
        "match_item_id": "b2",
    })
    result = run(describe_wearing(provider, data_url(make_jpeg()), items))
    assert result["match_item_id"] == "b2" and result["primary"]["category"] == "top"
    assert result["wearing"] == "áo sơ mi hồng, quần đen"
    mock = run(describe_wearing(MockProvider(), data_url(make_jpeg()), items))
    assert mock["source"] == "mock" and mock["match_item_id"] is None


def test_suggest_with_worn_top_keeps_it():
    wearing = {"wearing": "áo hồng", "garments": [{"category": "top", "colors": ["hồng"], "description": "áo hồng"}],
               "primary": {"category": "top", "colors": ["hồng"], "description": "áo hồng"}, "match_item_id": None}
    provider = FakeProvider({"outfits": [
        {"title": "A", "item_ids": ["t2", "b2"], "reason": "r"},
        {"title": "B", "item_ids": ["b1", "s1"], "reason": "r"},
    ]})
    result = run(suggest_outfits(provider, sample_wardrobe(), weather=weather(24, 30), wearing=wearing))
    # Keep the model selection and order; the worn garment is context in the prompt.
    assert [o["item_ids"] for o in result["outfits"]] == [["t2", "b2"], ["b1", "s1"]]
    assert "GIỮ NGUYÊN món đang mặc: áo hồng" in provider.calls[0]["content"][0]["text"]


# ---- API ------------------------------------------------------------------------------------------


def test_suggest_route_mock(wclient):
    assert wclient.post("/api/stylist/suggest", json={"event": "đi làm"}).status_code == 409
    assert seed_test_wardrobe()["added"] == 10
    response = wclient.post("/api/stylist/suggest", json={"event": "đi làm", "city": "Hà Nội"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "mock" and body["weather"]["available"] is True
    assert 2 <= len(body["outfits"]) <= 3 and body["wearing"] is None
    known = {it["id"] for it in wclient.get("/api/wardrobe/items").json()}
    for outfit in body["outfits"]:
        assert set(outfit["item_ids"]) <= known
        assert [it["id"] for it in outfit["items"]] == outfit["item_ids"]
        assert all(it["image_url"].startswith("/wardrobe-media/images/") for it in outfit["items"])
    anchored = wclient.post("/api/stylist/suggest", json={"event": "đi chơi", "anchor_item_id": "demo-quan-jean-xanh"}).json()
    assert all("demo-quan-jean-xanh" in o["item_ids"] for o in anchored["outfits"])
    assert wclient.post("/api/stylist/suggest", json={"anchor_item_id": "ghost"}).status_code == 404
    assert wclient.post("/api/stylist/suggest", json={"current_image": "data:image/gif;base64,AAAA"}).status_code == 422


def test_suggest_route_ai_with_current_image(wclient):
    seed_test_wardrobe()
    provider = FakeProvider(
        {"wearing": "áo sơ mi không tay hồng nhạt, quần đen",
         "garments": [{"category": "top", "colors": ["hồng nhạt"], "description": "áo sơ mi không tay hồng nhạt"}],
         "match_item_id": "demo-ao-blouse-cam-dao"},  # wrong color → rejected by verify_match
        {"outfits": [
            {"title": "Hẹn hò", "item_ids": ["demo-chan-vay-be", "demo-giay-sneaker-trang"], "reason": "Nhẹ nhàng.", "tips": "Mang ô."},
            {"title": "Lịch sự", "item_ids": ["demo-quan-au-den"], "reason": "Gọn.", "tips": ""},
        ]},
    )
    wclient.app.state.provider = provider
    body = wclient.post("/api/stylist/suggest", json={
        "event": "hẹn hò", "current_image": data_url(make_jpeg(color=(240, 180, 180)))
    }).json()
    assert body["wearing"] == "áo sơ mi không tay hồng nhạt, quần đen"
    assert body["anchor_item_id"] is None and body["wearing_kept"]["category"] == "top"
    assert body["source"] == "ai" and [o["item_ids"] for o in body["outfits"]] == [
        ["demo-chan-vay-be", "demo-giay-sneaker-trang"], ["demo-quan-au-den"]]
    assert len(provider.calls) == 2


# ---- taught rules enforced deterministically ---------------------------------------------------

WEDDING_RULE = [{"id": "r_w", "text": "Đi đám cưới không mặc đồ trắng", "taught_by": "Chị Lan"}]


def test_banned_colors_parsing():
    from stylist import banned_colors

    assert banned_colors("Đi đám cưới thì không mặc đồ trắng, tránh lấn át cô dâu.") == ["trắng"]
    assert banned_colors("Đi phỏng vấn tránh áo xanh lá hoặc đỏ") == ["xanh lá", "đỏ"]
    assert banned_colors("Áo sáng màu nên phối với quần tối màu") == []


def test_suggest_enforces_wedding_rule_and_attributes_it():
    # The model ignores the rule (white top + white sneakers) and leaves rules_used empty.
    provider = FakeProvider({"outfits": [
        {"title": "A", "item_ids": ["t1", "b2", "s1"], "reason": "r"},
        {"title": "B", "item_ids": ["t2", "b2", "s1"], "reason": "r"},
    ]})
    result = run(suggest_outfits(provider, sample_wardrobe(), weather=weather(20, 26), event="Đám cưới",
                                 rules=WEDDING_RULE))
    prompt = provider.calls[0]["content"][0]["text"]
    assert '"id": "t1"' not in prompt and '"id": "s1"' not in prompt  # banned items hidden from the model
    ids = [i for o in result["outfits"] for i in o["item_ids"]]
    assert "t1" not in ids and "s1" not in ids
    assert all(o["rules_used"] == ["r_w"] for o in result["outfits"])
    assert result["rules_used"] == WEDDING_RULE
    # Same rule is irrelevant for work: nothing banned, no attribution.
    other = run(suggest_outfits(MockProvider(), sample_wardrobe(), weather=weather(20, 26), event="đi làm",
                                rules=WEDDING_RULE))
    assert other["rules_used"] == [] and all(o["rules_used"] == [] for o in other["outfits"])


def test_suggest_mock_mode_applies_rules_too():
    result = run(suggest_outfits(MockProvider(), sample_wardrobe(), weather=weather(20, 26), event="đám cưới",
                                 rules=WEDDING_RULE))
    ids = [i for o in result["outfits"] for i in o["item_ids"]]
    assert result["outfits"] and "t1" not in ids and "s1" not in ids
    assert result["rules_used"] == WEDDING_RULE


def test_ai_layered_tops_reach_response_without_replacement_or_reranking():
    selected = ['t1', 't2', 'b2', 'o1']
    provider = FakeProvider({'outfits': [{
        'title': 'Layered shirts', 'item_ids': selected,
        'reason': 'Wear the second shirt over the first.', 'tips': 'Leave it open.',
    }]})
    result = run(suggest_outfits(provider, sample_wardrobe(), weather=weather(28, 35)))
    assert result['source'] == 'ai'
    assert len(result['outfits']) == 1
    assert result['outfits'][0]['item_ids'] == selected
    assert result['outfits'][0]['reason'] == 'Wear the second shirt over the first.'
    assert result['dropped'] == []

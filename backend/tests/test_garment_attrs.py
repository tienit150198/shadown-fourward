"""garment_attrs: colour from photo, keyword rules, normalisation, store migration, API editing."""

from __future__ import annotations

import io
import json

from wardrobe_testlib import data_url, make_jpeg, wardrobe_dir, wclient  # noqa: F401

from garment_attrs import (
    ATTRS_VERSION,
    derive_attrs,
    dominant_color_hex,
    fill_attrs,
    hex_to_rgb,
    normalize_attr_fields,
    build_tryon_description,
    tryon_descriptor,
)


def garment_on_backdrop(garment=(30, 40, 80), backdrop=(240, 240, 240), size=(120, 160)) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, backdrop)
    ImageDraw.Draw(image).rectangle((30, 30, 90, 130), fill=garment)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def close(hex_value, rgb, tol=20):
    return all(abs(a - b) <= tol for a, b in zip(hex_to_rgb(hex_value), rgb))


def test_dominant_color_ignores_backdrop():
    assert close(dominant_color_hex(garment_on_backdrop((30, 40, 80))), (30, 40, 80))
    assert close(dominant_color_hex(garment_on_backdrop((200, 30, 40), backdrop=(255, 255, 255))), (200, 30, 40))
    # white garment on a light-grey studio backdrop is still white, not the shadow/sole
    assert close(dominant_color_hex(garment_on_backdrop((252, 252, 252), backdrop=(236, 236, 236))), (252, 252, 252), 8)


def test_rules_from_shop_text():
    shirt = derive_attrs({"name": "Sơ mi cộc tay trắng công sở", "category": "top", "colors": ["trắng"],
                          "product_name": "Áo Sơ Mi Tay Ngắn Stayfresh™ Vải Sợi Ngải Cứu Kháng Khuẩn Đứng Phom "
                                          "Mát Lạnh Phom Vừa F3 - The Ceo 024 Trắng"})
    assert shirt["subtype"] == "dress_shirt" and shirt["sleeve"] == "short" and shirt["fit"] == "regular"
    assert shirt["fabric"] != "linen"  # "mát lạnh" (cool) is not "lanh" (linen)
    assert shirt["formality"] == 4 and shirt["color_family"] == "neutral"
    shorts = derive_attrs({"name": "Quần short thể thao đen", "category": "bottom", "colors": ["đen"],
                           "product_name": "Quần Short Thể Thao 5 Inch Vải Lưới Mesh Thoáng Khí Phom Rộng F4"})
    assert shorts["subtype"] == "sport_shorts" and shorts["length"] == "short" and shorts["formality"] == 1
    assert shorts["fit"] == "relaxed" and shorts["breathability"] == 5 and shorts["sleeve"] is None
    wind = derive_attrs({"name": "Áo khoác gió", "category": "outerwear", "colors": ["xám đậm"],
                         "product_name": "Áo Khoác Dù RainShield™ Vải Ripstop Trượt Nước Gấp Gọn Phom Rộng F4"})
    assert wind["subtype"] == "windbreaker" and wind["rain_ok"] is True and wind["fabric"] == "nylon"
    uv = derive_attrs({"name": "Áo khoác chống nắng UPF50+ be", "category": "outerwear", "colors": ["be"]})
    assert uv["subtype"] == "uv_jacket" and uv["uv_protect"] is True
    slides = derive_attrs({"name": "Dép quai ngang da lộn nâu", "category": "shoes", "colors": ["nâu"]})
    assert slides["subtype"] == "slides" and slides["rain_ok"] is False and slides["formality"] == 1
    jeans = derive_attrs({"name": "Quần jean xanh", "category": "bottom", "colors": ["xanh dương"]})
    assert jeans["fabric"] == "denim" and jeans["color_family"] == "denim" and jeans["length"] == "long"


def test_existing_values_win_in_fill_mode_but_rules_win_after_ai():
    raw = {"name": "Quần short kaki be", "category": "bottom", "colors": ["be"], "formality": 5, "fit": "slim"}
    assert derive_attrs(raw)["formality"] == 5 and derive_attrs(raw)["fit"] == "slim"
    after_ai = derive_attrs(raw, mode="rules_first")
    assert after_ai["formality"] == 2 and after_ai["length"] == "short"


def test_normalize_attr_fields_is_lenient():
    clean = normalize_attr_fields({"color_hex": "ABCDEF", "formality": "9", "fit": "Rộng", "sleeve": "tay ngắn",
                                   "fabric": "Lanh", "rain_ok": "có", "length": "nope", "breathability": True})
    assert clean == {"color_hex": "#abcdef", "formality": 5, "fit": "relaxed", "sleeve": "short",
                     "fabric": "linen", "rain_ok": True}


def test_fill_attrs_fills_description_and_seasons():
    filled = fill_attrs({"name": "Áo polo trắng", "category": "top", "colors": ["trắng"], "warmth": 1})
    assert filled["attrs_v"] == ATTRS_VERSION and filled["description_en"].endswith("polo shirt")
    assert filled["seasons"] and filled["color_hex"].startswith("#")


def test_store_migrates_old_items_and_api_edits(wclient, wardrobe_dir):
    images = wardrobe_dir / "images"
    images.mkdir(parents=True)
    (images / "old1.jpg").write_bytes(garment_on_backdrop((200, 30, 40)))
    (wardrobe_dir / "items.json").write_text(json.dumps([
        {"id": "old1", "name": "Áo thun đỏ", "category": "top", "colors": ["đỏ"], "warmth": 1,
         "image": "images/old1.jpg"}]), encoding="utf-8")
    item = wclient.get("/api/wardrobe/items").json()[0]
    assert item["attrs_v"] == ATTRS_VERSION and item["subtype"] == "tee" and item["color_family"] == "warm"
    assert close(item["color_hex"], (200, 30, 40)) and item["attrs_vi"]["sleeve"] == "tay ngắn"
    saved = json.loads((wardrobe_dir / "items.json").read_text(encoding="utf-8"))[0]
    assert saved["attrs_v"] == ATTRS_VERSION  # persisted once
    edited = wclient.put("/api/wardrobe/items/old1", json={"formality": 3, "fit": "oversized", "fabric": "linen",
                                                           "attrs_vi": {"x": 1}}).json()
    assert edited["formality"] == 3 and edited["fit"] == "oversized" and edited["fabric"] == "linen"
    assert edited["attrs_vi"]["fit"] == "oversize"
    moved = wclient.put("/api/wardrobe/items/old1", json={"category": "bottom"}).json()
    assert moved["sleeve"] is None and moved["length"] in ("short", "long")


def test_create_item_gets_attributes(wclient):
    res = wclient.post("/api/wardrobe/items", json={"image": data_url(make_jpeg(color=(30, 40, 80))),
                                                    "category": "bottom", "name": "Quần tây navy"})
    body = res.json()
    assert res.status_code == 200 and body["subtype"] == "trousers" and body["formality"] == 4
    assert body["length"] == "long" and body["color_hex"].startswith("#")


def test_tryon_descriptor_is_structured_and_confidence_tracks_provenance():
    confirmed = tryon_descriptor({
        "category": "top", "colors": ["xanh navy"], "fabric": "cotton", "pattern": "kẻ sọc",
        "fit": "relaxed", "length": None, "sleeve": "long", "subtype": "sweater",
        "tag_source": "ai", "metadata_confirmed": True,
    })
    inferred = tryon_descriptor({"category": "bottom", "name": "Món đồ", "tag_source": "mock"})

    assert confirmed == {
        "category": "top", "color": "navy", "material": "cotton", "pattern": "striped",
        "fit": "relaxed", "length": None, "details": ["long sleeves"],
        "confidence": 0.98, "needs_confirmation": False,
        "description": "navy striped cotton relaxed-fit sweater with long sleeves",
    }
    assert inferred["confidence"] < 0.8 and inferred["needs_confirmation"] is True
    assert "None" not in build_tryon_description(inferred)


def test_tryon_description_omits_unknown_values_and_is_bounded():
    description = build_tryon_description({
        "category": "outerwear", "color": "olive green", "material": "nylon", "pattern": None,
        "fit": "oversized", "length": "long", "details": ["hooded", "zip front", "ribbed cuffs"] * 5,
    })

    assert "None" not in description and "undefined" not in description
    assert len(description.split()) <= 30

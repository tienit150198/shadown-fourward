from __future__ import annotations

import json

import pytest

from wardrobe_testlib import data_url, make_jpeg

from wardrobe_store import (
    WardrobeError,
    WardrobeStore,
    ItemNotFound,
    decode_image_data_url,
    normalize_category,
    normalize_item_fields,
    normalize_occasions,
    to_jpeg,
)


def test_decode_accepts_jpeg_and_png_and_reencodes_to_jpeg():
    jpeg = decode_image_data_url(data_url(make_jpeg()))
    assert jpeg[:3] == b"\xff\xd8\xff"
    png = decode_image_data_url(data_url(make_jpeg(fmt="PNG", mode="RGBA"), "image/png"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    out = to_jpeg(png)
    assert out[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "hello",
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        "data:image/png;base64,bm90IGFuIGltYWdl",  # valid base64, wrong magic bytes
        "data:image/jpeg;base64,@@@",
        "data:image/jpeg;base64,",
    ],
)
def test_decode_rejects_bad_images(value):
    with pytest.raises(WardrobeError):
        decode_image_data_url(value)


def test_decode_rejects_oversize():
    with pytest.raises(WardrobeError):
        decode_image_data_url(data_url(make_jpeg(size=(256, 256))), max_bytes=100)


def test_to_jpeg_rejects_undecodable_bytes():
    with pytest.raises(WardrobeError):
        to_jpeg(b"\xff\xd8\xff" + b"\x00" * 20)


def test_to_jpeg_downscales():
    from io import BytesIO

    from PIL import Image

    out = to_jpeg(make_jpeg(size=(3000, 1500)))
    assert max(Image.open(BytesIO(out)).size) == 1024


@pytest.mark.parametrize(
    "text,expected",
    [
        ("top", "top"),
        ("Áo khoác jean", "outerwear"),
        ("blazer", "outerwear"),
        ("Quần jean", "bottom"),
        ("chân váy xếp ly", "bottom"),
        ("Váy liền", "dress"),
        ("áo dài", "dress"),
        ("áo sơ mi", "top"),
        ("giày sneakers", "shoes"),
        ("túi xách", "accessory"),
        ("mùa đông", None),
        ("", None),
    ],
)
def test_normalize_category(text, expected):
    assert normalize_category(text) == expected


def test_normalize_occasions_maps_to_fixed_list_and_keeps_extras():
    assert normalize_occasions(["tiệc cưới", "Đi làm", "cafe cuối tuần", "chụp ảnh kỷ yếu"]) == [
        "đi làm", "đi chơi", "dự tiệc", "đám cưới", "chụp ảnh kỷ yếu",
    ]


def test_normalize_item_fields_clamps_and_cleans():
    fields = normalize_item_fields({
        "name": "  Áo   sơ mi  ", "category": "Áo", "colors": "Trắng, xanh navy", "warmth": "9",
        "seasons": ["Mùa hè", "đông"], "pattern": "Trơn",
    })
    assert fields["name"] == "Áo sơ mi"
    assert fields["category"] == "top"
    assert fields["colors"] == ["trắng", "xanh navy"]
    assert fields["warmth"] == 5
    assert fields["seasons"] == ["hạ", "đông"]
    assert fields["pattern"] == "trơn"
    with pytest.raises(WardrobeError):
        normalize_item_fields({"category": "spaceship"}, partial=True)


def test_store_crud_and_media(tmp_path):
    store = WardrobeStore(tmp_path / "w")
    jpeg = to_jpeg(make_jpeg())
    created = store.create_item({"name": "Áo đỏ", "category": "top", "colors": ["đỏ"]}, jpeg)
    item_id = created["id"]
    assert created["image_url"] == f"/wardrobe-media/images/{item_id}.jpg"
    assert store.media_path(created["image"]).read_bytes() == jpeg
    assert [row["id"] for row in store.list_items()] == [item_id]

    updated = store.update_item(item_id, {"warmth": 4, "category": "Áo khoác", "id": "hack", "image": "../x"})
    assert updated["warmth"] == 4 and updated["category"] == "outerwear"
    assert updated["id"] == item_id and updated["image"] == f"images/{item_id}.jpg"
    assert updated["name"] == "Áo đỏ"  # untouched by partial update

    assert store.delete_item(item_id) is True
    assert store.delete_item(item_id) is False
    assert not (tmp_path / "w" / "images" / f"{item_id}.jpg").exists()
    with pytest.raises(ItemNotFound):
        store.get_item(item_id)
    # atomic writes leave no temp files and valid JSON
    assert json.loads((tmp_path / "w" / "items.json").read_text()) == []
    assert not [p for p in (tmp_path / "w").rglob("*.tmp")]


def test_store_rejects_unsafe_ids_and_paths(tmp_path):
    store = WardrobeStore(tmp_path / "w")
    store.create_item({"name": "x"}, to_jpeg(make_jpeg()), item_id="ok-1")
    for bad in ("../items", "a/b", "", "X" * 80, "UPPER"):
        with pytest.raises(ItemNotFound):
            store.get_item(bad)
    with pytest.raises(WardrobeError):
        store.create_item({"name": "x"}, to_jpeg(make_jpeg()), item_id="../evil")
    with pytest.raises(WardrobeError):
        store.create_item({"name": "x"}, b"GIF89a", item_id="gif")
    assert store.media_path("../w/items.json") is None
    assert store.media_path("/etc/passwd") is None
    assert store.media_path("images/../items.json") is None
    assert store.media_path("items.json") is not None  # safe but the HTTP route only serves .jpg


def test_rules(tmp_path):
    store = WardrobeStore(tmp_path / "w")
    rule = store.add_rule("  Đi làm không mặc áo thun ", "Chị Mai")
    assert rule["text"] == "Đi làm không mặc áo thun" and rule["taught_by"] == "Chị Mai"
    assert store.add_rule("Quy tắc 2", None)["taught_by"] == "Bạn"
    with pytest.raises(WardrobeError):
        store.add_rule("   ")
    assert len(store.list_rules()) == 2
    assert store.delete_rule(rule["id"]) is True
    assert store.delete_rule(rule["id"]) is False


def test_tryon_results_are_pruned(tmp_path, monkeypatch):
    import wardrobe_store

    monkeypatch.setattr(wardrobe_store, "MAX_TRYON_RESULTS", 3)
    store = WardrobeStore(tmp_path / "w")
    rels = [store.save_tryon(to_jpeg(make_jpeg())) for _ in range(5)]
    assert all(rel.startswith("tryon/") and rel.endswith(".jpg") for rel in rels)
    assert len(list((tmp_path / "w" / "tryon").glob("*.jpg"))) == 3

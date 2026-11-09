"""seed-demo `set` param + SHADOW_DEMO_SEED (offline: a tmp copy of a tiny 2-item shop set)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wardrobe_testlib import make_jpeg, wardrobe_dir, wclient  # noqa: F401

SHOP_DIR = Path(__file__).resolve().parent.parent / "wardrobe_seed_shop"

ROWS = [
    {
        "id": "shop-ao-thun-trang", "name": "Áo thun trắng", "category": "top", "colors": ["trắng"],
        "style": "thường ngày", "warmth": 1, "occasions": ["đi chơi"], "seasons": ["hạ"], "pattern": "trơn",
        "material": "cotton", "notes": "", "description_en": "white crew-neck T-shirt",
        "brand": "YaMe", "product_name": "Áo Thun Cổ Tròn Trắng", "product_url": "https://yame.vn/products/ao-thun",
        "product_code": "0024688", "price_vnd": 212550, "image_source_url": "https://cdn.example/x.jpg",
    },
    {
        "id": "shop-quan-short-be", "name": "Quần short be", "category": "bottom", "colors": ["be"],
        "warmth": 1, "occasions": ["đi biển"], "description_en": "beige chino shorts",
        "brand": "YaMe", "product_url": "javascript:alert(1)", "price_vnd": "free",
    },
]


@pytest.fixture
def shop_set(tmp_path, monkeypatch):
    import wardrobe_api

    root = tmp_path / "wardrobe_seed_shop"
    (root / "images").mkdir(parents=True)
    (root / "items.json").write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    for row, color in zip(ROWS, [(250, 250, 250), (200, 180, 140)]):
        (root / "images" / f"{row['id']}.jpg").write_bytes(make_jpeg(color=color, size=(24, 32)))
    monkeypatch.setitem(wardrobe_api.SEED_SETS, "shop", (root, "build_seed_shop.py"))
    return root


def test_seed_set_shop_keeps_product_fields(wclient, shop_set):
    res = wclient.post("/api/wardrobe/seed-demo?set=shop")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["set"] == "shop" and body["added_ids"] == ["shop-ao-thun-trang", "shop-quan-short-be"]
    items = {it["id"]: it for it in body["items"]}
    shirt = items["shop-ao-thun-trang"]
    assert shirt["brand"] == "YaMe" and shirt["product_url"] == "https://yame.vn/products/ao-thun"
    assert shirt["product_code"] == "0024688" and shirt["price_vnd"] == 212550 and shirt["demo"] is True
    assert "image_source_url" not in shirt and shirt["image_url"] == "/wardrobe-media/images/shop-ao-thun-trang.jpg"
    shorts = items["shop-quan-short-be"]
    assert "product_url" not in shorts and "price_vnd" not in shorts  # unsafe URL / bad price dropped
    # editing keeps the product fields
    edited = wclient.put("/api/wardrobe/items/shop-ao-thun-trang", json={"name": "Áo thun yêu thích"}).json()
    assert edited["brand"] == "YaMe" and edited["product_url"].startswith("https://yame.vn/")
    assert wclient.post("/api/wardrobe/seed-demo?set=shop").json()["added"] == 0


def test_seed_set_default_and_env(wclient, shop_set, monkeypatch):
    first = wclient.post("/api/wardrobe/seed-demo").json()
    assert first["set"] == "default" and first["added"] == 10
    assert not any(it.get("brand") for it in first["items"])
    monkeypatch.setenv("SHADOW_DEMO_SEED", "shop")
    second = wclient.post("/api/wardrobe/seed-demo").json()
    assert second["set"] == "shop" and second["added"] == 2
    # explicit param wins over the env var
    assert wclient.post("/api/wardrobe/seed-demo?set=default&replace=true").json()["set"] == "default"
    assert wclient.post("/api/wardrobe/seed-demo?set=uniqlo").status_code == 422


def test_seed_set_missing_images_says_how_to_fix(wclient, shop_set):
    (shop_set / "images" / "shop-quan-short-be.jpg").unlink()
    partial = wclient.post("/api/wardrobe/seed-demo?set=shop").json()
    assert partial["added_ids"] == ["shop-ao-thun-trang"] and partial["missing_images"] == ["shop-quan-short-be"]
    assert "build_seed_shop.py" in partial["warning"]
    for path in (shop_set / "images").glob("*.jpg"):
        path.unlink()
    res = wclient.post("/api/wardrobe/seed-demo?set=shop&replace=true")
    assert res.status_code == 503
    assert "chưa có ảnh" in res.json()["detail"] and "build_seed_shop.py" in res.json()["detail"]
    # nothing deleted when the set cannot be loaded
    assert [it["id"] for it in wclient.get("/api/wardrobe/items").json()] == ["shop-ao-thun-trang"]


def test_real_shop_seed_files_are_consistent():
    rows = json.loads((SHOP_DIR / "items.json").read_text(encoding="utf-8"))
    sources = (SHOP_DIR / "SOURCES.md").read_text(encoding="utf-8")
    assert 36 <= len(rows) <= 50 and len({row["id"] for row in rows}) == len(rows)
    from garment_attrs import ATTR_FIELDS, ATTRS_VERSION, normalize_attr_fields

    for row in rows:
        assert row["id"] in sources and row["image_source_url"] in sources
        assert row["brand"] and row["product_url"].startswith("https://yame.vn/products/")
        assert isinstance(row["price_vnd"], int) and row["description_en"]
        assert row["category"] in ("top", "bottom", "outerwear", "shoes", "accessory")
        assert row["attrs_v"] == ATTRS_VERSION and all(k in row for k in ATTR_FIELDS)
        clean = normalize_attr_fields(row)
        assert all(clean.get(k) == row[k] for k in ATTR_FIELDS if row[k] is not None)
    cats = [row["category"] for row in rows]
    assert cats.count("top") >= 12 and cats.count("bottom") >= 8 and cats.count("shoes") >= 4
    assert sum(1 for row in rows if row["category"] == "bottom" and row["length"] == "short") >= 4
    assert {"uv_jacket", "windbreaker", "overshirt", "sneakers", "slides", "loafers", "cap", "belt"} <= \
        {row["subtype"] for row in rows}


def test_default_seed_has_attributes():
    from garment_attrs import ATTR_FIELDS

    rows = json.loads((SHOP_DIR.parent / "wardrobe_seed" / "items.json").read_text(encoding="utf-8"))
    assert all(all(k in row for k in ATTR_FIELDS) for row in rows)

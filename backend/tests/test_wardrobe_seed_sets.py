"""Offline seed asset integrity; seeding is a manual CLI operation."""
import json
from pathlib import Path

SHOP_DIR = Path(__file__).resolve().parents[1] / 'wardrobe_seed_shop'

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

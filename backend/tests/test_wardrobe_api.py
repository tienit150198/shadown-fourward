from __future__ import annotations

from wardrobe_testlib import seed_test_wardrobe

import json
from collections import Counter
from pathlib import Path

from wardrobe_testlib import FakeProvider, data_url, make_jpeg, wardrobe_dir, wclient  # noqa: F401

SEED_DIR = Path(__file__).resolve().parent.parent / "wardrobe_seed"


def test_items_crud_mock_mode(wclient, wardrobe_dir):
    assert wclient.get("/api/wardrobe/items").json() == []
    response = wclient.post("/api/wardrobe/items", json={"image": data_url(make_jpeg(color=(200, 30, 40)))})
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["tag_source"] == "mock" and created["colors"] == ["đỏ"] and created["category"] == "top"
    assert created["image_url"] == f"/wardrobe-media/images/{created['id']}.jpg"
    image = wclient.get(created["image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"

    named = wclient.post("/api/wardrobe/items", json={
        "image": data_url(make_jpeg(fmt="PNG", mode="RGBA"), "image/png"), "name": "Váy của chị", "category": "dress",
    }).json()
    assert named["name"] == "Váy của chị" and named["category"] == "dress"
    assert (wardrobe_dir / named["image"]).read_bytes()[:3] == b"\xff\xd8\xff"  # PNG stored as JPEG

    updated = wclient.put(f"/api/wardrobe/items/{created['id']}", json={"name": "Áo đỏ yêu thích", "warmth": 7})
    assert updated.status_code == 200 and updated.json()["warmth"] == 5
    assert wclient.put(f"/api/wardrobe/items/{created['id']}", json={"category": "spaceship"}).status_code == 422
    assert wclient.put("/api/wardrobe/items/ghost", json={"name": "x"}).status_code == 404
    assert wclient.get(f"/api/wardrobe/items/{created['id']}").json()["name"] == "Áo đỏ yêu thích"

    assert wclient.delete(f"/api/wardrobe/items/{created['id']}").json() == {"ok": True}
    assert wclient.delete(f"/api/wardrobe/items/{created['id']}").status_code == 404
    assert [row["id"] for row in wclient.get("/api/wardrobe/items").json()] == [named["id"]]


def test_create_item_rejects_bad_images(wclient):
    for image in ("not an image", "data:image/gif;base64,R0lGODlh", "data:image/jpeg;base64,bm9wZQ=="):
        assert wclient.post("/api/wardrobe/items", json={"image": image}).status_code == 422
    assert wclient.post("/api/wardrobe/items", json={}).status_code == 422


def test_create_item_with_ai_tagging(wclient):
    provider = FakeProvider({
        "name": "Chân váy jean", "category": "chân váy", "colors": ["xanh dương"], "warmth": 2,
        "occasions": ["đi học"], "description_en": "blue denim skirt",
    })
    wclient.app.state.provider = provider
    created = wclient.post("/api/wardrobe/items", json={"image": data_url(make_jpeg())}).json()
    assert created["tag_source"] == "ai" and created["category"] == "bottom"
    assert created["name"] == "Chân váy jean" and created["occasions"] == ["đi học"]
    assert provider.calls[0]["model"] == "google/gemini-2.5-flash"  # VISION_MODEL from conftest


def test_seeding_is_not_an_http_api(wclient):
    assert '/api/wardrobe/seed-demo' not in wclient.get('/openapi.json').json()['paths']
    assert wclient.post('/api/wardrobe/seed-demo').status_code in (404, 405)


def test_seed_files_are_consistent():
    rows = json.loads((SEED_DIR / "items.json").read_text(encoding="utf-8"))
    sources = (SEED_DIR / "SOURCES.md").read_text(encoding="utf-8")
    for row in rows:
        path = SEED_DIR / "images" / f"{row['id']}.jpg"
        assert path.read_bytes()[:3] == b"\xff\xd8\xff"
        assert row["id"] in sources


def test_rules_routes(wclient):
    rule = wclient.post("/api/wardrobe/rules", json={"text": "Mưa thì đừng đi giày trắng", "taught_by": "Mẹ"}).json()
    assert rule["taught_by"] == "Mẹ" and rule["id"].startswith("r_")
    assert wclient.post("/api/wardrobe/rules", json={"text": "  "}).status_code == 422
    assert wclient.get("/api/wardrobe/rules").json() == [rule]
    assert wclient.delete(f"/api/wardrobe/rules/{rule['id']}").json() == {"ok": True}
    assert wclient.delete(f"/api/wardrobe/rules/{rule['id']}").status_code == 404


def test_media_route_is_path_safe(wclient):
    seed_test_wardrobe()
    assert wclient.get("/wardrobe-media/items.json").status_code == 404
    assert wclient.get("/wardrobe-media/../main.py").status_code == 404
    assert wclient.get("/wardrobe-media/images/%2e%2e/items.json").status_code == 404
    assert wclient.get("/wardrobe-media/images/nope.jpg").status_code == 404
    assert wclient.get("/wardrobe-media/images/demo-quan-au-den.jpg").status_code == 200

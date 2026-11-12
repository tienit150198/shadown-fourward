from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest
from PIL import Image

import wardrobe_seed_comprehensive.seed as seed
from wardrobe_store import CATEGORIES, WardrobeStore


def jpeg_bytes(color: tuple[int, int, int] = (40, 90, 160), *, size: tuple[int, int] = (320, 420)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG", quality=90)
    return out.getvalue()


def test_manifest_has_real_sources_metadata_and_category_coverage() -> None:
    rows = seed.load_manifest()
    assert 30 <= len(rows) <= 50
    assert {row["category"] for row in rows} == set(CATEGORIES)
    assert {row["presentation"] for row in rows} == {"menswear", "womenswear", "unisex"}
    assert len({row["source"]["image_url"] for row in rows}) == len(rows)
    assert all(row["source"]["source_url"] and row["source"]["license_status"] for row in rows)
    assert all(row.get("colors") and row.get("occasions") and row.get("seasons") for row in rows)
    assert all("placeholder" not in row["source"]["provenance"].lower() for row in rows)

    dress = [row for row in rows if row["category"] == "dress"]
    assert len(dress) >= 1
    assert "04_dress.png" in dress[0]["source"]["image_url"]


def test_fetch_image_uses_public_read_only_request(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class Response:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int) -> bytes:
            seen["read_size"] = size
            return b"data"

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(seed.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(seed, "_throttle", lambda: None)
    assert seed.fetch_image("https://cdn.shopify.com/example.jpg") == b"data"
    request = seen["request"]
    assert request.get_header("User-agent") == seed.USER_AGENT
    assert seen["timeout"] == seed.REQUEST_TIMEOUT_S
    assert seen["read_size"] == seed.MAX_DOWNLOAD_BYTES + 1


def test_seed_is_idempotent_and_preserves_manifest_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = copy.deepcopy(seed.load_manifest()[0])
    image_dir = tmp_path / "seed-images"
    store = WardrobeStore(tmp_path / "wardrobe")
    calls: list[str] = []

    def fake_fetch(url: str) -> bytes:
        calls.append(url)
        return jpeg_bytes((220, 220, 220))

    monkeypatch.setattr(seed, "fetch_image", fake_fetch)
    first = seed.seed_into_store(store, image_dir=image_dir, rows=[row])
    assert first["added"] == [row["id"]]
    assert len(calls) == 1
    saved = store.get_item(row["id"])
    assert saved["name"] == row["name"]
    assert saved["category"] == row["category"]
    assert saved["image_source_url"] == row["image_source_url"]
    assert saved["source_license_status"] == row["source"]["license_status"]
    assert saved["presentation"] == row["presentation"]
    assert saved["style_notes"] == row["style_notes"]
    before = store.items_path.read_bytes()

    second = seed.seed_into_store(store, image_dir=image_dir, rows=[row])
    assert second["added"] == []
    assert second["skipped"] == [row["id"]]
    assert len(calls) == 1
    assert store.items_path.read_bytes() == before


def test_corrupt_download_fails_before_wardrobe_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = copy.deepcopy(seed.load_manifest()[0])
    image_dir = tmp_path / "seed-images"
    store = WardrobeStore(tmp_path / "wardrobe")
    monkeypatch.setattr(seed, "fetch_image", lambda _url: b"not an image")

    with pytest.raises(seed.SeedError, match="failed JPEG/quality validation"):
        seed.seed_into_store(store, image_dir=image_dir, rows=[row])

    assert store.has_items((row["id"],)) == set()
    assert not (image_dir / f"{row['id']}.jpg").exists()
    assert not store.items_path.exists()


def test_preexisting_user_item_with_seed_id_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    row = copy.deepcopy(seed.load_manifest()[0])
    store = WardrobeStore(tmp_path / "wardrobe")
    store.create_item(
        {"name": "My edited item", "category": row["category"], "colors": ["purple"]},
        jpeg_bytes((100, 30, 140)),
        item_id=row["id"],
        extra={"custom_user_value": "keep"},
    )
    before = store.items_path.read_bytes()
    monkeypatch.setattr(seed, "fetch_image", lambda _url: pytest.fail("existing items must not download photos"))

    result = seed.seed_into_store(store, image_dir=tmp_path / "seed-images", rows=[row])
    assert result["added"] == []
    assert result["skipped"] == [row["id"]]
    assert store.get_item(row["id"])["name"] == "My edited item"
    assert store.get_item(row["id"])["custom_user_value"] == "keep"
    assert store.items_path.read_bytes() == before


def test_cached_asset_is_validated_and_not_replaced(tmp_path: Path) -> None:
    row = copy.deepcopy(seed.load_manifest()[0])
    image_dir = tmp_path / "seed-images"
    image_dir.mkdir()
    target = image_dir / f"{row['id']}.jpg"
    original = jpeg_bytes()
    target.write_bytes(original)

    assert seed.ensure_asset(row, image_dir=image_dir).startswith(b"\xff\xd8\xff")
    assert target.read_bytes() == original

    target.write_bytes(b"broken")
    with pytest.raises(seed.SeedError):
        seed.ensure_asset(row, image_dir=image_dir)
    assert target.read_bytes() == b"broken"

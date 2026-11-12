"""Seed a broad, photo-based demo wardrobe without replacing existing items.

Examples (from the repository root)::

    python backend/wardrobe_seed_comprehensive/build_seed.py --dry-run
    python backend/wardrobe_seed_comprehensive/build_seed.py --download-only
    python backend/wardrobe_seed_comprehensive/build_seed.py --seed

The script uses public image URLs recorded in items.json and reuses the six real
photo assets from backend/wardrobe_seed. It never calls an AI service. Downloads are
cached in this package's ignored images/ directory and normalized to JPEG before use.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from wardrobe_store import CATEGORIES, WardrobeError, WardrobeStore, is_safe_id, to_jpeg, wardrobe_root

MANIFEST_PATH = HERE / "items.json"
IMAGES_DIR = HERE / "images"
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
MIN_IMAGE_SIDE = 160
REQUEST_TIMEOUT_S = 30
REQUEST_GAP_S = 1.05
MAX_ATTEMPTS = 3
USER_AGENT = "Mozilla/5.0 (compatible; SHADOW wardrobe demo seed/1.0)"
ALLOWED_IMAGE_HOSTS = {"cdn.shopify.com", "huggingface.co"}
PRESENTATIONS = {"menswear", "womenswear", "unisex"}
ITEM_FIELD_KEYS = {
    "name", "category", "colors", "style", "warmth", "occasions", "seasons", "pattern", "material",
    "notes", "description_en", "color_hex", "color_family", "attrs_v", "formality", "fit", "sleeve",
    "length", "fabric", "breathability", "rain_ok", "subtype", "uv_protect",
}

_last_request_at = 0.0


class SeedError(RuntimeError):
    """Invalid manifest or unusable source photo."""


def load_manifest(path: Path = MANIFEST_PATH) -> list[dict[str, Any]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SeedError(f"Cannot read seed manifest {path}: {error}") from error
    if not isinstance(rows, list) or not 30 <= len(rows) <= 50:
        raise SeedError("Manifest must contain between 30 and 50 wardrobe items.")

    ids: set[str] = set()
    image_urls: set[str] = set()
    categories: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SeedError("Every manifest row must be an object.")
        item_id = row.get("id")
        fields = {key: row[key] for key in ITEM_FIELD_KEYS if key in row}
        source = row.get("source")
        if not is_safe_id(item_id) or item_id in ids:
            raise SeedError(f"Manifest has an unsafe or duplicate item id: {item_id!r}.")
        ids.add(item_id)
        if not isinstance(fields, dict) or not fields.get("name"):
            raise SeedError(f"{item_id}: missing item fields or name.")
        if fields.get("category") not in CATEGORIES:
            raise SeedError(f"{item_id}: unsupported category {fields.get('category')!r}.")
        categories.add(fields["category"])
        if not fields.get("colors") or not fields.get("occasions") or not fields.get("seasons"):
            raise SeedError(f"{item_id}: colors, occasions, and seasons are required.")
        if not isinstance(source, dict) or not source.get("source_url") or not source.get("image_url"):
            raise SeedError(f"{item_id}: source and image URLs are required.")
        if row.get("image_source_url") != source["image_url"] or row.get("source_url") != source["source_url"]:
            raise SeedError(f"{item_id}: flattened source URLs do not match source metadata.")
        if not source.get("license_status") or not source.get("provenance"):
            raise SeedError(f"{item_id}: license status and provenance are required.")
        image_url = source["image_url"]
        if image_url in image_urls:
            raise SeedError(f"Manifest repeats an image URL: {image_url}.")
        image_urls.add(image_url)
        if row.get("presentation") not in PRESENTATIONS or not row.get("style_notes"):
            raise SeedError(f"{item_id}: invalid presentation suggestion metadata.")
        local_source = row.get("local_source")
        if local_source:
            local_path = (HERE / local_source).resolve()
            try:
                local_path.relative_to(BACKEND.resolve())
            except ValueError as error:
                raise SeedError(f"{item_id}: local source must remain inside backend/.") from error
    if categories != set(CATEGORIES):
        missing = ", ".join(sorted(set(CATEGORIES) - categories))
        raise SeedError(f"Manifest does not cover all wardrobe categories: {missing}.")
    return rows


def _throttle() -> None:
    global _last_request_at
    delay = REQUEST_GAP_S - (time.monotonic() - _last_request_at)
    if delay > 0:
        time.sleep(delay)
    _last_request_at = time.monotonic()


def fetch_image(url: str) -> bytes:
    """Fetch a manifest image URL with size limits and a normal read-only request."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_IMAGE_HOSTS:
        raise SeedError(f"Image URL host is not allowed: {url}")
    _throttle()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.5"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise SeedError(f"Image response exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
        raw = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise SeedError(f"Image response exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}")
    return raw


def _validate_and_normalize(raw: bytes, *, item_id: str) -> bytes:
    if not raw or len(raw) > MAX_DOWNLOAD_BYTES:
        raise SeedError(f"{item_id}: image is empty or exceeds the download size limit.")
    try:
        jpeg = to_jpeg(raw, max_side=1024, quality=88)
        from PIL import Image

        import io

        with Image.open(io.BytesIO(jpeg)) as image:
            image.verify()
        with Image.open(io.BytesIO(jpeg)) as image:
            width, height = image.size
            if image.format != "JPEG" or min(width, height) < MIN_IMAGE_SIDE:
                raise SeedError(
                    f"{item_id}: photo is not a usable garment image ({width}x{height}; "
                    f"minimum side {MIN_IMAGE_SIDE}px)."
                )
    except SeedError:
        raise
    except (WardrobeError, OSError, ValueError) as error:
        raise SeedError(f"{item_id}: image failed JPEG/quality validation: {error}") from error
    if not jpeg.startswith(b"\xff\xd8\xff"):
        raise SeedError(f"{item_id}: normalized image is not a JPEG.")
    return jpeg


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Public garment assets must remain readable if built by a different deployment user.
        temp.chmod(0o644)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _local_source_path(row: dict[str, Any]) -> Path | None:
    local_source = row.get("local_source")
    if not local_source:
        return None
    path = (HERE / local_source).resolve()
    try:
        path.relative_to(BACKEND.resolve())
    except ValueError as error:
        raise SeedError(f"{row['id']}: local source path escapes backend/.") from error
    return path


def _source_bytes(row: dict[str, Any]) -> bytes:
    path = _local_source_path(row)
    if path is not None:
        try:
            return path.read_bytes()
        except OSError as error:
            raise SeedError(f"{row['id']}: bundled source photo is unavailable at {path}: {error}") from error

    url = row["source"]["image_url"]
    for attempt in range(MAX_ATTEMPTS):
        try:
            return fetch_image(url)
        except SeedError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            if attempt + 1 >= MAX_ATTEMPTS:
                raise SeedError(f"{row['id']}: image download failed after {MAX_ATTEMPTS} attempts: {error}") from error
            time.sleep(1 + attempt * 2)
    raise AssertionError("unreachable")


def ensure_asset(row: dict[str, Any], *, image_dir: Path = IMAGES_DIR) -> bytes:
    """Return a validated cached JPEG, atomically creating it when absent."""
    target = image_dir / f"{row['id']}.jpg"
    if target.exists():
        try:
            return _validate_and_normalize(target.read_bytes(), item_id=row["id"])
        except OSError as error:
            raise SeedError(f"{row['id']}: cannot read cached photo {target}: {error}") from error
    jpeg = _validate_and_normalize(_source_bytes(row), item_id=row["id"])
    _atomic_write(target, jpeg)
    return jpeg


def existing_item_ids(store: WardrobeStore) -> set[str]:
    return store.has_items(row["id"] for row in load_manifest())


def seed_into_store(
    store: WardrobeStore,
    *,
    image_dir: Path = IMAGES_DIR,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Download/validate missing photos, then add missing stable IDs through WardrobeStore."""
    manifest = rows if rows is not None else load_manifest()
    existing = store.has_items(row["id"] for row in manifest)
    pending = [row for row in manifest if row["id"] not in existing]
    if not pending:
        return {"added": [], "skipped": sorted(existing), "count": len(manifest)}

    prepared: dict[str, bytes] = {}
    failures: list[str] = []
    for row in pending:
        try:
            prepared[row["id"]] = ensure_asset(row, image_dir=image_dir)
        except SeedError as error:
            failures.append(str(error))
    if failures:
        raise SeedError("Seed stopped before writing wardrobe items because photos failed validation:\n  " + "\n  ".join(failures))

    added: list[str] = []
    skipped = set(existing)
    for row in pending:
        item_id = row["id"]
        # Re-check between rows so a concurrent seed run never replaces an item.
        if item_id in store.has_items((item_id,)):
            skipped.add(item_id)
            continue
        extra = {
            "seed_set": "comprehensive",
            "presentation": row["presentation"],
            "style_notes": row["style_notes"],
            "source_name": row["source"]["name"],
            "source_url": row["source"]["source_url"],
            "image_source_url": row["source"]["image_url"],
            "source_license_status": row["source"]["license_status"],
            "source_provenance": row["source"]["provenance"],
            "source_usage_note": row["source"].get("usage_note", ""),
        }
        for key in ("brand", "product_name", "product_url", "product_code", "price_vnd"):
            if row.get(key) is not None:
                extra[key] = row[key]
        fields = {key: row[key] for key in ITEM_FIELD_KEYS if key in row}
        store.create_item(fields, prepared[item_id], item_id=item_id, extra=extra)
        added.append(item_id)
    return {"added": added, "skipped": sorted(skipped), "count": len(manifest)}


def _read_ids(root: Path) -> set[str]:
    path = root / "items.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {row["id"] for row in value if isinstance(row, dict) and is_safe_id(row.get("id"))} if isinstance(value, list) else set()


def dry_run(store_root: Path, *, rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing = _read_ids(store_root)
    pending = [row for row in rows if row["id"] not in existing]
    missing_images = [row["id"] for row in pending if not (IMAGES_DIR / f"{row['id']}.jpg").is_file()]
    return {
        "manifest_items": len(rows),
        "already_present": len(rows) - len(pending),
        "would_add": len(pending),
        "photos_to_fetch_or_copy": len(missing_images),
        "store_root": str(store_root),
        "categories": sorted({row["category"] for row in rows}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="show planned adds without downloading or writing")
    modes.add_argument("--download-only", action="store_true", help="fetch/validate/cache photos without touching wardrobe data")
    modes.add_argument("--seed", action="store_true", help="fetch/validate photos and add missing items to WardrobeStore")
    parser.add_argument("--root", type=Path, default=None, help="wardrobe store root (defaults to SHADOW_WARDROBE_DIR or backend/wardrobe)")
    args = parser.parse_args(argv)
    rows = load_manifest()
    root = args.root or wardrobe_root()

    if args.dry_run:
        print(json.dumps(dry_run(root, rows=rows), ensure_ascii=False, indent=2))
        return 0
    if args.download_only:
        successes: list[str] = []
        failures: list[str] = []
        for row in rows:
            try:
                ensure_asset(row)
                successes.append(row["id"])
            except SeedError as error:
                failures.append(str(error))
        print(f"Validated/cached {len(successes)}/{len(rows)} photos in {IMAGES_DIR}")
        if failures:
            print("Failed photos:\n  " + "\n  ".join(failures), file=sys.stderr)
            return 1
        return 0

    result = seed_into_store(WardrobeStore(root), rows=rows)
    print(json.dumps({**result, "store_root": str(root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

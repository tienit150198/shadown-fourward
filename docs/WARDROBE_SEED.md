# Comprehensive wardrobe seed

`backend/wardrobe_seed_comprehensive/` provides a photo-based 48-item wardrobe for demos. Its `items.json` manifest records stable IDs, garment metadata and photo sources for the script. It includes 42 YaMe product photos and six existing real garment photos from the built-in demo seed. It covers all six wardrobe categories: 21 tops, 12 bottoms, one dress, seven outerwear items, five shoes, and two accessories. The seed excludes the four Pillow-drawn placeholder assets. IDs match the existing `shop` and `default` seeds so items already present are skipped instead of duplicated.

The manifest carries the item fields used by `WardrobeStore`, the product or source page, the direct image URL, and a provenance and license note for each photo. Shop items also retain product name, brand, product code, and price metadata. The optional `presentation` field uses `menswear`, `womenswear`, or `unisex`; `style_notes` explains the cue. Both are suggestions for styling only and must never be used to limit who may wear or receive an item.

## Run it

From the repository root on a host with the project requirements installed:

```sh
python backend/wardrobe_seed_comprehensive/build_seed.py --dry-run
python backend/wardrobe_seed_comprehensive/build_seed.py --download-only
python backend/wardrobe_seed_comprehensive/build_seed.py --seed
```

In the deployed Compose container, the same script uses the configured `SHADOW_WARDROBE_DIR`:

```sh
docker compose exec app python /app/backend/wardrobe_seed_comprehensive/build_seed.py --dry-run
docker compose exec app python /app/backend/wardrobe_seed_comprehensive/build_seed.py --seed
```

Use `--root /path/to/wardrobe` to select a different store. `--dry-run` reports existing IDs and missing photos without networking or writing. `--download-only` validates and caches every photo without touching wardrobe data. `--seed` downloads any missing photos, validates the full pending set, then calls `WardrobeStore.create_item` for missing stable IDs. `build_seed.py` with no mode defaults to `--download-only`, without mutating the wardrobe.

Existing items with the same stable ID are skipped, including user-edited items. The script does not delete wardrobe items or overwrite their images. It downloads sequentially, makes at most three attempts for transient network failures, limits response size to 12 MiB, checks that the image decodes, requires a minimum 160-pixel side, and normalizes it to a JPEG up to 1024 pixels. New cache assets are written atomically under `backend/wardrobe_seed_comprehensive/images/`; this directory is ignored by Git. The script makes no AI or paid-service calls.

## Sources and image rights

The 42 YaMe image URLs come from the existing public Shopify product snapshot in `backend/wardrobe_seed_shop/items.json`; product pages and image URLs are retained per item. The existing source record describes these as YaMe product photos, notes that no reusable image license was identified, and restricts the set to internal demo use. See `backend/wardrobe_seed_shop/SOURCES.md`. The script does not claim these photos are openly licensed.

Six previously bundled cloth photos are reused from `backend/wardrobe_seed/images/`: four IDM-VTON examples, a dress photo, and an upper-garment photo. Existing provenance and license caveats are recorded in `backend/wardrobe_seed/SOURCES.md` and copied into the manifest. In particular, that source record does not state image rights for the Kolors Space photos; the Space's Apache-2.0 code license is not represented as an image license here. These remain demo assets, with redistribution rights to be reviewed by the project.

The six local photos are copied into the package cache as needed. If those source files are unavailable, the script stops with a clear error instead of substituting generated art or placeholder drawings. The YaMe downloads are public, read-only image requests with a normal User-Agent and a 1.05-second request gap.

## Manual use only

Seeding is not exposed through any frontend or HTTP API. The legacy demo-seeding endpoint has been removed.
Run the script explicitly when you want to populate the configured wardrobe.
It never runs automatically during startup or deployment.

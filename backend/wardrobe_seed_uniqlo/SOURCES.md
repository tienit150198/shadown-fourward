# UNIQLO VN men's catalogue

Product names, identifiers and selected colors were read from UNIQLO's public Vietnamese and English men's category pages on 2026-11-13. Unisex items listed under men are included. `products.json` records the original product and image URL for every item. Photos remain © UNIQLO / their respective owners and are used as reference material for this requested local try-on demo.

This is a snapshot of accessible listings, **not verified as every men's product or every color**. Some category pages expose only the first 36 products; direct page/API access was unavailable. Do not advertise this snapshot as exhaustive or as live stock/price data.

Import with `.venv/bin/python backend/wardrobe_seed_uniqlo/import_catalog.py --apply`. Existing IDs and user uploads are preserved. Failed image downloads are reported and skipped, never replaced by fabricated images. Re-running retries missing records. Imported runtime images and wardrobe JSON are ignored by Git as before.

Images use the official AsianCommon product-only photographs, rather than Vietnamese model styling photos. This prevents the try-on model from copying unrelated clothes worn by the catalogue model. `--apply --refresh-images` updates only photos owned by this import while preserving user uploads and field edits.

Vietnamese color labels filled from photos are estimates (`colors_source: photo_estimate`). Original UNIQLO color codes and source URLs are retained separately.

Official category sources include:

- https://www.uniqlo.com/vn/vi/men/outerwear
- https://www.uniqlo.com/vn/vi/men/sweaters-and-knitwear
- https://www.uniqlo.com/vn/vi/men/t-shirts-sweat-and-fleece
- https://www.uniqlo.com/vn/en/men/shirts-and-polo-shirts
- https://www.uniqlo.com/vn/en/men/bottoms
- https://www.uniqlo.com/vn/vi/men/bottoms/warm-pants
- https://www.uniqlo.com/vn/vi/men/loungewear-and-homewear
- https://www.uniqlo.com/vn/vi/men/innerwear
- https://www.uniqlo.com/vn/vi/men/accessories
- https://www.uniqlo.com/vn/vi/men/accessories/neck-warmers
- https://www.uniqlo.com/vn/vi/men/accessories/gloves
- https://www.uniqlo.com/vn/vi/men/shirts-and-polo-shirts/casual-shirts
- https://www.uniqlo.com/vn/vi/men/shirts-and-polo-shirts/dress-shirts
- https://www.uniqlo.com/vn/vi/men/bottoms/trousers

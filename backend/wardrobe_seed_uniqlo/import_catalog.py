"""Import the checked public UNIQLO manifest; never overwrite existing clothes.

Run from repository root: .venv/bin/python backend/wardrobe_seed_uniqlo/import_catalog.py --apply
"""
import argparse
import io
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wardrobe_store import WardrobeStore, wardrobe_root, fold_vi, _atomic_write_json
from stylist import dominant_color_vi
from garment_attrs import fill_attrs


def refresh_owned_photo(store, row, jpeg, image_url):
    # Preserve names, edits and all user uploads. Only photos belonging to this
    # explicit catalogue import may be refreshed, after a successful download.
    with store._lock:
        items = store._load_items()
        item = next((item for item in items if item['id'] == row['id']), None)
        if not item or item.get('source') != 'uniqlo-vn-men-public-catalog':
            raise ValueError('Not an owned catalogue item')
        path = store.item_image_path(item)
        if path is None:
            raise ValueError('Missing catalogue image')
        temporary = path.with_suffix('.refresh.tmp')
        temporary.write_bytes(jpeg)
        os.replace(temporary, path)
        item['source_image_url'] = image_url
        _atomic_write_json(store.items_path, items)


def fields(row, jpeg=None):
    text = fold_vi(row['name'])
    winter = any(re.search(r'\b' + re.escape(word) + r'\b', text) for word in ('len', 'long vu', 'long cuu', 'giu am', 'giu nhiet', 'heattech', 'cashmere', 'merino', 'wool', 'down', 'fleece', 'pufftech', 'flannel', 'vai da', 'corduroy', 'ni', 'sweat'))
    color = dominant_color_vi(jpeg) if jpeg else None
    return {'name': ('UNIQLO ' + row['name'] + ' · ' + row['color_code'])[:80], 'category': row['category'],
            'warmth': 4 if winter else 2, 'seasons': ['autumn', 'winter'] if winter else ['spring', 'summer', 'autumn'],
            'style': 'casual', 'occasions': ['daily'], **({'colors': [color]} if color else {})}


def backfill_photo_colors(store):
    with store._lock:
        items = store._load_items()
        changed = 0
        for item in items:
            if item.get('source') != 'uniqlo-vn-men-public-catalog' or item.get('colors') or '/AsianCommon/' not in item.get('source_image_url', ''):
                continue
            path = store.item_image_path(item)
            if path is None:
                continue
            jpeg = path.read_bytes()
            color = dominant_color_vi(jpeg)
            if color:
                item['colors'] = [color]
                item['colors_source'] = 'photo_estimate'
                item.pop('color_hex', None)
                item.pop('color_family', None)
                item.update(fill_attrs(item, jpeg))
                changed += 1
        if changed:
            _atomic_write_json(store.items_path, items)
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--refresh-images', action='store_true', help='Refresh only photos owned by this catalogue; preserve user uploads and field edits')
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('products.json').read_text())
    store = WardrobeStore(wardrobe_root())
    existing = {item['id']: item for item in store.list_items()}
    pending = [row for row in manifest['products'] if row['id'] not in existing or (args.refresh_images and existing[row['id']].get('source') == 'uniqlo-vn-men-public-catalog' and existing[row['id']].get('source_image_url') != row['image_url'])]
    print(json.dumps({'manifest': len(manifest['products']), 'existing': len(existing), 'pending': len(pending), 'apply': args.apply}), flush=True)
    if not args.apply:
        return
    imported, failed = [], []
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for row in pending:
            try:
                if urlparse(row['image_url']).hostname != 'image.uniqlo.com' or urlparse(row['product_url']).hostname != 'www.uniqlo.com':
                    raise ValueError('Unexpected source host')
                image_url = row['image_url']
                response = client.get(image_url)
                response.raise_for_status()
                if len(response.content) > 8 * 1024 * 1024:
                    raise ValueError('Image too large')
                with Image.open(io.BytesIO(response.content)) as original:
                    image = ImageOps.exif_transpose(original).convert('RGB')
                    image.thumbnail((900, 1200))
                    output = io.BytesIO()
                    image.save(output, 'JPEG', quality=90)
                if row['id'] in existing:
                    refresh_owned_photo(store, row, output.getvalue(), image_url)
                else:
                    store.create_item(fields(row, output.getvalue()), output.getvalue(), item_id=row['id'], extra={
                    'brand': 'UNIQLO', 'product_url': row['product_url'], 'source_image_url': image_url,
                    'source': 'uniqlo-vn-men-public-catalog', 'source_captured_at': manifest['captured_at'],
                    'product_code': row['product_code'], 'color_code': row['color_code'], 'gender': row['gender'], 'colors_source': 'photo_estimate',
                    })
                imported.append(row['id'])
            except (httpx.HTTPError, OSError, ValueError) as error:
                failed.append({'id': row['id'], 'error': type(error).__name__})
            if (len(imported) + len(failed)) % 20 == 0:
                print(json.dumps({'imported': len(imported), 'failed': len(failed)}), flush=True)
            time.sleep(.4)
    colors_filled = backfill_photo_colors(store)
    report = {'processed_this_run': len(imported), 'total_uniqlo': sum(item.get('brand') == 'UNIQLO' for item in store.list_items()), 'colors_filled': colors_filled, 'failed': failed, 'categories': dict(Counter(item['category'] for item in store.list_items() if item.get('brand') == 'UNIQLO')), 'complete_catalogue_verified': False}
    Path(__file__).with_name('import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

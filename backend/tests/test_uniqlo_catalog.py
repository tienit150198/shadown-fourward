import io
import json
from pathlib import Path

import pytest
from PIL import Image

from wardrobe_seed_uniqlo.import_catalog import refresh_owned_photo
from wardrobe_store import WardrobeStore


def jpeg(color):
    output = io.BytesIO()
    Image.new('RGB', (20, 20), color).save(output, 'JPEG')
    return output.getvalue()


def test_refresh_only_changes_owned_photos_and_preserves_field_edits(tmp_path):
    store = WardrobeStore(tmp_path)
    store.create_item({'name': 'Tên do người dùng sửa', 'category': 'top'}, jpeg('red'), item_id='owned', extra={'source': 'uniqlo-vn-men-public-catalog'})
    store.create_item({'name': 'Ảnh riêng', 'category': 'top'}, jpeg('blue'), item_id='upload')
    refresh_owned_photo(store, {'id': 'owned'}, jpeg('green'), 'https://image.uniqlo.com/reference.jpg')
    assert store.get_item('owned')['name'] == 'Tên do người dùng sửa'
    assert store.get_item('owned')['source_image_url'].endswith('reference.jpg')
    path = store.item_image_path(store.get_item('upload'))
    original = path.read_bytes()
    with pytest.raises(ValueError):
        refresh_owned_photo(store, {'id': 'upload'}, jpeg('green'), 'https://image.uniqlo.com/reference.jpg')
    assert path.read_bytes() == original


def test_catalogue_has_distinct_ids_official_sources_and_separate_garment_photos():
    manifest = json.loads((Path(__file__).parents[1] / 'wardrobe_seed_uniqlo/products.json').read_text())
    rows = manifest['products']
    assert len({row['id'] for row in rows}) == len(rows)
    assert {'top', 'bottom', 'outerwear', 'accessory'} <= {row['category'] for row in rows}
    assert all(row['product_url'].startswith('https://www.uniqlo.com/vn/') for row in rows)
    assert all('/AsianCommon/imagesgoods/' in row['image_url'] for row in rows)
    assert manifest['complete'] is False

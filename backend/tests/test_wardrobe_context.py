from types import SimpleNamespace

from wardrobe_context import balanced_items, inventory_context
from stylist import _compact_wardrobe


def test_large_import_cannot_hide_trousers_from_voice_or_stylist():
    items = [{'id': str(i), 'category': 'top', 'name': 'Áo len'} for i in range(100)]
    items += [{'id': 'pants', 'category': 'bottom', 'name': 'Quần xanh'}, {'id': 'coat', 'category': 'outerwear', 'name': 'Áo khoác'}]
    store = SimpleNamespace(list_items=lambda: items, list_rules=lambda: [])
    context = inventory_context(store, 'Tủ đồ của tôi có những gì?')
    assert {row['category'] for row in context['items']} == {'top', 'bottom', 'outerwear'}
    assert '"id": "pants"' in _compact_wardrobe(items)
    assert len(balanced_items(items, 10)) == 10

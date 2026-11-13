"""Bounded, searchable inventory context shared by voice and text chat."""
import re
from collections import Counter

from wardrobe_store import fold_vi


def balanced_items(items: list[dict], limit: int) -> list[dict]:
    """Keep every garment category represented when the catalogue exceeds context."""
    from collections import deque
    groups = {}
    for item in items:
        groups.setdefault(item.get('category', 'other'), deque()).append(item)
    result = []
    while len(result) < limit and any(groups.values()):
        for group in groups.values():
            if group and len(result) < limit:
                result.append(group.popleft())
    return result


def inventory_context(store, text: str) -> dict:
    items = store.list_items()
    # Include matching records beyond the first page. Generic requests get a
    # spread across categories rather than only whichever category was imported first.
    stop = set('toi minh em anh chi muon co nhung gi trong tu do cua he thong shadow cho xem lay chon mac thu voi va mot cai chiec nao khong'.split())
    query = set(re.findall(r'[a-z0-9]+', fold_vi(text))) - stop
    def score(item):
        words = set(re.findall(r'[a-z0-9]+', fold_vi(' '.join(str(item.get(k, '')) for k in ('id', 'name', 'category', 'brand', 'colors', 'seasons')))))
        return len(query & words)
    groups = {}
    for item in items:
        groups.setdefault(score(item), []).append(item)
    ranked = []
    for weight in sorted(groups, reverse=True):
        ranked.extend(balanced_items(groups[weight], max(0, 48 - len(ranked))))
    keys = ('id', 'name', 'category', 'colors', 'warmth', 'occasions', 'seasons', 'brand')
    return {
        'source': 'SHADOW_SYSTEM_WARDROBE',
        'meaning': 'Tủ đồ trong ứng dụng SHADOW, không phải tủ vật lý trước camera. Dữ liệu bên dưới lấy từ hệ thống.',
        'total_items': len(items), 'category_counts': dict(Counter(i.get('category', 'other') for i in items)),
        'items': [{k: item[k] for k in keys if k in item} for item in ranked],
        'items_are_excerpt': len(items) > 48,
        'lookup': 'Để tìm món khác, gọi classify_request với yêu cầu có tên, màu hoặc loại đồ cần tìm. Không kết luận không có chỉ vì món đó không nằm trong phần trích.',
        'rules': [{k: row[k] for k in ('text', 'taught_by') if k in row} for row in store.list_rules()[:12]],
    }

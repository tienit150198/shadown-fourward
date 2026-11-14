"""Catalog audience filters, based only on explicit preferences and item metadata."""
from typing import Literal

from wardrobe_store import fold_vi

CatalogGender = Literal['men', 'women', 'unisex', 'all']


def catalog_gender(value):
    return {
        'men': 'men', 'man': 'men', 'male': 'men', 'nam': 'men',
        'women': 'women', 'woman': 'women', 'female': 'women', 'nu': 'women',
        'unisex': 'unisex', 'all': 'all',
    }.get(fold_vi(value))


def wardrobe_gender(context, requested=None):
    """An explicit clothing choice overrides self-described gender, across follow-ups."""
    if requested is not None:
        context['wardrobe_gender'] = requested
    return context.get('wardrobe_gender') or catalog_gender(context.get('style_profile', {}).get('gender'))


def matches_gender(item, gender):
    if gender in (None, 'all'):
        return True
    return catalog_gender(item.get('gender')) in {gender, 'unisex'}


def gender_error(items, gender):
    invalid = [item['id'] for item in items if not matches_gender(item, gender)]
    if not invalid:
        return None
    return {
        'error': 'Items do not match the current clothing audience filter. Read list_wardrobe again. Change gender only if the user requests another clothing group; all removes the filter. Never relabel unknown items as matching.',
        'gender_filter': gender, 'invalid_item_ids': list(dict.fromkeys(invalid)),
    }

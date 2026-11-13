from wardrobe_testlib import seed_test_wardrobe
import json

from wardrobe_testlib import wclient, wardrobe_dir  # noqa: F401


def test_optional_profile_reaches_model_without_filtering_the_inventory(wclient, monkeypatch):
    import wardrobe_api
    captured = {}

    async def suggest(provider, items, **kwargs):
        captured.update(kwargs)
        captured['items'] = items
        return {'outfits': [], 'rules_used': [], 'source': 'ai'}

    monkeypatch.setattr(wardrobe_api, 'suggest_outfits', suggest)
    assert seed_test_wardrobe()['added'] == 10
    response = wclient.post('/api/stylist/suggest', json={
        'event': 'Đi chơi', 'clothing_preferences': 'unisex', 'gender': 'không chia sẻ',
        'age_range': '25-34', 'fit_preferences': 'rộng thoải mái',
    })
    assert response.is_success, response.text
    assert 'unisex' in captured['notes'] and '25-34' in captured['notes']
    assert 'rộng thoải mái' in captured['notes'] and 'không chia sẻ' in captured['notes']
    assert len(captured['items']) == 10
    assert wclient.post('/api/stylist/suggest', json={'event': 'Đi chơi'}).is_success
    assert captured['notes'] == ''
    assert wclient.post('/api/stylist/suggest', json={'age_range': 'x' * 41}).status_code == 422

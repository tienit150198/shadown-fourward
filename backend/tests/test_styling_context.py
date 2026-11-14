import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest

from styling_context import StylingLocation, contextual_request, requested_conditions, requested_date, resolve_weather
from outfit_scoring import weather_profile
from weather import WeatherService, local_today
from wardrobe_testlib import wardrobe_dir, wclient, weather_transport  # noqa: F401


@pytest.mark.parametrize("text, expected", [
    ("Mai đi làm mặc gì?", "2026-11-15"),
    ("Ngày kia đi biển", "2026-11-16"),
    ("Phối một bộ đồ", None),
    ("Thứ hai tuần sau", "2026-11-16"),
    ("Chủ nhật", "2026-11-15"),
    ("Ngày 20 tháng 11", "2026-11-20"),
    ("Ngày 5/1", "2027-01-05"),
    ("Ngày 2026-11-19", "2026-11-19"),
    ("Sau 3 ngày", "2026-11-17"),
])
def test_requested_dates(text, expected):
    assert requested_date(text, today=date(2026, 11, 14)) == (expected, None)


@pytest.mark.parametrize("text", ["ngày 31/2", "hôm khác", "tháng sau", "tuần sau"])
def test_ambiguous_dates_do_not_silently_become_today(text):
    target, error = requested_date(text, today=date(2026, 11, 14))
    assert target is None and error


@pytest.mark.parametrize("text, hot, cold, rain", [
    ("Phối đồ mùa hè", True, False, False),
    ("Không phải mùa hè mà mùa đông", False, True, False),
    ("Nếu trời lạnh khoảng 15 độ C", False, True, False),
    ("Trời 32°C và có mưa", True, False, True),
    ("Mùa hè nhưng không mưa", True, False, False),
    ("Phối đồ mùa mưa", False, False, True),
])
def test_requested_conditions_override_gps_without_fabricated_temperatures(text, hot, cold, rain):
    async def must_not_fetch(**kwargs):
        pytest.fail("An explicit scenario must not fetch today's weather")
    result = asyncio.run(resolve_weather(SimpleNamespace(get=must_not_fetch), text,
                                        location=StylingLocation(lat=10.8, lon=106.6)))
    assert result["source"] == "requested" and result["is_forecast"] is False
    profile = weather_profile(result)
    assert (profile["hot"], profile["cold"], profile["rain"]) == (hot, cold, rain)
    if "mùa" in text.lower():
        assert result["t_min"] is None and result["t_max"] is None
        assert "°C" not in profile["temp_text"]


def test_weather_question_is_a_forecast_not_a_rain_scenario():
    service = WeatherService(transport=weather_transport())
    result = asyncio.run(resolve_weather(service, "Ngày mai có mưa không?",
        location=StylingLocation(lat=10.8, lon=106.6), allow_scenario=False))
    assert result["source"] == "forecast"
    assert result["date"] == (local_today() + timedelta(days=1)).isoformat()


def test_gps_not_default_city_and_date_used_for_forecast():
    calls = []
    async def forecast(**kwargs):
        calls.append(kwargs)
        return {"available": True, "summary_vi": "Vị trí của bạn, 25–32°C"}
    result = asyncio.run(resolve_weather(SimpleNamespace(get=forecast), "Ngày mai đi làm",
        location=StylingLocation(lat=10.8, lon=106.6), date=local_today().isoformat()))
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    assert calls == [{"lat": 10.8, "lon": 106.6, "date": tomorrow}]
    assert result["date"] == tomorrow and result["source"] == "forecast"


def test_no_location_and_far_future_remain_unknown_and_never_call_network():
    async def must_not_fetch(**kwargs):
        pytest.fail("Missing GPS/out-of-range date must not fall back to Hanoi/today")
    service = SimpleNamespace(get=must_not_fetch)
    no_gps = asyncio.run(resolve_weather(service, "Hôm nay mặc gì?"))
    assert no_gps["needs_location"] and not no_gps["available"]
    far = (local_today() + timedelta(days=90)).isoformat()
    future = asyncio.run(resolve_weather(service, f"Phối đồ ngày {far}", location=StylingLocation(lat=0, lon=0)))
    assert not future["available"] and future["date"] == far
    assert "Chưa có dự báo" in future["summary_vi"]


def test_weather_timeout_does_not_invent_conditions():
    async def fail(**kwargs):
        raise httpx.ConnectError("offline")
    result = asyncio.run(resolve_weather(SimpleNamespace(get=fail), "Mặc gì hôm nay",
        location=StylingLocation(lat=10.8, lon=106.6)))
    assert not result["available"] and not result["is_forecast"]
    assert "t_max" not in result


def test_short_followup_retains_occasion_but_replaces_old_season():
    text = contextual_request("Còn ngày mai?", [{"role": "user", "text": "Phối đồ đi làm mùa hè"}])
    assert "đi làm" in text
    result = asyncio.run(resolve_weather(WeatherService(transport=weather_transport(t_min=10, t_max=15)), text,
        location=StylingLocation(lat=21, lon=105)))
    assert result["source"] == "forecast" and weather_profile(result)["cold"]


def test_stylist_and_chat_share_summer_override_and_real_wardrobe_ids(wclient):
    assert wclient.post("/api/wardrobe/seed-demo").status_code == 200
    result = wclient.post("/api/stylist/suggest", json={
        "event": "Đi chơi", "notes": "Phối đồ mùa hè", "lat": 21, "lon": 105,
    }).json()
    assert result["weather"]["source"] == "requested"
    assert result["outfits"]
    for outfit in result["outfits"]:
        assert outfit["items"]
        assert not any(v["code"] == "too_hot" for v in outfit["violations"])
    response = wclient.post("/api/conversation", json={"text": "Phối đồ mùa hè", "location": {"lat": 21, "lon": 105}})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert "mùa hè (không phải dự báo)" in events[0]["text"]
    assert events[-1]["intent"] == "outfit_suggest"
    followup = wclient.post("/api/conversation", json={"text": "Còn ngày mai?", "location": {"lat": 21, "lon": 105},
        "history": [{"role": "user", "text": "Phối đồ đi làm mùa hè"}, {"role": "assistant", "text": "Gợi ý áo và quần."}]})
    next_events = [json.loads(line) for line in followup.text.splitlines()]
    assert next_events[-1]["intent"] == "outfit_suggest" and "Dự báo ngày" in next_events[0]["text"]


def test_invalid_gps_rejected_at_all_entry_points(wclient):
    for path in ("/api/conversation", "/api/live/classify"):
        assert wclient.post(path, json={"text": "Phối đồ", "location": {"lat": 100, "lon": 105}}).status_code == 422
    assert wclient.post("/api/stylist/suggest", json={"event": "Đi làm", "lat": 100, "lon": 105}).status_code == 422

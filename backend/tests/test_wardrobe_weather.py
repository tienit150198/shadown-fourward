from __future__ import annotations

import asyncio
from datetime import timedelta

from wardrobe_testlib import wardrobe_dir, wclient, weather_transport  # noqa: F401

import weather as weather_mod
from weather import WeatherService, summary_vi, wmo_description_vi


def run(coro):
    return asyncio.run(coro)


def test_wmo_and_summary():
    assert wmo_description_vi(61) == "mưa nhẹ"
    assert wmo_description_vi(95) == "dông"
    assert wmo_description_vi(1234) == "không rõ"
    assert summary_vi("Hà Nội", 18.4, 23.6, 60, "mưa nhẹ") == "Hà Nội, 18–24°C, 60% khả năng mưa – mưa nhẹ"


def test_city_forecast_prefers_vietnam_and_caches():
    counter: dict = {}
    service = WeatherService(transport=weather_transport(counter=counter))
    first = run(service.get(city="Hà Nội"))
    assert first["available"] is True
    assert first["city"] == "Hà Nội" and first["lat"] == 21.0245  # VN result, not the US one
    assert first["date"] == weather_mod.local_today().isoformat()
    assert first["t_min"] == 18.0 and first["t_max"] == 24.0 and first["rain_prob"] == 60 and first["code"] == 61
    assert first["summary_vi"] == "Hà Nội, 18–24°C, 60% khả năng mưa – mưa nhẹ"
    second = run(service.get(city="ha noi"))  # same city folded → cache hit
    assert second == first
    assert counter == {"geocoding-api.open-meteo.com": 1, "api.open-meteo.com": 1}


def test_coordinates_and_future_date():
    service = WeatherService(transport=weather_transport(t_min=25, t_max=33, rain=10, code=0))
    target = (weather_mod.local_today() + timedelta(days=3)).isoformat()
    result = run(service.get(lat=10.8, lon=106.6, date=target))
    assert result["available"] and result["date"] == target
    assert result["summary_vi"].startswith("Vị trí của bạn, 25–33°C")


def test_out_of_range_and_bad_inputs_do_not_hit_network():
    counter: dict = {}
    service = WeatherService(transport=weather_transport(counter=counter))
    far = (weather_mod.local_today() + timedelta(days=15)).isoformat()
    past = (weather_mod.local_today() - timedelta(days=1)).isoformat()
    for kwargs in ({"date": far}, {"date": past}, {"date": "13/11/2026"}, {"lat": 100.0, "lon": 0.0}):
        result = run(service.get(**kwargs))
        assert result["available"] is False and result["message"]
    assert counter == {}


def test_network_failure_and_unknown_city_are_graceful():
    service = WeatherService(transport=weather_transport(fail=True))
    result = run(service.get(city="Hà Nội"))
    assert result == {
        "available": False,
        "message": "Không lấy được thời tiết lúc này.",
        "city": "Hà Nội",
        "date": weather_mod.local_today().isoformat(),
    }
    missing = run(WeatherService(transport=weather_transport()).get(city="xyzzz"))
    assert missing["available"] is False and "xyzzz" in missing["message"]


def test_weather_route(wclient):
    response = wclient.get("/api/weather", params={"city": "Hà Nội"})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["city"] == "Hà Nội"
    assert set(body) >= {"city", "date", "t_min", "t_max", "rain_prob", "code", "summary_vi"}

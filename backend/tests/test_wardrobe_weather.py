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
    assert counter == {"wttr.in": 1, "geocoding-api.open-meteo.com": 1, "api.open-meteo.com": 1}
    assert first["provider"] == "open-meteo" and first["fallback"] is True


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


def wttr_payload(target=None):
    return {'nearest_area':[{'areaName':[{'value':'Hanoi'}],'latitude':'21.03','longitude':'105.85'}],
        'weather':[{'date':target or weather_mod.local_today().isoformat(),'mintempC':'18','maxtempC':'23',
                    'hourly':[{'chanceofrain':'75','weatherCode':'353'}]}]}


def test_wttr_is_primary_without_geocoding_and_is_cached():
    import httpx
    requests=[]
    def respond(request):
        requests.append(request)
        assert request.url.host=='wttr.in'
        return httpx.Response(200,json=wttr_payload())
    service=WeatherService(transport=httpx.MockTransport(respond))
    result=run(service.get(city='Hà Nội'))
    assert result['provider']=='wttr.in' and not result['fallback']
    assert result['city']=='Hà Nội'
    assert result['t_min']==18 and result['rain_prob']==75
    assert run(service.get(city='ha noi'))==result
    assert len(requests)==1


def test_wttr_wrong_date_falls_back_instead_of_reusing_today():
    import httpx
    base=weather_transport()
    seen=[]
    def respond(request):
        seen.append(request.url.host)
        if request.url.host=='wttr.in':
            return httpx.Response(200,json=wttr_payload((weather_mod.local_today()-timedelta(days=1)).isoformat()))
        return base.handle_request(request)
    result=run(WeatherService(transport=httpx.MockTransport(respond)).get(city='Hà Nội'))
    assert result['available'] and result['provider']=='open-meteo'
    assert result['date']==weather_mod.local_today().isoformat()
    assert seen==['wttr.in','geocoding-api.open-meteo.com','api.open-meteo.com']


def test_concurrent_weather_reads_share_one_primary_request():
    import httpx
    calls=[]
    async def respond(request):
        calls.append(request)
        await asyncio.sleep(.01)
        return httpx.Response(200,json=wttr_payload())
    async def check():
        service=WeatherService(transport=httpx.MockTransport(respond))
        results=await asyncio.gather(*(service.get(city='Hà Nội') for _ in range(3)))
        assert all(r['available'] for r in results)
    run(check())
    assert len(calls)==1

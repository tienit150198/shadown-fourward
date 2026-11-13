import asyncio
from datetime import timedelta
from urllib.parse import unquote

import httpx
import pytest

from weather import WeatherService, local_today


def primary():
    return {'daily': {'time':[local_today().isoformat()], 'temperature_2m_max':[24], 'temperature_2m_min':[18], 'precipitation_probability_max':[60], 'weather_code':[61]}}


def fallback(*, date=None, probability='80', code='296'):
    return {'nearest_area':[{'areaName':[{'value':'Hà Nội'}], 'latitude':'21.0245','longitude':'105.84117'}],
            'weather':[{'date':date or local_today().isoformat(),'mintempC':'18','maxtempC':'25',
                        'hourly':[{'chanceofrain':probability,'weatherCode':code}]}]}


def make_service(*, fail_geo=False, bad_primary=None, fallback_body=None, fail_fallback=False):
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.host.startswith('geocoding'):
            if fail_geo: raise httpx.ConnectError('offline',request=request)
            return httpx.Response(200,json={'results':[{'name':'Hà Nội','latitude':21.0245,'longitude':105.84117,'country_code':'VN'}]})
        if request.url.host=='api.open-meteo.com':
            return httpx.Response(200,json=bad_primary) if bad_primary is not None else httpx.Response(503)
        assert request.url.host=='wttr.in'
        assert 'fourward.thangqt.com' in request.headers['user-agent']
        assert request.url.params['format']=='j1'
        return httpx.Response(503) if fail_fallback else httpx.Response(200,json=fallback_body or fallback())
    return WeatherService(transport=httpx.MockTransport(handler)),calls


def test_forecast_failure_uses_coordinate_fallback_and_caches_it():
    service,calls=make_service()
    result=asyncio.run(service.get(city='Hà Nội'))
    assert result['available'] and result['provider']=='wttr.in' and result['fallback']
    assert result['t_min']==18 and result['t_max']==25 and result['rain_prob']==80 and result['code']==61
    assert result['lat']==21.0245 and result['lon']==105.84117
    assert calls[-1].url.path=='/21.024,105.841'
    again=asyncio.run(service.get(city='ha noi'))
    assert again==result and len(calls)==3


def test_geocoding_outage_does_not_block_independent_fallback():
    service,calls=make_service(fail_geo=True)
    result=asyncio.run(service.get(city='Hà Nội'))
    assert result['available'] and result['provider']=='wttr.in'
    assert result['lat']==21.0245
    assert unquote(calls[-1].url.path)=='/Hà Nội'
    assert len(calls)==2


@pytest.mark.parametrize('bad', [{}, {'daily':{'time':['2000-01-01'],'temperature_2m_max':[24]}}, {'daily':{'time':[local_today().isoformat()],'temperature_2m_max':['bad']}}])
def test_invalid_primary_payload_also_falls_back(bad):
    service,_=make_service(bad_primary=bad)
    assert asyncio.run(service.get(lat=21,lon=105))['provider']=='wttr.in'


def test_missing_fallback_date_does_not_substitute_today():
    service,_=make_service()
    target=(local_today()+timedelta(days=5)).isoformat()
    result=asyncio.run(service.get(city='Hà Nội',date=target))
    assert not result['available'] and result['date']==target


def test_missing_probability_and_unknown_codes_remain_unknown():
    service,_=make_service(fallback_body=fallback(probability='',code='999'))
    result=asyncio.run(service.get(city='Hà Nội'))
    assert result['available'] and result['rain_prob'] is None and result['code'] is None


def test_both_providers_fail_without_raising():
    service,calls=make_service(fail_geo=True,fail_fallback=True)
    result=asyncio.run(service.get(city='Hà Nội'))
    assert not result['available'] and result['message']
    assert len(calls)==2


def test_healthy_primary_never_calls_fallback():
    service,calls=make_service(bad_primary=primary())
    result=asyncio.run(service.get(city='Hà Nội'))
    assert result['provider']=='open-meteo' and result['fallback'] is False
    assert all(request.url.host!='wttr.in' for request in calls)


def test_primary_timeout_leaves_time_for_fallback():
    async def handler(request):
        if request.url.host=='api.open-meteo.com':
            await asyncio.sleep(.1)
            return httpx.Response(200,json=primary())
        return httpx.Response(200,json=fallback())
    service=WeatherService(transport=httpx.MockTransport(handler),timeout_s=.02)
    assert asyncio.run(service.get(lat=21,lon=105))['provider']=='wttr.in'

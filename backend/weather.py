"""Weather for the wardrobe stylist via Open-Meteo (free, no API key). New in SHADOW.

- Geocoding: https://geocoding-api.open-meteo.com/v1/search?name=..&language=vi (prefers country VN)
- Forecast:  https://api.open-meteo.com/v1/forecast (daily, timezone Asia/Bangkok), today .. +14 days

Never raises on network problems: returns {"available": False, "message": "..."} instead.
Results are cached in memory for 30 minutes. Tests inject an httpx transport.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any

import httpx

from wardrobe_store import fold_vi

logger = logging.getLogger("uvicorn.error")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE_NAME = "Asia/Bangkok"
MAX_DAYS_AHEAD = 14
CACHE_TTL_S = 30 * 60
HTTP_TIMEOUT_S = 8.0

WMO_VI: dict[int, str] = {
    0: "trời quang",
    1: "ít mây",
    2: "có mây",
    3: "nhiều mây",
    45: "sương mù",
    48: "sương mù đóng băng",
    51: "mưa phùn nhẹ",
    53: "mưa phùn",
    55: "mưa phùn dày",
    56: "mưa phùn lạnh",
    57: "mưa phùn lạnh dày",
    61: "mưa nhẹ",
    63: "mưa vừa",
    65: "mưa to",
    66: "mưa lạnh",
    67: "mưa lạnh to",
    71: "tuyết nhẹ",
    73: "tuyết",
    75: "tuyết dày",
    77: "tuyết hạt",
    80: "mưa rào nhẹ",
    81: "mưa rào",
    82: "mưa rào rất to",
    85: "mưa tuyết",
    86: "mưa tuyết to",
    95: "dông",
    96: "dông kèm mưa đá",
    99: "dông kèm mưa đá lớn",
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def wmo_description_vi(code: Any) -> str:
    try:
        return WMO_VI.get(int(code), "không rõ")
    except (TypeError, ValueError):
        return "không rõ"


def local_today() -> date_cls:
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(TIMEZONE_NAME)
    except Exception:  # pragma: no cover - zoneinfo data missing
        tz = timezone(timedelta(hours=7))
    return datetime.now(tz).date()


def parse_target_date(value: Any) -> date_cls:
    """Default today (Asia/Bangkok); raises ValueError for bad format or out of range."""
    today = local_today()
    if value in (None, ""):
        return today
    try:
        target = date_cls.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ValueError("Ngày phải có dạng YYYY-MM-DD.") from None
    if target < today:
        raise ValueError("Chỉ xem được thời tiết từ hôm nay trở đi.")
    if target > today + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError(f"Chỉ dự báo được tối đa {MAX_DAYS_AHEAD} ngày tới.")
    return target


def is_rainy(weather: dict[str, Any] | None) -> bool:
    if not weather or not weather.get("available"):
        return False
    if "rain" in weather.get("conditions", {}):
        return weather["conditions"]["rain"]
    prob = weather.get("rain_prob")
    return (isinstance(prob, (int, float)) and prob >= 50) or weather.get("code") in RAIN_CODES


def summary_vi(city: str, t_min: Any, t_max: Any, rain_prob: Any, description: str) -> str:
    parts = [city or "Vị trí của bạn"]
    if isinstance(t_min, (int, float)) and isinstance(t_max, (int, float)):
        parts.append(f"{round(t_min)}–{round(t_max)}°C")
    if isinstance(rain_prob, (int, float)):
        parts.append(f"{round(rain_prob)}% khả năng mưa")
    text = ", ".join(parts)
    return f"{text} – {description}" if description else text


def unavailable(message: str, **extra: Any) -> dict[str, Any]:
    return {"available": False, "message": message, **extra}


class WeatherService:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        ttl_s: float = CACHE_TTL_S,
        timeout_s: float = HTTP_TIMEOUT_S,
    ) -> None:
        self._transport = transport
        self._ttl = ttl_s
        self._timeout = timeout_s
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    # ---- cache ---------------------------------------------------------------
    def _cache_get(self, key: tuple) -> Any:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def _cache_put(self, key: tuple, value: Any) -> None:
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = (time.monotonic(), value)

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    # ---- geocoding -----------------------------------------------------------
    async def geocode(self, city: str) -> dict[str, Any] | None:
        key = ("geo", fold_vi(city))
        cached = self._cache_get(key)
        if cached is not None:
            return cached or None
        place = None
        for name in dict.fromkeys([city.strip(), fold_vi(city).title()]):
            data = await self._get_json(
                GEOCODING_URL, {"name": name, "count": 10, "language": "vi", "format": "json"}
            )
            results = [r for r in (data or {}).get("results") or [] if isinstance(r, dict)]
            if results:
                vn = [r for r in results if str(r.get("country_code", "")).upper() == "VN"]
                best = (vn or results)[0]
                place = {
                    # Open-Meteo sometimes spells Đ as U+00D0 (Icelandic eth): "Ðà Lạt".
                    "name": str(best.get("name") or city).replace("\u00d0", "Đ").replace("\u00f0", "đ"),
                    "lat": float(best["latitude"]),
                    "lon": float(best["longitude"]),
                    "country_code": best.get("country_code"),
                }
                break
        self._cache_put(key, place or {})
        return place

    # ---- forecast ------------------------------------------------------------
    async def get(
        self,
        *,
        city: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        try:
            target = parse_target_date(date)
        except ValueError as error:
            return unavailable(str(error), date=str(date))
        try:
            if lat is not None and lon is not None:
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    return unavailable("Toạ độ không hợp lệ.", date=target.isoformat())
                place = {"name": (city or "").strip() or "Vị trí của bạn", "lat": lat, "lon": lon}
            else:
                name = (city or "").strip()
                if not name:
                    return unavailable("Chưa có vị trí GPS để lấy thời tiết.", needs_location=True, date=target.isoformat())
                place = await self.geocode(name)
                if place is None:
                    return unavailable(f"Không tìm thấy thành phố “{name}”.", city=name, date=target.isoformat())
            return await self._forecast(place, target)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            logger.warning("weather lookup failed: %r", error)
            return unavailable(
                "Không lấy được thời tiết lúc này.",
                city=(city or "Vị trí của bạn"),
                date=target.isoformat(),
            )

    async def _forecast(self, place: dict[str, Any], target: date_cls) -> dict[str, Any]:
        lat, lon = round(place["lat"], 3), round(place["lon"], 3)
        key = ("fc", lat, lon, target.isoformat())
        daily = self._cache_get(key)
        if daily is None:
            data = await self._get_json(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                    "timezone": TIMEZONE_NAME,
                    "start_date": target.isoformat(),
                    "end_date": target.isoformat(),
                },
            )
            block = (data or {}).get("daily") or {}

            def first(name: str) -> Any:
                values = block.get(name) or []
                return values[0] if values else None

            daily = {
                "t_max": first("temperature_2m_max"),
                "t_min": first("temperature_2m_min"),
                "rain_prob": first("precipitation_probability_max"),
                "code": first("weather_code"),
            }
            if daily["t_max"] is None and daily["t_min"] is None:
                raise ValueError("forecast has no daily values")
            self._cache_put(key, daily)

        def num(value: Any) -> float | int | None:
            if isinstance(value, (int, float)):
                return round(float(value), 1)
            return None

        t_min, t_max = num(daily["t_min"]), num(daily["t_max"])
        rain_prob = int(daily["rain_prob"]) if isinstance(daily["rain_prob"], (int, float)) else None
        code = int(daily["code"]) if isinstance(daily["code"], (int, float)) else None
        description = wmo_description_vi(code) if code is not None else ""
        return {
            "available": True,
            "city": place["name"],
            "lat": place["lat"],
            "lon": place["lon"],
            "date": target.isoformat(),
            "t_min": t_min,
            "t_max": t_max,
            "rain_prob": rain_prob,
            "code": code,
            "description_vi": description,
            "summary_vi": summary_vi(place["name"], t_min, t_max, rain_prob, description),
        }

"""Resolve requested clothing conditions before consulting GPS weather.

Requested seasons/conditions are styling scenarios, never fabricated forecasts.
The same resolver serves chat, Live tools and the dedicated stylist endpoint.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date as Date, timedelta

import httpx
from pydantic import BaseModel, Field

from wardrobe_store import fold_vi
from weather import MAX_DAYS_AHEAD, PROVIDER_BUDGET_S, local_today


class StylingLocation(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


LOCATION_MESSAGE = "Chưa có vị trí GPS để lấy thời tiết. Bạn có thể bật quyền vị trí hoặc nói mùa, nhiệt độ muốn phối."


def requested_date(text: str, *, today: Date | None = None) -> tuple[str | None, str | None]:
    """An explicit date wins over UI defaults; ambiguous/out-of-range dates stay explicit."""
    today = today or local_today()
    line = fold_vi(text).lower()
    try:
        if re.search(r"\bmốt\b", text.lower()):
            return (today + timedelta(days=2)).isoformat(), None
        if match := re.search(r"\b(\d{4}-\d{2}-\d{2})\b", line):
            return Date.fromisoformat(match[1]).isoformat(), None
        match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?\b", line)
        if not match:
            match = re.search(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})(?:\s+nam\s+(\d{4}))?\b", line)
        if match:
            target = Date(int(match[3] or today.year), int(match[2]), int(match[1]))
            if not match[3] and target < today:
                target = target.replace(year=today.year + 1)
            return target.isoformat(), None
        for pattern, delta in ((r"\b(?:ngay kia|ngay mot)\b", 2),
                               (r"\b(?:ngay mai|hom mai|mai|tomorrow)\b", 1),
                               (r"\b(?:hom nay|bay gio|hien tai|today)\b", 0),
                               (r"\bhom qua\b", -1)):
            if re.search(pattern, line):
                return (today + timedelta(days=delta)).isoformat(), None
        if match := re.search(r"\b(?:sau|trong)\s+(\d{1,3})\s+ngay\b", line):
            return (today + timedelta(days=int(match[1]))).isoformat(), None
        weekdays = {"hai": 0, "ba": 1, "tu": 2, "nam": 3, "sau": 4, "bay": 5,
                    "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5}
        if match := re.search(r"\bthu\s+(hai|ba|tu|nam|sau|bay|[2-7])\b|\b(chu nhat)\b", line):
            day = weekdays[match[1]] if match[1] else 6
            delta = (day - today.weekday()) % 7
            if "tuan sau" in line:
                delta = 7 - today.weekday() + day
            elif delta == 0:
                delta = 7
            return (today + timedelta(days=delta)).isoformat(), None
        if re.search(r"\bcuoi tuan\b", line):
            delta = max(0, 5 - today.weekday())
            if "tuan sau" in line:
                delta = 12 - today.weekday()
            return (today + timedelta(days=delta)).isoformat(), None
        if re.search(r"\b(?:hom khac|ngay khac|tuan sau|thang sau|nam sau|thang \d+)\b", line):
            return None, "Bạn muốn phối cho ngày nào? Chưa có ngày cụ thể nên mình chưa dùng dự báo thời tiết."
    except ValueError:
        return None, "Ngày bạn nói chưa hợp lệ. Bạn nói lại ngày/tháng giúp mình nhé."
    return None, None


def requested_conditions(text: str) -> dict | None:
    line = fold_vi(text).lower()
    # Drop explicitly rejected seasons, but preserve "không mưa" as a dry scenario.
    line = re.sub(r"\b(?:khong(?: phai)?|dung|bo)\s+(?:cho\s+)?mua\s+(?:he|ha|dong|thu|xuan)\b", "", line)
    seasons = {"he": ("mùa hè", "hot"), "ha": ("mùa hè", "hot"),
               "dong": ("mùa đông", "cold"), "thu": ("mùa thu", "cool"), "xuan": ("mùa xuân", "mild")}
    conditions: dict = {}
    labels = []
    matches = list(re.finditer(r"\bmua\s+(he|ha|dong|thu|xuan)\b", line))
    if matches:
        label, heat = seasons[matches[-1][1]]
        conditions.update(season=label, heat=heat)
        labels.append(label)
    # Numbers are only temperatures when explicitly followed by a temperature unit.
    if match := re.search(r"(?<![\d/])(-?\d{1,2}(?:[.,]\d+)?)(?:\s*[-–]\s*(-?\d{1,2}(?:[.,]\d+)?))?\s*(?:°\s*c?|do(?:\s*c)?|celsius)\b", line):
        low, high = float(match[1].replace(",", ".")), float((match[2] or match[1]).replace(",", "."))
        if -70 <= low <= high <= 60:
            conditions.update(t_min=low, t_max=high)
            conditions.pop("heat", None)
            labels.append(f"{low:g}–{high:g}°C" if low != high else f"{low:g}°C")
    elif re.search(r"\b(?:troi|thoi tiet|khi|neu)\s+(?:rat\s+)?(?:lanh|ret)\b", line):
        conditions["heat"] = "cold"
        labels.append("trời lạnh")
    elif re.search(r"\b(?:troi|thoi tiet|khi|neu)\s+(?:rat\s+)?nong\b", line):
        conditions["heat"] = "hot"
        labels.append("trời nóng")
    if re.search(r"\b(?:khong mua|troi kho rao)\b", line):
        conditions["rain"] = False
        labels.append("không mưa")
    elif re.search(r"\b(?:troi mua|mua mua|neu mua|khi mua|co mua)\b", line) and not re.search(r"\b(?:co mua|troi mua)\s+(?:khong|khong nhi|hay khong)\b", line):
        conditions["rain"] = True
        labels.append("trời mưa")
    if not conditions:
        return None
    return {"available": True, "source": "requested", "is_forecast": False,
            "conditions": conditions, "t_min": conditions.get("t_min"), "t_max": conditions.get("t_max"),
            "summary_vi": "Phối theo yêu cầu: " + ", ".join(labels) + " (không phải dự báo)."}


def contextual_request(text: str, history: list[dict]) -> str:
    """Retain the occasion on short follow-ups; a new condition/date replaces the old one."""
    line = fold_vi(text).lower()
    if not re.match(r"^(?:con\b|the\b|vay\b|doi\b|neu\b|ngay\b|mai\b|mua\b|hom\b|thu\b|bo khac\b)", line):
        return text
    from intent_router import local_intent
    previous = next((turn["text"] for turn in reversed(history) if turn.get("role") == "user"
                     and local_intent(turn.get("text", "")) == "outfit_suggest"), None)
    if not previous:
        return text
    return f"{previous}\nYêu cầu mới: {text}"


async def resolve_weather(service, text: str, *, location: StylingLocation | None = None,
                          date: str | None = None, city: str | None = None, allow_scenario: bool = True) -> dict:
    # Follow-up text is authoritative for weather; earlier text only supplies the occasion.
    latest = text.rsplit("\nYêu cầu mới: ", 1)[-1]
    target, date_error = requested_date(latest)
    scenario = requested_conditions(latest) if allow_scenario else None
    if latest != text and not scenario and not target and not date_error:
        previous = text.rsplit("\nYêu cầu mới: ", 1)[0]
        target, date_error = requested_date(previous)
        scenario = requested_conditions(previous) if allow_scenario else None
    if scenario:
        return {**scenario, "date": target}
    if date_error:
        return {"available": False, "source": "unknown", "is_forecast": False,
                "needs_date": True, "message": date_error, "summary_vi": date_error}
    target = target or date or local_today().isoformat()
    try:
        day = Date.fromisoformat(target)
        if not local_today() <= day <= local_today() + timedelta(days=MAX_DAYS_AHEAD):
            message = "Chưa có dự báo cho ngày này. Mình vẫn phối theo dịp; bạn có thể nói thêm mùa hoặc nhiệt độ mong muốn."
            return {"available": False, "source": "unknown", "is_forecast": False,
                    "date": target, "message": message, "summary_vi": message}
    except ValueError:
        return {"available": False, "source": "unknown", "message": "Ngày chưa hợp lệ.", "is_forecast": False}
    if not location and not city:
        return {"available": False, "source": "unknown", "is_forecast": False, "needs_location": True,
                "date": target, "message": LOCATION_MESSAGE, "summary_vi": LOCATION_MESSAGE}
    try:
        data = await asyncio.wait_for(service.get(date=target, **(
            {"lat": location.lat, "lon": location.lon} if location else {"city": city}
        )), timeout=2 * PROVIDER_BUDGET_S + 1)
    except (asyncio.TimeoutError, httpx.HTTPError):
        data = {"available": False, "message": "Chưa lấy được dự báo thực tế. Mình vẫn có thể phối theo dịp hoặc mùa bạn nói."}
    result = {**data, "date": target, "source": "forecast" if data.get("available") else "unknown",
              "is_forecast": bool(data.get("available"))}
    if result["is_forecast"]:
        result["summary_vi"] = f"Dự báo ngày {day.strftime('%d/%m/%Y')}: {data.get('summary_vi') or ''}"
    else:
        result["summary_vi"] = data.get("message") or "Chưa có dữ liệu thời tiết."
    return result

"""Shared text and camera conversation endpoint for the web app and mobile."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from intent_router import IntentResult, TurnText, local_intent, normalize
from providers import ProviderError, conversation_model, vision_model
from skill_store import SkillNotFound
import style_memory
from stylist import suggest_outfits
from wardrobe_api import _store as wardrobe_store, _weather
from wardrobe_store import WardrobeError, decode_image_data_url, jpeg_data_url, to_jpeg

logger = logging.getLogger(__name__)
router = APIRouter()

ACTIONS = {
    "wardrobe_add": ("wardrobe", "Mở tủ đồ", "Bạn có thể chụp hoặc chọn ảnh món đồ trong tủ."),
    "tryon_photo": ("stylist", "Chọn đồ để thử", "Chọn bộ đồ rồi tải ảnh của bạn lên để mặc thử."),
    "tryon_live": ("tryon-live", "Mở thử đồ trực tiếp", "Mình mở phần thử đồ trực tiếp để bạn chọn món và dùng camera nhé."),
    "teach_skill": ("teach", "Quay bài hướng dẫn", "Bạn mở phần dạy kỹ năng để mình ghi lại từng bước bạn làm nhé."),
    "generate_image": ("image-studio", "Tạo ảnh", "Mình mở phần tạo ảnh với mô tả của bạn nhé."),
}
CITY_FOLLOWUP = "Bạn muốn xem thời tiết ở thành phố nào?"
GUIDE_FOLLOWUP = "Bạn muốn mình hướng dẫn kỹ năng nào?"
GENERIC_GUIDE_REQUESTS = {"ban chi toi cach lam", "chi toi cach lam", "huong dan toi", "huong dan minh"}
SYSTEM = (
    "Bạn là Shadow, trợ lý trò chuyện bằng tiếng Việt. Trả lời trực tiếp, tự nhiên để đọc thành tiếng. "
    "Ưu tiên một câu ngắn có ích trước, tối đa ba câu. Không dùng markdown hay biểu tượng. "
    "Khi cần, hỏi một câu để làm rõ. Chỉ mô tả camera nếu có ảnh đính kèm. "
    "Không bịa thời tiết hiện tại, bài đã lưu hay đồ trong tủ. Không nói đã mở màn hình, tạo ảnh hoặc lưu dữ liệu."
)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: TurnText


class ConversationRequest(BaseModel):
    text: TurnText
    history: list[ConversationTurn] = Field(default_factory=list, max_length=6)
    image: str | None = Field(None, max_length=500_000)


def event(kind: str, **data: object) -> str:
    return json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n"


def city_and_date(payload: ConversationRequest) -> tuple[str | None, str | None]:
    city = None
    text = payload.text
    followup = bool(payload.history and payload.history[-1].text == CITY_FOLLOWUP)
    if followup:
        text = re.sub(r"^(?:ở|tại|cho)\s+", "", text, flags=re.IGNORECASE)
        city = text
        if len(payload.history) >= 2:
            text = payload.history[-2].text + " " + text
    else:
        location = re.search(r"\b(?:ở|tại|cho)\s+(.+)$", text, re.IGNORECASE)
        if location:
            city = location[1]
    date = None
    today = datetime.now(timezone(timedelta(hours=7))).date()
    if "ngay mai" in normalize(text):
        date = (today + timedelta(days=1)).isoformat()
    elif "hom nay" in normalize(text):
        date = today.isoformat()
    if city:
        city = re.split(r"\s+(?:hôm nay|ngày mai|thế nào|ra sao|có mưa|như nào)\b", city, flags=re.I)[0]
        city = city.strip(" ,.!?")[:80] or None
    return city, date


async def resolve_intent(payload: ConversationRequest, request: Request) -> IntentResult:
    if match := local_intent(payload.text):
        return IntentResult(intent=match)
    if payload.history and payload.history[-1].text == CITY_FOLLOWUP:
        return IntentResult(intent="weather")
    if payload.history and payload.history[-1].text.startswith(GUIDE_FOLLOWUP):
        return IntentResult(intent="guide_skill")
    try:
        return await asyncio.wait_for(request.app.state.intent_router.resolve(payload.text), timeout=0.6)
    except asyncio.TimeoutError:
        return IntentResult()


def saved_guide(payload: ConversationRequest, request: Request) -> tuple[dict | None, list[dict]]:
    store = request.app.state.skill_store
    skills = store.list()
    message = f" {normalize(payload.text)} "
    matches = [row for row in skills if f" {normalize(row['display_name'])} " in message]
    if not matches and len(message.split()) >= 2:
        matches = [row for row in skills if message in f" {normalize(row['display_name'])} "]
    if len(matches) == 1:
        try:
            return store.get_dict(matches[0]["id"]), skills
        except SkillNotFound:
            pass
    return None, skills


def compact_image(image: str) -> str | None:
    try:
        return jpeg_data_url(to_jpeg(decode_image_data_url(image), max_side=448, quality=76))
    except WardrobeError:
        return None


async def outfit_reply(payload: ConversationRequest, request: Request) -> AsyncIterator[str]:
    store = wardrobe_store(request)
    items = store.list_items()
    if not items:
        yield event("text", text="Tủ đồ chưa có món nào. Bạn có thể thêm quần áo, giày hoặc phụ kiện trước nhé.")
        yield event("action", route="wardrobe", label="Mở tủ đồ")
        return
    result = await suggest_outfits(
        request.app.state.provider, items, weather=None, event=payload.text[:200],
        rules=store.list_rules(), memory=style_memory.load_memory(store.root), prefer_rules=True,
    )
    if result["outfits"]:
        outfit = result["outfits"][0]
        by_id = {item["id"]: item["name"] for item in items}
        names = [by_id[item_id] for item_id in outfit["item_ids"] if item_id in by_id]
        if names:
            yield event("text", text=f"Mình gợi ý phối {', '.join(names)}.")
            yield event("action", route="tryon-photo", label="Thử bộ này bằng ảnh", items=outfit["item_ids"])
            yield event("action", route="tryon-live", label="Thử trực tiếp", items=outfit["item_ids"])
            return
    yield event("text", text="Mình chưa tìm được bộ phù hợp trong tủ. Bạn muốn xem các lựa chọn không?")
    yield event("action", route="stylist", label="Mở phối đồ")


async def generate(payload: ConversationRequest, request: Request) -> AsyncIterator[str]:
    result = await resolve_intent(payload, request)
    intent = result.intent
    provider = request.app.state.provider
    skill, skills = await asyncio.to_thread(saved_guide, payload, request) if intent == "guide_skill" else (None, [])
    if intent in ACTIONS:
        route, label, answer = ACTIONS[intent]
        yield event("text", text=answer)
        if intent == "generate_image":
            yield event("action", route=route, label=label, prompt=payload.text)
        else:
            yield event("action", route=route, label=label)
    elif intent == "outfit_suggest":
        async for item in outfit_reply(payload, request):
            yield item
    elif intent == "weather":
        city, date = city_and_date(payload)
        answer = CITY_FOLLOWUP
        if city:
            try:
                forecast = await asyncio.wait_for(_weather(request).get(city=city, date=date), timeout=6)
                answer = forecast.get("summary_vi") or forecast.get("message") or "Mình chưa lấy được dự báo."
            except asyncio.TimeoutError:
                answer = "Dịch vụ thời tiết đang phản hồi chậm. Bạn thử lại sau nhé."
        yield event("text", text=answer)
    elif intent == "guide_skill" and (skill or normalize(payload.text) in GENERIC_GUIDE_REQUESTS):
        if skill:
            start = next((step for step in skill["steps"] if step["id"] == skill["start_step_id"]), None)
            if start:
                yield event("text", text=f"Bước đầu tiên, {start['instruction'].rstrip('.')} nhé.")
            yield event("action", route="guide", id=skill["id"], label=skill["display_name"])
        else:
            names = ", ".join(item["display_name"] for item in skills[:4])
            answer = f"{GUIDE_FOLLOWUP} Mình đã học {names}." if names else GUIDE_FOLLOWUP
            yield event("text", text=answer)
            if names:
                yield event("action", route="home", label="Mở thư viện bài học")
    elif provider.mode == "mock":
        yield event("text", text="Mình đang ở chế độ thử. Bạn có thể hỏi thời tiết, phối đồ hoặc mở một bài đã lưu.")
    else:
        messages = [{"role": "system", "content": SYSTEM}]
        messages.extend({"role": turn.role, "content": turn.text} for turn in payload.history)
        content: str | list[dict] = payload.text
        model = conversation_model()
        if payload.image:
            image = await asyncio.to_thread(compact_image, payload.image)
            if image:
                content = [{"type": "text", "text": payload.text}, {"type": "image_url", "image_url": {"url": image}}]
                model = vision_model()
        messages.append({"role": "user", "content": content})
        received = False
        try:
            async with aclosing(provider.stream_text(model=model, messages=messages)) as stream:
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(stream), timeout=10 if not received else 6)
                    except StopAsyncIteration:
                        break
                    received = True
                    yield event("text", text=chunk)
            if not received:
                raise ProviderError("empty stream")
        except (ProviderError, asyncio.TimeoutError):
            logger.info("Conversation upstream stream failed model=%s", model)
            yield event("error", text="Kết nối AI bị gián đoạn. Bạn thử lại nhé.")
            return
    yield event("done", intent=intent, source=result.source, suggestions=result.suggestions)


@router.post("/api/conversation")
async def converse(payload: ConversationRequest, request: Request) -> StreamingResponse:
    return StreamingResponse(
        generate(payload, request),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

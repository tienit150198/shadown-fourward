"""REST routes of the built-in "Trợ lý tủ đồ" skill (new in SKI). Registered in main.py.

Wardrobe/rules CRUD, demo seeding, weather, stylist suggestions, try-on (live token + photo)
and /wardrobe-media/<rel> for item photos and try-on results. Services are created lazily on
`app.state` (tests may pre-set `wardrobe_weather` / `wardrobe_tryon` with fakes).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import style_memory
from garment_attrs import tag_item_with_attrs
from shop_import import ShopImportError, import_product
from stylist import describe_wearing, suggest_outfits
from tryon import TryOnError, TryOnService, create_live_token, live_prompt
from vidu_live import live_config
from wardrobe_store import (
    MAX_IMAGE_DATA_URL_CHARS,
    ItemNotFound,
    WardrobeError,
    WardrobeStore,
    decode_image_data_url,
    is_safe_id,
    jpeg_data_url,
    now_iso,
    to_jpeg,
    wardrobe_root,
)
from weather import WeatherService
from styling_context import StylingLocation, resolve_weather

logger = logging.getLogger("uvicorn.error")

DISCONNECT_POLL_S = 1.0

SEED_DIR = Path(__file__).resolve().parent / "wardrobe_seed"
# Demo wardrobe sets for POST /api/wardrobe/seed-demo?set=…: name → (dir, build script that re-creates images).
SEED_SETS: dict[str, tuple[Path, str]] = {
    "default": (SEED_DIR, "build_seed.py"),
    "shop": (SEED_DIR.parent / "wardrobe_seed_shop", "build_seed_shop.py"),  # đồ nam mùa hè (YaMe)
}
# Product fields of shop seed items kept as-is on the stored item (shown in the web editor).
SEED_EXTRA_FIELDS = {"brand": 40, "product_name": 200, "product_url": 300, "product_code": 40}

router = APIRouter()


# ---- state helpers ---------------------------------------------------------------


def _provider(request: Request):
    return request.app.state.provider


def _store(request: Request) -> WardrobeStore:
    root = wardrobe_root()
    store = getattr(request.app.state, "wardrobe_store", None)
    if store is None or Path(store.root) != root:
        store = WardrobeStore(root)
        request.app.state.wardrobe_store = store
    return store


def _weather(request: Request) -> WeatherService:
    service = getattr(request.app.state, "wardrobe_weather", None)
    if service is None:
        service = WeatherService()
        request.app.state.wardrobe_weather = service
    return service


def _tryon(request: Request) -> TryOnService:
    service = getattr(request.app.state, "wardrobe_tryon", None)
    if service is None:
        service = TryOnService()
        request.app.state.wardrobe_tryon = service
    return service


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Không tìm thấy món đồ.")


# ---- wardrobe items ----------------------------------------------------------------


class NewItemRequest(BaseModel):
    image: str = Field(..., max_length=MAX_IMAGE_DATA_URL_CHARS)
    name: str | None = Field(None, max_length=80)
    category: str | None = Field(None, max_length=40)


@router.get("/api/wardrobe/items")
async def list_items(request: Request) -> list[dict[str, Any]]:
    return _store(request).list_items()


@router.get("/api/wardrobe/items/{item_id}")
async def get_item(item_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store(request).get_item(item_id)
    except ItemNotFound:
        raise _not_found()


@router.post("/api/wardrobe/items")
async def create_item(payload: NewItemRequest, request: Request) -> dict[str, Any]:
    try:
        jpeg = await asyncio.to_thread(lambda: to_jpeg(decode_image_data_url(payload.image)))
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))
    name = (payload.name or "").strip() or None
    category = (payload.category or "").strip() or None
    fields, source = await tag_item_with_attrs(_provider(request), jpeg, name=name, category=category)
    try:
        return _store(request).create_item(fields, jpeg, extra={"tag_source": source})
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))


class ImportRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=600)


@router.post("/api/wardrobe/import")
async def import_item(payload: ImportRequest, request: Request) -> dict[str, Any]:
    """Add an item from a shop product link (Shopify; hosts in SHADOW_IMPORT_HOSTS). See shop_import.py."""
    try:
        return await import_product(_provider(request), _store(request), payload.url)
    except ShopImportError as error:
        raise HTTPException(status_code=error.status, detail={"code": error.code, "message": str(error)})
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail={"code": "invalid_item", "message": str(error)})


@router.put("/api/wardrobe/items/{item_id}")
async def update_item(item_id: str, request: Request, patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return _store(request).update_item(item_id, patch)
    except ItemNotFound:
        raise _not_found()
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))


@router.delete("/api/wardrobe/items/{item_id}")
async def delete_item(item_id: str, request: Request) -> dict[str, Any]:
    if not _store(request).delete_item(item_id):
        raise _not_found()
    return {"ok": True}


def _seed_extra(row: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {"tag_source": "seed", "demo": True}
    for key, max_len in SEED_EXTRA_FIELDS.items():
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            extra[key] = value.strip()[:max_len]
    if not str(extra.get("product_url", "")).startswith("https://"):
        extra.pop("product_url", None)
    price = row.get("price_vnd")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and 0 < price < 1e9:
        extra["price_vnd"] = int(price)
    return extra


@router.post("/api/wardrobe/seed-demo")
async def seed_demo(
    request: Request,
    replace: bool = Query(False),
    set_name: str | None = Query(None, alias="set", max_length=20),
) -> dict[str, Any]:
    """Load a demo wardrobe (pre-tagged offline, see wardrobe_seed*/SOURCES.md). Idempotent.

    `set`: "default" (bộ mẫu cơ bản) | "shop" (đồ nam mùa hè từ shop thật). Without it the
    SHADOW_DEMO_SEED env var decides (default "default").
    """
    name = (set_name or os.getenv("SHADOW_DEMO_SEED") or "default").strip().lower()
    if name not in SEED_SETS:
        raise HTTPException(status_code=422, detail=f"Bộ tủ đồ mẫu không hợp lệ: chọn {', '.join(SEED_SETS)}.")
    seed_dir, script = SEED_SETS[name]
    store = _store(request)
    try:
        seed = json.loads((seed_dir / "items.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Thiếu bộ tủ đồ mẫu: {error}")
    seed = [row for row in seed if isinstance(row, dict) and is_safe_id(row.get("id"))]
    rerun_hint = f"cd backend/{seed_dir.name} && ../../.venv/bin/python {script}"
    missing = [row["id"] for row in seed if not (seed_dir / "images" / f"{row['id']}.jpg").is_file()]
    if missing and len(missing) == len(seed):
        # Images are git-ignored for the shop set: say exactly how to get them back.
        raise HTTPException(
            status_code=503,
            detail=f"Bộ tủ đồ mẫu “{name}” chưa có ảnh. Chạy: {rerun_hint} để tải ảnh rồi thử lại.",
        )
    if replace:
        for row in seed:
            store.delete_item(row["id"])
    existing = store.has_items(row["id"] for row in seed)
    added: list[str] = []
    for row in seed:
        item_id = row["id"]
        if item_id in existing or item_id in missing:
            continue
        jpeg = (seed_dir / "images" / f"{item_id}.jpg").read_bytes()
        fields = {k: v for k, v in row.items() if k not in ("id", "image", "image_url", "created_at")}
        try:
            store.create_item(fields, jpeg, item_id=item_id, extra=_seed_extra(row))
        except WardrobeError as error:
            logger.warning("seed item %s skipped: %s", item_id, error)
            continue
        added.append(item_id)
    result: dict[str, Any] = {"added": len(added), "added_ids": added, "set": name, "items": store.list_items()}
    if missing:
        logger.warning("seed set %s: %d image(s) missing", name, len(missing))
        result["missing_images"] = missing
        result["warning"] = f"Thiếu {len(missing)} ảnh của bộ mẫu “{name}”. Chạy: {rerun_hint} để tải lại."
    return result


# ---- rules ---------------------------------------------------------------------------


class NewRuleRequest(BaseModel):
    text: str = Field(..., max_length=300)
    taught_by: str | None = Field(None, max_length=60)
    taught_via: str | None = Field(None, pattern="^(text|voice)$")  # how the teacher entered it


@router.get("/api/wardrobe/rules")
async def list_rules(request: Request) -> list[dict[str, Any]]:
    """Rules with their parsed constraint + "Trợ lý hiểu là: …" text (see style_memory.public_rule)."""
    store = _store(request)
    by_id = {it["id"]: it for it in store.list_items()}
    return [style_memory.public_rule(rule, by_id) for rule in store.list_rules()]


@router.post("/api/wardrobe/rules")
async def add_rule(payload: NewRuleRequest, request: Request) -> dict[str, Any]:
    """Teach a rule: parsed ONCE into a structured constraint (compiler model; pattern parser offline)."""
    store = _store(request)
    try:
        return await style_memory.teach_rule(
            store, _provider(request), payload.text, payload.taught_by, store.list_items(),
            taught_via=payload.taught_via or "text",
        )
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))


@router.delete("/api/wardrobe/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request) -> dict[str, Any]:
    if not _store(request).delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy quy tắc.")
    return {"ok": True}


# ---- weather -------------------------------------------------------------------------


@router.get("/api/weather")
async def weather(
    request: Request,
    city: str | None = Query(None, max_length=80),
    lat: float | None = None,
    lon: float | None = None,
    date: str | None = Query(None, max_length=10),
) -> dict[str, Any]:
    return await _weather(request).get(city=city, lat=lat, lon=lon, date=date)


# ---- stylist -------------------------------------------------------------------------


class SuggestRequest(BaseModel):
    event: str = Field("", max_length=200)
    city: str | None = Field(None, max_length=80)
    lat: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)
    date: str | None = Field(None, max_length=10)
    current_image: str | None = Field(None, max_length=MAX_IMAGE_DATA_URL_CHARS)
    anchor_item_id: str | None = Field(None, max_length=64)
    notes: str = Field("", max_length=300)


@router.post("/api/stylist/suggest")
async def stylist_suggest(payload: SuggestRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    provider = _provider(request)
    items = store.list_items()
    if not items:
        raise HTTPException(status_code=409, detail="Tủ đồ đang trống — hãy thêm đồ hoặc nạp tủ đồ mẫu.")
    anchor_id = payload.anchor_item_id or None
    if anchor_id and anchor_id not in {it["id"] for it in items}:
        raise HTTPException(status_code=404, detail="Không tìm thấy món đồ cần giữ.")

    current_image = None
    if payload.current_image:
        try:
            jpeg = await asyncio.to_thread(
                lambda: to_jpeg(decode_image_data_url(payload.current_image), max_side=768, quality=80)
            )
        except WardrobeError as error:
            raise HTTPException(status_code=422, detail=str(error))
        current_image = jpeg_data_url(jpeg)

    location = StylingLocation(lat=payload.lat, lon=payload.lon) if payload.lat is not None and payload.lon is not None else None
    weather_task = resolve_weather(_weather(request), f"{payload.event}. {payload.notes}",
                                   location=location, city=payload.city, date=payload.date)
    if current_image:
        weather_info, wearing = await asyncio.gather(
            weather_task, describe_wearing(provider, current_image, items)
        )
    else:
        weather_info, wearing = await weather_task, None
    if wearing and not anchor_id and wearing.get("match_item_id"):
        anchor_id = wearing["match_item_id"]

    result = await suggest_outfits(
        provider,
        items,
        weather=weather_info,
        event=payload.event.strip(),
        notes=payload.notes.strip(),
        rules=store.list_rules(),
        anchor_id=anchor_id,
        wearing=wearing,
        memory=style_memory.load_memory(store.root),
    )
    by_id = {it["id"]: it for it in items}
    for outfit in result["outfits"]:
        outfit["items"] = [
            {k: by_id[i].get(k) for k in ("id", "name", "category", "colors", "image_url", "color_hex", "subtype",
                                          "formality")}
            for i in outfit["item_ids"]
        ]
    return {
        "weather": weather_info,
        "wearing": (wearing or {}).get("wearing") or None,
        "wearing_garments": (wearing or {}).get("garments") or [],
        # garment kept as-is when no wardrobe item matched (outfits are built around it)
        "wearing_kept": (wearing or {}).get("primary") if wearing and not anchor_id else None,
        "anchor_item_id": anchor_id,
        "outfits": result["outfits"],
        "rules_used": result["rules_used"],
        # model outfits removed by outfit_scoring (hard rule violations), each with a Vietnamese reason_vi
        "dropped_outfits": result.get("dropped") or [],
        "source": result["source"],
        "learning": result.get("memory") or {},
        # ready-made context for POST /api/stylist/feedback (add item_ids + vote)
        "feedback_context": {
            "event": payload.event.strip(),
            "date": (weather_info or {}).get("date") or payload.date,
            "weather": {k: (weather_info or {}).get(k) for k in ("t_min", "t_max", "rain_prob", "summary_vi")},
        },
        "generated_at": now_iso(),
    }


# ---- try-on --------------------------------------------------------------------------


@router.get("/api/tryon/config")
async def tryon_config(request: Request) -> dict[str, Any]:
    config = await _tryon(request).config()
    config["live"] = live_config()
    return config


@router.get("/api/tryon/ar/config")
async def tryon_ar_config() -> JSONResponse:
    # Camera Kit Web expects the public app token in the browser.
    config = {
        "api_token": os.getenv("SNAP_CAMERA_KIT_API_TOKEN", "").strip(),
        "lens_group_id": os.getenv("SNAP_CAMERA_KIT_LENS_GROUP_ID", "").strip(),
        "lens_id": os.getenv("SNAP_CAMERA_KIT_LENS_ID", "").strip(),
    }
    available = all(config.values())
    return JSONResponse(
        {"available": True, **config} if available else {"available": False},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/tryon/live-token")
async def tryon_live_token() -> dict[str, Any]:
    try:
        return await create_live_token()
    except TryOnError as error:
        raise HTTPException(status_code=error.status, detail=str(error))


class LivePromptRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1, max_length=8)


def _items_for(store: WardrobeStore, item_ids: list[str]) -> list[dict[str, Any]]:
    items = []
    for item_id in dict.fromkeys(item_ids):
        try:
            items.append(store.get_item(item_id))
        except ItemNotFound:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy món đồ {item_id}.")
    return items


@router.post("/api/tryon/live-prompt")
async def tryon_live_prompt(payload: LivePromptRequest, request: Request) -> dict[str, Any]:
    """Prompt + reference garment for Decart lucy-vton (ported idea: tryon-examples/outfit-builder)."""
    items = _items_for(_store(request), payload.item_ids)
    result = live_prompt(items)
    ref = next((it for it in items if it["id"] == result["reference_item_id"]), None)
    result["reference_image_url"] = ref.get("image_url") if ref else None
    return result


class PhotoTryOnRequest(BaseModel):
    person_image: str = Field(..., max_length=MAX_IMAGE_DATA_URL_CHARS)
    item_ids: list[str] = Field(..., min_length=1, max_length=8)


@router.post("/api/tryon/photo")
async def tryon_photo(payload: PhotoTryOnRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    items = _items_for(store, payload.item_ids)
    garments: dict[str, bytes] = {}
    for item in items:
        path = store.item_image_path(item)
        if path is not None:
            garments[item["id"]] = path.read_bytes()
    task = asyncio.ensure_future(_tryon(request).run(payload.person_image, items, garments))
    try:
        # If the user cancels (client disconnects) stop waiting on the provider
        # call and don't write a result image nobody will fetch.
        while not task.done():
            await asyncio.wait({task}, timeout=DISCONNECT_POLL_S)
            if not task.done() and await request.is_disconnected():
                task.cancel()
                logger.info("try-on cancelled by client; result discarded")
                raise HTTPException(status_code=499, detail="Đã huỷ mặc thử.")
        result = task.result()
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))
    except TryOnError as error:
        raise HTTPException(status_code=error.status, detail=str(error))
    finally:
        if not task.done():
            task.cancel()
    rel = store.save_tryon(result.pop("jpeg"))
    return {"image_url": f"/wardrobe-media/{rel}", **result}


# ---- media ---------------------------------------------------------------------------


@router.get("/wardrobe-media/{rel_path:path}")
async def wardrobe_media(rel_path: str, request: Request) -> FileResponse:
    path = _store(request).media_path(rel_path)
    if path is None or path.suffix.lower() not in (".jpg", ".jpeg"):
        raise HTTPException(status_code=404, detail="Không tìm thấy ảnh.")
    return FileResponse(path, media_type="image/jpeg")


# ---- teachable + learning stylist (style_memory.py) -----------------------------------------------
# Edit a taught rule, 👍/👎/"Mặc bộ này" feedback → taste profile, teach-by-example outfits.


class EditRuleRequest(BaseModel):
    text: str | None = Field(None, max_length=300)  # changed text → parsed again (confirmed resets to false)
    taught_by: str | None = Field(None, max_length=60)
    strength: str | None = Field(None, pattern="^(must|should)$")
    confirmed: bool | None = None  # teacher tapped "Đúng rồi" on the interpretation
    constraint: dict[str, Any] | None = None  # full structured constraint (validated strictly)


@router.put("/api/wardrobe/rules/{rule_id}")
async def edit_rule(rule_id: str, payload: EditRuleRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    try:
        return await style_memory.edit_rule(
            store, _provider(request), rule_id, payload.model_dump(exclude_none=True), store.list_items()
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Không tìm thấy quy tắc.")
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))


class FeedbackWeather(BaseModel):
    t_min: float | None = None
    t_max: float | None = None
    rain_prob: float | None = None
    summary_vi: str | None = Field(None, max_length=200)


class FeedbackRequest(BaseModel):
    item_ids: list[str] = Field(..., min_length=1, max_length=8)
    vote: str = Field(..., pattern="^(up|down|wear)$")  # 👍 | 👎 | "Mặc bộ này"
    reason: str | None = Field(None, max_length=40)  # code (color|too_formal|too_hot|dislike_item) or its Vietnamese label
    disliked_item_ids: list[str] | None = Field(None, max_length=8)  # with reason=dislike_item: which items
    event: str = Field("", max_length=200)
    date: str | None = Field(None, max_length=10)  # YYYY-MM-DD the outfit is worn (vote=wear); default today
    weather: FeedbackWeather | None = None
    weather_summary: str | None = Field(None, max_length=200)


def _reason_code(value: str | None) -> str | None:
    if not value:
        return None
    if value in style_memory.REASONS:
        return value
    folded = value.strip().lower()
    for code, label in style_memory.REASONS.items():
        if label.lower() == folded:
            return code
    raise HTTPException(
        status_code=422,
        detail=f"reason không hợp lệ: dùng một trong {', '.join(style_memory.REASONS)}.",
    )


def _profile_response(store: WardrobeStore) -> dict[str, Any]:
    items = store.list_items()
    summary = style_memory.profile_summary(style_memory.load_memory(store.root), items)
    by_id = {it["id"]: it for it in items}
    for key in ("liked_items", "disliked_items", "never_worn"):
        for row in summary[key]:
            row["image_url"] = (by_id.get(row["item_id"]) or {}).get("image_url")
    summary["reason_codes"] = [{"code": c, "label_vi": label} for c, label in style_memory.REASONS.items()]
    summary["vote_codes"] = [{"code": "up", "label_vi": "Thích"}, {"code": "down", "label_vi": "Không thích"},
                             {"code": "wear", "label_vi": "Mặc bộ này"}]
    return summary


@router.post("/api/stylist/feedback")
async def stylist_feedback(payload: FeedbackRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    known = {it["id"] for it in store.list_items()}
    missing = [i for i in payload.item_ids if i not in known]
    if len(missing) == len(payload.item_ids):
        raise HTTPException(status_code=404, detail="Các món trong bộ này không còn trong tủ đồ.")
    weather_info = payload.weather.model_dump(exclude_none=True) if payload.weather else {}
    if payload.weather_summary and "summary_vi" not in weather_info:
        weather_info["summary_vi"] = payload.weather_summary
    reason = _reason_code(payload.reason)
    try:
        entry = style_memory.record_feedback(
            store.root, item_ids=payload.item_ids, vote=payload.vote, event=payload.event,
            weather=weather_info, reason=reason, disliked_item_ids=payload.disliked_item_ids,
            day=payload.date, known_ids=known,
        )
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))
    message = {
        "up": "Đã ghi nhận: bạn thích bộ này.",
        "down": "Đã ghi nhận: bạn không thích bộ này" + (f" ({style_memory.REASONS[reason].lower()})" if reason else "") + ".",
        "wear": "Đã lưu: hôm nay bạn mặc bộ này.",
    }[payload.vote]
    return {
        "ok": True,
        "recorded": {k: entry[k] for k in ("vote", "item_ids", "event", "occasions", "reason", "disliked_item_ids", "at")},
        "ignored_item_ids": missing,
        "message_vi": message,
        "profile": _profile_response(store),
    }


@router.get("/api/stylist/profile")
async def stylist_profile(request: Request) -> dict[str, Any]:
    return _profile_response(_store(request))


@router.delete("/api/stylist/profile")
async def stylist_profile_reset(request: Request) -> dict[str, Any]:
    store = _store(request)
    style_memory.reset_profile(store.root)
    return {"ok": True, "message_vi": "Đã xoá gu đã học (phản hồi và lịch sử mặc).", "profile": _profile_response(store)}


class NewExampleRequest(BaseModel):
    event: str = Field(..., max_length=80)  # "Đám cưới"
    item_ids: list[str] = Field(..., min_length=2, max_length=8)
    taught_by: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=200)  # "Đi đám cưới thì mặc như này"


def _public_example(example: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = dict(example)
    out["items"] = [
        {k: by_id[i].get(k) for k in ("id", "name", "category", "colors", "image_url")}
        for i in example.get("item_ids") or [] if i in by_id
    ]
    out["missing_item_ids"] = [i for i in example.get("item_ids") or [] if i not in by_id]
    who = example.get("taught_by") or "Bạn"
    out["label_vi"] = f"Bộ mẫu “{example.get('event')}” — học từ {who}"
    return out


@router.get("/api/wardrobe/examples")
async def list_examples(request: Request) -> list[dict[str, Any]]:
    store = _store(request)
    by_id = {it["id"]: it for it in store.list_items()}
    return [_public_example(e, by_id) for e in style_memory.list_examples(store.root)]


@router.post("/api/wardrobe/examples")
async def add_example(payload: NewExampleRequest, request: Request) -> dict[str, Any]:
    store = _store(request)
    items = store.list_items()
    try:
        example = style_memory.add_example(
            store.root, item_ids=payload.item_ids, event=payload.event, taught_by=payload.taught_by,
            note=payload.note, known_ids={it["id"] for it in items},
        )
    except WardrobeError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return _public_example(example, {it["id"]: it for it in items})


@router.delete("/api/wardrobe/examples/{example_id}")
async def delete_example(example_id: str, request: Request) -> dict[str, Any]:
    if not style_memory.delete_example(_store(request).root, example_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ mẫu.")
    return {"ok": True}

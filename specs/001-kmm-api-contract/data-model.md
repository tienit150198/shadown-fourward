# Data Model: KMM API Contract

V1 không thay đổi persistence schema. File này mô tả các DTO boundary cần dùng; domain model KMM không được phụ thuộc
trực tiếp vào FastAPI/Python types.

## REST DTOs

- `HealthStatus`: `ok`, `mode`, `vision_model`, `compiler_model`, `access_required`.
- `SkillSummary`: `id`, `display_name`, `learned_from`, `summary`, `steps`, `cover_image_url`, `created_at`.
- `WardrobeItem`: item identity, category, colors, image URL và các thuộc tính public đã normalize.
- `WeatherInfo`: city/date/temperature/rain/summary/availability.
- `StylistSuggestRequest`: event, city/coordinates/date, current image, anchor item, notes.
- `OutfitSuggestion`: title, item IDs/items, reason, tips, rules used, score/why khi backend cung cấp.
- `StylistSuggestResponse`: weather, wearing, outfits, rules used, source, learning, feedback context.
- `ApiError`: HTTP status, detail, optional machine-readable code and retry metadata.

## WebSocket DTOs

- `SessionReady`: session ID and vision capabilities.
- `SessionStart`: skill ID.
- `VisionFrame`: JPEG data URL, timestamp, trigger.
- `HudState`: phase, skill, tasks, active step, speech epoch.
- `SpeechMessage`: text, epoch, kind.
- `VisionResult`: detector, result, latency, confirmation metadata.
- `HudError`: code and Vietnamese message.

## Invariants

- IDs qua navigation là primitive strings.
- Unknown response fields are ignored by clients.
- Missing required fields are treated as contract errors, not guessed.
- `vision.result` is server-originated and never an outbound client command.
- DTO does not imply durable session ownership.

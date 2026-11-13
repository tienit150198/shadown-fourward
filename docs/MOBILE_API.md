# Native mobile integration

Base URL: `https://fourward.thangqt.com`. Use HTTPS in production and resolve relative media paths against this origin. Contracts: [OpenAPI JSON](api/openapi.json), [HTTP reference](api/REFERENCE.md), [AI tool schemas](api/tools.json). Runtime FastAPI documentation is at `/docs`, `/redoc`, and `/openapi.json`. The checked-in spec describes this checkout; a deployed server may run an older commit.

## Interactive API explorer

Open [Swagger UI](https://fourward.thangqt.com/docs) for expandable endpoints, request/response schemas, and **Try it out**. Click **Authorize**, enter the shared app access code under **ShadowAccessHeader** (no `Bearer` prefix), then authorize. Use `GET /api/services/status` to inspect service configuration. The code is kept only for the current page session. Do not enter any upstream provider API key into Swagger.

[ReDoc](https://fourward.thangqt.com/redoc) provides a reading-oriented view; [runtime OpenAPI JSON](https://fourward.thangqt.com/openapi.json) supports client generation. The pages/spec are public documentation; protected API calls still require the shared code. Try-it-out calls are real operations, including charged generation. The NDJSON schema describes individual streamed lines; a native streaming adapter is still needed.

## Authentication and state

Call public `GET /api/health` first. `access_required` indicates whether the shared access gate is enabled. This is a demo-wide access code, not user login, OAuth, or tenant isolation: wardrobe items and learned styling memory are shared by everyone using this deployment.

For native clients, send `X-Shadow-Access: <access-code>` on every protected HTTP request, including image downloads. Store it in Keychain/Keystore. Alternatively, `POST /api/access` with `{"code":"..."}` creates the `shadow_access` HttpOnly cookie with a 12-hour lifetime; use a persistent cookie jar for API and image requests. Do not substitute the raw access code as the cookie value: the server derives it. Health and access endpoints are public. Browser query-code links exist, but native clients should use the header and avoid putting codes in URLs.

No AI provider key belongs in the mobile app. OpenRouter, Hypertek, Vidu and other provider secrets remain on the backend. Returned live-session credentials and the Snap app token are the limited client-side credentials those SDKs require; do not log them.

There are no user-account, durable conversation-list, or conversation-delete endpoints. Create a new chat locally by clearing the conversation ID and bootstrap history. The server will expire old conversation memory automatically.

## Recommended app flow

1. Fetch health, wardrobe items, `/api/tryon/config`, and optional voice/image/AR configs. Hide or explain unavailable features.
2. Keep the user's selected city in native settings. Send `configured_city` on every chat turn; it is an app setting, not inferred GPS. Ask for location permission only if using coordinates with the weather/stylist HTTP endpoints.
3. Upload clothes through `POST /api/wardrobe/items`, or import an allowed shop URL through `/api/wardrobe/import`. Use returned IDs and URLs rather than local filenames.
4. Chat through `/api/conversation`. Render streamed content and outfit cards in arrival order. Let the model call backend tools; the phone does not execute internal AI tools directly.
5. When the user taps an outfit's photo try-on button, capture/upload their photo and send the card's ordered `item_ids` to `/api/tryon/photo`. Do not start paid generation merely because a card or navigation action appeared.

## Chat transport: NDJSON, not SSE

`POST /api/conversation`, JSON request, response `application/x-ndjson`:

```json
{
  "text": "Suggest an outfit for dinner tonight",
  "configured_city": "Hà Nội",
  "history": []
}
```

Later turns include `conversation_id` returned by a successful `done`. Optional `history` is a list of at most six `{ "role": "user" | "assistant", "text": "..." }` records; it bootstraps new/expired sessions, rather than replacing live server history. An optional `image` is a small JPEG data URL (maximum 500,000 characters). Images are only inspected when the model invokes its vision tool. Each text field is limited by the request schema.

Decode UTF-8 incrementally and buffer until a newline. One network chunk may contain half a JSON object, several objects, or half a Unicode character. Do not use SSE's `data:` parser. Process each complete line as JSON; ignore unknown event types for forward compatibility. A stream that closes without `done` or `error` is interrupted, not successful. HTTP status errors can occur before streaming; after HTTP 200, failures arrive as `error` events.

| Event | Native UI handling |
| --- | --- |
| `text` with `text` | Append text to the current text segment. A later text segment can follow cards or tool activity. |
| `tool_status` with `id`, `state`, `label` | Insert activity at its first arrival; update the same row by ID for `running`, `done`, or `error`. Do not move all activity above the answer. |
| `outfits` with `outfits` | Insert up to three cards at this point in the timeline. Use their actual IDs, ordered garment lists and image URLs. |
| `action` with `route`, `label` and optional `items`, `id`, `prompt` | Map a known route to a native destination and show a user-controlled action. Unknown routes should be ignored. Never interpret it as an arbitrary URL or execute generation automatically. |
| `done` | Commit the completed transcript and optional `conversation_id`. Mock mode may omit the ID. |
| `error` with `text`, `code`, `retryable` | Mark the turn failed, retain already displayed partial output as partial, and let the user choose whether to retry when `retryable` is true. Codes are `conversation_capacity`, `conversation_busy`, and `conversation_unavailable`. An error need not be followed by `done`. |

Example event order (illustrative, not a fixed sequence):

```jsonl
{"type":"tool_status","id":"call_1","state":"running","label":"Đang xem tủ đồ"}
{"type":"tool_status","id":"call_1","state":"done","label":"Đã xem tủ đồ"}
{"type":"text","text":"Đây là bộ phối phù hợp cho buổi tối."}
{"type":"outfits","outfits":[{"title":"Dinner","item_ids":["shirt-id","jacket-id"],"items":[]}]}
{"type":"done","conversation_id":"6d39cfaa-e454-4469-a0ec-e05cb2bef780","source":"tools","suggestions":[]}
```

The generated schema is authoritative for optional fields; outfit objects are extensible. Show garment images in cards, retain multiple tops as intentional layers, and put photo/live try-on controls on the cards. Do not add a separate “prepared options” label or duplicate standalone try-on control for a displayed outfit.

Server conversation context is in memory: up to 128 sessions, 12 hours, 10 complete turns and approximately 60,000 retained characters. Restart loses it. Keep the displayed timeline locally and the last six plain-text history records for bootstrap. Treat the conversation UUID as a capability. Only one request per conversation may be active; disable duplicate sends. Only a completed turn commits server context. A cancelled/interrupted turn is not guaranteed to roll back external tool work.

## AI tools and data flow

The chat coordinator uses model function calls through the existing custom provider harness. `/api/intent` is a legacy classifier, not a required step before chat. See [conversation architecture](CONVERSATION.md).

`list_wardrobe` reads inventory and taught rules, with a default/max page size of 50 and offset pagination. This pagination applies to the internal tool; HTTP `GET /api/wardrobe/items` returns the full array. `suggest_outfits` reads the complete wardrobe internally, reuses retained weather and user-stated style preferences, and stores proposed outfits. `showcase_outfits` emits its introduction followed by grounded cards, and completes the turn. Backend validation rejects invalid IDs; it does not replace AI layers with a one-top rule.

`get_weather` uses the configured city unless the user provides another city; its forecast is retained for suggestions. Changing configured city invalidates that retained weather. The standalone stylist HTTP endpoint independently obtains weather through the backend cache; it does not accept an arbitrary frontend forecast object.

`get_style_profile` and `update_style_profile` manage optional self-described preferences within the conversation. These are distinct from `/api/stylist/profile`, which exposes deployment-wide learned styling memory. Gender and age are optional user-provided information, never inferred from photos. `inspect_image` calls the vision model only when an image is attached. `list_skills`, `get_skill`, and `open_screen` supply learned-skill information and native navigation hints. Full function schemas are in [tools.json](api/tools.json); there is no public generic “execute tool” endpoint.

## Images, wardrobe and styling

Image uploads are JSON data URLs, not multipart files: `data:image/jpeg;base64,...`. Resize phone images to a long edge of about 1024 pixels and compress before encoding. Wardrobe and photo fields allow up to 8 MiB decoded image data; the global request cap defaults to 16 MiB. Chat's image field is much smaller. The server enforces a 40-million-pixel Pillow limit. Use the field-specific limits in OpenAPI; base64 expands binary size by roughly one third.

```json
{"image":"data:image/jpeg;base64,...","name":"White shirt","category":"top"}
```

Categories are `top`, `bottom`, `dress`, `outerwear`, `shoes`, and `accessory`. Adding an item returns the item object directly; listing returns an array directly. Updates currently use PUT with partial JSON fields, rather than replacing the entire record. IDs are opaque strings. Do not assume response wrappers such as `{ "items": [...] }`. Preserve unknown item attributes when round-tripping editable data.

`POST /api/stylist/suggest` is the standalone styling flow. Send `event`, optional `city`/`lat`/`lon`/`date`, `current_image`, `anchor_item_id`, notes, and optional `clothing_preferences`, `gender`, `age_range`, `fit_preferences` fields. See the generated request schema for exact defaults and bounds. The response includes weather, outfits, selected item objects, styling-rule references, source and feedback context. An empty wardrobe returns 409. Feedback can be sent to `/api/stylist/feedback`; persist returned context rather than inventing item IDs.

Weather dates use ISO `YYYY-MM-DD`; default “today” uses UTC+7 and forecasts are limited to the next 14 days. The backend caches forecasts for 30 minutes.

## Photo, live and AR try-on

`GET /api/tryon/config` describes configured photo/live capability. This reports configuration, not an upstream credit or SDK health check.

```json
{"person_image":"data:image/jpeg;base64,...","item_ids":["shirt-id","jacket-id","pants-id"]}
```

`POST /api/tryon/photo` accepts one to eight IDs. Keep the selection order for layers. It returns `image_url`, `provider`, `seconds`, `rendered_item_ids`, `skipped_item_ids`, and optional explanatory fields. Inspect the returned IDs and display skipped-item or fallback notices. Resolve the output URL against the backend and attach authentication while downloading. The direct Gemini and optional OpenRouter paths use Nano Banana 2.1, one 1K image, and all available selected references in one request. The current deployment selects direct Gemini (`provider:gemini`). Legacy providers have different capabilities. Missing image references can be skipped.

Generation can take minutes. Use a 300-second client timeout for photo try-on and let the user cancel. A disconnected request stops backend waiting and avoids saving the result; it cannot guarantee cancellation or refund of an already accepted upstream generation. Never automatically retry a paid POST after an ambiguous timeout.

Live video is a separate SDK integration, not a REST image response. `/api/tryon/live-session` creates Vidu session details from a `reference_image` data URL; `/api/tryon/live-token` is the legacy Decart token path. Do not assume the legacy token endpoint is the Vidu transport. Inspect `backend/vidu_live.py` and `web/js/vidu-live.js` when implementing a native SDK adapter; REST alone is insufficient for the media session. Apply the session limits returned by the server and close SDK sessions when backgrounded.

`GET /api/tryon/ar/config` returns `available:false` until Snap app token, Lens group and Lens IDs are configured. The current web AR feature applies a fixed Lens; it does not bind wardrobe images to a Lens. Native AR requires the native Snap Camera Kit SDK and app/platform configuration. Treat it as a separate preview capability.

## Voice, generated images and learned skills

- `/api/voice/config`: discover backend audio support. `/api/voice/transcribe` takes raw audio bytes, maximum 4 MiB, with supported `Content-Type` (`audio/webm`, `video/webm`, `audio/mp4`, `audio/mpeg`, `audio/ogg`, `audio/wav`); it does not take multipart form data. It returns `{ "text": "..." }`.
- `/api/voice/speech`: JSON text request; response is binary `audio/wav`. Use native audio playback and stop it when the user interrupts.
- `/api/images/config` and `/api/images/generate`: separate prompt-based image studio, not wardrobe photo try-on. Size constraints are in OpenAPI.
- `/api/skills` and `/api/skills/{skill_id}`: learned-skill listing and detail. Teaching compile, frame and verification endpoints are included in the HTTP reference; some responses remain extensible JSON.
- `/session/control`: WebSocket guidance protocol, outside OpenAPI's HTTP scope. Connect with the same access header/cookie. Server sends `session.ready` with a session ID and suggested vision image size, followed by `hud.state`. Client messages include `session.start` with `skill_id`, `session.stop`, `debug.step`, and `vision.frame` with image data URL. Frames are latest-wins, not an upload queue. Server emits HUD, speech and vision events; see `backend/session_manager.py`, `session_workflow.py`, `vision_runtime.py` and the web guide client before implementing. WebSocket state is connection-scoped and is not restored by reconnecting.

## Errors, retries and limits

Errors use `code` for machine handling, `message` for display, and `retryable` for retry policy. Optional `details` holds structured field-validation issues or provider metadata; optional `service` identifies a failing service. Validation issues include `loc`, `message`, and `code`, without echoing submitted inputs. The old `detail` field is no longer emitted.

| Status | Handling |
| --- | --- |
| 401 | Ask for the access code again; provider-auth failures may instead be surfaced as 503. |
| 404 | Refresh stale item/skill selection. |
| 409 | Resolve state, such as an empty wardrobe, before retrying. |
| 413 / 415 / 422 | Fix size, media type, or fields; do not retry unchanged. |
| 429 | Back off, honor `Retry-After` when present, and avoid duplicate requests. |
| 499 | Client disconnected/cancelled. |
| 502 / 503 / 504 | Show a service error; retry safe reads with bounded backoff. Let users explicitly retry costly writes. |

Default limits are per IP and deployment-wide, not per user. Photo generation is capped at two concurrent requests; requests are also constrained by token buckets (photo: six/minute with burst four; chat: 45/minute with burst 12) and general API/write limits. Many phones behind one network share those buckets. Deployment settings can change them. No idempotency-key contract exists today; do not silently replay item creation, paid generation, teaching, or feedback writes.

Suggested client timeouts: 20 seconds for ordinary CRUD, 15 seconds for weather, 90 seconds for item tagging, 180 seconds for streamed chat/stylist, and 300 seconds for photo generation. Preserve cancellation semantics and show progress without fabricating completion percentages.

## Regenerate contracts

Run in the app's Python environment:

```sh
python scripts/export_api.py
python scripts/export_api.py --check
```

Without host Python dependencies, use the already built image (the mounts let it see the checkout):

```sh
sudo docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace" -w /workspace shadow-merge-check \
  python scripts/export_api.py
```

Generation imports the application and its tool schemas without starting the app lifespan or calling providers. It writes OpenAPI JSON, an HTTP endpoint reference, and the AI function schema manifest. Commit regenerated artifacts alongside API changes. OpenAPI generators can supply ordinary HTTP clients, but NDJSON, binary media authentication, live SDKs and WebSockets require native adapters. Some legacy endpoints still return arbitrary JSON; avoid pretending generated DTOs fully describe those payloads.

## Service status and actionable failures

Call authenticated `GET /api/services/status` on app startup, when a feature screen opens, or after a service error. It reports `llm`, `chat`, `vision`, `stylist`, `teaching`, `photo_tryon`, `live_tryon` (Vidu), `legacy_live_tryon` (Decart), `image_generation`, `voice`, `ar_tryon` (Snap), and `weather`.

Each entry includes `provider`, `state`, `configured`, `required_configuration`, `missing_configuration`, a safe `message` and `code`, its shared `dependency`, and optional `last_observation`. States:

- `configured`: local settings are present; credentials, credits and connectivity are **not verified**.
- `unconfigured`: required configuration is missing; show the missing setting names to the administrator.
- `invalid_configuration`: settings or a required component are invalid/incomplete.
- `disabled`: the optional feature is turned off.
- `demo`: the feature uses mock/offline behavior, rather than real AI.
- `degraded`: a real request recently failed; inspect its safe error code and message.

Status never performs a paid generation or validates an account balance. Recent observations are process-local, last five minutes, and reset on restart. Successful requests clear recent failures for that service. The LLM observation is shared by its dependent features because they use the same account/transport; it is not independent proof that every model works. Snap/browser SDK failures cannot be detected by this backend-only status endpoint. Client network reachability still needs handling on the device.

HTTP errors share a compact contract (`code`, `message`, `retryable`, and optional `service`/structured `details`):

```json
{
  "message": "OpenRouter không đủ credit. Hãy nạp credit rồi thử lại.",
  "code": "insufficient_credits",
  "service": "photo_tryon",
  "retryable": false
}
```

Service errors use `missing_api_key`, `invalid_configuration`, `service_disabled`, `provider_auth_failed`, `insufficient_credits`, `billing_required`, `rate_limited`, `provider_timeout`, `provider_unavailable`, `provider_request_rejected`, or `invalid_provider_response`. Other HTTP errors include `invalid_access_code` (wrong code supplied to `/api/access`), `access_required` (missing/invalid credentials on a protected request), `not_found`, `validation_error`, `state_conflict`, `payload_too_large`, `unsupported_media_type`, `request_cancelled`, and `internal_error`. `service` is optional for generic errors. Treat unknown codes generically and display `message` to the user.

Missing/invalid keys and exhausted credit are non-retryable until an administrator fixes the account. Provider credential failures map to HTTP 503, rather than 401, to avoid confusing them with the app access gate. Transient connection failures, rate limits and timeouts may be retryable; this permits an explicit retry, not automatic replay of a paid operation. Inspect `Retry-After` when supplied.

For streamed chat, HTTP 200 errors retain `code:conversation_unavailable` and add `service:chat`, underlying `reason_code`, and `retryable`; use `reason_code` to distinguish account/setup problems from transient outages. Capacity/busy errors retain their existing conversation codes. Existing config endpoints still expose their compatibility fields; the consolidated status endpoint provides the detailed status view.

Access-code rejection example (HTTP 401):

```json
{"code":"invalid_access_code","message":"Mã truy cập không đúng.","retryable":false}
```

Use `code` for client branching and `message` for display. Optional `details` contains structured diagnostics only. Correcting an access code is a new request; `retryable:false` means do not automatically resend the same invalid value. Existing middleware codes `busy`, `quota`, and `too_large` remain supported for compatibility.

## Optional Gemini Live voice

[Gemini Live integration](GEMINI_LIVE.md) documents the Google AI Studio/Developer API provider, `/api/voice/live/config`, and `/session/voice` audio WebSocket contract. This is separate from `/api/voice/transcribe` and `/api/voice/speech`. The backend holds the Google key and executes app tools; native clients send 16 kHz PCM and play 24 kHz output. The feature is off until explicitly configured and does not replace typed HTTP chat.

## Weather fallback

Open-Meteo is primary; wttr.in is the keyless fallback for network/provider/malformed-data failures, including primary geocoding outages. Weather JSON adds `provider`, `fallback`, and optional `fallback_reason`, while preserving existing fields. Both successful providers are cached for 30 minutes. wttr.in normally covers three days; a requested date must match exactly or the backend reports unavailable. See [weather behavior](WEATHER.md) for timeout budgets, provider-specific day boundaries and diagnostic statuses.

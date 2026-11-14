# Model-driven conversation

Real chat uses the configured `CONVERSATION_MODEL` as its coordinator. It sends
function schemas through the existing Hypertek Chat Completions provider. It does
not run fixed phrase routing or Jev first. `/api/intent` remains available to older
clients, and offline mock conversations retain their deterministic behavior.

The coordinator can read wardrobe items and styling rules, fetch weather, suggest
outfits, read learned skills, inspect an attached camera image, remember explicitly
stated style preferences, and offer app navigation/try-on actions. Vision runs as
`inspect_image` using `VISION_MODEL`; the vision endpoint does not need to support
tool calling. No camera image is automatically sent to the chat model.

The client supplies foreground GPS in `location`; coordinates are never inferred by
the model. The weather tool prefers GPS, while an explicitly requested or configured
city remains supported. There is no default city. Changing coordinates or the
configured city invalidates the retained forecast. Requested dates, seasons and
temperatures override current weather; scenario results use `source=requested` and
`is_forecast=false`. Weather tool results are retained for the coordinator to use when choosing outfits.
The model can request a new forecast when location/date changes. Outfit IDs,
layer order, tool results and assistant messages remain available on follow-up
turns. The coordinator chooses outfits directly; chat never invokes the standalone stylist model, rule picker, scoring or reranking. The coordinator validates IDs and tool arguments; it does not copy the entire wardrobe into every conversation prompt.

Clothing preferences, optional self-described gender, age range and fit
preferences are supplied by the user. The assistant asks for relevant missing
preferences, accepts a decision to skip, and must not infer gender or age from
camera images, names or clothing. These preferences currently belong to the
conversation, not a shared global user profile.

## API and state

`POST /api/conversation` accepts `text`, optional legacy text `history`, optional
`image` data URL, optional `location` (`lat`, `lon`), optional `configured_city` from app settings, and optional `conversation_id` UUID. NDJSON events include `text`, `action`, `error`, and `done`, plus `tool_status`
(short running/completed/error labels) and `outfits` (grounded item IDs and photo URLs
for chat cards). A successful `done` includes `conversation_id`.
Clients send it on later turns; they should retain the legacy text history to
bootstrap a replacement session after server restart or expiry.

Server memory retains up to 128 conversations for 12 hours, with at most 10
complete turns and approximately 60,000 characters of retained tool/text history
per conversation. Entire turns are removed together to avoid orphaned tool
results. Raw camera images are not retained. Server restart clears this memory.
The UUID is an unguessable capability: do not share or log it unnecessarily.
Browser tab sessionStorage holds the conversation ID and recent transcript.

Only a completed answer commits a turn's messages and tool context. A concurrent
request to the same conversation gets an error. The loop permits eight model
rounds, twelve calls, 90 seconds per tool and 150 seconds overall. Tool arguments
are validated against Pydantic schemas; unknown tools and service errors return
bounded error results for the model to explain or recover from.

`open_screen` returns a user-clickable action. It does not start a camera session,
perform paid generation, or claim that navigation already occurred.

## Provider verification

Live probes of Hypertek confirmed automatic/multiple/streamed function calls and
tool-result follow-ups for `qwen3.6-35b-a3b`. The deployed vision endpoint rejects
tool calls because its tool parser is disabled. Forced-call completion reasons
were inconsistent, so the harness uses assembled `tool_calls`, validates complete
arguments, and rejects truncated replies rather than trusting only a finish label.

The coordinator reads `list_wardrobe` (50 items per page, follow `next_offset` as needed), then selects outfits using actual IDs and the conversation, weather and preferences. It calls `showcase_outfits` with `{introduction,outfits:[{title,item_ids,reason,tips}]}`. The backend validates bounded arguments, unique IDs per outfit and current inventory membership, attaches authoritative item metadata/images, and preserves all chosen layers and their order. Invalid/deleted IDs reject the whole call so the model can refresh inventory and retry; no partial rewritten outfit is shown. Multiple tops are allowed. There is no `suggest_outfits` chat tool or second stylist model request. Cards include try-on buttons; repeated actions do not add standalone buttons.

A successful final `showcase_outfits` call completes the turn after displaying its
introduction and cards, without another model request just to acknowledge the UI.

## Carry-over onto the teammate’s main implementation

The function harness was selectively cherry-picked from `353bac2` and adapted to `99a49ef`. The competing backend-relay Live implementation, image providers, seed tooling and deployment changes from the older feature branch were not imported.

Text `/api/conversation` uses model-chosen function calls. Offline/mock mode keeps the existing deterministic demo behavior. A camera attachment remains with the conversation coordinator and is inspected only through the explicit vision tool.

Live preserves the teammate’s direct Gemini connection, server-minted constrained ephemeral tokens, shared camera, teaching workflow and Xmax coordinator. New tokens declare the same wardrobe/weather/profile/skill/showcase tools, plus `camera_task`; they do not declare `classify_request` or require a Decisions call on every turn. The deprecated `/api/live/classify` route and client adapter remain for already-issued/older sessions only.

`POST /api/live/session` accepts optional `configured_city` and the existing recent text history. It returns `toolSessionId` alongside the existing ephemeral token and socket details. Starting a session supplies instructions, selected city and tool definitions; it does not query or inject the wardrobe. Native camera frames and text/audio still flow directly to Gemini.

`POST /api/live/tool` accepts `{session_id,call_id,name,arguments}` and optional current camera state/recent history. It executes validated tools on the server and returns `{result,events}`. Events reuse `text`, `outfits` and `action`; the client shows tool progress and feeds the actual result back to Gemini using the matching call ID. `camera_task` returns a validated camera decision to the existing client coordinator; only its real `execution` result is sent back as completed camera state. Ordinary teacher narration is recorded by the app without a separate classifier call. No model-derived confidence score is fabricated for function calls.

Tool sessions are opaque UUID capabilities, process-local, capped at 128 and expire after five minutes. Each session serializes calls and permits at most sixty unique IDs. Duplicate IDs with identical arguments reuse the result; changed arguments return 409. Unknown/expired sessions return 410; exhausted call budgets return 429. The client also bounds calls per turn and suppresses results/events after cancellation or switching turns. Protected endpoints retain the shared access gate and existing rate limits; these are not per-user accounts.

`list_wardrobe` reads at most fifty actual items per page plus taught rules. `showcase_outfits` accepts the coordinator’s own selections and sends grounded images to chat cards. The coordinator reads additional inventory pages when needed; no 80-item stylist prompt is involved. Weather and style preferences are retained in the active tool session rather than fetched again for every suggestion. The existing standalone stylist scoring and Xmax garment/category limits remain main’s implementation. Live state is connection-scoped; only the recent visible text/cards are retained in browser session storage.

### What upstream main did before this port

Live required `classify_request` before each answer, made a separate Gemini Decisions request, and returned an inventory excerpt only for wardrobe-related intents. That excerpt included up to 48 search-ranked/category-balanced items and 12 styling rules. It was not a complete wardrobe dump at Live startup. Text chat used intent routing with dedicated wardrobe/outfit/weather branches, and only its generic fallback streamed directly from the conversation/vision model.

Live weather tool requests also carry fresh optional `location` and `request_text`
from the current transcript. `get_weather.request` can supply an explicitly spoken
season/temperature when transcription is unavailable. GPS is requested only for a
weather tool call; ordinary Live audio remains free of classifier/weather calls.

## Live presentation and execution

Live `showcase_outfits` displays a dismissible dark glass suggestion panel with the app’s yellow accent over the shared video, with garment images, alternative outfits and an optional try button. It does not add clothing cards to the transcript. Spoken acceptance uses `camera_task(intent="tryon_live")` directly. The visible outfit IDs are included as context for resolving follow-up requests, while currently worn IDs remain preferred for changes. The client returns actual camera execution to Gemini before it claims success.

Live tools have transport-specific descriptions: inventory/weather are reused, skills start through camera tasks, and `open_screen` only offers explicitly requested navigation away from the camera. It cannot offer a live fitting/teaching/guidance button. `set_live_view` accepts `original`, `tryon`, `dismiss_outfits` or `show_outfits` and returns actual client UI execution. Comparing original and generated video keeps the Xmax session running; stopping uses camera task cancellation. While fitting, a compact garment strip supplies the same original/try-on controls and a stop action. Text chat retains inline cards and navigation actions.

## Live-first entry and weather

`#/conversation` remains the default entry. Other page routes remain accessible; no redirects were added. The Tủ đồ and Bài đã học navigation links still open their existing pages; transcript try-on actions are handled in the current camera screen. A clear-chat control aborts current requests, clears visible/persisted conversation state and starts a fresh Live session when Live was active. Xmax starts/stops through voice camera tasks or in-page controls. New suggestions from both Live and HTTP fallback render on the video overlay; restored history no longer renders old garment cards.

Weather now uses wttr.in directly as primary, with exact-day validation, a six-second provider budget and 30-minute cache. Open-Meteo geocoding/forecast is only attempted if wttr.in fails or lacks the requested day. Concurrent weather reads share the cached result.

Text chat now exposes `start_tryon` with exact item IDs or a one-based recent outfit number, and `stop=true` for stopping. It emits an executable in-page camera action, rather than a navigation button. The client starts the existing camera task; completion/failure appears in actual task status. Shoes/accessories remain in recommendations but are excluded from Xmax execution; unsupported garment combinations produce a clear error instead of silently dropping layers. Production credentials belong in `/var/lib/shadowdeploy/deployment/.env`, not the checkout's `.env`.

Suggestions now contain exactly one current outfit. “Bộ khác” asks the active AI stack for a replacement rather than cycling hidden alternatives. Original-camera comparison hides the SDK container with display suppression (keeping its session alive), and optional status/tip nodes are omitted rather than inserted as literal null text.

The Google account-error classifier was carried over from the prior branch. Session HTTP failures and direct WebSocket errors distinguish depleted credit, billing disabled, auth failures, quota and connection errors without displaying raw provider data. Gemini startup can mint a token even when Live setup will be rejected for insufficient credit. A Live failure automatically switches the UI to the regular tool-driven conversation stack with browser speech input/output (or configured transcription), while keeping camera frames available for the vision tool and preserving the current outfit. Missing Live configuration keeps the same regular chat available. Gender/age questions are optional and never gate suggestions; visible clothing/style cues can inform advice but gender identity is not inferred from appearance.

## Teaching and learning in the shared camera

Teaching starts with `lesson_control(start_teaching)`. Spoken narration and sampled frames stay in the camera task. `finish_teaching` compiles an AI draft and shows a read-only review overlay with captured reference images and ordered instructions; it no longer saves automatically. The user can request rename, instruction/tip/warning changes, insert/delete steps or reorder them through conversation. Changes require the current revision, update linked tasks and transitions, and instruction changes supply a new matching completion detector. Edits do not claim the original self-check still applies. `save` or the confirmation button persists only after the user's approval. Failed saves preserve the draft; cancellation prevents late compiler results from restoring a closed lesson. New recordings get unique skill IDs; editing a saved skill opens a draft before an explicit save updates that skill.

`lesson_control(start_guidance,skill_id)` uses the shared camera and the existing `/session/control` detector engine. The overlay shows the current instruction, hints/warnings and progress; the engine can advance from observed frames. Voice `next`, `previous` and `repeat` use the same engine. It throttles frames and stops/reconnects with the screen lifecycle without opening a second camera. Gemini Live receives actual execution and revision snapshots in tool results. Regular chat receives a bounded lesson-state snapshot and executable in-page actions. For lesson actions, the stream issues a one-use opaque result token and waits for the client to POST the actual execution to `/api/conversation/action-result` before the next AI round. This lets a rename/edit/save sequence use the latest revision and prevents premature success claims. Pending actions are bounded to 128, expire after 90 seconds and are removed on disconnect; replay/expired tokens return 410.

The learned-skill library remains a separate accessible page. Its teach, practice and review/edit entry points now open the Live screen with an explicit lesson action. Legacy standalone routes remain accessible; no blanket redirects were added. Live has no text inputs for editing lesson steps. Lesson tools read saved skill data through the regular skill API instead of copying full detector prompts into the AI conversation. Active teaching/review state survives a Gemini-to-browser-voice fallback while the camera stays open.

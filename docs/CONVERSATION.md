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

The selected city is injected as app configuration, not inferred GPS location.
The weather tool uses it when no explicit city is supplied. Changing the configured
city invalidates the retained forecast. Weather tool results are retained and passed directly into `suggest_outfits`.
The model can request a new forecast when location/date changes. Outfit IDs,
layer order, tool results and assistant messages remain available on follow-up
turns. The tool delegates outfit generation to main’s existing stylist implementation, including its current rule review and ranking. The coordinator validates IDs and tool arguments; it does not copy the entire wardrobe into every conversation prompt.

Clothing preferences, optional self-described gender, age range and fit
preferences are supplied by the user. The assistant asks for relevant missing
preferences, accepts a decision to skip, and must not infer gender or age from
camera images, names or clothing. These preferences currently belong to the
conversation, not a shared global user profile.

## API and state

`POST /api/conversation` accepts `text`, optional legacy text `history`, optional
`image` data URL, optional `configured_city` from app settings, and optional `conversation_id` UUID. NDJSON events include `text`, `action`, `error`, and `done`, plus `tool_status`
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

Outfit computation and presentation are separate: `suggest_outfits` retains results without
rendering them; `showcase_outfits` emits a short model-written introduction followed by
the selected cards. Cards include try-on buttons. Presentation tools have no progress
labels, and repeat try-on actions for displayed outfits do not add standalone buttons.

A successful final `showcase_outfits` call completes the turn after displaying its
introduction and cards, without another model request just to acknowledge the UI.

## Carry-over onto the teammate’s main implementation

The function harness was selectively cherry-picked from `353bac2` and adapted to `99a49ef`. The competing backend-relay Live implementation, image providers, seed tooling and deployment changes from the older feature branch were not imported.

Text `/api/conversation` uses model-chosen function calls. Offline/mock mode keeps the existing deterministic demo behavior. A camera attachment remains with the conversation coordinator and is inspected only through the explicit vision tool.

Live preserves the teammate’s direct Gemini connection, server-minted constrained ephemeral tokens, shared camera, teaching workflow and Xmax coordinator. New tokens declare the same wardrobe/weather/profile/skill/showcase tools, plus `camera_task`; they do not declare `classify_request` or require a Decisions call on every turn. The deprecated `/api/live/classify` route and client adapter remain for already-issued/older sessions only.

`POST /api/live/session` accepts optional `configured_city` and the existing recent text history. It returns `toolSessionId` alongside the existing ephemeral token and socket details. Starting a session supplies instructions, selected city and tool definitions; it does not query or inject the wardrobe. Native camera frames and text/audio still flow directly to Gemini.

`POST /api/live/tool` accepts `{session_id,call_id,name,arguments}` and optional current camera state/recent history. It executes validated tools on the server and returns `{result,events}`. Events reuse `text`, `outfits` and `action`; the client shows tool progress and feeds the actual result back to Gemini using the matching call ID. `camera_task` returns a validated camera decision to the existing client coordinator; only its real `execution` result is sent back as completed camera state. Ordinary teacher narration is recorded by the app without a separate classifier call. No model-derived confidence score is fabricated for function calls.

Tool sessions are opaque UUID capabilities, process-local, capped at 128 and expire after five minutes. Each session serializes calls and permits at most sixty unique IDs. Duplicate IDs with identical arguments reuse the result; changed arguments return 409. Unknown/expired sessions return 410; exhausted call budgets return 429. The client also bounds calls per turn and suppresses results/events after cancellation or switching turns. Protected endpoints retain the shared access gate and existing rate limits; these are not per-user accounts.

`list_wardrobe` reads at most fifty actual items per page plus taught rules. `suggest_outfits` reads the full wardrobe internally for the stylist model; `showcase_outfits` sends grounded images to chat cards. Weather and style preferences are retained in the active tool session rather than fetched again for every suggestion. The existing standalone stylist scoring and Xmax garment/category limits remain main’s implementation. Live state is connection-scoped; only the recent visible text/cards are retained in browser session storage.

### What upstream main did before this port

Live required `classify_request` before each answer, made a separate Gemini Decisions request, and returned an inventory excerpt only for wardrobe-related intents. That excerpt included up to 48 search-ranked/category-balanced items and 12 styling rules. It was not a complete wardrobe dump at Live startup. Text chat used intent routing with dedicated wardrobe/outfit/weather branches, and only its generic fallback streamed directly from the conversation/vision model.

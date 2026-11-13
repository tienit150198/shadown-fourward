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
turns. The stylist model owns garment choices; backend scoring does not rewrite
AI outfits. Invalid item IDs and malformed tool arguments are still rejected.

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

## Optional native audio conversation

The Gemini Live relay is a separate, optional voice coordinator using the same validated server tool implementations. It provides speech-to-speech audio, transcripts, interruption/cancellation, progress and outfit cards. Existing typed chat continues to use `CONVERSATION_MODEL`; ordinary browser/Hypertek voice remains the default when Live is disabled. See [Gemini Live setup and protocol](GEMINI_LIVE.md).

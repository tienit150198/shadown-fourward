# Gemini Live voice (Google AI Studio)

Optional native audio conversation through the **Gemini Developer API**, using a Google AI Studio API key. This does not use Vertex AI or OpenRouter. The default model is `gemini-3.8-live`, configurable through `GEMINI_LIVE_MODEL` to a Live-compatible Gemini model available to your project.

## Enable

```dotenv
GEMINI_LIVE_ENABLED=on
GEMINI_API_KEY=your-google-ai-studio-key
GEMINI_LIVE_MODEL=gemini-3.8-live
GEMINI_LIVE_MAX_SESSION_SECONDS=180
GEMINI_LIVE_MAX_CONCURRENT=2
```

The same `GEMINI_API_KEY` also supports [direct Nano Banana photo try-on](GEMINI_IMAGE.md) when `TRYON_PHOTO_PROVIDER=gemini`. Photo generation does not require Live voice to be enabled.

Restart/recreate the app after changing `.env`. The feature defaults to **off**. No upstream Google call occurs until the user explicitly starts the microphone. When the feature is disabled or browser live-audio support is absent, the existing browser speech/Hypertek flow remains available. If configured credentials/model access fail at runtime, show the error; the app does not silently replay the conversation through another paid provider.

`GET /api/voice/live/config` reports availability, configuration errors, model, WebSocket path and audio rates. `/api/services/status` includes `gemini_live` with recent observed success/failure. Configuration checks do not validate Google credits, quota or model permissions.

Use a **Gemini Developer API key created in Google AI Studio**, not an OpenRouter key or OAuth bearer token. Keep it only in server configuration. The backend connects to:

```text
wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent
```

Authentication is the `x-goog-api-key` WebSocket upgrade header. The browser never receives that key. A relay connection does not require ephemeral tokens; those are for clients connecting directly to Google. This implementation uses the existing `websockets` dependency and the documented JSON protocol, rather than adding another SDK/framework.

## Behavior and tools

When enabled and supported, the conversation screen's microphone starts a Live session; tap again to stop. Audio is streamed in both directions without browser STT or browser TTS in the Live path. The existing text chat provider remains unchanged. Submitting typed input closes Live and resumes normal HTTP chat using recent text history.

The backend configures native audio output and input/output transcription. It reuses the app's validated function tools for wardrobe, weather, preferences, styling, learned-skill reads, showcase cards and user-clickable navigation. Tool progress and outfit cards are emitted to the UI in order. Paid try-on and camera navigation require a separate user click. `inspect_image` is excluded: this initial Live implementation is **audio-only**, and turning on the web camera does not stream video to Gemini.

The selected city and up to six recent user/assistant text records are sent as context. If an unexpired HTTP `conversation_id` is supplied, the relay copies its retained style preferences, weather and outfit context; changing city clears copied weather. This is a one-way snapshot, not a shared conversation lock or durable Gemini session. Voice-session changes stay in that connection's context. The web client saves visible transcripts/cards locally and clears the stale HTTP conversation ID after a Live turn, so future typed messages bootstrap from recent text. Older hidden tool context and preferences are not guaranteed to survive switching back to HTTP. No session resumption or automatic reconnect is implemented.

Input is continuous **16 kHz mono signed PCM16 little-endian**. The AudioWorklet resamples from the browser's actual capture rate and batches 60 ms packets. Output is **24 kHz PCM16**, scheduled through Web Audio. Google automatic voice activity detection detects turns. On interruption, queued playback is stopped. Microphone tracks, audio graph and sockets are closed on stop, cancellation, navigation or backgrounding. Permission prompts that resolve after cancellation also release their tracks.

## Native/mobile WebSocket contract

Authenticate to the backend with the same `X-Shadow-Access` header or `shadow_access` cookie as other protected calls. Browser WebSockets use the cookie established through `/api/access`; native clients can provide the header during the upgrade. Do not put the access code or provider key into a WebSocket query string.

Connect to `wss://fourward.thangqt.com/session/voice`. Within ten seconds, send:

```json
{
  "type": "start",
  "configured_city": "Hà Nội",
  "history": [{"role":"user","text":"I prefer loose fits"}],
  "conversation_id": "6d39cfaa-e454-4469-a0ec-e05cb2bef780"
}
```

Only `type` is required. `conversation_id` is optional and must be a UUID. History is at most six nonempty text records of at most 500 characters each. The city is at most 80 characters. Wait for `ready` before sending audio.

Client messages after ready:

| Type | Fields | Behavior |
| --- | --- | --- |
| `audio` | `data` base64 PCM16 | 16 kHz mono little-endian; batch about 60–100 ms. Maximum 32,000 decoded bytes per message; the general WebSocket message rate cap still applies. |
| `audio_end` | none | Marks an actual pause in microphone streaming. Do not send on every packet; continuous capture uses automatic VAD. |
| `text` | `text` up to 500 characters | Text input within this Gemini session, available to native clients. The current web UI switches to HTTP chat for typed input. |
| `stop` | none | Close the session. |

Server messages:

| Type | Fields | Client behavior |
| --- | --- | --- |
| `ready` | `model`, `max_session_seconds` | Start sending microphone PCM. |
| `audio` | `data`, `sample_rate:24000` | Decode PCM16 and queue bounded playback. |
| `input_transcription` | `text` | Append user transcript chunks. |
| `text` | `text` | Append assistant transcript/presentation text. Do not run separate TTS over it. |
| `interrupted` | none | Stop and flush queued audio playback immediately. |
| `tool_status`, `outfits`, `action` | same fields as HTTP chat | Render progress/cards/navigation without auto-executing actions. |
| `turn_complete` | none | Finalize this visible turn; keep listening. |
| `error` | `code`, `message`, `service:gemini_live`, `retryable` | Terminal failure; stop media. Explicit user retry only. |
| `closed` | `reason` | Clean termination, such as `session_limit` or `provider_session_limit`; stop media. |

HTTP streaming `done` and its UUID are **not** emitted by this WebSocket. A socket close without a terminal message is a disconnected session; do not fabricate a completed answer or retry automatically. Interruption may cancel a tool: the server cancels pending named tasks and suppresses late tool results. Work already completed cannot be rolled back.

## Limits and failure handling

The server enforces a default 180-second session cap (configurable up to 900), two concurrent Live sessions (configurable up to ten), twelve tool calls per turn and sixty unique tool calls per connection. Tool executions are sequential, bounded to 90 seconds, and responses are size-limited. Audio upload is limited to approximately real time with a small burst allowance. Existing same-origin, shared-code, per-IP WebSocket connection/message/concurrency guards also apply. Slow microphone upload or output playback queues are bounded and close with a readable error rather than growing indefinitely.

Google can reject the WebSocket setup with a close frame when prepaid credit is depleted. This is reported as non-retryable `insufficient_credits`, rather than a generic connection failure.

Account errors use `missing_api_key`, `invalid_configuration`, `provider_auth_failed`, `insufficient_credits`, `billing_required`, `rate_limited`, `provider_request_rejected`, `provider_timeout` or `provider_unavailable`. `service_disabled`, `service_busy`, `invalid_request`, `invalid_provider_response` and `tool_budget_exceeded` cover local setup, capacity and protocol failures. No raw Google errors, user audio, tokens or API keys are included in public diagnostics.

Costs depend on your Gemini project and model. Closing our relay stops local transmission and closes the provider socket, but already processed audio/tool calls can incur charges. Other existing tools may still call their configured Hypertek/stylist/weather providers; Gemini Live does not replace those dependencies. No paid provider test is performed by the unit suite.

## Official references

- [Live API WebSocket guide](https://aistudio.google.com/docs/live-api/get-started-websocket)
- [Live API wire reference](https://aistudio.google.com/api-reference/live)
- [Tool calling and cancellation](https://aistudio.google.com/docs/live-api/tools)
- [Audio, VAD and session capabilities](https://aistudio.google.com/docs/live-api/capabilities)
- [Ephemeral token guidance](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens)

The offline tests exercise relay setup/authentication, PCM/transcript forwarding, real validated tools, cancellation, malformed messages and duration cleanup. Frontend tests cover continuous resampling, PCM decoding and resource cleanup. A real browser/microphone and a Google project with access are still required to validate voice quality and model availability.

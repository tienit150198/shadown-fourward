# API and tool review for native mobile

Reviewed the custom model-tool loop, FastAPI routes, middleware authentication, streaming transports, image/voice flows, and API generation support.

## Improvements delivered

- OpenAPI now declares the actual shared-code authentication alternatives: `X-Shadow-Access` header or `shadow_access` cookie. Health/access stay public. Media requests carry the same gate.
- Added stable response contracts for health, access, wardrobe items, weather and photo try-on. Responses remain extensible; no runtime field filtering was introduced.
- Described raw voice uploads, WAV output and protected media as binary transports instead of arbitrary JSON.
- Documented shared middleware errors, validation/provider error variants, request caps, rate limiting and generation cancellation/timeouts.
- Added a discriminated union for the six NDJSON chat event types. The response explicitly describes one event per line rather than an array or SSE.
- Terminal chat failures now include stable `code` and `retryable` fields, while retaining the existing `text` message.
- Reduced `open_screen` tool selections to eight item IDs, matching the photo/live HTTP request limit. The model can no longer emit a navigation action that the try-on API rejects solely for exceeding its selection count.
- Cleared retained outfit suggestions when a suggestion request sees an empty wardrobe, preventing a later showcase from displaying stale outfits.
- Added deterministic OpenAPI, endpoint reference and tool schema export with a `--check` mode for stale artifacts.

## Current boundaries

The custom harness already supports validated tool arguments, bounded rounds/calls/timeouts, server-executed tools, ordered streaming UI events, and weather reuse. These targeted changes do not require adopting another AI framework.

The API remains a shared demo deployment, not an account-based mobile backend. The access code does not isolate users, and wardrobe/style memory is shared. Chat sessions are ephemeral capabilities in server memory. No conversation history/list/delete service exists. Do not present these capabilities as durable private user accounts.

Some legacy skill, stylist and upstream-session payloads remain extensible JSON. The checked-in schema documents stable fields and transport requirements, but does not fully type every provider's evolving response. Live Vidu and Snap integrations require native SDK work beyond generated HTTP clients. The guidance WebSocket is documented separately in the mobile guide because OpenAPI describes HTTP only.

There is no idempotency-key guarantee for writes. Network retries can duplicate item creation/feedback and repeat charged generation. Native clients should use explicit user retries for these operations. Cancellation stops local waiting, but cannot promise cancellation/refund upstream.

## Before a public multi-user mobile release

Prioritize real identity and per-user authorization/storage, durable conversation history with ownership checks, idempotent costly writes/job status, then versioned contracts and more fully typed legacy DTOs. These require product and data-model decisions and were not introduced implicitly into this shared deployment.

See [mobile integration](../MOBILE_API.md), [OpenAPI](openapi.json), [HTTP reference](REFERENCE.md), and [tool schemas](tools.json).

## Service diagnostics and errors

Added authenticated `/api/services/status` for credential-safe configuration and recent request outcomes, with no paid probes. HTTP failures expose `message`, `code`, optional `service`/structured `details`, and `retryable`; chat failures carry their underlying `reason_code`. Image, voice, LLM and live-provider account/auth/credit/rate/timeout failures are classified separately. Observations are temporary, not a monitoring history or a balance API. See the mobile guide for status meanings and retry behavior.

## Endpoint overlap and cleanup candidates

- Legacy candidates: `/api/intent` (main chat now uses function calls), `/api/tryon/live-token`, and `/api/tryon/live-prompt` (Decart path; the web live screen now creates Vidu sessions). No current web callers were found. Remove or deprecate these after checking external consumers.
- Active, distinct flows: `/api/ask` answers questions within the guided skill screen; `/api/conversation` is general streamed chat. `/api/stylist/suggest` supports the dedicated outfit screen while chat uses the same underlying stylist logic through tools. `/api/images/generate` is prompt-only image studio; `/api/tryon/photo` edits a person using wardrobe references.
- Configuration overlap: `/api/services/status` is the diagnostic overview; voice/image/try-on/AR config endpoints expose feature-specific capability and SDK settings used by existing screens. A future bootstrap endpoint could consolidate mobile startup reads, but deleting these config routes now would break callers.
- Specialist operations: teaching compilation/progress/frames/verification, guidance WebSocket, and evaluation capture are still used by their web flows. They can be grouped or hidden from a consumer mobile subset without being called obsolete.
- Removed: the demo-seeding route and frontend controls. Seeding is manual CLI only.

The native mobile surface should prioritize access/health/status, conversation, wardrobe CRUD/media, styling/feedback, and the chosen try-on transport. Additional teaching, audio, image studio and AR endpoints should be adopted only for features the native app implements.

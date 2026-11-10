# WebSocket Contract — `/session/control`

## Client-to-server messages

- `session.start` with `skill_id`.
- `session.stop`.
- `debug.step` with `forward|backward` (demo/debug capability).
- `vision.frame` with JPEG data URL, `ts` and `trigger`.

## Server-to-client messages

- `session.ready` with `session_id` and vision capability.
- `hud.state` with phase, current step/task and `speech_epoch`.
- `speech` with text, epoch and kind.
- `vision.result` with detector/result/latency and confirmation metadata.
- `hud.error` with safe user-facing error.

## Client invariants

- Client reads speech and state; it does not advance the server state itself.
- When result is confirming, client sends the next frame promptly.
- On disconnect, client reports session lost and starts a new session; V1 does not promise resume.
- Client must tolerate reordered/obsolete UI updates by using epoch/state semantics supplied by server.

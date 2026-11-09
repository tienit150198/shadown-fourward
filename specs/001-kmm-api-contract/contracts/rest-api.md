# REST Contract — KMM V1

## In scope

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Backend mode, model and access requirement |
| `GET /api/skills` | List selectable skills |
| `GET /api/skills/{skill_id}` | Read a skill and reference media |
| `GET /api/wardrobe/items` | Read public wardrobe items |
| `GET /api/weather` | Read weather context |
| `POST /api/stylist/suggest` | Generate outfit suggestions |
| `POST /api/stylist/chat` | Experimental short-lived conversational stylist turn |
| `GET /api/tryon/config` | Read live/photo provider capabilities |
| `POST /api/tryon/live-token` | Issue a short-lived live VTON client token |
| `POST /api/tryon/live-prompt` | Build a grounded live prompt from wardrobe item IDs |
| `POST /api/tryon/photo` | Generate a photo VTON result with provider fallback |

## Rules

- JSON uses UTF-8; examples must match runtime casing.
- When the demo access gate is enabled, native clients send `X-Shadow-Access`; cookie exchange is a browser concern.
- Images are JPEG data URLs when sent in request bodies.
- Client ignores unknown response fields.
- `source`/`provider` values are user-visible capability metadata, not internal debugging only.
- Error examples must include HTTP status and Vietnamese user-safe message.
- Q3 URL versioning remains provisional; do not publish `/api/v1` as committed contract yet.

## Experimental conversational stylist

`POST /api/stylist/chat` is additive and does not replace the stable
`POST /api/stylist/suggest` contract.

Minimal request:

```json
{
  "message": "Tôi đi đám cưới ở Hà Nội"
}
```

Continuation request:

```json
{
  "session_id": "3a83f65e-57b7-443f-915c-8f5e72ee9ef7",
  "message": "Áo này đang giặt"
}
```

Optional structured context is `event`, `city`, `date` and
`anchor_item_id`. `message` is required and limited to 500 characters.
The server returns:

```json
{
  "session_id": "3a83f65e-57b7-443f-915c-8f5e72ee9ef7",
  "phase": "recommendation",
  "assistant_message": "Mình đã chọn một vài bộ phù hợp với bạn.",
  "missing_slots": [],
  "outfits": [{"item_ids": ["item-a", "item-b"]}],
  "suggested_actions": ["Trang trọng hơn", "Đổi bộ khác", "Mặc thử bộ 1"],
  "state": {"state_version": 1, "turn_count": 1}
}
```

Contract constraints:

- Session state is in-memory only, expires after 30 minutes and is capped at 100 sessions per process. It is not durable and is not shared across replicas.
- The endpoint uses the same rate-limit budget as stylist suggestion. A client must handle `429` and retry only after user action/backoff.
- The response never contains raw prompts, transcript history, image data or provider secrets.
- Every outfit ID must exist in the current wardrobe. Anchor, exclusions and hard rules are enforced outside the LLM.
- In `SHADOW_MODE=mock`, the complete flow is offline and `source` is `mock`/deterministic. Mock results validate product flow and invariants, not real-model recommendation quality.
- Ordinary messages never write long-term preferences. A write requires the explicit `learning_action: "save_preference"` action with validated `learning_text`.

## Virtual try-on capability and limitations

- Read `GET /api/tryon/config` before showing provider-specific actions. `live.available`, `photo.provider`, `photo.chain`, `photo.renders_bottoms`, `photo.expected_seconds` and `photo.notice` are capability metadata.
- Live VTON is the primary experience when available. The browser receives only a short-lived client token; the permanent Decart key remains on the backend. Session duration/cost caps still apply.
- Photo VTON is the fallback. Some providers can render only tops or one-piece garments; when `renders_bottoms` is false the client must warn before generation and surface skipped item IDs.
- Request/navigation payloads contain primitive wardrobe item IDs, never conversation objects, camera frames or domain objects.
- All VTON surfaces must disclose: “Ảnh/video do AI tạo; màu, độ rủ và độ vừa có thể không chính xác.” A mock composite must additionally say “Ảnh minh hoạ”.
- VTON output is a visual style simulation. It is not a size recommendation and does not predict physical fit.

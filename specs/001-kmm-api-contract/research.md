# Research: KMM API Contract

## Facts verified from repository

- SHADOW backend là FastAPI modular monolith; PWA dùng REST và WebSocket.
- Guide endpoint là `/session/control`; client event gồm `session.start`, `session.stop`, `debug.step` và
  `vision.frame`.
- Backend gửi `session.ready`, `hud.state`, `speech`, `vision.result` và `hud.error`.
- Access gate hỗ trợ `X-Shadow-Access`; cookie phù hợp browser nhưng không nên là contract chính của KMM.
- Stylist endpoint là `POST /api/stylist/suggest`; output chứa weather, outfits, source và learning metadata.
- JSON/file persistence và Guide session RAM không cung cấp durable resume.
- Roadmap yêu cầu `docs/API.md`, Kotlin serialization DTO và Ktor examples.
- FastAPI tự sinh `/docs`, nhưng đây là reference runtime, chưa phải compatibility policy.

## Decision direction

Contract/documentation-first, giữ runtime URL hiện tại trong pilot. Không thêm `/api/v1`, auth system hoặc dependency
trước khi Q3/Q6/Q7 được chốt.

## Options considered

| Option | Đánh giá |
|---|---|
| Chép payload vào Markdown thủ công | Nhanh nhưng dễ drift; chỉ chấp nhận tạm thời trước Q7 |
| OpenAPI làm nguồn duy nhất | Hữu ích cho REST nhưng không mô tả đủ Guide state semantics và WebSocket behavior |
| DTO/contract thủ công + fixture test | Phù hợp KMM, kiểm soát semantics; cần giữ fixture đồng bộ |
| Generated DTO/client | Giảm drift nhưng thêm tooling/dependency; chưa phù hợp pilot khi Q7 chưa chốt |

## Open research

- Xác minh Ktor/kotlinx.serialization version hiện có trong `BaseKMM`.
- Chọn contract evidence: fixture, OpenAPI validation, generated DTO hoặc kết hợp.
- Chốt policy URL versioning.
- Chốt auth contract cho native client.

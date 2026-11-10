# Feature Specification: Hợp đồng API SHADOW v1 cho KMM

**Feature ID:** `001-kmm-api-contract`  
**Status:** Clarified V1 — Q3/Q6/Q7 provisional  
**Bounded contexts:** Guide Runtime, Wardrobe & Stylist, Platform/API  
**Primary client:** BaseKMM  
**Source architecture:** `docs/SYSTEM_DESIGN_ANALYSIS.md`

## Context

SHADOW có backend FastAPI và PWA reference nhưng API contract hiện phân tán trong code, README và roadmap. Đội KMM
cần tích hợp một phiên Guide và một lượt Stylist mà không phải suy luận payload từ PWA/Python.

Round 1 đã xác nhận:

- KMM là client sản phẩm tương lai; PWA tiếp tục là demo/reference.
- V1 chỉ bao phủ Guide + Stylist và dependency tối thiểu.
- DTO/contract nằm ở common layer của KMM; UI native tiêu thụ state đã chuẩn hóa.
- Guide disconnect không được giả lập là durable resume.
- URL versioning chính thức, authentication contract cuối cùng và contract-test mechanism đang chờ thông tin đồng nghiệp.

## Outcome

Tạo contract/documentation-first cho KMM: API reference tiếng Việt, DTO Kotlin, Ktor examples, error semantics và
compatibility rules. V1 không đổi runtime endpoint và không thêm dependency nếu chưa được phê duyệt.

## User stories

### US-001 — Chạy một phiên Guide

KMM có thể kết nối `/session/control`, bắt đầu một skill, gửi frame và hiển thị state/speech do server quyết định.

### US-002 — Nhận gợi ý phối đồ

KMM có thể gọi `/api/stylist/suggest`, hiển thị weather/outfits/rules/source và xử lý deterministic fallback.

### US-003 — Xử lý access và lỗi

KMM có thể gửi access header và phân biệt lỗi 401/404/409/422/429/499/500/503.

## Functional requirements

- **FR-001:** Tạo `docs/API.md` bằng tiếng Việt.
- **FR-002:** Mỗi endpoint/message có purpose, request, response, example và errors.
- **FR-003:** Mô tả handshake và message flow của WebSocket `/session/control`.
- **FR-004:** Phân biệt external client events và internal server events.
- **FR-005:** Mô tả `speech_epoch`, `confirming`, `step`, `phase` và vision errors.
- **FR-006:** Cung cấp Kotlin DTO dùng `kotlinx.serialization`.
- **FR-007:** DTO đọc response bỏ qua field mới chưa biết.
- **FR-008:** Cung cấp ví dụ Ktor REST/WebSocket.
- **FR-009:** Ghi cơ chế `X-Shadow-Access` cho KMM.
- **FR-010:** Ghi giới hạn ảnh, JPEG data URL, frame size và rate limit.
- **FR-011:** Mô tả AI failure, fallback và `source/provider` semantics.
- **FR-012:** Ghi compatibility policy cho Contract v1; URL `/api/v1` là quyết định provisional.
- **FR-013:** Xác định contract-test/fixture approach; implementation chờ Q7.
- **FR-014:** Ghi rõ Guide không durable-resume sau disconnect/restart.
- **FR-015:** Navigation KMM chỉ truyền primitive IDs.

## Scope

### In scope

`/api/health`, access header, skill read APIs, `/session/control`, wardrobe read API, weather, stylist suggest, media
URLs, Kotlin DTO và Ktor examples.

### Out of scope

Teach/compile/verify trên KMM, wardrobe CRUD, feedback/profile/examples, try-on, `/api/v1` aliases, authentication,
multi-tenant, durable resume, native app release và thay PWA.

## Acceptance criteria

- Một developer KMM mới có thể gọi Guide và Stylist chỉ từ `docs/API.md`.
- 100% contract trong scope có request/response/example/error behavior.
- DTO examples biên dịch trong `commonMain` nếu Q4 được triển khai bằng code.
- Backend behavior không thay đổi.
- Có fixture hoặc contract check khi Q7 được chốt.
- Android host tests và debug build pass nếu feature chạm `BaseKMM`.
- Tài liệu phân biệt `mock verified`, `integration verified` và `device/vendor not verified`.

## Deferred decisions

| ID | Decision | Status |
|---|---|---|
| Q3 | Giữ URL hiện tại trong V1 hay thêm `/api/v1` aliases | Provisional; chờ đồng nghiệp |
| Q6 | Access header hiện tại có đủ cho KMM pilot hay cần auth/session token | Provisional; chờ đồng nghiệp |
| Q7 | Fixture, OpenAPI schema check hay generated contract test | Provisional; chờ đồng nghiệp |

# Clarify Round 1 — KMM API Contract

**Date:** 2026-11-10  
**Status:** Direction accepted; Q3/Q6/Q7 pending colleague input  
**Source:** user response to Round 1

## Decisions accepted

| Question | Decision | Effect |
|---|---|---|
| Q1 — KMM role | Có | KMM là client sản phẩm tương lai; PWA là demo/reference trong giai đoạn chuyển tiếp |
| Q2 — Contract scope | Có | V1 chỉ bao phủ Guide + Stylist và dependency tối thiểu |
| Q4 — Kotlin DTO location | Có | DTO/client boundary nằm ở common layer; UI native tiêu thụ state chuẩn hóa |
| Q5 — Disconnect behavior | Có | V1 không hứa durable resume; client bắt đầu session mới sau disconnect |

## Decisions accepted in direction, pending colleague information

| Question | Current assumption | Must be updated when information arrives |
|---|---|---|
| Q3 — URL versioning | Documentation/DTO v1 trước; chưa cam kết `/api/v1` aliases | Chọn giữ path hiện tại hoặc thêm aliases/migration policy |
| Q6 — Native authentication | Dùng `X-Shadow-Access` cho pilot | Xác nhận access header hay thay bằng auth/session token |
| Q7 — Contract evidence | Cần fixture/schema/generated check; chưa chọn cơ chế | Chọn mechanism và exact verification command |

## Pending input protocol

Khi đồng nghiệp cung cấp thông tin:

1. cập nhật bảng này trước;
2. cập nhật `spec.md`, `plan.md`, `research.md` và task tương ứng;
3. bỏ nhãn `provisional` chỉ khi quyết định có owner và evidence;
4. nếu thay đổi scope, chạy lại `analyze` trước `implement`.

## Clarify outcome

Round 1 đủ để viết bản Spec/Plan/Tasks sơ bộ và giữ các điểm chưa chốt ở trạng thái có thể truy vết. Chưa đủ để coi
URL versioning, native authentication hoặc contract-test strategy là quyết định cuối cùng.

# M4 — Real-provider pilot

**Ngày thực hiện:** 2026-11-10  
**Trạng thái:** Đã tích hợp và kiểm thử contract; chưa đạt gate chạy thật vì workspace chưa có credentials, thiết bị và xác nhận legal/privacy.

## Quyết định provider

M4 chỉ chọn **một** photo provider: **FASHN Virtual Try-On `tryon-v1.6`**.

Lý do chọn:

- endpoint Stable, chuyên cho virtual try-on và hỗ trợ `tops`, `bottoms`, `one-pieces`;
- không phụ thuộc Mac mini/GPU cục bộ và không dùng model phi thương mại của CatVTON/IDM-VTON;
- chế độ `balanced` có thời gian công bố khoảng 8 giây mỗi output;
- gửi input bằng data URI và đặt `return_base64=true`, do đó request history chỉ chứa placeholder `<base64>`; output chỉ khả dụng qua status endpoint trong 60 phút;
- một output tốn một credit. Bộ áo + quần chạy hai lượt tuần tự nên tốn hai credit.

Nguồn chính thức: [FASHN try-on v1.6](https://docs.fashn.ai/api-reference/tryon-v1-6), [API fundamentals](https://docs.fashn.ai/api-overview/api-fundamentals), [data retention & privacy](https://docs.fashn.ai/api-overview/data-retention-privacy).

Live provider giữ nguyên **Decart Lucy VTON** qua client token ngắn hạn. Permanent API key chỉ ở backend; browser nhận token tạm. Nguồn chính thức: [Decart try-on examples](https://github.com/DecartAI/tryon-examples), [Lucy Virtual Try-On](https://platform.decart.ai/models/lucy-vton).

## Phần đã hoàn thành trong source code

- [x] Adapter `fashn` gọi `POST /v1/run`, poll `GET /v1/status/{id}` và xử lý trạng thái completed/failed/timeout.
- [x] Ánh xạ `upper → tops`, `lower → bottoms`, `overall → one-pieces`.
- [x] Bộ nhiều món được render tuần tự; output lượt trước là model image của lượt sau.
- [x] Input/output dùng base64; output được chuẩn hóa JPEG trước khi lưu bằng storage hiện tại.
- [x] `FASHN_API_KEY` chỉ được đọc tại backend và không xuất hiện trong response.
- [x] UI nhận diện provider `FASHN AI` và hiển thị thời gian dự kiến.
- [x] Unit test dùng HTTP transport cô lập, không tiêu credit và không gọi mock composite.
- [x] Luồng Decart hiện tại đúng pattern permanent-key → short-lived client token → WebRTC.

## Cổng bắt buộc trước khi chạy thật

- [ ] Vendor/procurement phê duyệt FASHN account và có credit.
- [ ] Legal/privacy duyệt Terms, DPA/subprocessors, consent và retention của ảnh cơ thể.
- [ ] Có bộ ảnh pilot đã được người trong ảnh đồng ý; không commit ảnh này vào Git.
- [ ] Có Decart account, API key và credit.
- [ ] Có URL HTTPS truy cập từ điện thoại thật, camera permission và mạng hỗ trợ WebRTC.

Không gửi API key qua chat hoặc commit vào repository. Tạo `.env` cục bộ:

```dotenv
TRYON_PHOTO_PROVIDER=fashn
FASHN_API_KEY=<đặt key FASHN tại máy chạy backend>
DECART_API_KEY=<đặt key Decart tại máy chạy backend>
DECART_TOKEN_TTL_S=60
DECART_MAX_SESSION_S=180
```

## Kịch bản nghiệm thu chạy thật

### Photo — FASHN

1. Khởi động SHADOW và gọi `GET /api/tryon/config`.
2. Gate đạt khi `photo.provider == "fashn"`, `photo.configured == "fashn"`, `photo.available == true`, `photo.renders_bottoms == true`.
3. Dùng một ảnh toàn thân đã consent và bộ áo + quần có thật trong wardrobe.
4. Gọi luồng **Ảnh mặc thử** từ UI; response phải có `provider == "fashn"`, không có `fallback_reason`, và cả hai item nằm trong `rendered_item_ids`.
5. Ghi latency từng lượt, tổng latency, prediction ID phục vụ support và số credit đã dùng; không ghi base64/key vào log.
6. Kiểm tra thủ công: giữ danh tính/khuôn mặt, đúng loại đồ, không sót đồ cũ nghiêm trọng, không có biến dạng tay/chân rõ rệt. Ghi rõ đây là mô phỏng, không phải đo size/fit thật.

### Live — Decart

1. Mở app bằng HTTPS trên điện thoại thật và kiểm tra `live.available == true`.
2. Bắt đầu từ outfit đã chốt trong hội thoại; backend phải cấp client token, không trả permanent key.
3. Gate đạt khi remote AI stream xuất hiện, thay đúng garment, đổi sang garment khác không cần reconnect và dừng camera/WebRTC khi thoát.
4. Xác nhận session bị giới hạn bởi `maxSessionDuration=180` và không tiếp tục tính phí sau disconnect.
5. Ghi loại mạng, thời gian kết nối, lỗi camera/WebRTC và chất lượng hình; không quay/lưu người dùng nếu chưa có consent riêng.

## Điều kiện đóng M4

Chỉ đánh dấu M4 hoàn tất khi **cả FASHN photo và Decart live đều chạy bằng provider thật**, có evidence không phải mock, và legal/privacy gate đã được người có thẩm quyền xác nhận. Unit test hoặc token giả không thay thế hai lần chạy nghiệm thu này.

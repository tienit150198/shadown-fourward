# Log điều tra hội thoại Live

`SHADOW_LIVE_TRACE=on` bật log chữ cho **các phiên web mới**. Mặc định tắt.
Backend trả `traceEnabled` khi tạo phiên; browser gom sự kiện khoảng 2 giây/lượt,
gửi bằng `/api/live/trace`. Lỗi gửi log không ngắt hội thoại, không gọi thêm model.

Log gồm transcript do Gemini nhận dạng của người dùng/trợ lý, trạng thái phiên,
tên công cụ và trạng thái kết quả, lời hướng dẫn được đưa vào hàng chờ/đã gửi sang Live.
Mỗi sự kiện có thời gian browser, mã phiên Live, số lượt và số thứ tự. Lời hướng dẫn
kèm `guide_session_id` để nối với log detector (`session=... step=... vision result`).
Transcript là kết quả nhận dạng; không chứng minh chính xác âm thanh đã phát ra loa.
Đây là dữ liệu chẩn đoán do client gửi, không dùng làm quyết định hay xác thực nghiệp vụ.

Không gửi ảnh, audio/video, GPS, token hay toàn bộ đối số/kết quả công cụ vào trace.
Backend chỉ nhận schema chữ cho phiên đã cấp còn hạn, che các dạng key/token phổ biến,
giới hạn 1.000 sự kiện / 512 KB mỗi phiên. Không có API công khai để đọc log.
Các phiên web đang mở cần dừng/mở lại micro sau khi tải bản web mới.
iOS/Android chưa gửi transcript vào endpoint này; log API và detector phía BE vẫn có.

## Theo dõi trên VPS

`shadow-log-capture.service` chạy liên tục qua các lần dựng lại container, ghi tại
`/opt/shadow/logs/runtime.log`. Giữ tối đa khoảng 30 MB (file 5 MB và 5 bản xoay vòng),
chỉ root đọc được. Docker còn giữ log riêng theo giới hạn của `compose.yaml`.
Mốc đầu dòng là UTC; giờ Việt Nam cộng 7 giờ.

```sh
systemctl status shadow-log-capture.service
tail -F /opt/shadow/logs/runtime.log
# Tìm theo một phiên hoặc xem lời nói gần nhất:
rg 'session=<mã-phiên>' /opt/shadow/logs/runtime.log*
rg 'SKI live_trace' /opt/shadow/logs/runtime.log | tail -30
```

Cài dịch vụ: đặt `deploy/collect-runtime-logs.py` tại `/opt/shadow/`, đặt
`deploy/shadow-log-capture.service` tại `/etc/systemd/system/`, chạy
`systemctl daemon-reload` rồi `systemctl enable --now shadow-log-capture`.

Tắt log chữ: đặt `SHADOW_LIVE_TRACE=off`, dựng lại container bằng Compose.
Dừng bộ thu log: `systemctl disable --now shadow-log-capture`.
Log cũ còn tồn tại đến khi xoay vòng hoặc được quản trị viên xóa.

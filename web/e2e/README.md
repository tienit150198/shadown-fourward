# Browser mock E2E

Chạy `node web/e2e/run.mjs`.

Runner chỉ dùng Playwright nếu môi trường đã cài sẵn. Nếu thiếu, runner trả exit code `77` và thông báo `SKIP`; nó không tự cài package hoặc tải browser. Bộ test chạy FastAPI ở mock mode với wardrobe tạm, camera canvas giả và không gọi API trả phí.

Gate gồm: Stylist handoff, đổi outfit không reconnect, route leave giải phóng phiên/camera, photo cancel và disclosure AI/fallback.

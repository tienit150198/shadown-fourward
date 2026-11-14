# Checklist sáng ngày thi — thứ Bảy 14/11/2026

Làm lần lượt từ trên xuống. Mỗi dòng có lệnh hoặc link để kiểm tra. Kịch bản pitch: [../DEMO_SCRIPT.md](../DEMO_SCRIPT.md).

## 0. Mang theo (tối hôm trước)

- [ ] Laptop chạy SHADOW + sạc. Thư mục `shadow/` có file `.env` (gateway Qwen; thêm `DECART_API_KEY` nếu có).
- [ ] Điện thoại demo + sạc + **giá đỡ nhìn từ trên xuống**; đồ vật bài "Lắp giá đỡ điện thoại".
- [ ] Điện thoại thứ hai làm **hotspot 4G** (còn nhiều data) — *không* dùng điện thoại demo làm hotspot.
- [ ] Mac mini + sạc + **cáp mạng (Ethernet) hoặc cáp Thunderbolt** để nối thẳng với laptop (dự phòng khi Wi-Fi chặn).
- [ ] Cáp / bộ chuyển chiếu màn hình điện thoại và laptop lên máy chiếu.
- [ ] Ảnh mặc thử làm sẵn + ảnh toàn thân của C (trong điện thoại và laptop).

## 1. Kế hoạch mạng

| Thiết bị | Cần gì | Cách kết nối |
|---|---|---|
| Laptop | Internet (gọi Qwen, Hugging Face, ngrok) + nói chuyện với Mac mini | Wi-Fi hội trường **hoặc** hotspot của điện thoại thứ hai |
| Mac mini | Chỉ cần nói chuyện với laptop (mô hình đã tải sẵn) | **Cùng mạng với laptop**; nếu Wi-Fi chặn các máy thấy nhau → cắm cáp thẳng laptop ↔ Mac mini |
| Điện thoại demo | Internet để mở link ngrok (và gửi video cho Decart) | **4G của chính nó** là ổn định nhất; Wi-Fi hội trường nếu sóng 4G yếu |

- Wi-Fi hội trường thường **chặn các máy nhìn thấy nhau** → Mac mini sẽ không gọi được. Thử ngay khi đến (bước 4).
- Đổi mạng thì IP Mac mini đổi → dùng tên `http://<Tên-Mac-mini>.local:8188` trong `.env` cho đỡ phải sửa
  (xem tên trên Mac mini: `scutil --get LocalHostName`).

## 2. Laptop (ngay khi đến)

```bash
cd ~/shadow                 # thư mục dự án trên laptop
caffeinate -d &             # không cho laptop ngủ
./run.sh tunnel             # để cửa sổ này mở suốt buổi
```

- [ ] Terminal in dòng `Open on your phone (HTTPS, camera allowed): https://….ngrok-free.app` → gửi link cho cả nhóm (nhắn riêng).
- [ ] Mở **cửa sổ Terminal thứ hai** cho các lệnh kiểm tra bên dưới.

## 3. Kiểm tra nhanh trên laptop (Terminal thứ hai)

```bash
curl -s http://localhost:8000/api/health
#   phải có "mode":"ai" và "vision_model":"qwen3.6-35b-a3b"

curl -s http://localhost:8000/api/skills | grep -o lap-gia-do-dien-thoai
#   phải in ra: lap-gia-do-dien-thoai  (kỹ năng dự phòng)

curl -s -X POST http://localhost:8000/api/wardrobe/seed-demo | head -c 120; echo
#   nạp tủ đồ mẫu: "added":10 lần đầu, "added":0 nếu đã có — đều được

curl -s "http://localhost:8000/api/weather?city=Ha%20Noi&date=2026-11-14"; echo
#   có nhiệt độ t_min / t_max → thời tiết OK

curl -s http://localhost:8000/api/wardrobe/rules; echo
#   xem đã có quy tắc "Học từ Chị Lan" chưa; chưa có thì thêm trong app (bước 6) hoặc:
curl -s -X POST http://localhost:8000/api/wardrobe/rules -H 'Content-Type: application/json' \
  -d '{"text":"Đi đám cưới thì không mặc đồ trắng, tránh lấn át cô dâu.","taught_by":"Chị Lan"}'; echo

curl -s http://localhost:8000/api/tryon/config; echo
#   "live":{"available":true}  → có Decart
#   "photo":{"provider":"worker"}  → đang dùng Mac mini; "hf_space" → Hugging Face; "mock" → chỉ giả lập (cần sửa)
```

- [ ] (Nếu có Decart) kiểm tra cấp token được, **không in token ra màn hình**:
  `curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/tryon/live-token` → phải ra `200`
  (`503` = chưa có `DECART_API_KEY`). Vào trang tài khoản Decart xem **còn credit**.
- [ ] (Tuỳ chọn) chạy test: `cd backend && ../.venv/bin/python -m pytest -q` → `115 passed`.

## 4. Mac mini (nếu mang theo)

Trên Mac mini, mở Terminal:
```bash
cd ~/tryon-worker
./run.sh --warmup           # để cửa sổ mở; dòng cuối in địa chỉ http://…:8188
```
Trên laptop:
```bash
curl -s http://<ip-mac-mini>:8188/health; echo
#   phải có "ok":true, "device":"mps"; sau warmup "warm":true
```
- [ ] Gọi được → `.env` của laptop có `TRYON_WORKER_URL=http://<ip-mac-mini>:8188` (và `TRYON_TOKEN` nếu Mac mini có đặt)
      → **Ctrl+C rồi chạy lại** `./run.sh tunnel` (link ngrok sẽ đổi) → `/api/tryon/config` phải báo `"provider":"worker"`.
- [ ] Không gọi được → thử `ping <ip-mac-mini>`; cắm cáp thẳng; vẫn không được thì bỏ qua, app tự dùng Hugging Face.
- [ ] Chạy 1 ảnh mặc thử thật qua app để mô hình "nóng" và **ghi lại số giây** (chưa từng đo trên máy thật).

Chi tiết + xử lý sự cố: [../tryon-worker/README.md](../tryon-worker/README.md).

## 5. Điện thoại demo

- [ ] Mở link ngrok → **Visit Site** (nếu hiện trang cảnh báo của ngrok) → cho phép **camera** và **micro**.
- [ ] `/api/health` trả `vision_model: qwen3.6-35b-a3b`; tủ đồ và kỹ năng "Lắp giá đỡ điện thoại" mở được.
- [ ] Âm lượng tối đa, tắt im lặng, bật "Không làm phiền", tắt khoá màn hình tự động, pin > 80%.
- [ ] Chạm màn hình một lần cho giọng đọc chạy (iPhone).

## 6. Chạy thử toàn bộ (đúng chỗ, đúng ánh sáng sân khấu)

- [ ] **Hướng dẫn** "Lắp giá đỡ điện thoại" trọn 5 bước, có cố tình làm sai bước 2 → SHADOW nhắc → sửa → khen.
- [ ] **Giữ 🎤** hỏi một câu → có trả lời bằng giọng.
- [ ] **Dạy** thử một kỹ năng ngắn → "AI học ngay" ra kết quả trong ~15 giây → Lưu (hoặc xoá sau đó).
- [ ] Nếu AI hay chấm sai: ⚙️ → "Thu mẫu eval" → 10 ảnh ✓ / 10 ảnh ✗ mỗi bước hay sai → trên laptop:
      `cd backend && ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai` → sửa câu kiểm tra → **tắt "Thu mẫu eval"**.
- [ ] **Trợ lý tủ đồ → Dạy trợ lý**: có quy tắc "Học từ Chị Lan".
- [ ] **Phối đồ**: Đám cưới, thứ Bảy, Hà Nội → "Gợi ý cho tôi" → có nhãn "Học từ Chị Lan" trên ít nhất một bộ.
- [ ] **Mặc thử trực tiếp** (nếu có Decart): 1 phiên ngắn < 30 giây, C đứng cách 2 m → thoát ngay.
- [ ] **Ảnh mặc thử**: chạy 1 ảnh của C với bộ Đám cưới → **Lưu ảnh** làm ảnh dự phòng.
- [ ] Trên laptop mở sẵn `http://localhost:8000/#/wardrobe`, chọn bộ, **tải sẵn ảnh toàn thân của C** ở màn Ảnh mặc thử
      (để giây 2:30 của pitch chỉ cần bấm).

## 7. Năm phút trước khi lên

- [ ] `curl -s http://localhost:8000/api/health` vẫn `"mode":"ai"`; cửa sổ `run.sh` không có lỗi đỏ.
- [ ] Điện thoại đang ở **trang chủ** SHADOW, đã tải lại trang một lần.
- [ ] "Thu mẫu eval" **đang tắt**. Mac mini còn chạy. Hotspot còn bật.
- [ ] Phương án cứu: nhấn giữ khung hướng dẫn = ◀ ▶; mất mạng = `SHADOW_MODE=mock ./run.sh tunnel` (mở lại link ngrok mới).

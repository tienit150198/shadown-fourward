# SHADOW — Roadmap & hướng dẫn chạy

> Viết cho: nhóm 4 người đi thi Codex Community Hackathon – Hanoi (thứ Bảy 14/11/2026) và đội làm app KMM.
> Mỗi phần có: **mục tiêu → lệnh chạy → làm xong khi nào**. Các lệnh chạy từ thư mục gốc `shadow/` trừ khi ghi khác.

---

## 0. Hiện trạng (9/11/2026)

| Phần | Trạng thái | Ghi chú |
|---|---|---|
| Dạy kỹ năng (Teach) | ✅ chạy với AI thật | AI tự chia bước + **tự kiểm tra** bằng video người dạy (đúng ~88–95%) |
| Hướng dẫn (Guide) | ✅ chạy với AI thật | Chấm 1 frame ~0,4–0,8 giây; chuyển bước nhanh hơn ~3 lần nhờ xác nhận nhanh; đúng ~95% trên ảnh thật |
| Hỏi Shadow | ✅ | ~1 giây, tiếng Việt |
| Trợ lý tủ đồ: gắn nhãn, phối đồ, thời tiết | ✅ | Gắn nhãn 2–4 s, phối đồ 5–13 s, có chấm điểm bộ phối |
| Trợ lý tủ đồ: học & dạy | ✅ | Quy tắc tiếng Việt → luật; học gu từ 👍/👎; dạy bằng bộ mẫu |
| Tủ đồ mẫu | ✅ | Đồ nam mùa hè shop YaMe (~40 món) + bộ cơ bản |
| Mặc thử bằng ảnh (Hugging Face) | ⚠️ cần `HF_TOKEN` | Miễn phí chỉ ~2 lượt/ngày |
| Mặc thử trên Mac mini (CatVTON) | ⚠️ chưa chạy trên Mac mini thật | Code + script cài đã sẵn |
| Mặc thử trực tiếp (Decart) | ⚠️ cần `DECART_API_KEY` | Code đã sẵn, chưa thử với key thật |
| Bảo mật khi demo công khai | ✅ | Giới hạn tần suất, mã truy cập tuỳ chọn |
| Giao diện | ✅ bản demo (web) | App thật do đội KMM làm sau |
| Test tự động | ✅ ~240 test backend + 29 test Mac mini | |

---

### Lỗi biết trước (chưa sửa)
- Phối đồ đôi khi chỉ trả **2 bộ** thay vì 3 (khi model trả ít bộ hợp lệ; chỉ tự bù khi có bộ bị loại).
- Quy tắc nói về **form dáng** ("không mặc đồ ôm") có thể không khớp: `style_memory` đoán "ôm/rộng/vừa" còn món đồ lưu mã `slim/relaxed/regular`.
- Gateway Qwen có lúc quá tải (503, mỗi lần chấm 1,5–3,7 s thay vì ~0,7 s) → hôm thi nên đo lại; dự phòng chế độ Demo.
- Skill tự kiểm tra: bước 1 không có ảnh "chưa xong" nên câu kiểm tra quá lỏng ở bước 1 không bị phát hiện.

## 1. Giai đoạn 1 — Trước ngày thi (10–13/11)

### 1.1 Chạy app trên laptop
**Mục tiêu:** ai trong nhóm cũng tự chạy được.

```bash
git clone https://github.com/tienit150198/shadown-fourward.git shadow && cd shadow
cp .env.example .env        # rồi điền LLM_API_KEY (gateway Qwen) — hỏi người giữ key, KHÔNG gửi key lên chat/nhóm công khai
./run.sh                    # lần đầu tự tạo .venv và cài thư viện
```
Mở http://localhost:8000 → badge góc trên phải ghi **"AI · qwen3-vl-8b-instruct"** là đang chạy AI thật
(ghi "Demo" = chưa có key, AI giả lập).

Kiểm tra nhanh:
```bash
curl -s localhost:8000/api/health        # "mode":"ai"
```
Tra cứu toàn bộ API tự động: http://localhost:8000/docs

**Xong khi:** cả 4 người chạy được và thấy badge "AI".

### 1.2 Mở trên điện thoại
Camera trên điện thoại **bắt buộc HTTPS** → dùng ngrok.
```bash
ngrok config add-authtoken <token-của-bạn>   # làm 1 lần, lấy token tại dashboard.ngrok.com
./run.sh tunnel                               # in ra link https://....ngrok-free.app
```
Mở link trên điện thoại → bấm "Visit Site" (ngrok bản miễn phí hiện trang cảnh báo 1 lần) → cho phép camera + micro.

**Thử ngay:** Home → "Lắp giá đỡ điện thoại" → Bắt đầu → nghe giọng đọc tiếng Việt.
**Xong khi:** nghe được giọng, camera chạy, AI chấm được bước (badge trạng thái đổi màu).

### 1.3 Bật mặc thử đồ (chọn ít nhất 1 cách)

**Cách A — Hugging Face (nhanh nhất để có):**
1. Tạo tài khoản https://huggingface.co → Settings → Access Tokens → tạo token (quyền "Read").
2. Thêm vào `.env`: `HF_TOKEN=hf_...` → chạy lại `./run.sh`.
3. Thử: Trợ lý tủ đồ → Phối đồ → "Ảnh mặc thử" (chờ ~25–35 giây).

**Cách B — Mac mini M4 (không giới hạn lượt, miễn phí):**
```bash
# Trên Mac mini (chép thư mục tryon-worker/ sang bằng AirDrop/USB)
cd ~/tryon-worker
chmod +x setup_macmini.sh run.sh
./setup_macmini.sh          # cài Python + thư viện + tải ~2,8 GB model, cuối cùng in ra dòng TRYON_WORKER_URL
./run.sh --warmup           # chạy server, cho phép kết nối khi macOS hỏi
```
Trên laptop thêm vào `.env`:
```
TRYON_WORKER_URL=http://<ip-mac-mini>:8188
TRYON_TOKEN=<mật khẩu giống trong tryon-worker/.env, nếu có đặt>
```
Kiểm tra: `curl http://<ip-mac-mini>:8188/health` → `"ok": true`.
Thử nhanh không cần SHADOW: `.venv/bin/python try_tryon.py nguoi.jpg ao.jpg` (trên Mac mini).
Chậm quá thì trong `tryon-worker/.env` đặt `TRYON_RESOLUTION=384x512` hoặc `TRYON_STEPS=20`.
Chi tiết: [tryon-worker/README.md](../tryon-worker/README.md).

**Cách C — Decart (mặc thử trực tiếp trên camera, như soi gương):**
1. Đăng ký https://platform.decart.ai (được tặng ~$10 ≈ 8 phút) → tạo API key.
2. `.env`: `DECART_API_KEY=...` (tuỳ chọn `DECART_MAX_SESSION_S=180`) → chạy lại.
3. Trên điện thoại (HTTPS): Phối đồ → "Mặc thử trực tiếp" → đứng lùi để thấy từ đầu đến đầu gối.
Mỗi phiên tự ngắt sau 3 phút / 60 giây không thao tác để không tốn tiền.

**Xong khi:** ít nhất 1 cách cho ra ảnh/video mặc thử thật. Thứ tự app tự chọn: Decart (trực tiếp) · Mac mini → Hugging Face → ảnh minh hoạ.

### 1.4 Thu ảnh thật & đo độ chính xác bài demo
**Mục tiêu:** chắc chắn AI chấm đúng bài "lắp giá đỡ điện thoại" dưới ánh sáng thật.

1. Trên điện thoại: Hướng dẫn → ⚙️ Cài đặt → bật **"Thu mẫu eval"**.
2. Mỗi bước: bấm **✓ Đã xong** khi bước đã xong, **✗ Chưa xong** khi chưa/làm sai. 10–20 ảnh mỗi loại, đổi góc/ánh sáng, tay che một phần. Dùng ◀ ▶ đổi bước. Tắt "Thu mẫu eval" khi xong.
3. Chấm điểm (so sánh được nhiều cấu hình):
```bash
cd backend
../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai
../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai --image-side 384,448 --reference always,never
```
4. Đọc cột **"báo xong sai"** (chưa xong mà AI nói xong → nhảy bước). Bước nào hay sai: sửa câu kiểm tra (`detectors.<bước>.prompt` trong `backend/skills/lap-gia-do-dien-thoai.json`) rồi chấm lại.

**Xong khi:** mọi bước "báo xong sai" = 0 và % đúng ≥ 90%.

### 1.5 Bật mã truy cập khi demo công khai
Link ngrok ai cũng mở được → bật mã để người lạ không dùng tốn tiền:
```
SHADOW_ACCESS_CODE=<mã-bí-mật>      # trong .env
```
Link chia sẻ cho máy demo: `https://<ngrok>/?code=<mã-bí-mật>` (mở 1 lần là nhớ).

### 1.6 Tổng duyệt
Theo [DEMO_SCRIPT.md](../DEMO_SCRIPT.md) (kịch bản 5 phút) và [SETUP_CHECKLIST.md](SETUP_CHECKLIST.md) (checklist sáng ngày thi).
Tập ít nhất 3 lượt trọn vẹn, có 1 lượt **tắt mạng giả lập sự cố** để tập phương án dự phòng:
```bash
SHADOW_MODE=mock ./run.sh tunnel     # chế độ Demo: AI giả lập tự đi hết các bước
```

---

## 2. Giai đoạn 2 — Ghép vào app KMM (song song hoặc sau thi)

### 2.1 Tài liệu API cho đội KMM *(việc kế tiếp của AI)*
`docs/API.md`: mỗi API = mục đích · request · response · ví dụ thật · lỗi; data class `kotlinx.serialization`;
ví dụ Ktor client (HTTP + WebSocket). Trong lúc chờ: http://localhost:8000/docs (tự sinh từ code).

### 2.2 Hướng dẫn nhanh: gọi API từ Kotlin (Ktor)

**Phối đồ (REST):**
```kotlin
@Serializable data class SuggestReq(val event: String, val city: String? = null, val current_image: String? = null)

val client = HttpClient { install(ContentNegotiation) { json(Json { ignoreUnknownKeys = true }) } }
val res: JsonObject = client.post("$BASE/api/stylist/suggest") {
    contentType(ContentType.Application.Json)
    setBody(SuggestReq(event = "Đi chơi", city = "Hà Nội"))
}.body()
// res["outfits"] → [{title, items[{id,name,image_url}], reason, tips, score, why[{code,ok,text_vi}], learned[...]}]
```
Ảnh gửi lên luôn là **data URL JPEG base64** (`data:image/jpeg;base64,...`), cạnh dài ≤ 512px (server tự thu nhỏ về 448).

**Hướng dẫn từng bước (WebSocket `/session/control`):**
```kotlin
client.webSocket("$WS_BASE/session/control") {
    // 1. nhận {"type":"session.ready", ...}  2. gửi bắt đầu
    send("""{"type":"session.start","skill_id":"lap-gia-do-dien-thoai"}""")
    // 3. mỗi ~1,5 giây gửi 1 frame camera:
    //    {"type":"vision.frame","image":"data:image/jpeg;base64,...","ts":<ms>,"trigger":"interval"}
    for (frame in incoming) {
        val msg = Json.parseToJsonElement((frame as Frame.Text).readText()).jsonObject
        when (msg["type"]?.jsonPrimitive?.content) {
            "hud.state"     -> { /* hiển thị Bước i/n, instruction, tip, warning, ảnh mẫu */ }
            "speech"        -> { /* đọc TTS tiếng Việt câu msg["text"]; câu mới ngắt câu cũ */ }
            "vision.result" -> { if (msg["confirming"]?.jsonPrimitive?.boolean == true) { /* GỬI FRAME MỚI NGAY */ } }
            "hud.error"     -> { /* toast lỗi tiếng Việt */ }
        }
    }
}
```
Quy tắc cho client: chỉ đọc câu nói theo `speech`, không tự quyết định chuyển bước (server quyết định);
khi `confirming=true` gửi frame kế tiếp ngay (chuyển bước nhanh hơn ~1 giây); lỗi 401 = cần mã truy cập, 429 = gọi quá nhanh.

### 2.3 Việc của đội KMM
| Việc | Gợi ý |
|---|---|
| Camera + gửi frame | CameraX (Android) / AVFoundation (iOS) → JPEG 512px, chất lượng ~0,7 |
| Đọc/nghe tiếng Việt | TTS/STT có sẵn của Android/iOS, giọng vi-VN |
| Màn hình | Home · Dạy · Xem lại · Hướng dẫn · Hoàn thành · Tủ đồ · Phối đồ · Mặc thử · Dạy trợ lý (tham khảo bản web trong `web/`) |
| Mặc thử trực tiếp | Decart có SDK Android/iOS/JS; backend đã có `POST /api/tryon/live-token` phát token ngắn hạn |

**Xong khi:** app KMM chạy trọn 1 lượt Hướng dẫn + 1 lượt Phối đồ với server thật.

---

## 3. Giai đoạn 3 — Sau hackathon (nếu đi tiếp thành sản phẩm)

| # | Việc | Vì sao | Cách làm / chạy thử |
|---|---|---|---|
| 3.1 | **Thay model phi thương mại** (IDM-VTON, CatVTON, ảnh mẫu VITON-HD/Kolors, ảnh YaMe) | Không được dùng thương mại | Dùng API có giấy phép thương mại (FASHN, Kling…) hoặc tự train; ảnh tủ đồ do người dùng tự chụp. Đổi provider qua `TRYON_PHOTO_PROVIDER` trong `backend/tryon.py` |
| 3.2 | **Đưa server lên cloud** | Không phụ thuộc laptop + ngrok | Đóng gói Docker (FastAPI + uvicorn), deploy lên VPS/Cloud Run; HTTPS thật; giữ `SHADOW_ACCESS_CODE` hoặc thay bằng đăng nhập |
| 3.3 | **Tài khoản & lưu dữ liệu** | Mỗi người có kỹ năng, tủ đồ, gu riêng | Thay file JSON (`backend/skills`, `backend/wardrobe`) bằng database (PostgreSQL) + lưu ảnh (S3) |
| 3.4 | **Model chạy ngay trên Mac mini / máy chủ riêng** | Nhanh hơn (bỏ đường mạng), riêng tư, không tốn phí | Thử Qwen3-VL nhỏ qua MLX trên M4; đo bằng `eval_detectors.py --models <model-mới>` trước khi đổi `VISION_MODEL` |
| 3.5 | **Thử Qwen3.8 cho việc chấm frame** | Có thể chính xác hơn với bài khó | `eval_detectors.py <skill> --models qwen3-vl-8b-instruct,qwen3.6-35b-a3b` |
| 3.6 | **Chia sẻ kỹ năng** | Người giỏi dạy 1 lần, nhiều người học | Xuất/nhập file skill JSON + ảnh; sau đó là "chợ kỹ năng" |
| 3.7 | **Đo chất lượng liên tục** | Biết AI có tệ đi khi đổi model/prompt | Mỗi skill đã có bộ eval tự tạo khi dạy → chạy `eval_detectors.py` định kỳ, lưu báo cáo trong `backend/evals/reports/` |
| 3.8 | **Đa ngôn ngữ** | Mở rộng người dùng | Prompt đang viết cho tiếng Việt; thêm tham số ngôn ngữ cho câu nói/TTS |

---

## Phụ lục

### Biến môi trường hay dùng (`.env`)
| Biến | Ý nghĩa |
|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY` | Gateway AI (đang dùng Qwen tại llm.hypertek.vn) |
| `VISION_MODEL` / `COMPILER_MODEL` | Model chấm frame (qwen3-vl-8b-instruct) / model sinh skill & phối đồ (qwen3.6-35b-a3b) |
| `LLM_EXTRA_BODY` | Tắt "suy nghĩ" của Qwen cho nhanh — để trong nháy đơn |
| `SHADOW_MODE` | `auto` / `mock` (Demo, không cần AI) / `ai` |
| `COMPLETE_CONFIRM_FRAMES` | Số frame liên tiếp phải báo xong mới chuyển bước (mặc định 2) |
| `VISION_FAST_CONFIRM`, `VISION_IMAGE_MAX_SIDE`, `VISION_REFERENCE_MODE`, `VISION_DECISION` | Tinh chỉnh tốc độ/độ chính xác Guide (mặc định đã chọn theo số liệu đo) |
| `HF_TOKEN`, `TRYON_WORKER_URL`, `TRYON_TOKEN`, `DECART_API_KEY` | Các cách mặc thử |
| `SHADOW_ACCESS_CODE` | Mã truy cập khi demo công khai |

### Test
```bash
cd backend && ../.venv/bin/python -m pytest -q          # ~240 test, không cần mạng
```

### Tải lại ảnh tủ đồ YaMe (ảnh không nằm trong git)
```bash
cd backend && ../.venv/bin/python wardrobe_seed_shop/build_seed_shop.py
```

### Sự cố thường gặp
| Hiện tượng | Cách xử lý |
|---|---|
| Badge ghi "Demo" dù đã có key | Kiểm tra `.env` có `LLM_API_KEY`, chạy lại `./run.sh`; xem `curl localhost:8000/api/health` |
| Điện thoại không mở được camera | Phải mở bằng link **https** (ngrok), không dùng http://ip |
| Không nghe giọng đọc (iPhone) | Chạm màn hình 1 lần, bật âm lượng, tắt chế độ im lặng |
| Mặc thử báo "hết lượt" | Hugging Face hết quota → thêm `HF_TOKEN` hoặc dùng Mac mini |
| AI chấm sai lúc demo | Nhấn giữ khung hướng dẫn → hiện ◀ ▶ để chuyển bước bằng tay |
| Mất mạng | `SHADOW_MODE=mock ./run.sh tunnel` |

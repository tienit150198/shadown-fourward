# SHADOW

**Làm mẫu một lần, AI hướng dẫn người sau.** (*Show it once. AI can guide the next person.*)

Làm cho Codex Community Hackathon – Hanoi (thứ Bảy 14/11/2026, chủ đề "AI in Your Palm").
Dự án dựa trên GlassKit (MIT). Phần dùng lại và phần làm mới ghi rõ trong [NOTICE.md](NOTICE.md).

## SHADOW là gì?

SHADOW là một **trợ lý AI chạy trên điện thoại**, nhìn qua camera và nói chuyện bằng tiếng Việt. Trợ lý có hai kiểu kỹ năng:

1. **Kỹ năng do người thật dạy.** Người giỏi vừa làm vừa nói trước camera một lần (Dạy). AI tự chia việc đó thành
   từng bước, có câu hướng dẫn, mẹo, cảnh báo và ảnh mẫu. Sau đó người mới mở camera làm lại (Hướng dẫn): SHADOW nhìn
   từng bước, khen khi đúng, nhắc ngay khi sai, và trả lời khi được hỏi.
2. **Kỹ năng làm sẵn "Trợ lý tủ đồ".** Trợ lý nhìn xem bạn đang mặc gì, phối đồ từ chính tủ đồ của bạn theo thời tiết
   và sự kiện, rồi cho bạn **mặc thử**: trực tiếp trên camera (Decart) hoặc bằng ảnh (Hugging Face hoặc Mac mini của nhóm).
   Kỹ năng này **cũng dạy được**: một người (stylist, chị gái, mẹ…) nói "quy tắc phối đồ" của mình, trợ lý áp dụng và
   ghi "Học từ Chị Lan".

---

## Chạy nhanh

### 1. Chạy trên laptop

```bash
cd shadow
./run.sh            # mở http://localhost:8000 trên laptop
```

Lần đầu `run.sh` tự tạo môi trường Python (`.venv`) và cài thư viện, mất 1–2 phút. Trên laptop, camera dùng được
ngay ở `localhost`.

### 2. Hai chế độ: Demo (giả lập) và AI thật

| Chế độ | Khi nào | Hoạt động thế nào |
|---|---|---|
| **Demo** (mock) | Không có `.env` / không có `LLM_API_KEY`, hoặc chạy `SHADOW_MODE=mock ./run.sh` | AI giả lập, không cần mạng. Mỗi bước tự "xong" sau vài khung hình; **bước 2 cố tình báo sai một lần** để thấy cảnh AI sửa lỗi. Trợ lý tủ đồ cũng có kết quả giả lập. |
| **AI thật** | Có `.env` với `LLM_API_KEY` | Gọi model Qwen qua gateway của nhóm. Huy hiệu trên trang chủ ghi `AI · qwen3-vl-8b-instruct`. |

Xem chế độ đang chạy: mở http://localhost:8000/api/health → `"mode":"ai"` hoặc `"mode":"mock"`.

### 3. File `.env` — các khoá cần biết

File `.env` nằm ở thư mục gốc `shadow/`, **không bao giờ commit hay gửi lên nhóm chat công khai** (có API key).
Mẫu đầy đủ ở [.env.example](.env.example). Máy của trưởng nhóm đã cấu hình sẵn gateway Qwen; máy khác thì xin file
`.env` riêng.

| Biến | Bắt buộc? | Dùng để làm gì |
|---|---|---|
| `LLM_BASE_URL`, `LLM_API_KEY` | Có (cho AI thật) | Địa chỉ + khoá của gateway LLM. Nhóm dùng gateway Qwen `https://llm.hypertek.vn/v1` (đã cấu hình). |
| `VISION_MODEL` | Có | Model nhìn ảnh: `qwen3-vl-8b-instruct` (chấm từng khung hình, gắn nhãn quần áo). |
| `COMPILER_MODEL` | Có | Model "suy nghĩ": `qwen3.6-35b-a3b` (thực chất là Qwen3.8-27B) — sinh kỹ năng, phối đồ. |
| `LLM_EXTRA_BODY` | Nên có | `'{"chat_template_kwargs": {"enable_thinking": false}}'` — tắt chế độ "suy nghĩ" cho nhanh. **Phải để trong dấu nháy đơn.** |
| `COMPLETE_CONFIRM_FRAMES` | Không (mặc định 2) | Phải 2 khung hình liên tiếp báo "xong" mới chuyển bước (chống nhảy bước nhầm). |
| `DECART_API_KEY` | Không | Bật **mặc thử trực tiếp** trên camera (Decart, trả phí ~$0.02/giây). Không có thì nút "Mặc thử trực tiếp" bị mờ. |
| `TRYON_WORKER_URL` | Không | Địa chỉ Mac mini chạy mặc thử bằng ảnh, ví dụ `http://192.168.1.50:8188` (cổng mặc định của worker là **8188**). |
| `TRYON_TOKEN` | Không | Mật khẩu chung với Mac mini — chỉ cần nếu đã đặt `TRYON_TOKEN` bên Mac mini. |
| `HF_TOKEN` | Không | Token Hugging Face, giúp mặc thử bằng ảnh qua Hugging Face ít bị giới hạn lượt. |
| `TRYON_PHOTO_PROVIDER` | Không (mặc định `auto`) | Chọn máy mặc thử bằng ảnh: `auto` = Mac mini → Hugging Face → giả lập; hoặc ép `worker` / `hf_space` / `mock`. |
| `STYLIST_MODEL` | Không | Model riêng cho phối đồ; bỏ trống thì dùng `COMPILER_MODEL`. |

Sửa `.env` xong thì **tắt server (Ctrl+C) và chạy lại** `./run.sh`.

### 4. Mở trên điện thoại

Điện thoại chỉ cho dùng camera trên trang **HTTPS**, nên cần đường hầm ngrok:

```bash
brew install ngrok                       # một lần
ngrok config add-authtoken <token>       # một lần, lấy token ở dashboard.ngrok.com
./run.sh tunnel
```

Terminal sẽ in một dòng `Open on your phone (HTTPS, camera allowed): https://xxxx.ngrok-free.app`.
Mở link đó trên điện thoại (điện thoại dùng 4G hay Wi-Fi nào cũng được), bấm **Visit Site** nếu ngrok hiện trang cảnh báo,
rồi **cho phép camera và micro**. Có thể "Thêm vào màn hình chính" để mở như app.

> Lưu ý iPhone: phải chạm vào màn hình một lần thì giọng đọc mới phát ra được.

**Khi mở ra internet (demo công khai):** link ngrok ai cũng vào được, nên nên đặt `SHADOW_ACCESS_CODE=<mã>` trong `.env`.
Khi đó mọi `/api/*`, `/session/*` và ảnh cần mã: app tự hỏi mã một lần (lưu cookie 12 giờ), hoặc chia sẻ link
`https://xxxx.ngrok-free.app/?code=<mã>` để vào thẳng. Để trống = tắt (chạy local như cũ). Luôn bật sẵn: giới hạn tần suất
theo IP (báo "thao tác nhanh quá" khi vượt), giới hạn kích thước request, tối đa 2 ảnh mặc thử cùng lúc, và mỗi phiên
mặc thử trực tiếp bị Decart cắt sau `DECART_MAX_SESSION_S` (mặc định 180 giây). Chi tiết: `backend/security.py`, `.env.example`.

---

## Đi một vòng các màn hình

### Trang chủ
Huy hiệu chế độ (Demo / AI) ở góc trên, nút lớn **"Dạy kỹ năng mới"**, thẻ **"👗 Trợ lý tủ đồ"** (mục *Kỹ năng làm sẵn*),
và danh sách **"Kỹ năng đã học"** (mỗi thẻ ghi "Học từ <người dạy>", số bước). Có sẵn kỹ năng mẫu
**"Lắp giá đỡ điện thoại"** (5 bước, học từ Tuấn). Nút ⋯ trên thẻ: bắt đầu, chỉnh sửa, xoá.

### Phần 1 — Dạy và Hướng dẫn

1. **Dạy** (`Dạy kỹ năng mới`): camera sau toàn màn hình, bấm nút tròn để ghi. Vừa làm vừa nói
   ("đầu tiên…, nhớ là…, đừng…"). App nghe lời nói tiếng Việt và tự chụp ảnh mỗi ~2 giây. Bấm dừng → nhập tên kỹ năng và
   tên người dạy → **"AI học ngay"** (khoảng 13–15 giây, có đồng hồ đếm, huỷ được).
2. **Xem lại** ("AI đã học xong"): sửa tên, người dạy, mô tả; mỗi bước có ảnh mẫu, câu hướng dẫn, **mẹo**, **cảnh báo**,
   và câu AI sẽ nói khi bắt đầu / khi làm sai / khi làm đúng. Đổi thứ tự, xoá bước → **"Lưu kỹ năng"**.
3. **Hướng dẫn**: người học bấm Bắt đầu, đặt điện thoại nhìn xuống chỗ làm. Trên cùng là "Bước 2/5" + câu hướng dẫn lớn,
   ảnh "Mẫu" của người dạy (chạm để phóng to), bong bóng câu AI vừa nói. SHADOW đọc từng bước, nhìn camera,
   khen khi đúng, nhắc khi sai, đọc mẹo của người dạy.
   - **Giữ nút 🎤 "Giữ để hỏi Shadow"** để hỏi bằng giọng ("cái chốt này vặn chiều nào?"); AI trả lời theo ảnh camera.
   - **Nút ◀ ▶ (cứu demo)**: nhấn giữ lâu vào khung hướng dẫn, hoặc bật trong ⚙️ Cài đặt → "Nút debug ◀ ▶".
   - ⚙️ Cài đặt còn có: bật/tắt giọng nói, **"Thu mẫu eval"** (xem mục Eval bên dưới).
   - Mất mạng / AI lỗi: chấm trạng thái chuyển vàng, có thông báo; tự kết nối lại và tiếp tục từ bước đang làm.
4. **Hoàn thành**: chúc mừng, thời gian làm, số lần được sửa; nút "Làm lại" hoặc "Về trang chủ".

### Phần 2 — Trợ lý tủ đồ (kỹ năng làm sẵn)

1. **Trang Trợ lý tủ đồ** ("Hôm nay mặc gì?"): nút lớn **"Phối đồ hôm nay"**, ô **🧺 Tủ đồ** (số món),
   ô **🎓 Dạy trợ lý** (số quy tắc).
2. **Tủ đồ**: lưới ảnh chia theo loại (áo, quần, váy, áo khoác, giày…). **"Thêm đồ"** → chụp ảnh hoặc chọn ảnh có sẵn →
   AI tự nhận diện loại, màu, phong cách, độ ấm, dịp mặc (2–4 giây) → sửa nếu chưa đúng → Lưu.
   Nút **"Nạp tủ đồ mẫu"** cho chọn: **16 món đồ nam mùa hè của shop YaMe** (mặc định, `SHADOW_DEMO_SEED=shop`) hoặc bộ mẫu cơ bản 10 món.
   Ảnh YaMe không nằm trong git — nếu thiếu, chạy `cd backend && ../.venv/bin/python wardrobe_seed_shop/build_seed_shop.py` để tải lại.
3. **Phối đồ**: chọn **"Bạn sắp đi đâu?"** (chip: Đi làm, Đi chơi, Hẹn hò, Đám cưới, Phỏng vấn, Thể thao, Du lịch, Ở nhà;
   hoặc gõ / nói), **"Khi nào?"** (Hôm nay / Ngày mai / Chọn ngày). Thời tiết tự lấy theo thành phố hoặc 📍 vị trí.
   Tuỳ chọn: **📷 "Chụp đồ đang mặc"** (trợ lý nhìn bạn đang mặc gì và phối quanh món đó) hoặc **📌 "Giữ món này"**.
   Bấm **"Gợi ý cho tôi"** (9–14 giây) → 2–3 thẻ bộ phối: ảnh các món, lý do, mẹo, nút 🔊 đọc lý do, và nhãn
   **"Học từ Chị Lan · …"** khi bộ đó dùng quy tắc đã được dạy. Mỗi thẻ có **🎥 Mặc thử trực tiếp** và **📸 Ảnh mặc thử**.
4. **Mặc thử trực tiếp** (cần `DECART_API_KEY`): camera trước. Màn chuẩn bị kiểm tra camera, khung hình
   ("đứng lùi lại để thấy từ đầu đến đầu gối"), mạng và máy chủ → **"Bắt đầu mặc thử"** (kết nối ~3 giây) → video đã thay đồ
   do AI tạo (có watermark). Đổi bộ bằng dải ảnh dưới đáy hoặc **giơ tay phải 1,5 giây = bộ tiếp, tay trái = bộ trước**.
   Có đồng hồ chi phí (~$0.02/giây), **tự ngắt sau 60 giây không thao tác** và tối đa 3 phút mỗi phiên.
5. **Ảnh mặc thử**: chụp ảnh toàn thân (có khung nét đứt và hẹn giờ 3 giây) hoặc tải ảnh lên → bấm mặc thử →
   chờ có thanh tiến trình (Hugging Face ~30 giây) → kéo thanh **Trước / Sau** để so sánh → Lưu ảnh / Chia sẻ.
   Màn hình ghi rõ đang dùng máy nào (Mac mini / Hugging Face / giả lập).
   - Hugging Face (IDM-VTON) chỉ vẽ **áo hoặc váy liền**; quần sẽ bị bỏ qua (app có ghi chú). Mac mini vẽ được cả áo + quần.
6. **Dạy trợ lý**: gõ hoặc nói một quy tắc phối đồ (có câu mẫu như "Đi đám cưới thì không mặc đồ trắng, tránh lấn át
   cô dâu"), nhập tên người dạy (VD "Chị Lan") → **"Thêm quy tắc"**. Danh sách "Đã dạy" ghi "Học từ <tên>".
   Nút "Thử phối đồ với quy tắc mới" quay lại Phối đồ.

---

## Mô hình AI và tốc độ (đo ngày 09/11/2026)

| Việc | Model / dịch vụ | Thời gian đo được |
|---|---|---|
| Chấm từng khung hình khi Hướng dẫn | Qwen3-VL-8B (`qwen3-vl-8b-instruct`) | ~0,4–0,8 giây / khung hình |
| AI học kỹ năng từ lời nói + ảnh (Dạy) | Qwen3.8-27B (`qwen3.6-35b-a3b`, tắt thinking) | ~13–15 giây |
| Hỏi Shadow | Qwen3.8 | ~1 giây (0,6–1,5 giây) |
| Gắn nhãn món đồ mới | Qwen3-VL-8B | 2–4 giây |
| Phối đồ (stylist) | Qwen3.8 | 9–14 giây |
| Thời tiết | Open-Meteo (miễn phí, không cần key) | < 1 giây |
| Ảnh mặc thử — Hugging Face | IDM-VTON (Space `yisol/IDM-VTON`) | ~30 giây / ảnh, miễn phí, có lúc phải xếp hàng |
| Ảnh mặc thử — Mac mini M4 | CatVTON (`tryon-worker/`) | **Chưa đo trên máy thật** (ước lượng 45–90 giây ở 576x768, 30 bước) |
| Mặc thử trực tiếp | Decart `lucy-vton-latest` | ~17 khung hình/giây, ~$0.02/giây, kết nối ~3 giây — **chưa thử thật (chưa có key)** |

Độ chính xác: trên bộ 20 ảnh thật (kỹ năng "pha trà"), Qwen3-VL-8B chấm **đúng 90%** (trung vị 0,84 giây),
Qwen3.8 cũng 90% nhưng chậm hơn (1,37 giây) → giữ VL-8B để chấm. Thêm luật **2 khung hình liên tiếp** mới chuyển bước.

---

## Đo độ chính xác (eval) và chọn model

1. **Thu mẫu trên điện thoại**: vào Hướng dẫn → ⚙️ Cài đặt → bật **"Thu mẫu eval"**. Ở mỗi bước, chĩa camera vào chỗ làm rồi
   bấm **✓ Đã xong** (bước đã làm xong) hoặc **✗ Chưa xong** (chưa xong hoặc làm sai). Dùng ◀ ▶ để đổi bước.
   Nên có 10–20 ảnh mỗi loại cho mỗi bước, đổi góc máy, ánh sáng, tay che một phần. Ảnh lưu ở `backend/evals/`.
   Khi đang thu mẫu, AI **tạm ngừng** tự chuyển bước — **nhớ tắt trước khi demo**.
2. **Chấm điểm** (cần `.env` có key):
   ```bash
   cd backend
   ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai
   ../.venv/bin/python eval_detectors.py lap-gia-do-dien-thoai --models qwen3-vl-8b-instruct,qwen3.6-35b-a3b
   ```
   Kết quả: % đúng, **báo xong sai** (chưa xong mà AI nói xong → nhảy bước, nguy hiểm nhất khi demo), bỏ sót, lỗi,
   độ trễ p50/p95. Báo cáo JSON kèm danh sách ảnh bị chấm sai nằm ở `backend/evals/reports/`.
   Bước nào hay "báo xong sai" thì sửa câu kiểm tra của bước đó (màn Xem lại hoặc file JSON) rồi chấm lại.

---

## Mac mini mặc thử bằng ảnh (tuỳ chọn)

Thư mục `tryon-worker/` là một máy chủ nhỏ chạy mô hình **CatVTON** trên Mac mini M4 của nhóm (dùng GPU Apple).
Laptop gọi nó qua mạng LAN khi bấm "Ảnh mặc thử". Ưu điểm so với Hugging Face: không phụ thuộc Internet, không xếp hàng,
vẽ được cả quần.

Tóm tắt: chép thư mục sang Mac mini → `./setup_macmini.sh` (một lần, tải ~4 GB) → `./run.sh --warmup` →
trên laptop thêm `TRYON_WORKER_URL=http://<ip-mac-mini>:8188` vào `.env` → kiểm tra `curl http://<ip-mac-mini>:8188/health`.
Mac mini và laptop phải **cùng một mạng**. Hướng dẫn từng bước, xử lý sự cố, giấy phép:
[tryon-worker/README.md](tryon-worker/README.md).

---

## Kiểm tra (test)

```bash
cd backend && ../.venv/bin/python -m pytest -q                         # 115 test, không cần mạng hay key
cd tryon-worker && TRYON_MOCK=1 ../.venv/bin/python -m pytest tests/test_server.py -q   # 25 test, chế độ giả lập
```

Các test khác của `tryon-worker` (mask, pipeline) cần PyTorch — chạy trên Mac mini: `TRYON_MOCK=1 .venv/bin/python -m pytest tests -q`.

---

## Cấu trúc thư mục

```
shadow/
  README.md  DEMO_SCRIPT.md  PLAN.md  PROGRESS.md  NOTICE.md  LICENSE-GlassKit
  docs/SETUP_CHECKLIST.md       # việc cần làm sáng ngày thi
  run.sh  requirements.txt  .env.example   (.env: khoá bí mật, không commit)
  upstream/glasskit-mocktail-backend/      # bản gốc GlassKit, KHÔNG sửa (để so sánh)
  backend/                                  # máy chủ Python (FastAPI)
    main.py                 # các đường dẫn API + WebSocket hướng dẫn, phục vụ web/
    session_workflow.py     # máy trạng thái chấm bước (fork GlassKit)
    vision_runtime.py       # nhận khung hình, gọi model nhìn ảnh, chế độ Demo
    providers.py            # kết nối gateway LLM (OpenAI-compatible) + AI giả lập
    skill_compiler.py       # Dạy: lời nói + ảnh → kỹ năng
    skill_store.py  skills/ # kỹ năng đã lưu (JSON + ảnh mẫu)
    eval_store.py  eval_detectors.py        # thu mẫu + chấm độ chính xác
    wardrobe_api.py         # API Trợ lý tủ đồ
    wardrobe_store.py  wardrobe/            # tủ đồ + quy tắc của người dùng (không commit)
    wardrobe_seed/          # tủ đồ mẫu cơ bản 10 món + SOURCES.md (nguồn ảnh)
    wardrobe_seed_shop/     # 16 món đồ nam mùa hè YaMe (ảnh © YaMe, chỉ demo nội bộ) + SOURCES.md
    stylist.py              # gắn nhãn đồ, nhìn đồ đang mặc, phối đồ
    weather.py              # thời tiết Open-Meteo
    tryon.py                # mặc thử: token Decart, Mac mini / Hugging Face / giả lập
    tests/
  web/                      # app điện thoại (JS thuần, không cần build)
    index.html  app.js  styles.css  wardrobe.css  tryon-live.css  sw.js  manifest.webmanifest
    js/screens/             # home, teach, review, guide, done, wardrobe, stylist, rules, tryon-live, tryon-photo
    js/                     # camera, giọng nói, MediaPipe, Decart, gọi API…
  tryon-worker/             # máy chủ mặc thử CatVTON cho Mac mini (README riêng)
```

Đặc tả kỹ thuật chi tiết (giao thức, API, định dạng kỹ năng): [PLAN.md](PLAN.md). Nhật ký làm việc: [PROGRESS.md](PROGRESS.md).

---

## Giấy phép (tóm tắt)

Chi tiết: [NOTICE.md](NOTICE.md), [tryon-worker/README.md](tryon-worker/README.md#giấy-phép--chỉ-dùng-phi-thương-mại),
[backend/wardrobe_seed/SOURCES.md](backend/wardrobe_seed/SOURCES.md).

- **GlassKit** (MIT): nền tảng backend được fork; phần dùng lại / làm mới ghi trong NOTICE.md.
- **DecartAI/tryon-examples** (MIT): tham khảo cách cấp token và cách viết câu lệnh thay đồ. Decart là dịch vụ trả phí.
- **Open-Meteo**: dữ liệu CC BY 4.0, miễn phí cho mục đích phi thương mại.
- ⚠️ **Phi thương mại** — chỉ dùng cho demo / nghiên cứu, ghi nguồn khi trình diễn:
  - **IDM-VTON** (Hugging Face Space) — CC BY-NC-SA 4.0.
  - **CatVTON** trên Mac mini — CC BY-NC-SA 4.0.
  - **Ảnh đồ nam YaMe** (`wardrobe_seed_shop/`) — © YaMe (yame.vn), chỉ dùng demo nội bộ, không phân phối lại.
  - **Ảnh tủ đồ mẫu cơ bản** — ảnh áo lấy từ ví dụ của IDM-VTON (bộ VITON-HD) và Kolors; quần, chân váy, giày là hình tự vẽ.

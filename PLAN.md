# SHADOW — Kế hoạch triển khai

> "Show it once. AI can guide the next person."
> Người A làm mẫu một lần → AI học thành "skill" → người B làm lại, AI nhìn camera và hướng dẫn từng bước bằng giọng nói.

Hackathon: Codex Community Hackathon – Hanoi, thứ Bảy 14/11/2026 (chủ đề "AI in Your Palm" – app di động có AI).

## 1. Nền tảng: fork từ GlassKit

- Nguồn: https://github.com/RealComputer/GlassKit (MIT), commit `4431554046e5ef4e03187469045182954dff746b` (2026-08-21),
  ví dụ `examples/rokid-overshoot-openai-realtime` (coach pha đồ uống trên kính Rokid).
- Bản gốc giữ nguyên trong `upstream/glasskit-mocktail-backend/` để so sánh / công khai phần nào là có sẵn.
- **Giữ lại từ GlassKit:** kiến trúc "backend quyết định mọi thứ" (server-authoritative), vòng lặp sự kiện mỗi session,
  định dạng recipe JSON (tasks / detectors / steps / evaluation_mode), các bộ đánh giá bước
  (`match_value`, `momentary_true_complete`, `enum_progress_once_then_complete`, …), HUD state qua WebSocket.
- **Thay thế:**
  | GlassKit | SHADOW |
  |---|---|
  | App Android cho kính Rokid | Web app mobile (PWA) chạy trên trình duyệt điện thoại |
  | Overshoot (stream video WebRTC → VLM) | Điện thoại gửi frame JPEG qua WebSocket → backend gọi vision LLM qua **API bên thứ 3 (OpenAI-compatible, mặc định OpenRouter)** |
  | OpenAI Realtime để nói | Backend gửi câu thoại dạng text → điện thoại đọc bằng TTS của trình duyệt (vi-VN) |
  | Quét nguyên liệu + LLM chọn recipe | Người dùng chọn skill từ danh sách |
  | Recipe viết tay | **Teach mode: AI tự sinh skill từ video + lời nói của người dạy (phần mới chính)** |
- **Thêm mới:** Teach/Skill Compiler, ảnh mẫu của người dạy cho từng bước (gửi kèm khi chấm), mẹo/cảnh báo của người dạy,
  "Hỏi Shadow" (giữ nút hỏi bằng giọng), chế độ demo offline (mock), CV trên điện thoại (MediaPipe) để chỉ gửi frame khi cần.
- Chưa dùng Qwen (chậm) — model cấu hình qua `.env`, đổi sau được.

## 2. Cấu trúc thư mục

```
shadow/
  PLAN.md  PROGRESS.md  README.md  NOTICE.md  LICENSE-GlassKit
  upstream/glasskit-mocktail-backend/   # bản gốc, KHÔNG sửa
  backend/
    main.py                # FastAPI: WS /session/control, REST /api/*, phục vụ web/ tĩnh
    session_manager.py     # ghép các mixin
    session_workflow.py    # state machine (fork từ GlassKit, bỏ inventory/recipe-selection)
    vision_runtime.py      # (mới, thay session_runtime.py) nhận frame, gọi provider, nói = gửi text
    providers.py           # (mới) LLMClient OpenAI-compatible + MockProvider
    skill_compiler.py      # (mới) Teach: transcript + keyframes → Skill JSON
    skill_store.py         # (mới) đọc/ghi skills/*.json + ảnh
    recipe_catalog.py      # schema (đã mở rộng thêm field SHADOW)
    session_types.py  session_constants.py  session_helpers.py
    skills/                # skill JSON + skills/<id>/frames/*.jpg
    tests/
  web/                     # vanilla JS ES modules, không cần build
    index.html  styles.css  app.js  manifest.webmanifest  sw.js  icons/
    js/  (api.js, ws.js, camera.js, speech.js, cv.js, screens/*.js)
  requirements.txt  run.sh  .env.example
```

## 3. Cấu hình (.env)

```
LLM_BASE_URL=https://openrouter.ai/api/v1   # API bên thứ 3, OpenAI-compatible
LLM_API_KEY=
VISION_MODEL=google/gemini-2.5-flash         # model nhanh, có vision — chấm từng frame
COMPILER_MODEL=google/gemini-2.5-flash       # model sinh skill (có thể mạnh hơn)
SHADOW_MODE=auto        # auto = mock nếu không có key; mock; ai
VISION_MIN_INTERVAL_MS=1200
```

## 4. Schema Skill (mở rộng Recipe của GlassKit — tương thích ngược)

Recipe (GlassKit) + các field tùy chọn:
- Recipe: `language` ("vi"), `learned_from` (tên người dạy), `summary`, `created_at`, `cover_image` (path tương đối).
- RecipeStep: `instruction` (câu hướng dẫn hiển thị), `tip` (mẹo của người dạy), `warning`, `reference_image`
  (path tương đối trong `skills/<id>/frames/`), `allow_dynamic_hint` (bool, mặc định true).
- Detector field cho phép: `flag` (bool), `state` (string enum), `color`, `level`, `ingredients` (giữ nguyên GlassKit).
- Skill do AI sinh ra chỉ dùng: `momentary_true_complete` (field `flag`, `complete_on: true`),
  `match_value` (field `state`/`color`), `enum_progress_once_then_complete` (field `state`).
- Kết quả vision luôn có thể kèm `hint` (string tiếng Việt ngắn) — dùng khi sai mà step không có `mismatch_speech`.

## 5. Giao thức WebSocket `/session/control` (JSON)

Client → server:
- `{"type":"session.start","skill_id":"<id>"}`
- `{"type":"session.stop"}`
- `{"type":"vision.frame","image":"data:image/jpeg;base64,...","ts":<ms>,"trigger":"interval|still"}`
  (server chỉ xử lý 1 frame một lúc; frame đến khi đang bận thì giữ frame mới nhất, bỏ frame cũ)
- `{"type":"debug.step","direction":"forward|backward"}`

Server → client:
- `{"type":"session.ready","session_id":...}`
- `{"type":"hud.state","screen":"start|running","phase":"WAITING_FOR_START|GUIDING|COMPLETED|ERROR","recipe_name":...,
   "skill_id":...,"tasks":[{"id","text","completed"}],"active_task_id":...,"step":{"id","index","total","instruction","tip","warning","reference_image_url"}|null,
   "speech_epoch":n}`
- `{"type":"speech","text":"...","epoch":n,"kind":"enter|success|mismatch|hint|progress|complete|tip"}` → client đọc TTS, câu mới ngắt câu cũ
- `{"type":"vision.result","detector":...,"result":{...},"latency_ms":n}` (để debug/hiển thị)
- `{"type":"hud.error","message":"..."}`

## 6. REST API

- `GET  /api/health` → `{"ok":true,"mode":"mock|ai","vision_model":...,"compiler_model":...}`
- `GET  /api/skills` → `[{"id","display_name","learned_from","summary","steps":n,"cover_image_url"}]`
- `GET  /api/skills/{id}` → Skill JSON đầy đủ (+ `*_url` cho ảnh)
- `PUT  /api/skills/{id}` → lưu Skill (sau khi người dạy chỉnh), validate bằng pydantic
- `DELETE /api/skills/{id}`
- `POST /api/teach/compile` body:
  `{"title":"...","teacher_name":"...","transcript":[{"t0":ms,"t1":ms,"text":"..."}],"frames":[{"t":ms,"image":"data:image/jpeg;base64,..."}],"notes":"..."}`
  → `{"skill": <Skill JSON chưa lưu, id đề xuất>, "draft_id":"..."}` (ảnh frame đã lưu tạm, PUT sẽ chốt)
- `POST /api/ask` body `{"skill_id","step_id","question","image"}` → `{"answer":"..."}` (tiếng Việt, ≤2 câu)
- Static: `/` → web/, `/skills-media/<id>/frames/<file>` → ảnh.

## 7. Web app (mobile-first)

Màn hình:
1. **Home**: danh sách skill (thẻ: tên, "Học từ <người dạy>", số bước) + nút "Dạy kỹ năng mới". Badge chế độ (Demo/AI).
2. **Teach**: camera sau toàn màn hình, nút Record. Trong khi quay: nhận giọng nói (Web Speech API vi-VN, có timestamp),
   chụp keyframe mỗi ~2s và ngay khi kết thúc một câu nói; hiện transcript live. Dừng → nhập tên skill/người dạy → "AI học".
   Không có speech recognition → cho gõ ghi chú.
3. **Review**: danh sách bước (ảnh mẫu, instruction, tip, câu nói khi sai) sửa được, xoá/đổi thứ tự → Lưu.
4. **Guide**: camera full màn, HUD trên cùng "Bước 2/5" + instruction lớn, thumbnail ảnh mẫu người dạy, bong bóng câu AI vừa nói,
   chấm trạng thái (đang nhìn / đúng / sai), nút giữ-để-hỏi 🎤, nút debug ◀ ▶ (ẩn sau long-press), TTS vi-VN.
   Gửi frame: CV (MediaPipe HandLandmarker) phát hiện tay vừa dừng → gửi ngay; ngoài ra gửi định kỳ mỗi ~1.5s; ảnh 512px JPEG q≈0.7.
5. **Done**: hoàn thành, số lần được sửa, thời gian.

## 8. Chế độ demo offline (mock)

- MockProvider vision: mỗi step trả "chưa xong" 2 frame rồi "xong"; riêng step thứ 2 lần đầu trả kết quả sai để demo việc AI sửa lỗi.
- Mock compiler: tách transcript theo câu → mỗi câu một step (`momentary_true_complete`), dùng frame gần nhất làm ảnh mẫu.
- Có sẵn 1 skill mẫu tiếng Việt trong `backend/skills/`.

## 9. Checklist

- [x] Setup dự án, copy upstream, venv, schema mở rộng, PLAN.md
- [x] Backend core: providers.py, vision_runtime.py, workflow fork (bỏ inventory/recipe-selection, start theo skill_id), main.py routes, skill_store.py
- [x] Skill compiler + prompt + validate + mock + skill mẫu
- [x] Web app: Home / Teach / Review / Guide / Done, TTS, STT, camera, CV gating, PWA
- [x] Tests backend (pytest) + smoke test WS end-to-end với mock
- [x] run.sh (HTTP local + HTTPS qua ngrok cho điện thoại), README tiếng Việt, NOTICE.md (công khai phần dùng lại)
- [x] Chạy thử với API thật — gateway Qwen llm.hypertek.vn (vision: qwen3-vl-8b-instruct ~0.7s/frame; compiler: qwen3.6-35b-a3b = Qwen3.8-27B, tắt thinking)
- [x] Eval: thu frame có nhãn trên điện thoại (POST /api/eval/capture) + `backend/eval_detectors.py` chấm nhiều model (độ chính xác, báo xong sai, độ trễ)
- [x] Eval với API thật trên ảnh thật (Wikimedia, 20 frame, skill "pha trà"): VL-8B 90% đúng, p50 0.84s; Qwen3.8 90%, p50 1.37s → giữ VL-8B cho vision
- [x] Thử Qwen3.8-27B: tắt thinking ~1.2s/frame (bật 5–14s); đang dùng cho compiler
- [x] E2E với AI thật: sửa 10 lỗi (spam hint, nhảy bước do JSON sai, mất AI, timeout riêng vision 10s / ask 20s / compile 90s…)
- [x] Chống nhảy bước: `COMPLETE_CONFIRM_FRAMES=2` (2 frame liên tiếp báo xong mới chuyển bước) + tests
- [ ] Thu mẫu thật cho bài demo (lắp giá đỡ, ánh sáng sân khấu) + chạy eval, chỉnh prompt detector
- [ ] So VL-8B vs Qwen3.8 bằng eval_detectors.py trên mẫu thật của bài demo
- [ ] Thử trên điện thoại thật (iPhone + Android) qua `./run.sh tunnel`: TTS/STT tiếng Việt, MediaPipe, camera

---

# Phần 2 — Trợ lý tủ đồ (kỹ năng làm sẵn trong SHADOW)

Ý tưởng: SHADOW là trợ lý. Ngoài kỹ năng do người thật dạy (Teach), có **kỹ năng làm sẵn "Trợ lý tủ đồ"**:
nhìn bạn đang mặc gì, phối đồ từ tủ đồ của bạn theo thời tiết và sự kiện, rồi cho **mặc thử** (trực tiếp trên camera
hoặc ảnh). Kỹ năng này **cũng dạy được**: một người (stylist, chị gái, mẹ…) ghi lại "quy tắc phối đồ" bằng giọng nói hoặc chữ,
trợ lý áp dụng và ghi "Học từ <tên>".

## Công nghệ (đã kiểm chứng 2026-11-09)
- Gắn nhãn đồ và nhìn đồ đang mặc: **Qwen3-VL-8B** (gateway llm.hypertek.vn, `VISION_MODEL`).
- Chọn bộ phối và giải thích: **Qwen3.8** (`COMPILER_MODEL`, tắt thinking), có thể dùng `STYLIST_MODEL` riêng.
- Thời tiết: **Open-Meteo** (miễn phí, không key) — geocoding `geocoding-api.open-meteo.com/v1/search?name=..&language=vi`,
  forecast `api.open-meteo.com/v1/forecast?...daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code`.
- Mặc thử, 2 hướng, có công tắc tự chọn:
  1. **Trực tiếp**: Decart `lucy-vton-latest` qua WebRTC, SDK `@decartai/sdk@0.2.14` (ESM qua jsDelivr). Backend giữ
     `DECART_API_KEY` và chỉ phát client token ngắn hạn. Tham khảo/port từ **DecartAI/tryon-examples** (MIT), ví dụ
     `outfit-builder` (ghép áo + quần, tạo prompt cho bộ) và `mobile-fitting-room`. ~17 fps, ~$0.02/giây, có watermark.
  2. **Ảnh**: provider `TRYON_PHOTO_PROVIDER` = `hf_space` (mặc định: Hugging Face Space `yisol/IDM-VTON` qua gradio_client,
     ~25s/ảnh, miễn phí, giấy phép phi thương mại; `HF_TOKEN` tuỳ chọn để có quota tốt hơn) | `worker` (Mac mini chạy
     `tryon-worker/` với CatVTON, `TRYON_WORKER_URL`) | `mock`. Thứ tự tự động: worker (nếu URL cấu hình & /health OK) → hf_space → mock.

## Dữ liệu
`backend/wardrobe/items.json` + `backend/wardrobe/images/<id>.jpg`; `backend/wardrobe/rules.json` (quy tắc được dạy);
`backend/wardrobe/tryon/` (ảnh kết quả mặc thử). Item:
```
{"id","name","category":"top|bottom|dress|outerwear|shoes|accessory","colors":["trắng"],"style":"công sở|thường ngày|thể thao|dự tiệc|...",
 "warmth":1-5,"occasions":["đi làm","đám cưới",...],"seasons":[...],"pattern":"trơn|kẻ|hoa|...","material":"...","notes":"...",
 "image":"images/<id>.jpg","image_url":"/wardrobe-media/images/<id>.jpg","created_at"}
```
Rule: `{"id","text","taught_by","created_at"}`.

## API
- `GET /api/wardrobe/items` · `POST /api/wardrobe/items` `{image, name?, category?}` → AI gắn nhãn (Qwen-VL; mock khi offline) → item
- `PUT /api/wardrobe/items/{id}` (sửa nhãn) · `DELETE /api/wardrobe/items/{id}`
- `POST /api/wardrobe/seed-demo` → nạp bộ tủ đồ mẫu (để demo khi chưa chụp đồ thật)
- `GET/POST/DELETE /api/wardrobe/rules` — quy tắc do người dạy `{text, taught_by}`
- `GET /api/weather?city=Hà Nội&date=YYYY-MM-DD` hoặc `?lat=&lon=` → `{city, date, t_min, t_max, rain_prob, code, summary_vi}`
- `POST /api/stylist/suggest` `{event, city?, lat?, lon?, date?, current_image?, anchor_item_id?, notes?}` →
  `{"weather":{...},"wearing":"mô tả đồ đang mặc (nếu có ảnh)","outfits":[{"title","item_ids":[...],"reason","tips"}],"rules_used":[...]}`
  (2–3 bộ, chỉ dùng item có trong tủ; nếu có `current_image`/`anchor_item_id` thì giữ món đó và phối quanh nó.)
- `GET /api/tryon/config` → `{"live":{"available":bool,"model":"lucy-vton-latest"},"photo":{"provider":"worker|hf_space|mock"}}`
- `POST /api/tryon/live-token` → token ngắn hạn của Decart (503 nếu chưa có `DECART_API_KEY`)
- `POST /api/tryon/photo` `{person_image, item_ids:[...]}` → `{"image_url","provider","seconds"}` (áo/váy trước; nếu có cả quần thì chạy 2 lượt)
- Static `/wardrobe-media/...`.

## Web
- Home: thẻ nổi bật "👗 Trợ lý tủ đồ" (kỹ năng làm sẵn) bên cạnh danh sách kỹ năng.
- **Tủ đồ**: lưới ảnh theo loại, thêm đồ bằng camera/ảnh (AI tự gắn nhãn, sửa được), nút "Nạp tủ đồ mẫu".
- **Phối đồ**: chọn sự kiện (chip + gõ/nói), thời tiết tự lấy theo vị trí/thành phố, tuỳ chọn "Chụp đồ đang mặc" hoặc chọn 1 món giữ nguyên →
  2–3 thẻ bộ phối (ảnh các món + lý do + mẹo) → "Mặc thử trực tiếp" / "Ảnh mặc thử". Đọc lý do bằng TTS.
- **Mặc thử trực tiếp**: camera trước, kết nối Decart, hiện video đã thay đồ, vuốt/nút đổi bộ, đồng hồ chi phí + tự ngắt khi rảnh 60s.
- **Ảnh mặc thử**: chụp ảnh toàn thân (khung hướng dẫn tư thế), chờ 25–60s có tiến trình, so sánh trước/sau.
- **Dạy trợ lý**: danh sách quy tắc + thêm bằng giọng nói/chữ, ghi tên người dạy.

## Mac mini worker (`tryon-worker/`)
FastAPI + CatVTON (diffusers, PyTorch MPS), `POST /tryon {person_image, garment_image, category} → {image}`, `GET /health`.
Script `setup_macmini.sh` (venv, cài torch, tải weights), `run.sh`; README tiếng Việt. Không test được trên máy Intel hiện tại.

## Checklist phần 2
- [x] Backend: wardrobe/rules/weather/stylist/tryon providers + tests (`wardrobe_api.py`, `stylist.py`, `weather.py`, `tryon.py`, `tests/test_wardrobe_*.py`; pytest 115 passed)
- [x] Web: Tủ đồ / Phối đồ / Mặc thử trực tiếp / Ảnh mặc thử / Dạy trợ lý (`web/js/screens/wardrobe|stylist|tryon-live|tryon-photo|rules.js`)
- [x] tryon-worker cho Mac mini: CatVTON + mask SCHP, `setup_macmini.sh`, `run.sh`, README tiếng Việt, tests (mock 25 passed)
- [ ] Chạy tryon-worker thật trên Mac mini M4 (cài đặt, đo giây/ảnh, gọi từ laptop qua LAN)
- [x] Tủ đồ mẫu 10 món (`backend/wardrobe_seed/`, nguồn ảnh trong `SOURCES.md`) + `POST /api/wardrobe/seed-demo`
- [x] Test với Qwen thật (gắn nhãn 2–4s, phối đồ 9–14s) + HF Space IDM-VTON thật (~30s/ảnh)
- [ ] Test Decart mặc thử trực tiếp thật (chưa có `DECART_API_KEY`)
- [ ] Thử Trợ lý tủ đồ trên điện thoại thật (camera trước, chụp đồ đang mặc, WebRTC Decart)
- [x] Tài liệu: README, DEMO_SCRIPT (kịch bản 5 phút gộp 2 phần), docs/SETUP_CHECKLIST.md (sáng ngày thi)

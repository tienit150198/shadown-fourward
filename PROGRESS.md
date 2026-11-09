# PROGRESS

## 2026-11-09 ~02:20
- Đã tạo dự án, copy GlassKit (upstream/ + backend/), tạo .venv (Python 3.11), mở rộng schema trong backend/recipe_catalog.py, viết PLAN.md, NOTICE.md.
- Đang chạy song song 3 agent:
  1. Backend lõi: providers.py, vision_runtime.py, sửa session_workflow.py, skill_store.py, main.py, tests, run.sh, .env.example, requirements.txt
  2. Skill compiler: backend/skill_compiler.py, tests/test_skill_compiler.py, skill mẫu backend/skills/lap-gia-do-dien-thoai.json
  3. Web app: web/*
- Việc tiếp theo sau khi 3 agent xong: ghép nối, chạy toàn bộ test, smoke test WS end-to-end (mock), README tiếng Việt, cập nhật checklist PLAN.md.
- Trigger 06:51 (một lần, chỉ sống trong session hiện tại) sẽ tiếp tục nếu bị gián đoạn.

## 2026-11-09 ~02:50 — MVP xong (chế độ Demo)
- Backend lõi, Teach compiler, web app đều xong. `pytest`: 39 passed. `./run.sh` boot OK, /api/health, /api/skills, / đều 200.
- Test giao diện bằng Chrome headless + camera giả (390px & 360px): Home → Guide 5 bước (có cảnh sửa lỗi bước 2) → Done; Teach → Review → Lưu. Không lỗi JS, không tràn ngang.
- Đã sửa: chữ "null" hiện trên HUD khi bước không có cảnh báo.
- CÒN LẠI (cần user):
  1. LLM_API_KEY (OpenRouter hoặc API OpenAI-compatible khác) để chạy AI thật — prompt compiler & vision chưa từng chạy với model thật.
  2. Thử trên điện thoại thật qua `./run.sh tunnel` (ngrok cần authtoken): TTS/STT tiếng Việt, MediaPipe (headless không có WebGL nên chưa test được).
  3. Quay bộ clip đúng/sai cho bài demo để đo độ chính xác + chỉnh prompt; chọn model vision nhanh nhất.

## 2026-11-09 ~07:05 — Trigger 06:51: thêm công cụ eval
- Vẫn chưa có `.env` / LLM_API_KEY → chưa chạy được AI thật.
- Thêm: `backend/eval_store.py` + API `POST /api/eval/capture`, `GET /api/eval/{skill}`, `DELETE /api/eval/{skill}/{step}/{label}/last`.
- Thêm: `backend/eval_detectors.py` — chấm detector trên frame có nhãn, so sánh nhiều model (--models), báo % đúng / báo xong sai / bỏ sót / độ trễ p50-p95, xuất JSON ở `backend/evals/reports/`. Dùng đúng prompt như lúc hướng dẫn thật (kèm ảnh mẫu).
- Web: Hướng dẫn → Cài đặt → "Thu mẫu eval": nút ✓ Đã xong / ✗ Chưa xong / Hoàn tác, đếm số mẫu từng bước, tạm dừng AI chấm khi đang thu. Test bằng Chrome headless OK (390px, 360px).
- `pytest`: 44 passed. README có mục hướng dẫn eval.
- CÒN LẠI (cần user): API key; thử trên điện thoại thật; thu mẫu thật bài demo rồi chạy eval, chỉnh prompt; (sau) thử Qwen3.8-27B bằng `--models`.

## 2026-11-09 ~09:25 — Chạy AI thật với gateway Qwen
- User cung cấp key gateway https://llm.hypertek.vn/v1 (OpenAI-compatible, vLLM). Model: qwen3-vl-8b-instruct, qwen3.6-35b-a3b (thực chất trả về Qwen/Qwen3.8-27B), + embedding/reranker.
- Đo trên ảnh test (2 câu hỏi đúng/sai): VL-8B 0.7–1.1s đúng 2/2; Qwen3.8 bật thinking 5–14s; tắt thinking (`chat_template_kwargs.enable_thinking=false`) 1.1–1.3s đúng 2/2.
- Compile skill (transcript pha trà): Qwen3.8 13.6s, chất lượng tốt (tip/warning đúng lời người dạy); VL-8B 5s nhưng kém hơn.
- Cấu hình .env: VISION_MODEL=qwen3-vl-8b-instruct, COMPILER_MODEL=qwen3.6-35b-a3b, LLM_EXTRA_BODY tắt thinking (phải để trong nháy đơn vì run.sh source .env).
- E2E thật qua TestClient: WS hướng dẫn 0.57–0.71s/frame, /api/ask 1.1s trả lời tiếng Việt đúng ngữ cảnh. `./run.sh` boot mode=ai OK.
- Sửa: tests/conftest.py ghim biến môi trường để .env thật không lọt vào test. pytest 44 passed.
- CÒN LẠI: thử trên điện thoại thật; thu mẫu bài demo + chạy eval so VL-8B vs Qwen3.8; (tuỳ) bật hint động cho VL-8B nếu model ít trả hint.

## 2026-11-09 ~10:00 — Bắt đầu Phần 2: Trợ lý tủ đồ (gộp vào SHADOW theo yêu cầu user)
- Eval ảnh thật (Wikimedia, 20 frame, skill "pha trà" do Qwen3.8 sinh): VL-8B 90% đúng, p50 0.84s; Qwen3.8 90%, p50 1.37s → giữ VL-8B cho vision.
- Phát hiện cần sửa (chờ agent E2E xong mới sửa để tránh đụng file): (1) timeout 20s quá ngắn khi compile có ảnh; (2) momentary_true_complete nhảy bước chỉ với 1 frame → đổi thành 2 frame liên tiếp.
- Viết DEMO_SCRIPT.md (kịch bản pitch 5 phút).
- Nghiên cứu mặc thử: Decart lucy-vton realtime (live, ~17fps, $0.02/s, cần key) + IDM-VTON HF Space (đã chạy thật 25s/ảnh, miễn phí, phi thương mại) + CatVTON trên Mac mini M4 24GB (worker LAN).
- Đang chạy song song: agent E2E SHADOW với AI thật; agent backend tủ đồ; agent tryon-worker Mac mini; agent web tủ đồ; agent web mặc thử trực tiếp (Decart).

## 2026-11-09 ~10:40 — Agent E2E với AI thật xong + chống nhảy bước
- Agent E2E sửa 10 lỗi: spam hint, kiểu JSON sai làm nhảy bước, pill đỏ sai nghĩa, ▶ bước cuối, xử lý mất AI (pill vàng + toast), fallback compile có thông báo, timeout riêng (vision 10s / ask 20s / compile 90s), huỷ compile dừng hẳn, đồng hồ chờ compile, Hỏi Shadow có ngữ cảnh toàn skill.
- Đo: vision 0.34–0.83s (có ảnh mẫu 0.39–0.64s), compile 13–15s, ask 0.6–1.5s.
- Thêm COMPLETE_CONFIRM_FRAMES (mặc định 2): bước chỉ hoàn thành khi 2 frame liên tiếp báo xong. tests/test_confirm_frames.py. pytest 46 passed.

## 2026-11-09 — Cập nhật tài liệu cho cả 2 phần (chưa commit)
- README.md viết lại: SHADOW là trợ lý AI (kỹ năng do người dạy + kỹ năng làm sẵn "Trợ lý tủ đồ", cũng dạy được); chạy nhanh (Demo vs AI, bảng khoá `.env`, `./run.sh tunnel`); đi qua từng màn hình; bảng model + tốc độ; eval; tóm tắt Mac mini worker; test (backend 115 passed, worker mock 25 passed); cấu trúc thư mục; tóm tắt giấy phép (phần phi thương mại).
- DEMO_SCRIPT.md viết lại: một câu chuyện 5:00 — mở đầu 0:30 → Dạy 1:00 → Hướng dẫn 1:20 (cố tình sai bước 2) → Trợ lý tủ đồ 1:00 (quy tắc "Học từ Chị Lan", phối đồ Đám cưới, mặc thử trực tiếp hoặc ảnh chạy sẵn từ giây 2:30) → công nghệ 0:40 → kết 0:30; phân vai, checklist, bảng xử lý sự cố + thứ tự dự phòng mặc thử.
- Thêm docs/SETUP_CHECKLIST.md: việc sáng ngày thi (mang theo, kế hoạch mạng/hotspot, lệnh kiểm tra laptop, Mac mini, điện thoại, chạy thử toàn bộ).
- PLAN.md: cập nhật checklist. Còn mở: thử điện thoại thật, Decart thật (chưa có key), Mac mini chạy thật, thu mẫu eval thật của bài demo.
- Lưu ý: `.env.example` ghi ví dụ `TRYON_WORKER_URL=http://mac-mini.local:8001` nhưng worker mặc định cổng 8188 (tài liệu dùng 8188; chưa sửa `.env.example` vì ngoài phạm vi).

## 2026-11-09 ~12:30 — Hoàn thiện Phần 2 + rà soát
- Xong: backend tủ đồ (gắn nhãn Qwen-VL, Open-Meteo, stylist, mặc thử Decart/HF/Mac mini/mock), web Tủ đồ/Phối đồ/Ảnh mặc thử/Dạy trợ lý, màn Mặc thử trực tiếp (Decart SDK 0.2.14, mock dev flag), tryon-worker CatVTON cho Mac mini (chạy thật trên CPU; MPS chưa test; mặc định 576x768), header X-Tryon-Token.
- E2E AI thật tủ đồ: sửa 10 lỗi (quota HF, fallback rõ ràng, huỷ dọn rác, tag tiếng Việt, luật màu "Học từ Chị Lan", v.v.). Gắn nhãn 2.8–3.8s, stylist ~5–13s, HF try-on 22–28s.
- Bảo mật: security.py (rate limit theo IP, giới hạn body/WS, Origin check, SHADOW_ACCESS_CODE tuỳ chọn, Decart session cap 180s, chống decompression bomb, không lộ lỗi upstream). 
- Tủ đồ mẫu đồ nam mùa hè: 16 món YaMe (UNIQLO chặn truy cập tự động). Đặt SHADOW_DEMO_SEED=shop trong .env.
- Docs: README, DEMO_SCRIPT (5 phút, 2 phần), docs/SETUP_CHECKLIST.md.
- Kiểm tra cuối: backend 157 passed, tryon-worker 29 passed, node --check toàn bộ JS OK, boot AI mode OK (seed shop 16 món, weather, stylist 10.5s, web 200).
- CẦN USER: HF_TOKEN (HF miễn phí ~2 lượt/ngày, máy này đang hết quota ~23h) hoặc chạy Mac mini; DECART_API_KEY cho mặc thử trực tiếp; test trên điện thoại thật; ảnh eval thật của bài demo.

## 2026-11-09 ~12:45 — Vòng nâng cấp AI "học & dạy" + dữ liệu quần áo (4 agent song song)
1. Guide (dạy): xác nhận nhanh frame thứ 2, giảm kích thước ảnh, chế độ ảnh mẫu, chế độ quyết định logprob (kiểu OpenJev) + so sánh cấu hình bằng eval.
2. Teach (học): keyframe thông minh (phát hiện đổi cảnh), AI tự kiểm tra skill bằng chính video người dạy + tự sửa câu kiểm tra yếu, tạo sẵn bộ eval, Review hiện huy hiệu chất lượng + chọn lại ảnh mẫu.
3. Dữ liệu quần áo: thuộc tính phong phú (màu hex, độ trang trọng, form, chất liệu…), bộ chấm điểm bộ phối (màu/dịp/thời tiết), mở rộng ~40 món YaMe, nhập đồ bằng link shop (chống SSRF).
4. Style memory: quy tắc tiếng Việt → ràng buộc có cấu trúc (must/should), học gu từ 👍/👎 + "Mặc bộ này", dạy bằng bộ mẫu, thẻ "Gu của bạn".

## 2026-11-09 ~16:15 — Guide (dạy) nhanh + chính xác hơn (agent Guide, chưa commit)
- **Xác nhận nhanh** (`VISION_FAST_CONFIRM=on`): frame đầu báo "xong" → `vision.result` có `"confirming": true`, server chấm frame kế tiếp NGAY (bỏ qua `VISION_MIN_INTERVAL_MS` một lần; frame đến trong lúc đang chấm cũng được chấm ngay). App nên gửi frame mới ngay khi thấy `confirming` (guide.js: 1 dòng).
- **Ảnh**: server tự thu nhỏ ảnh người học về `VISION_IMAGE_MAX_SIDE` (mặc định 448, tối đa 512; Pillow, ảnh hỏng thì giữ nguyên); ảnh mẫu cũng thu nhỏ (cache). `session.ready` báo `vision.image_max_side`.
- **Ảnh mẫu** `VISION_REFERENCE_MODE` = always | first | never | auto (mặc định auto: frame đầu của bước + frame ngay sau một lần "chưa đúng" có gợi ý).
- **Quyết định** `VISION_DECISION` = json (mặc định) | logprob (1 token yes/no hoặc chữ cái A/B/C cho detector dạng state, `max_tokens=1` + `logprobs`, cộng xác suất mọi biến thể token; "xong" khi P ≥ `VISION_DONE_THRESHOLD`=0.9; kết quả có `confidence`). Gợi ý ở chế độ logprob: gọi JSON ngắn chạy nền khi kẹt (≥3 frame chưa xong liên tiếp sau mẹo), chịu giới hạn tần suất như cũ. Gateway không có logprobs → tự về json.
- Dùng chung 1 hàm chấm `VisionRuntimeMixin._judge_frame` cho Guide, `eval_detectors.py` và `skill_verify.py` (Teach tự kiểm tra = đúng cách Guide chấm).
- `eval_detectors.py`: `--decision json,logprob --image-side 384,448,512 --reference always,never` (tích Đề-các, chạy xen kẽ cho công bằng), `--threshold`, `--skills-dir`/`SHADOW_SKILLS_DIR`, bảng quét ngưỡng cho logprob.
- Tests: `tests/test_guide_speed.py` (20 test, provider giả). pytest: 239 passed + 1 lỗi của agent tủ đồ (`test_real_shop_seed_files_are_consistent`: seed shop đang mở rộng 42 món > 20).

### Eval ảnh thật (20 frame, skill "pha-tra-eval" 3 bước, Qwen3-VL-8B, concurrency 1, ~11:45–12:05)
Gateway đang tải nặng (bình thường ~0.7 s/lần gọi, lúc đo 1.5–3.5 s) → so sánh độ trễ TƯƠNG ĐỐI giữa các cấu hình (chạy xen kẽ), không phải tuyệt đối.

| cấu hình | đúng | báo xong sai | bỏ sót | p50 ms | p95 ms |
|---|---|---|---|---|---|
| json 384 ảnh mẫu | 95% | 1 | 0 | 2989 | 5344 |
| json 384 không ảnh mẫu | 90% | 2 | 0 | 1456 | 3175 |
| **json 448 ảnh mẫu** | 95% | 1 | 0 | 2925 | 5169 |
| **json 448 không ảnh mẫu** | 95% | 1 | 0 | 1510 | 3276 |
| json 512 ảnh mẫu | 95% | 0 | 1 | 3586 | 6550 |
| json 512 không ảnh mẫu | 90% | 1 | 1 | 2555 | 5709 |
| logprob 384 ảnh mẫu | 95% | 1 | 0 | 1890 | 6617 |
| logprob 384 không ảnh mẫu | 90% | 2 | 0 | 1614 | 7085 |
| logprob 448 ảnh mẫu | 95% | 1 | 0 | 2783 | 5452 |
| logprob 448 không ảnh mẫu | 95% | 1 | 0 | 1530 | 3098 |
| logprob 512 ảnh mẫu | 90% | 1 | 1 | 3330 | 5760 |
| logprob 512 không ảnh mẫu | 90% | 1 | 1 | 1584 | 4885 |

Lặp lại ở 448 (~12:10): json ảnh mẫu 95% p50 3179 · json không ảnh mẫu 95% p50 2048 · logprob ảnh mẫu 95% p50 3595 · logprob không ảnh mẫu 95% p50 1942.
- Lỗi gần như chỉ ở 1–2 ảnh khó: img5 (túi trà treo NGOÀI mép cốc → model nói "đã cho túi trà vào cốc"; logprob P(yes)=0.998–0.9997) và img2 ở 512 (bỏ sót).
- logprob: quét ngưỡng 0.5→0.99 KHÔNG đổi kết quả nào (model quá tự tin: sai thì P≥0.998, bỏ sót thì P≤0.15) và không nhanh hơn json → **giữ json mặc định**; logprob để tuỳ chọn (có `confidence`, không bao giờ vỡ JSON).
- **448** là cỡ duy nhất đạt 95% ở cả 4 cấu hình → mặc định `VISION_IMAGE_MAX_SIDE=448`.
- Bỏ ảnh mẫu: độ chính xác như nhau ở 448 nhưng p50 nhanh hơn 35–50% (bớt 1 ảnh phải đọc) → mặc định `VISION_REFERENCE_MODE=auto` (đa số frame không kèm ảnh mẫu, vẫn kèm ở frame đầu của bước và khi người học đang làm sai để so sánh/gợi ý).

### Độ trễ hoàn thành bước (giả lập đúng nhịp guide.js: frame định kỳ 1.5 s, cách nhau ≥1.2 s; COMPLETE_CONFIRM_FRAMES=2)
`xác nhận` = từ lúc nhận kết quả "xong" đầu tiên đến lúc chuyển bước; `đầu-cuối` = từ lúc gửi ảnh "đã xong" đầu tiên đến lúc chuyển bước. 3 lượt × 3 bước.

| | trước (không xác nhận nhanh, ảnh mẫu mọi frame, 512) | sau (mặc định mới) |
|---|---|---|
| mock (độ trễ giả 600 ms): xác nhận | 1509 ms | 605 ms |
| mock: đầu-cuối | 2114 ms | 1211 ms |
| AI thật (~16:10, gateway tải nặng): xác nhận, trung vị | 3861 ms (2099–7707) | 912 ms (703–1420) |
| AI thật: đầu-cuối, trung vị | 6257 ms (3992–11773) | 2032 ms (1343–2986) |

## 2026-11-09 — Kết thúc vòng nâng cấp AI (dừng theo yêu cầu user)
- Xong 4 agent: Guide nhanh hơn ~3x (xác nhận nhanh, ảnh 448px, ảnh mẫu chế độ auto, logprob tuỳ chọn), Teach tự kiểm tra bằng video người dạy (+ eval tự tạo), dữ liệu quần áo (thuộc tính, chấm điểm bộ phối, 42 món YaMe, nhập bằng link), style memory (luật must/should, học gu, bộ mẫu).
- Phạm vi: chỉ AI + API (giao diện web chỉ để demo; app thật là KMM do đội làm).
- Viết docs/ROADMAP.md (giai đoạn 1–3 + hướng dẫn chạy + lỗi biết trước). Việc kế tiếp: docs/API.md cho KMM.

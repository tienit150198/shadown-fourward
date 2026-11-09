# NOTICE — Phần dùng lại & phần làm mới

SHADOW được xây dựng dựa trên mã nguồn mở **GlassKit** của RealComputer, giấy phép MIT
(https://github.com/RealComputer/GlassKit, commit `4431554046e5ef4e03187469045182954dff746b`, 2026-08-21).
Toàn văn giấy phép: [LICENSE-GlassKit](LICENSE-GlassKit). Bản gốc không chỉnh sửa nằm ở
[upstream/glasskit-mocktail-backend/](upstream/glasskit-mocktail-backend/) để tiện so sánh (`diff -ru upstream/glasskit-mocktail-backend backend`).

## Dùng lại từ GlassKit (ví dụ `rokid-overshoot-openai-realtime`)

- Kiến trúc backend FastAPI "server-authoritative": mỗi session có một vòng lặp sự kiện, backend quyết định bước, câu nói và HUD.
- Định dạng recipe JSON: `tasks` / `detectors` / `steps` / `evaluation_mode`.
- Các bộ đánh giá bước: `match_value`, `numeric_threshold_with_progress_once`, `count_rising_edges_true`,
  `enum_progress_once_then_complete`, `momentary_true_complete`.
- Giao thức HUD state qua WebSocket điều khiển.

## Làm mới cho SHADOW (trong hackathon)

- **Teach mode / Skill Compiler**: AI tự sinh skill (các bước, câu kiểm tra hình ảnh, câu nói, mẹo/cảnh báo, ảnh mẫu) từ
  lời nói + keyframe khi người dạy làm mẫu. GlassKit chỉ có recipe viết tay.
- Thay Overshoot (video WebRTC) bằng: điện thoại gửi frame → backend gọi vision LLM qua API OpenAI-compatible của bên thứ 3.
- Chấm bước có **so với ảnh mẫu của người dạy**; gợi ý sửa lỗi động (`hint`) do AI sinh ra.
- Thay OpenAI Realtime bằng TTS/STT tiếng Việt ngay trên điện thoại.
- Thay app Android cho kính Rokid bằng web app mobile (PWA): Home / Teach / Review / Guide / Done.
- "Hỏi Shadow": hỏi bằng giọng nói kèm ảnh camera hiện tại.
- Computer vision trên máy (MediaPipe Hand Landmarker) để gửi frame đúng lúc tay vừa dừng.
- Chế độ demo offline (mock) và skill mẫu tiếng Việt.

## Thư viện bên thứ 3 khác

- FastAPI, httpx, websockets (Python).
- MediaPipe Tasks Vision (tải qua CDN jsDelivr) — Apache-2.0.

## Trợ lý tủ đồ (kỹ năng làm sẵn) — nguồn tham khảo & giấy phép

- **DecartAI/tryon-examples** (MIT, © 2026 Decart AI — https://github.com/DecartAI/tryon-examples): tham khảo/port
  mẫu "backend giữ API key, chỉ phát client token" (`app/api/tokens/route.ts`), cách kết nối `lucy-vton-latest`
  (`mobile-fitting-room/hooks/useDecartRealtime.ts`) và định dạng prompt thay đồ cho cả bộ áo + quần
  (`outfit-builder/app/api/outfit-prompt/route.ts` → `backend/tryon.py::live_prompt`, ghép từ nhãn có sẵn thay vì gọi LLM).
- **Decart SDK** (`@decartai/sdk`, DecartAI/sdk; `decart-python`): `backend/tryon.py::create_live_token` gọi trực tiếp
  REST `POST https://api.decart.ai/v1/client/tokens` (header `X-API-KEY`) giống `client.tokens.create()` của SDK.
  Dùng Decart là dịch vụ trả phí (~$0.02/giây, có watermark) theo điều khoản của Decart.
- **IDM-VTON** (yisol/IDM-VTON, Hugging Face Space — **CC BY-NC-SA 4.0, phi thương mại**): mặc thử bằng ảnh qua
  `gradio_client` (gọi Space công khai, không phân phối lại model).
- **CatVTON** (Zheng-Chong/CatVTON — CC BY-NC-SA 4.0, phi thương mại): model dự kiến cho `tryon-worker/` chạy trên Mac mini.
- **Open-Meteo** (https://open-meteo.com, dữ liệu CC BY 4.0, API miễn phí không cần key cho mục đích phi thương mại):
  geocoding + dự báo thời tiết. "Weather data by Open-Meteo.com".
- **Ảnh tủ đồ mẫu** (`backend/wardrobe_seed/`, chi tiết từng file trong `SOURCES.md`): ảnh áo từ ví dụ của Space
  IDM-VTON (bộ dữ liệu nghiên cứu VITON-HD, phi thương mại) và Space Kwai-Kolors/Kolors-Virtual-Try-On; quần, chân váy,
  giày là hình vẽ tự tạo bằng Pillow (CC0). **Chỉ dùng cho demo, phi thương mại.**
- Thư viện: Pillow (MIT-CMU / HPND), gradio_client (Apache-2.0).

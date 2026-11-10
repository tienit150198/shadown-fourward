# VTON Output Quality P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hoàn thiện toàn bộ P0 trong `IMPROVE_STYLIST_VTON_OUTPUT_QUALITY.md` bằng các quality gate chạy cục bộ, descriptor/prompt có cấu trúc, bộ eval không chứa ảnh riêng tư, browser mock E2E và post-check ảnh đầu ra mà không cần API key/GPU.

**Architecture:** Các phép kiểm tra ảnh và metadata là hàm thuần, tất định để dùng lại ở API, store và test. MediaPipe tiếp tục là nguồn pose phía trình duyệt; màn photo chạy cùng pose validator trước khi gọi backend, màn live chặn bắt đầu/apply khi outfit cần chân nhưng khung hình chưa đạt. E2E dùng browser automation ở mock mode, còn eval chỉ commit manifest/schema và placeholder công khai, không commit ảnh người dùng.

**Tech Stack:** Python 3, FastAPI, Pillow, pytest; vanilla ES modules, MediaPipe Tasks Vision, Node built-in test runner; Playwright (chỉ dùng nếu đã có sẵn trong môi trường, nếu không E2E có script tự bỏ qua với lý do rõ ràng).

**Spec:** `docs/IMPROVE_STYLIST_VTON_OUTPUT_QUALITY.md` — mục `P0 — Làm ngay, không cần API key/GPU`.

## Global Constraints

- Không thêm dependency hoặc đổi version khi chưa được người dùng duyệt; chỉ dùng Pillow, MediaPipe và thư viện chuẩn đã có.
- Không cần và không gọi FASHN/Decart/Hugging Face thật trong test P0.
- Không commit ảnh người thật/ảnh riêng tư; eval manifest chỉ tham chiếu asset consented nằm ngoài Git hoặc placeholder công khai.
- Quality gate trả mã máy đọc được và hướng dẫn tiếng Việt; cảnh báo không được giả thành lỗi hard-block nếu heuristic chưa đủ chắc chắn.
- Item ID vẫn là source of truth; prompt/descriptor không được tự tạo garment ngoài wardrobe.
- Mọi hành vi mới đi qua Red–Green–Refactor; production code chỉ được sửa sau khi test tương ứng đã đỏ đúng lý do.

## Review Focus

- Ảnh garment hợp lệ nhưng trắng/đen chủ đạo không được nhận nhầm là ảnh rỗng; Task 1 có fixture sáng/tối hợp lệ và fixture phẳng thực sự.
- Bottom/dress không được gửi khi landmark hông/gối bị khuất hoặc ngoài khung; Task 2 kiểm tra visibility và tọa độ biên.
- Metadata thiếu/không hợp lệ phải hạ confidence và yêu cầu xác nhận, không sinh prompt chứa `undefined`; Task 3 có bảng case thiếu field.
- Provider trả HTTP 200 nhưng ảnh rỗng/đen/quá nhỏ phải bị từ chối trước khi lưu; Task 5 kiểm tra từng nhánh bằng ảnh Pillow thật.
- Route leave/hidden/cancel trong mock browser phải giải phóng camera/request và giữ disclosure; Task 6 kiểm tra hành vi quan sát được thay vì grep source.

---

## How to do

### Task 1: Garment image quality gate và UI cảnh báo

**Files:**
- Create: `backend/image_quality.py`
- Modify: `backend/wardrobe_store.py`
- Modify: `backend/wardrobe_api.py`
- Modify: `web/js/screens/wardrobe.js`
- Test: `backend/tests/test_image_quality.py`
- Test: `backend/tests/test_wardrobe_api.py`

**Interfaces:**
- Produces: `inspect_garment_image(jpeg: bytes) -> dict[str, Any]` với `status`, `score`, `width`, `height`, `garment_photo_type`, `warnings`.
- Produces: item public có `image_quality` và `garment_photo_type`; API vẫn nhận ảnh hợp lệ nhưng trả warning cho heuristic low-res/blur/exposure/crop.

- [ ] Viết fixture Pillow và test đỏ cho ảnh cạnh ngắn dưới 512, blur/phẳng, quá tối/quá sáng, crop chạm biên và ảnh product hợp lệ.
- [ ] Chạy `pytest -q backend/tests/test_image_quality.py` và xác nhận FAIL vì module/interface chưa tồn tại.
- [ ] Cài đặt analyzer bằng Pillow (`ImageStat`, `FIND_EDGES`, border/foreground bounding box), không dùng OpenCV/ML và không hard-block từ heuristic.
- [ ] Chạy test target và xác nhận PASS.
- [ ] Viết test API đỏ chứng minh item mới lưu quality metadata và warning được trả cho client.
- [ ] Tích hợp analyzer trong `WardrobeStore.create_item`, hiển thị warning cụ thể sau upload ở wardrobe screen.
- [ ] Chạy `pytest -q backend/tests/test_image_quality.py backend/tests/test_wardrobe_api.py` và `node --check web/js/screens/wardrobe.js`.

### Task 2: Person/pose quality gate cho photo và live

**Files:**
- Modify: `web/js/tryon-pose.js`
- Modify: `web/js/screens/tryon-photo.js`
- Modify: `web/js/screens/tryon-live.js`
- Create: `web/tests/tryon-pose.test.mjs`

**Interfaces:**
- Produces: `personPoseVerdict(landmarks, { needKnees }) -> { ok, code, message }`.
- Produces: `detectImagePose(imageSource) -> Promise<landmarks|null>` dùng landmarker cache hiện có.
- Consumes: outfit `bottom|dress` để đặt `needKnees=true`.

- [ ] Viết Node test đỏ cho pose không có người, mất đầu, thiếu hông, thiếu gối, visibility thấp và pose đạt.
- [ ] Chạy `node --test web/tests/tryon-pose.test.mjs` và xác nhận FAIL do export chưa tồn tại.
- [ ] Cài đặt verdict thuần và still-image detector dùng MediaPipe hiện có.
- [ ] Chạy Node test và xác nhận PASS.
- [ ] Tích hợp photo: validate ảnh đã chọn ngay trước POST; fail thì không gọi API và hiển thị cách lùi/hạ máy.
- [ ] Tích hợp live: start/apply bị chặn khi pose hiện tại không đạt cho bottom/dress; top chỉ cần đầu–hông; network critical vẫn bị chặn như cũ.
- [ ] Chạy Node test và `node --check` cho ba file JS.

### Task 3: TryOnGarmentDescriptor, confidence và xác nhận UI

**Files:**
- Modify: `backend/garment_attrs.py`
- Modify: `backend/tryon.py`
- Modify: `backend/wardrobe_store.py`
- Modify: `web/js/decart-live.js`
- Modify: `web/js/screens/wardrobe.js`
- Test: `backend/tests/test_garment_attrs.py`
- Test: `backend/tests/test_wardrobe_tryon.py`
- Create: `web/tests/decart-live.test.mjs`

**Interfaces:**
- Produces: `tryon_descriptor(item) -> dict` gồm `category`, `color`, `material`, `pattern`, `fit`, `length`, `details`, `confidence`, `needs_confirmation`.
- Produces: `build_tryon_description(item) -> str` giới hạn 20–30 từ, bỏ field thiếu, không chứa giá trị ngoài allowlist.
- Item public có `attribute_confidence` và `needs_confirmation`; chỉnh category/tag qua API làm confidence đã xác nhận.

- [ ] Viết pytest đỏ cho descriptor đủ/thiếu metadata, confidence thấp và prompt tất định không chứa `None`.
- [ ] Chạy test target, xác nhận FAIL đúng interface thiếu.
- [ ] Cài đặt descriptor, confidence theo provenance (`user/seed` cao hơn AI/default), prompt builder và dùng chung trong `live_prompt`/worker.
- [ ] Chạy pytest target và xác nhận PASS.
- [ ] Viết Node test đỏ để prompt frontend khớp contract backend trên các literal fixture.
- [ ] Dùng descriptor field do backend trả về trong Decart prompt; UI wardrobe gắn nhãn “Cần xác nhận” và mở edit hiện có.
- [ ] Chạy pytest/Node test và syntax check.

### Task 4: Eval schema và consented placeholders

**Files:**
- Create: `backend/evals/vton_cases.schema.json`
- Create: `backend/evals/vton_cases.json`
- Create: `backend/evals/README.md`
- Create: `backend/evals/validate_vton_cases.py`
- Create: `backend/tests/test_vton_eval_manifest.py`

**Interfaces:**
- Produces: manifest case gồm `id`, asset refs tương đối, categories, consent record, expected checks và human labels trống.
- Produces: validator CLI không cần package ngoài, từ chối absolute path, data URL, URL từ xa và consent thiếu.

- [ ] Viết pytest đỏ cho manifest placeholder hợp lệ và case chứa absolute/private/data URL bị từ chối.
- [ ] Chạy test target, xác nhận FAIL vì validator chưa tồn tại.
- [ ] Cài đặt schema/validator và manifest placeholder không chứa ảnh riêng tư.
- [ ] Chạy `pytest -q backend/tests/test_vton_eval_manifest.py` và `python backend/evals/validate_vton_cases.py backend/evals/vton_cases.json`.

### Task 5: Post-generation technical image check

**Files:**
- Modify: `backend/image_quality.py`
- Modify: `backend/tryon.py`
- Test: `backend/tests/test_image_quality.py`
- Test: `backend/tests/test_wardrobe_tryon.py`

**Interfaces:**
- Produces: `validate_generated_image(raw: bytes, *, min_width=384, min_height=512) -> dict[str, Any]` hoặc ném `ImageQualityError` với code `decode_failed|too_small|blank|near_black|near_white|blurred`.
- `TryOnService` chạy check sau mỗi provider result và trước khi đánh dấu item rendered/lưu output.

- [ ] Viết test đỏ bằng ảnh thật cho corrupt, quá nhỏ, đen, trắng, phẳng và ảnh có nội dung hợp lệ.
- [ ] Chạy test target, xác nhận FAIL đúng interface thiếu.
- [ ] Cài đặt validator kỹ thuật bằng Pillow với threshold bảo thủ để không loại ảnh trang phục đen/trắng có texture.
- [ ] Viết test service đỏ chứng minh output provider HTTP-success nhưng invalid thành `TryOnError` và không được lưu/return.
- [ ] Tích hợp check tại boundary chung của provider, giữ fallback chain hoạt động với provider auto.
- [ ] Chạy target tests và xác nhận PASS.

### Task 6: Browser E2E mock cho các release gate P0

**Files:**
- Create: `web/e2e/tryon-mock.spec.mjs`
- Create: `web/e2e/run.mjs`
- Create: `web/e2e/README.md`
- Modify: `README.md`

**Interfaces:**
- Produces: runner dùng Playwright đã cài sẵn nếu có; nếu thiếu thì exit code riêng/skip có thông báo, không tự tải browser hoặc thêm dependency.
- Kiểm tra: Stylist handoff → live mock; đổi outfit không reconnect; route leave/hidden cleanup; photo cancel/compare/fallback disclosure.

- [ ] Viết E2E theo hành vi người dùng, dùng fake media stream và `window.__DECART_MOCK_LOG__`, không assert source text.
- [ ] Chạy runner; nếu Playwright có sẵn, xác nhận test đỏ ở behavior P0 chưa tích hợp hoặc fixture chưa phục vụ.
- [ ] Hoàn thiện test harness/server fixture tối thiểu và sửa hành vi thật nếu E2E phát hiện lỗi.
- [ ] Chạy lại E2E đến PASS; nếu môi trường không có Playwright, ghi rõ SKIP và giữ unit/integration gate bắt buộc xanh.

## How to check

- [ ] Unit/backend P0: `pytest -q backend/tests/test_image_quality.py backend/tests/test_garment_attrs.py backend/tests/test_wardrobe_api.py backend/tests/test_wardrobe_tryon.py backend/tests/test_vton_eval_manifest.py` → 0 failed.
- [ ] Frontend pure rules: `node --test web/tests/*.test.mjs` → 0 failed.
- [ ] Frontend syntax: `node --check web/js/tryon-pose.js && node --check web/js/screens/tryon-photo.js && node --check web/js/screens/tryon-live.js && node --check web/js/screens/wardrobe.js && node --check web/js/decart-live.js` → exit 0.
- [ ] Eval privacy/schema: `python backend/evals/validate_vton_cases.py backend/evals/vton_cases.json` → valid, không có absolute path/data URL/remote URL.
- [ ] Browser mock: `node web/e2e/run.mjs` → PASS nếu Playwright có sẵn; nếu SKIP phải báo là bằng chứng còn thiếu, không đổi thành claim E2E pass.
- [ ] Full regression backend: `pytest -q backend/tests` → 0 failed.
- [ ] Diff hygiene: `git diff --check` và `git status --short`; không chạm các file untracked có sẵn ngoài phạm vi.
- [ ] Đối chiếu lại 6 dòng P0 trong spec và ghi mapping test/evidence trong báo cáo cuối.

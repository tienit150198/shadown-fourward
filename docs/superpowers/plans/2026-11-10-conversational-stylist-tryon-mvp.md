# Conversational Stylist → Virtual Try-on MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng luồng người dùng trò chuyện với Stylist AI, bổ sung ngữ cảnh theo từng lượt, nhận bộ đồ chỉ gồm item thật trong tủ và mặc bộ đã chọn trực tiếp trên camera như một “gương AI”; ảnh mặc thử là fallback.

**Architecture:** Thêm một state machine hội thoại mỏng trước engine `suggest_outfits()` hiện tại. LLM chỉ chuyển lời người dùng thành intent có cấu trúc và viết câu trả lời; việc hợp lệ hóa item, áp dụng luật, scoring và fallback tiếp tục do code tất định kiểm soát. Khi người dùng chốt outfit, client mở live camera và gửi từng garment reference/prompt vào Decart Lucy VTON qua WebRTC; người dùng có thể đổi outfit mà không tạo lại cuộc hội thoại. MVP chạy đầy đủ ở `SHADOW_MODE=mock`; Decart thật là bước xác nhận tích hợp sau khi có key, HTTPS và thiết bị.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, pytest, vanilla JavaScript, sessionStorage, REST APIs hiện có của SHADOW.

**Spec:** `docs/RESEARCH_CONVERSATIONAL_STYLIST_VTON.md`; `docs/SYSTEM_DESIGN_ANALYSIS.md`; mô tả luồng của người dùng ngày 10/11/2026. Người dùng xác nhận reel mô tả camera giữ hình người dùng và AI thay bộ quần áo khác trực tiếp trên màn hình.

## Global Constraints

- Không phá contract `POST /api/stylist/suggest`; endpoint chat là contract mới.
- Không thêm dependency hoặc đổi version khi chưa được người dùng phê duyệt.
- Mọi outfit trả về phải chỉ chứa item ID đang tồn tại trong wardrobe.
- Hard rule, weather guard, anchor và excluded item phải được kiểm tra ngoài LLM.
- Mock mode phải chạy end-to-end không cần API key, mạng, GPU hoặc build KMM.
- Session memory ngắn hạn không được tự động ghi thành style profile dài hạn.
- Chỉ feedback/rule có hành động xác nhận rõ mới được ghi vào `style_memory`.
- CatVTON và IDM-VTON chỉ là provider demo/nghiên cứu; MVP không thay đổi tuyên bố giấy phép.
- VTON phải được mô tả là mô phỏng hình ảnh, không phải dự đoán size hoặc độ vừa thật.
- Live camera là trải nghiệm chính; photo VTON chỉ là fallback khi live unavailable hoặc kết nối không đạt yêu cầu.
- Permanent Decart key không được xuống browser; browser chỉ nhận client token ngắn hạn có session/cost cap.
- Camera stream phải dừng ngay khi rời màn live try-on, mất consent hoặc hết thời lượng phiên.
- Ảnh/base64, secret và nội dung nhạy cảm không được ghi vào log.

## Review Focus

- Người dùng nói đủ occasion, location và style ngay câu đầu: không hỏi lại các slot đã có.
- Người dùng nhắc item không tồn tại hoặc item thuộc tủ khác: từ chối an toàn, không đưa ID đó vào outfit.
- Người dùng nói “áo này đang giặt” sau khi đã nhận outfit: item bị loại khỏi mọi gợi ý tiếp theo trong session.
- Provider LLM timeout/sai JSON: rule parser + deterministic outfit picker vẫn hoàn tất lượt chat.
- Provider try-on không hỗ trợ bottoms hoặc không khả dụng: UI báo trước và vẫn cho đường photo/mock phù hợp.
- Người dùng đổi từ outfit A sang outfit B khi camera đang mở: dùng lại WebRTC session và thay garment reference, không xin lại permanent key hoặc tạo session chat mới.

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `backend/stylist_conversation.py` | Kiểu dữ liệu state/intent, validation và transition thuần tất định |
| `backend/stylist_chat.py` | Parse turn, registry session TTL và orchestration gọi stylist engine |
| `backend/wardrobe_api.py` | Pydantic request/response và route `/api/stylist/chat` |
| `backend/stylist.py` | Nhận exclusions/refinement context nhưng vẫn là engine gợi ý có guard |
| `backend/security.py` | Rate-limit endpoint chat và cap chi phí |
| `backend/tests/test_stylist_conversation.py` | Unit/golden multi-turn tests không cần mạng |
| `backend/tests/test_wardrobe_stylist.py` | Regression cho engine và route suggest cũ |
| `backend/tests/test_wardrobe_api.py` | Contract/error tests của endpoint chat |
| `web/js/wardrobe-api.js` | Client method `stylistChat()` |
| `web/js/store.js` | Session state của UI stylist |
| `web/js/screens/stylist.js` | Guided chat, action chips, cards và handoff sang try-on |
| `web/wardrobe.css` | Bubble, composer, quick actions và trạng thái responsive |

### Task 1: ✅ Khóa baseline và contract tương thích

**Files:**
- Modify: `backend/tests/test_wardrobe_stylist.py`
- Modify: `backend/tests/test_wardrobe_api.py`
- Reference: `backend/wardrobe_api.py:283`

**Interfaces:**
- Consumes: `POST /api/stylist/suggest` hiện tại.
- Produces: regression assertions mà mọi task sau phải giữ nguyên.

- [ ] **Step 1: Viết regression tests cho request/response suggest hiện tại**

  Pin các field `weather`, `outfits`, `rules_used`, `source`, `learning`, `feedback_context`; pin lỗi wardrobe rỗng `409`, anchor lạ `404`, ảnh sai `422`.

- [ ] **Step 2: Chạy tests để ghi baseline**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_wardrobe_stylist.py tests/test_wardrobe_api.py -q`

  Expected: PASS; ghi lại số test và thời gian trong commit/PR notes.

- [ ] **Step 3: Commit baseline tests**

  `git commit -m "test: lock stylist suggestion contract"`

### Task 2: ✅ Mô hình hóa state và transition hội thoại

**Files:**
- Create: `backend/stylist_conversation.py`
- Create: `backend/tests/test_stylist_conversation.py`

**Interfaces:**
- Produces: `StylistConversationState`, `StylistIntent`, `apply_intent()` và `missing_required_slots()`.
- Consumes: tập item ID thật từ wardrobe.

- [ ] **Step 1: Viết failing tests cho state transition**

  Test các action: `provide_context`, `exclude_item`, `keep_item`, `refine_formality`, `refine_temperature`, `show_another`, `select_outfit`, `reset`.

- [ ] **Step 2: Chạy test và xác nhận fail vì module chưa tồn tại**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_stylist_conversation.py -q`

  Expected: FAIL tại import.

- [ ] **Step 3: Tạo kiểu dữ liệu và hàm transition**

  Interfaces bắt buộc:

  ```python
  @dataclass
  class StylistConversationState:
      session_id: str
      event: str = ""
      city: str | None = None
      date: str | None = None
      style: str = ""
      formality_delta: int = 0
      temperature_delta: int = 0
      anchor_item_id: str | None = None
      excluded_item_ids: set[str] = field(default_factory=set)
      current_outfit_ids: list[str] = field(default_factory=list)
      shown_outfit_keys: set[str] = field(default_factory=set)
      pending_question: str | None = None
      turn_count: int = 0

  def apply_intent(state: StylistConversationState, intent: StylistIntent, valid_item_ids: set[str]) -> StylistConversationState: ...
  def missing_required_slots(state: StylistConversationState) -> list[str]: ...
  ```

- [ ] **Step 4: Chạy test transition**

  Expected: tất cả PASS; intent có item lạ không làm thay đổi anchor/exclusions.

- [ ] **Step 5: Commit domain state machine**

  `git commit -m "feat: add stylist conversation state machine"`

### Task 3: ✅ Parse lời người dùng thành structured intent với fallback offline

**Files:**
- Create: `backend/stylist_chat.py`
- Modify: `backend/tests/test_stylist_conversation.py`
- Reference: `backend/providers.py`

**Interfaces:**
- Consumes: `LLMProvider`, message, state và item summaries.
- Produces: `async parse_stylist_intent(...) -> StylistIntent`.

- [ ] **Step 1: Viết failing tests cho parser tiếng Việt**

  Pin các câu: “đi đám cưới ở Hà Nội”, “giữ quần này”, “áo này đang giặt”, “trang trọng hơn”, “mát hơn”, “bộ khác”, “mặc thử bộ 2”.

- [ ] **Step 2: Implement deterministic parser cho mock/fallback**

  ```python
  async def parse_stylist_intent(
      provider: LLMProvider,
      message: str,
      state: StylistConversationState,
      items: list[dict[str, Any]],
  ) -> StylistIntent: ...
  ```

  Non-mock dùng `chat_json` với schema allowlist; JSON lỗi/timeout chuyển sang deterministic parser.

- [ ] **Step 3: Thêm test prompt injection và giới hạn input**

  Text OCR/tên item/rule chỉ là data; model output có action hoặc item ID ngoài allowlist phải bị loại.

- [ ] **Step 4: Chạy parser tests**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_stylist_conversation.py -q`

  Expected: PASS trong mock mode, không gọi mạng.

- [ ] **Step 5: Commit parser**

  `git commit -m "feat: parse Vietnamese stylist chat intents"`

### Task 4: ✅ Dùng lại engine gợi ý cho refinement nhiều lượt

**Files:**
- Modify: `backend/stylist.py:811`
- Modify: `backend/tests/test_wardrobe_stylist.py`
- Modify: `backend/tests/test_stylist_conversation.py`

**Interfaces:**
- Consumes: `excluded_item_ids`, refinement notes và outfit keys đã hiển thị.
- Produces: outfit mới vẫn giữ schema hiện tại.

- [ ] **Step 1: Viết failing tests cho exclusions và anchor**

  Assert item đang giặt không xuất hiện; anchor còn tồn tại trong mọi outfit; hard rule vẫn thắng refinement.

- [ ] **Step 2: Mở rộng signature của `suggest_outfits()` bằng optional parameters**

  ```python
  async def suggest_outfits(..., excluded_item_ids: set[str] | None = None, avoid_outfit_keys: set[str] | None = None) -> dict[str, Any]: ...
  ```

  Mặc định `None` phải giữ nguyên hành vi route cũ.

- [ ] **Step 3: Filter trước scoring và loại combo đã hiển thị sau validation**

  Nếu không còn combo mới, trả bộ hợp lệ tốt nhất kèm metadata `repeated: true`; không bịa item.

- [ ] **Step 4: Chạy regression + conversation tests**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_wardrobe_stylist.py tests/test_outfit_scoring.py tests/test_stylist_conversation.py -q`

- [ ] **Step 5: Commit refinement support**

  `git commit -m "feat: support grounded outfit refinements"`

### Task 5: ✅ Thêm registry TTL và endpoint `/api/stylist/chat`

**Files:**
- Modify: `backend/stylist_chat.py`
- Modify: `backend/wardrobe_api.py`
- Modify: `backend/security.py:218`
- Modify: `backend/tests/test_wardrobe_api.py`
- Modify: `backend/tests/test_security.py`

**Interfaces:**
- Produces: `POST /api/stylist/chat`.
- Consumes: state machine, parser, wardrobe store, weather và engine gợi ý.

- [ ] **Step 1: Viết contract tests cho chat endpoint**

  Request tối thiểu `{ "message": "Đi đám cưới" }`; response có `session_id`, `phase`, `assistant_message`, `missing_slots`, `outfits`, `suggested_actions`, `state_version`.

- [ ] **Step 2: Implement bounded in-memory registry**

  ```python
  class StylistConversationRegistry:
      async def get_or_create(self, session_id: str | None) -> StylistConversationState: ...
      async def save(self, state: StylistConversationState) -> None: ...
      async def delete(self, session_id: str) -> bool: ...
  ```

  TTL mặc định 30 phút; cap 100 session; hết cap loại session cũ nhất. Không persist transcript hoặc ảnh.

- [ ] **Step 3: Implement endpoint và one-question policy**

  Thiếu slot ảnh hưởng lớn thì hỏi đúng một câu. Đủ context thì gọi `suggest_outfits()`. Response chỉ chứa state public, không trả raw prompt/transcript.

- [ ] **Step 4: Thêm rate rule `stylist_chat`**

  Dùng ngân sách tương đương hoặc chặt hơn `suggest`; test `429` và cleanup khi request lỗi.

- [ ] **Step 5: Chạy API/security tests**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_wardrobe_api.py tests/test_security.py tests/test_stylist_conversation.py -q`

- [ ] **Step 6: Commit endpoint**

  `git commit -m "feat: add bounded stylist chat endpoint"`

### Task 6: Tạo guided chat UI trên màn Stylist

**Files:**
- Modify: `web/js/wardrobe-api.js:92`
- Modify: `web/js/store.js`
- Modify: `web/js/screens/stylist.js:240`
- Modify: `web/wardrobe.css`

**Interfaces:**
- Consumes: response `/api/stylist/chat`.
- Produces: chat composer, bubbles, action chips và outfit cards dùng lại renderer hiện có.

- [ ] **Step 1: Thêm `wardrobeApi.stylistChat(body, opts)`**

  Timeout bằng suggest hiện tại; hỗ trợ `AbortSignal`.

- [ ] **Step 2: Thêm session UI state**

  `setStylistSession()`, `getStylistSession()`, `clearStylistSession()` dùng `sessionStorage`; chỉ lưu ID, message text giới hạn và public response, không lưu ảnh base64.

- [ ] **Step 3: Tách `renderResult()` thành renderer dùng được trong chat**

  Giữ collage, reason, tips, rule badges và hai CTA try-on hiện có.

- [ ] **Step 4: Thêm composer và quick actions**

  Quick actions lấy từ `suggested_actions`; có “Bỏ qua”, “Bắt đầu lại”, Enter gửi, Shift+Enter xuống dòng, disabled/loading/cancel rõ ràng.

- [ ] **Step 5: Kiểm tra thủ công ở mock mode**

  Run: `SHADOW_MODE=mock ./run.sh`

  Verify: chat được nhiều lượt, reload cùng tab giữ session, tab mới không thấy transcript, provider lỗi có fallback và không dead-end.

- [ ] **Step 6: Commit guided chat UI**

  `git commit -m "feat: add guided conversational stylist UI"`

### Task 7: Nối bộ đã chọn sang Live AI Mirror và Photo fallback

**Files:**
- Modify: `web/js/screens/stylist.js`
- Modify: `web/js/screens/tryon-photo.js`
- Modify: `web/js/screens/tryon-live.js`
- Modify: `backend/tests/test_wardrobe_tryon.py`

**Interfaces:**
- Consumes: selected outfit `item_ids`, `/api/tryon/config`, `/api/tryon/live-token`, `/api/tryon/live-prompt` và photo fallback.
- Produces: live camera thay outfit trên màn hình, capability-aware CTA và handoff chỉ bằng primitive IDs.

- [ ] **Step 1: Viết backend tests pin capability hiện tại**

  Assert `live.available`, `photo.provider`, `chain`, `renders_bottoms`, `expected_seconds`, `notice`.

- [ ] **Step 2: Pin contract live token/prompt**

  Test permanent key không xuất hiện trong response; token endpoint trả generic error; live prompt chỉ dùng item ID thật và mô tả garment đã chuẩn hóa.

- [ ] **Step 3: Thêm client capability mapping**

  Nếu photo provider không render bottoms, hiển thị cảnh báo trước khi chuyển màn; nếu live unavailable, giữ photo CTA và giải thích điều kiện.

- [ ] **Step 4: Giữ handoff bằng `outfitHash(..., item_ids)`**

  Không truyền domain object, conversation transcript hoặc ảnh người qua URL/navigation.

- [ ] **Step 5: Cho phép đổi outfit trong cùng live session**

  Dùng `setImage()` cho garment/outfit tiếp theo trên kết nối WebRTC đang hoạt động; giữ camera stream và framing guide, không tạo token mới nếu token/session vẫn hợp lệ.

- [ ] **Step 6: Dừng camera và session đúng lifecycle**

  Rời route, bấm dừng, hết idle/session cap hoặc lỗi consent phải stop mọi local track, đóng realtime client và xoá object URL tạm.

- [ ] **Step 7: Thêm disclosure trên kết quả**

  Copy bắt buộc: “Ảnh/video do AI tạo; màu, độ rủ và độ vừa có thể không chính xác.” Mock phải có nhãn “Ảnh minh hoạ”.

- [ ] **Step 8: Chạy try-on tests và smoke mock**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_wardrobe_tryon.py -q`

  Verify: live mock mở camera giả lập, đổi được outfit trong cùng session và cleanup khi rời màn; live unavailable tự đưa người dùng tới photo fallback.

- [ ] **Step 9: Commit handoff**

  `git commit -m "feat: hand off stylist outfits to try-on safely"`

### Task 8: Chỉ học gu khi có xác nhận rõ

**Files:**
- Modify: `backend/stylist_chat.py`
- Modify: `backend/tests/test_stylist_conversation.py`
- Reference: `backend/style_memory.py`

**Interfaces:**
- Consumes: explicit feedback actions.
- Produces: gọi API/style-memory hiện có hoặc trả suggested action; không tự ghi preference từ chat thường.

- [ ] **Step 1: Viết tests phân biệt session context và long-term preference**

  “Hôm nay không mặc trắng” chỉ tác động session; “Hãy nhớ là tôi không thích màu trắng” phải yêu cầu xác nhận trước khi tạo rule/profile signal.

- [ ] **Step 2: Implement confirmation response**

  Chat trả `suggested_actions: ["Lưu thành sở thích", "Chỉ áp dụng lần này"]`; chỉ action đầu gọi path ghi hiện hữu sau validation.

- [ ] **Step 3: Chạy style memory + conversation tests**

  Run: `cd backend && ../.venv/bin/python -m pytest tests/test_style_memory.py tests/test_stylist_conversation.py -q`

- [ ] **Step 4: Commit explicit learning control**

  `git commit -m "feat: require consent before stylist learns preferences"`

### Task 9: ✅ Golden conversations, docs và release gate MVP

**Files:**
- Create: `backend/evals/stylist_conversations.json`
- Create: `backend/evals/run_stylist_conversations.py`
- Modify: `docs/RESEARCH_CONVERSATIONAL_STYLIST_VTON.md`
- Modify: `specs/001-kmm-api-contract/contracts/rest-api.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: chat endpoint/service trong mock và provider modes.
- Produces: report định lượng có thể so sánh giữa prompt/model versions.

- [x] **Step 1: Tạo ít nhất 30 golden scenarios tiếng Việt**

  Bao phủ đi làm/đi học/đám cưới/đi chơi, nóng/mưa/lạnh, anchor, đồ đang giặt, rule được dạy, provider lỗi, tủ thiếu category và prompt injection.

- [x] **Step 2: Implement deterministic evaluator**

  Metrics: catalog-only rate, hard-constraint pass, anchor/exclusion adherence, correction success, turns-to-outfit, repeated-outfit rate và fallback rate.

- [x] **Step 3: Đặt promotion gates**

  Catalog-only = 100%; hard-constraint pass = 100%; anchor/exclusion adherence = 100%; không có profile write khi thiếu explicit confirmation.

- [x] **Step 4: Cập nhật REST docs và giới hạn VTON**

  Ghi rõ endpoint chat là experimental, session TTL, rate limit, mock behavior, provider capability và disclosure size/fit.

- [x] **Step 5: Chạy full backend verification**

  Run: `cd backend && ../.venv/bin/python -m pytest -q`

  Expected: PASS; không cần network/API key.

- [x] **Step 6: Chạy evaluator mock**

  Run: `cd backend && SHADOW_MODE=mock ../.venv/bin/python evals/run_stylist_conversations.py`

  Expected: mọi invariant gate đạt 100%; report ghi rõ recommendation quality chưa phải kết quả model thật.

- [x] **Step 7: Commit eval/docs**

  `git commit -m "test: add conversational stylist release gates"`

## Deferred Provider Qualification Backlog

Các việc dưới đây không nằm trên critical path của MVP và chỉ bắt đầu khi có tài nguyên tương ứng:

| Work item | Phụ thuộc ngoài | Điều kiện bắt đầu |
|---|---|---|
| Tune Qwen intent/prompt | LLM key và gateway ổn định | MVP mock/golden tests đã chạy |
| Decart live validation | API key, credit, HTTPS, điện thoại thật | Capability-aware handoff hoàn tất |
| CatVTON Mac mini benchmark | Mac mini M4, model download/build | Chỉ profile research/demo |
| HF IDM-VTON validation | `HF_TOKEN`, quota public Space | Chỉ profile research/demo |
| FASHN/Vertex photo pilot | Vendor account, consented image set, procurement | Provider contract và privacy review hoàn tất |
| FASHN self-host pilot | NVIDIA GPU + dependency/license audit | Xác minh commercial path không dùng dependency NC |
| Durable job/DB/object storage | Auth/tenant/retention decisions | Q6 và production identity boundary được chốt |
| KMM UI implementation | Q3/Q6/Q7 và build toolchain | REST contract experimental được duyệt |

## Milestone đề xuất

1. **M1 — Offline conversational MVP:** Tasks 1–5, backend chat chạy mock, tests xanh.
2. **M2 — Web experience:** Tasks 6–8, trò chuyện → chọn outfit → live AI mirror mock hoàn chỉnh, photo là fallback.
3. **M3 — Quality gate:** Task 9, golden scenarios và tài liệu contract.
4. **M4 — Real-provider pilot:** chọn đúng một photo provider và Decart live sau khi có key/hardware/legal review.

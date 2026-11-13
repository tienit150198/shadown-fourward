# SHADOW

**Làm mẫu một lần, AI hướng dẫn người sau.** (*Show it once. AI can guide the next person.*)

Làm cho Codex Community Hackathon – Hanoi (thứ Bảy 14/11/2026, chủ đề "AI in Your Palm").
Dự án dựa trên GlassKit (MIT). Phần dùng lại và phần làm mới ghi rõ trong [NOTICE.md](NOTICE.md).

## SHADOW là gì?

SHADOW là trợ lý AI cho web và điện thoại. Màn hình chính dùng giọng nói, văn bản và camera để trò chuyện, xem thời tiết, phối đồ và hướng dẫn bài đã học. Hai lối vào riêng dành cho việc thêm đồ và quay bài hướng dẫn.

1. **Kỹ năng do người thật dạy.** Người giỏi vừa làm vừa nói trước camera một lần (Dạy). AI tự chia việc đó thành
   từng bước, có câu hướng dẫn, mẹo, cảnh báo và ảnh mẫu. Sau đó người mới mở camera làm lại (Hướng dẫn): SHADOW nhìn
   từng bước, khen khi đúng, nhắc ngay khi sai, và trả lời khi được hỏi.
2. **Kỹ năng làm sẵn "Trợ lý tủ đồ".** Trợ lý nhìn xem bạn đang mặc gì, phối đồ từ chính tủ đồ của bạn theo thời tiết
   và sự kiện, rồi cho bạn **mặc thử** bằng Vidu S2-Editing trên camera hoặc Vidu Q2 trên ảnh.
   Kỹ năng này **cũng dạy được**: một người (stylist, chị gái, mẹ…) nói "quy tắc phối đồ" của mình, trợ lý áp dụng và
   ghi "Học từ Chị Lan".

---

## Chạy nhanh

Trên máy chủ Linux, chạy `bash deploy/run.sh`. Script tạo `.env` lần đầu. Điền các khóa Hypertek, OpenRouter và Vidu rồi chạy lại. Xem cấu hình HTTPS và lưu trữ trong [hướng dẫn triển khai](docs/DOCKER.md).

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
| `COMPILER_MODEL`, `CONVERSATION_MODEL` | Có | `qwen3.6-35b-a3b` cho sinh kỹ năng và trò chuyện. |
| `LLM_EXTRA_BODY` | Nên có | `'{"chat_template_kwargs": {"enable_thinking": false}}'` — tắt chế độ "suy nghĩ" cho nhanh. **Phải để trong dấu nháy đơn.** |
| `COMPLETE_CONFIRM_FRAMES` | Không (mặc định 2) | Phải 2 khung hình liên tiếp báo "xong" mới chuyển bước (chống nhảy bước nhầm). |
| `OPENROUTER_API_KEY` | Có khi bật Jev | TypeSafe Jev phân loại yêu cầu. Các lệnh rõ ràng có đường xử lý tại backend. |
| `VIDU_API_KEY` | Có khi mặc thử | Vidu Q2 cho ảnh và Vidu S2-Editing cho video trực tiếp. |
| `TRYON_WORKER_URL` | Không | Địa chỉ Mac mini chạy mặc thử bằng ảnh, ví dụ `http://192.168.1.50:8188` (cổng mặc định của worker là **8188**). |
| `TRYON_TOKEN` | Không | Mật khẩu chung với Mac mini — chỉ cần nếu đã đặt `TRYON_TOKEN` bên Mac mini. |
| `HF_TOKEN` | Không | Token Hugging Face, giúp mặc thử bằng ảnh qua Hugging Face ít bị giới hạn lượt. |
| `TRYON_PHOTO_PROVIDER` | Không | Mẫu cloud chọn `vidu`. Có thể đặt `mock` cho demo offline. |
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
mặc thử trực tiếp mặc định giới hạn 120 giây qua `VIDU_MAX_SESSION_SECONDS`.

---

## Đi một vòng các màn hình

### Thử đồ trực tiếp với Xmax X2.1

Camera chung dùng **Xmax X2.1** cho yêu cầu mặc thử trực tiếp. Đặt `XMAX_API_KEY` trong `.env` và dùng `XMAX_ENVIRONMENT=global` (mặc định). Tạo key tại [Xmax Platform](https://platform.xmax.ai/api-keys). Key lâu dài chỉ nằm ở backend; trình duyệt nhận key tạm có thời hạn 150 giây, tối đa một phiên đồng thời và giới hạn 200 credit. Phiên trên màn hình tự dừng sau tối đa 2 phút. Bấm dừng hoặc rời trang sẽ đóng kết nối SDK và thu hồi key tạm.

Nói **“Chọn một bộ đồ nam và mặc thử luôn”** hoặc **“Mặc áo thun trắng với quần nỉ xám cho tôi”**: backend tìm đồ thật trong tủ và tự mở Xmax, không cần bấm nút mặc thử. Nói **“mặc bộ đó đi”** dùng bộ đã chọn hoặc bộ vừa được gợi ý trong hội thoại; **“đổi sang quần jeans xanh”** giữ áo hiện tại. Nếu chưa tìm được món phù hợp, Shadow hỏi lại bằng giọng nói. Chỉ xem danh sách khi người dùng yêu cầu xem/chọn trước. Các ảnh và nút **Mặc thử bộ đã chọn** vẫn dùng được tùy ý. Nếu chưa có ảnh, dùng **Thêm ảnh trang phục vào bộ**. Backend ghép ảnh của tất cả món đã chọn và yêu cầu Xmax mặc đồng thời trong một phiên. Để camera thấy cả người khi thử áo và quần. SHADOW dùng lại camera đang mở. Trong lúc Xmax tải, vẫn có thể trò chuyện; chỉ báo video đã chạy khi nhận được khung hình kết quả đầu tiên.

Trong hội thoại, **“tủ đồ”, “trong tủ”, “đồ của tôi” mặc định là tủ đồ trong SHADOW**. Decisions và Live nhận cùng quy tắc này, cùng tổng số món, số lượng từng loại và danh sách thật được tìm theo yêu cầu. Chỉ hiểu là tủ vật lý khi người dùng nói rõ tủ trong phòng/ngoài đời/đang chỉ qua camera.

Chạy `npm ci`, `npm run build:xmax` và `npm run build:app` trong `web-sdk/` khi chạy backend trực tiếp bằng `uvicorn`. `./run.sh` và Docker chuẩn bị SDK theo cấu hình. [Web SDK chính thức](https://github.com/Xmax-AI/XmaxSDK-Web).

### Snap Camera Kit (màn kiểm tra riêng)

Màn **Tủ đồ → Thử áo AR** vẫn có thể mở riêng để kiểm tra Snap SDK. Lens là mẫu đã cấu hình trong Snap, không tự biến mọi món đồ trong tủ thành áo AR. Camera chung mặc định dùng Xmax X2.1.

1. Vào [My Lenses → Camera Kit](https://my-lenses.snapchat.com/camera-kit), bật Camera Kit cho tổ chức của chủ dự án, rồi vào **Apps** tạo app Web. Trong trang ứng dụng, lấy **Web Staging API Token** để thử nội bộ. Production token cần Snap duyệt. Xem [Portal and Project Setup](https://developers.snap.com/camera-kit/getting-started/setting-up-accounts).
2. Dùng [Lens Studio Cloth Simulation Try-On](https://developers.snap.com/lens-studio/features/try-on/cloth-simulation-try-on) để tạo Lens áo, bật Web và thêm Lens vào nhóm của app trong [Lens Scheduler](https://developers.snap.com/camera-kit/ar-content/lens-scheduler). Lens mẫu của Snap cũng dùng được để kiểm tra SDK. Tương tác nắm và kéo vạt áo cần được thiết lập trong chính Lens.
3. Đặt `SNAP_CAMERA_KIT_API_TOKEN`, `SNAP_CAMERA_KIT_LENS_GROUP_ID` và `SNAP_CAMERA_KIT_LENS_ID` trong `.env`, rồi chạy lại script. Docker tự đóng gói SDK. `./run.sh` cần Node.js và npm khi bật Snap. Nếu chạy `uvicorn` trực tiếp, chạy `npm ci` và `npm run build` trong `web-sdk/` trước.
4. Mở qua `localhost` hoặc HTTPS. Trong DevTools, `performance.getEntriesByName('shadow:snap-ar-startup')` cho biết thời gian mở phiên. Để đo độ trễ khi kéo áo, quay đồng thời bàn tay thật và màn hình bằng camera tốc độ cao, rồi đếm số khung hình giữa hai chuyển động.

### Trang chủ
Trang mặc định là **một camera chung**. Khi vào trang, SHADOW tự mở camera và Gemini Live nếu đã có cấu hình. Cho phép camera/micro khi trình duyệt hỏi rồi nói nhu cầu tự nhiên. Nếu trình duyệt chặn tự phát âm thanh, chạm **Bật âm thanh** một lần để mở khóa; các lần dừng thủ công vẫn được giữ cho tới khi bạn bật lại hoặc vào lại trang. **Tủ đồ** và **Bài đã học** là tài nguyên phụ ở thanh điều hướng; không cần chọn chế độ trước.

Camera phủ toàn màn hình. Chữ chat hiện trực tiếp trên hình camera, không có nền khung hay bong bóng; chữ có bóng nhẹ để dễ đọc. Ô nhập nằm phía dưới cùng. Bấm **Mở rộng / Thu gọn** để xem lại đoạn chat hoặc dành thêm chỗ nhìn thao tác. Ô nhập tự nâng lên khi bàn phím mở.

Khi có `GEMINI_API_KEY` (hoặc `GOOGLE_API_KEY`) trong `.env`, nút **Live** được chọn sẵn. **Gemini Live nghe → Google Decisions phân loại → camera thực hiện tác vụ → Gemini Live nói lại theo kết quả thật**. Bạn có thể hỏi tiếp, nhập câu hỏi trong cùng phiên hoặc nói chen để ngắt lời. Bấm micro lần nữa để dừng; mỗi phiên tối đa 5 phút. Nhóm nhu cầu và độ tin cậy hiện phía trên cuộc trò chuyện. Nút đổi camera trước/sau nằm ngay trên khung hình.

Ví dụ dùng ngay trong camera:

- **“Hướng dẫn tôi pha cà phê / đóng gói ly / gấp áo.”** Shadow hướng dẫn bằng giọng nói, xem hình ảnh nếu camera mở và ưu tiên bài đã học. Nếu có bài khớp, các bước hiện ngay trên camera; có thể bấm Trước/Tiếp để xem.
- **“Tôi dạy bạn cách gấp áo để bạn học lại.”** Tự bắt đầu ghi khung hình và lời hướng dẫn từ cùng camera và micro Live. Có thể sửa tên bài ngay trên khung hình. Nói từng bước, rồi nói **“Tôi đã dạy xong toàn bộ bài, lưu lại”**, hoặc bấm **Lưu bài dạy**. Decisions phân biệt kết thúc cả bài với “xong bước một”. SHADOW biên soạn bằng API gốc, lưu thành bài mới với mã riêng, rồi thông báo kết quả. Không tự lưu bản compiler fallback/mock như bài đã học bằng AI.
- **“Tôi muốn thử áo.”** Hiện các món trong tủ để chọn, rồi tự bật video mặc thử Xmax X2.1 ngay trong camera. Khi thiếu key hoặc dịch vụ báo lỗi, hiện đúng trạng thái; không giả lập kết quả.
- **“Tư vấn tôi mặc gì đi làm.”** Dùng đồ và quy tắc thực trong tủ để tư vấn bằng giọng nói. Các chức năng phụ như thêm/xóa đồ, tạo ảnh và mặc thử ảnh vẫn có đường dẫn tài nguyên riêng.

Nói **“Hủy bài / dừng thử áo”** để dừng tác vụ hiện tại, hoặc bấm **Dừng tác vụ**. Khi đang ghi bài, muốn chuyển việc cần lưu hoặc hủy bài trước. Đưa ứng dụng vào nền, tắt camera hoặc rời màn hình sẽ dừng tác vụ và hủy bản ghi chưa lưu. Tắt micro dừng ghi bài và thử áo; bài đang biên soạn có thể tiếp tục khi màn hình vẫn mở. Nếu từ chối quyền camera, vẫn có thể trò chuyện qua micro.

### Tải trang nhanh

`npm run build:app` trong `web-sdk/` gom ứng dụng thành một tệp JavaScript và một stylesheet. Backend nén các tài nguyên web bằng gzip; các API giọng nói và luồng NDJSON không đi qua lớp nén này. SDK thử đồ chỉ tải khi cần dùng. Service worker hiển thị bản đã cache ngay và cập nhật ở nền; không cache API, token, ảnh tủ đồ hay mã truy cập. Font tải ở nền để không giữ trang ở màn chờ.

`./run.sh` tự build frontend khi có Node/npm; Docker luôn build. Chạy `uvicorn` trực tiếp vẫn hỗ trợ mã nguồn chưa build; nếu sửa source sau khi build, backend tự bỏ qua bundle cũ để tránh hiện sai phiên bản. Build lại để giữ tốc độ tải tốt nhất. Tunnel miễn phí vẫn có độ trễ và đường dẫn riêng; số đo trên máy và trên mạng có thể khác nhau.

Camera tự mở cùng phiên Live, gửi tối đa khoảng 1 ảnh/giây. Bản ghi bài dạy giữ tối đa 48 khung hình và chỉ lời người dạy, không thu lời Shadow vào bài. Tắt **Live** để dùng chế độ từng câu hiện có; chế độ đó gửi một ảnh camera cùng mỗi câu hỏi. Không có key Gemini thì luồng trò chuyện và giọng nói cũ vẫn dùng được, nhưng không có điều phối tác vụ tự động qua Live.

Backend giữ key lâu dài và cấp token Google dùng một lần, mở kết nối trong 60 giây, hết hạn sau 5 phút. Cấu hình model, công cụ phân loại và hướng dẫn được khóa trong token. Trình duyệt nối trực tiếp đến Google bằng token tạm; không cần chạy server demo Node riêng. Các endpoint mới dùng chung mã truy cập và giới hạn tần suất của SHADOW. Khi chuyển màn hình hoặc đưa trang vào nền, phiên Live giải phóng micro, âm thanh và kết nối.

### Backend cho app mobile

- `POST /api/conversation` nhận `text`, tối đa 6 lượt `history` gồm `role` và `text`, và `image` tùy chọn dưới dạng data URL JPEG. Backend trả từng dòng NDJSON với `type` là `text`, `action`, `error` hoặc `done`. Phát nội dung ngay khi tích lũy đủ một câu.
- `POST /api/intent` nhận `text` và trả `intent`, `suggestions` và `source`.
- `GET /api/live/config` trả trạng thái Gemini Live, model và các nhóm nhu cầu. `POST /api/live/session` nhận tối đa 6 lượt `history` để tiếp tục ngữ cảnh trước đó và trả token tạm. `POST /api/live/classify` nhận `text` (tối đa 12.000 ký tự), `current_task` và `task_title` tùy chọn; gọi `gemini-decision-1` và trả `intent`, `label`, `confidence`, `probabilities`, `camera_command`, ngữ cảnh thật và các action. Khi có tác vụ ghi bài/thử áo, Decisions trả thêm lệnh tiếp tục/kết thúc/hủy/chuyển việc. Trình duyệt bổ sung `context.execution` sau khi thực hiện, trước khi trả tool response cho Live. Model Live dùng `gemini-3.8-live`.
- `POST /api/voice/transcribe` nhận audio thô với MIME type tương ứng. `POST /api/voice/speech` nhận `text` và trả WAV. Web ưu tiên nhận và phát giọng bằng API trình duyệt để giảm thời gian chờ. Hypertek là đường dự phòng.
- `POST /api/images/generate` nhận `prompt` và `size` tùy chọn. Tạo ảnh là tác vụ riêng và có thể mất khoảng một phút.
- `POST /api/tryon/photo` nhận `person_image` và `item_ids`. `POST /api/tryon/live-session` trả thông tin phiên Vidu ngắn hạn để client nối video qua Agora.

Khi bật cổng truy cập demo, client native gửi mã qua header `X-Shadow-Access`.

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
| Mặc thử ảnh và video trực tiếp | Vidu Q2 và Vidu S2-Editing | Phụ thuộc thời gian xử lý và kết nối Vidu |

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
cd backend && ../.venv/bin/python -m pytest -q                         # 303 test, không cần mạng hay key
cd tryon-worker && TRYON_MOCK=1 ../.venv/bin/python -m pytest tests/test_server.py -q   # 25 test, chế độ giả lập
```

Các test khác của `tryon-worker` (mask, pipeline) cần PyTorch — chạy trên Mac mini: `TRYON_MOCK=1 .venv/bin/python -m pytest tests -q`.

Kiểm tra JavaScript cho Gemini Live từ thư mục gốc, không cần cài thư viện npm:

```bash
npm --prefix web run check
npm --prefix web test     # 8 test: PCM, thứ tự Decisions trước câu trả lời, ngắt lời và hủy request cũ
```

Đã kiểm tra bằng Chrome với micro/camera giả lập và Google API thật: nhận yêu cầu pha cà phê, phân loại, phát tiếng Việt; gửi câu hỏi tiếp trong cùng Live; mở đúng màn hình tủ đồ; giải phóng micro/camera/âm thanh khi chuyển màn hình; mở lại Live với lịch sử đã có. Quyền và chất lượng thiết bị thật phụ thuộc trình duyệt người dùng.

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
    conversation.py         # chat tiếng Việt, ảnh camera và sự kiện NDJSON
    intent_router.py        # lệnh rõ ràng và TypeSafe Jev
    skill_compiler.py       # Dạy: lời nói + ảnh → kỹ năng
    skill_store.py  skills/ # kỹ năng đã lưu (JSON + ảnh mẫu)
    eval_store.py  eval_detectors.py        # thu mẫu + chấm độ chính xác
    wardrobe_api.py         # API Trợ lý tủ đồ
    wardrobe_store.py  wardrobe/            # tủ đồ + quy tắc của người dùng (không commit)
    wardrobe_seed/          # tủ đồ mẫu cơ bản 10 món + SOURCES.md (nguồn ảnh)
    wardrobe_seed_shop/     # 16 món đồ nam mùa hè YaMe (ảnh © YaMe, chỉ demo nội bộ) + SOURCES.md
    stylist.py              # gắn nhãn đồ, nhìn đồ đang mặc, phối đồ
    weather.py              # thời tiết Open-Meteo
    vidu_live.py            # mở phiên mặc thử trực tiếp với Vidu
    tryon.py                # mặc thử ảnh và các provider tương thích
    tests/
  web/                      # app điện thoại (JS thuần, không cần build)
    index.html  app.js  styles.css  wardrobe.css  tryon-live.css  sw.js  manifest.webmanifest
    js/screens/             # home, teach, review, guide, done, wardrobe, stylist, rules, tryon-live, tryon-photo
    js/                     # camera, giọng nói, MediaPipe, Vidu và gọi API
  tryon-worker/             # máy chủ mặc thử CatVTON cho Mac mini (README riêng)
```

Đặc tả kỹ thuật chi tiết (giao thức, API, định dạng kỹ năng): [PLAN.md](PLAN.md). Nhật ký làm việc: [PROGRESS.md](PROGRESS.md).

---

## Giấy phép (tóm tắt)

Chi tiết: [NOTICE.md](NOTICE.md), [tryon-worker/README.md](tryon-worker/README.md#giấy-phép--chỉ-dùng-phi-thương-mại),
[backend/wardrobe_seed/SOURCES.md](backend/wardrobe_seed/SOURCES.md).

Danh mục UNIQLO nam/unisex: 252 mẫu tham chiếu từ các trang công khai, gồm áo khoác/lông vũ, áo len, áo thun, sơ mi, quần, HEATTECH và phụ kiện. [Nguồn và cách nhập lại](backend/wardrobe_seed_uniqlo/SOURCES.md). Đây là phần danh mục truy cập được, chưa xác minh đủ mọi sản phẩm/màu của UNIQLO.

- **GlassKit** (MIT): nền tảng backend được fork; phần dùng lại / làm mới ghi trong NOTICE.md.
- **DecartAI/tryon-examples** (MIT): tham khảo cách cấp token và cách viết câu lệnh thay đồ. Decart là dịch vụ trả phí.
- **Open-Meteo**: dữ liệu CC BY 4.0, miễn phí cho mục đích phi thương mại.
- ⚠️ **Phi thương mại** — chỉ dùng cho demo / nghiên cứu, ghi nguồn khi trình diễn:
  - **IDM-VTON** (Hugging Face Space) — CC BY-NC-SA 4.0.
  - **CatVTON** trên Mac mini — CC BY-NC-SA 4.0.
  - **Ảnh đồ nam YaMe** (`wardrobe_seed_shop/`) — © YaMe (yame.vn), chỉ dùng demo nội bộ, không phân phối lại.
  - **Ảnh tủ đồ mẫu cơ bản** — ảnh áo lấy từ ví dụ của IDM-VTON (bộ VITON-HD) và Kolors; quần, chân váy, giày là hình tự vẽ.

# Nghiên cứu: conversational wardrobe stylist tích hợp virtual try-on cho SHADOW

> Cập nhật: 10/11/2026 (Asia/Ho_Chi_Minh)<br>
> Phạm vi: nguồn sơ cấp hiện hành, ưu tiên repository/model card/paper/tài liệu nhà cung cấp chính thức. Đây là nghiên cứu kỹ thuật và sản phẩm, không phải ý kiến pháp lý.

## 1. Kết luận điều hành

SHADOW **không cần thay lõi stylist bằng một framework conversational recommender có sẵn**. Mã hiện tại đã có các thành phần quan trọng hơn đối với một tủ đồ cá nhân: chỉ chọn item có thật, deterministic scoring/guard, thời tiết, luật do người thật dạy, feedback có time decay, lịch sử đã mặc và fallback khi LLM lỗi. Các toolkit CRS như CRSLab/RecWizard phù hợp để học cách đánh giá hội thoại, nhưng cũ, thiên về movie recommendation và không giải quyết invariant thời trang của SHADOW.

Kiến nghị:

1. **Tái sử dụng trực tiếp lõi hiện tại** (`stylist.py`, `outfit_scoring.py`, `style_memory.py`, các endpoint wardrobe/stylist), thêm một conversation state mỏng phía backend; LLM chỉ hiểu ý định và diễn đạt, deterministic code vẫn quyết định tính hợp lệ.
2. **Chưa thêm vector database/model recommendation** cho tủ đồ cá nhân nhỏ. Khi catalog vượt khoảng vài trăm item hoặc có external catalog, pilot [Marqo-FashionSigLIP](https://huggingface.co/Marqo/marqo-fashionSigLIP) (Apache-2.0) như candidate retriever; guard/scorer hiện tại vẫn là tầng cuối.
3. **Giữ CatVTON worker hiện tại cho demo/phi thương mại**, không đưa vào sản phẩm thương mại vì CatVTON là CC BY-NC-SA 4.0. Pilot thương mại theo thứ tự:
   - API ảnh: FASHN `tryon-v1.6` hoặc Google Vertex AI `virtual-try-on-001`;
   - live video: Decart Lucy VTON (SHADOW đã tích hợp đúng mô hình client-token);
   - self-host: FASHN VTON v1.5 đáng benchmark, nhưng CUDA-oriented và pipeline mặc định kéo theo human parser có NVIDIA non-commercial license; chưa phải bản thay thế “drop-in” cho Mac mini MPS.
4. **Không quảng cáo VTON là đo size/fit thật**. Đây là ảnh/video mô phỏng phong cách; phải hiển thị nhãn AI, giới hạn, quyền xóa ảnh và consent trước khi gửi camera/body image cho vendor.
5. **Đóng identity/tenant boundary trước khi lưu hội thoại/ảnh**. Phân tích kiến trúc của repo đã chỉ ra deployment hiện tại chia sẻ wardrobe/profile và chưa có multi-tenant auth; thêm memory hội thoại trước auth sẽ tạo nguy cơ lộ dữ liệu giữa người dùng.

## 2. SHADOW đang có gì và nên giữ gì

### 2.1 Baseline kiến trúc đã quan sát

- Backend là FastAPI modular monolith/BFF; PWA gọi REST/WebSocket. KMM dự kiến dùng cùng contract, trong đó client sở hữu UI/native permissions còn backend sở hữu orchestration và quyết định session.
- `POST /api/stylist/suggest` đã nhận `event`, địa điểm/ngày, ảnh đang mặc, `anchor_item_id`, notes; trả weather, outfits, `score/why`, luật đã dùng, nguồn/fallback, learning metadata và feedback context.
- `outfit_scoring.py` chấm giải thích được theo occasion, weather, color harmony, completeness và variety; hard violation làm bộ đồ không hợp lệ.
- `style_memory.py` đã có luật do stylist dạy, feedback 👍/👎/“Mặc bộ này”, thuộc tính ưa/không ưa có time decay, wear history, teach-by-example và reset profile.
- Photo VTON đã được tách qua `TryOnService` với route worker → Hugging Face Space → mock; live VTON dùng Decart token ngắn hạn; worker riêng có health/queue/auth.
- Persistence vẫn là file JSON/media local; chưa có user/tenant isolation, durable job queue hay retention policy.

### 2.2 Quyết định reuse

| Thành phần SHADOW | Quyết định | Lý do |
|---|---|---|
| Deterministic outfit scoring/guards | **Giữ làm source of truth** | Ngăn LLM bịa item, vi phạm occasion/weather và tạo fallback ổn định |
| `style_memory` | **Mở rộng, không thay** | Đã hỗ trợ feedback, wear history, taught rules và explanation |
| FastAPI provider abstraction | **Mở rộng cho VTON provider/job** | Có timeout/fallback/test seam sẵn; phù hợp BFF orchestration |
| `/api/stylist/suggest` | **Giữ tương thích; thêm conversation endpoint mới** | KMM v1 không nên bị phá contract; unknown response fields đã được phép bỏ qua |
| CatVTON worker | **Demo/benchmark nội bộ** | Chạy local/MPS nhưng license phi thương mại và chất lượng/mask có giới hạn |
| HF Space IDM-VTON fallback | **Chỉ demo/research** | Quota không ổn định, dữ liệu đi ra ngoài và model CC BY-NC-SA |
| File JSON dùng chung | **Không mở rộng sang production memory** | Không có tenant/auth, delete/audit/retention ở mức người dùng |

## 3. Conversational recommendation và fashion recommendation

### 3.1 Framework/repository đáng xem

| Candidate | License/độ duy trì đã kiểm chứng | Giá trị thực tế cho SHADOW | Quyết định |
|---|---|---|---|
| [CRSLab](https://github.com/RUCAIBox/CRSLab) | MIT; release công khai gần nhất trên GitHub là 0.1.2 (2021) | Nhiều baseline/dataset/metric cho conversational recommendation; thiên về research và domain movie | **Chỉ học/eval**, không đưa vào runtime |
| [RecWizard](https://github.com/McAuley-Lab/RecWizard) | Repo lab UCSD; hoạt động gần nhất hiển thị 04/2024 | Cách đóng gói module CRS/LLM; không có lợi thế fashion-specific hoặc production maintenance | **Chỉ học** |
| [RecBole](https://github.com/RUCAIBox/RecBole) | MIT; v1.2.1 và PR/issue vẫn có hoạt động 2025–2026, nhưng có issue tương thích NumPy 2/PyTorch mới | Framework benchmark offline mạnh cho ranking, split và metrics khi đã có nhiều interaction | **Có thể reuse offline**, không dùng trong request path hiện tại |
| [Marqo-FashionSigLIP](https://huggingface.co/Marqo/marqo-fashionSigLIP) / [code](https://github.com/marqo-ai/marqo-FashionCLIP) | Model/card Apache-2.0; fashion-specific multimodal embedding; Marqo OSS search server chính đã bị deprecate | Image/text retrieval cho catalog lớn, candidate generation, duplicate detection | **Pilot model embedding**, không kéo cả Marqo server |
| [FashionCLIP](https://github.com/patrickjohncyh/fashion-clip) | Code khai báo MIT; paper/model phục vụ retrieval/zero-shot fashion | Baseline nhẹ, phổ biến; cũ hơn FashionSigLIP | **Baseline**, không phải lựa chọn đầu |
| [What to Wear](https://github.com/fatemenajafi135/what-to-wear) | Repo rất sát bài toán; không thấy license rõ ở snapshot kiểm tra | Kiến trúc query → retrieval → grounded generation → coherence guards → scoring → citations; golden-set eval | **Chỉ học kiến trúc**, không copy code/assets khi chưa có license |
| [Closet](https://github.com/sudhiraneta/closet) | Repo nhỏ, không thấy license rõ | Explicit state machine, `excluded_ids` cho đồ đang giặt, `suggested_ids`, short-circuit khi context đủ | **Chỉ học UX/state**, không copy code |
| [Complete the Look](https://github.com/googleinterns/compatible-product) | Repo nghiên cứu của Google interns; maintenance thấp | Học embedding/retrieval cho complementary item trên Polyvore | **Chỉ benchmark/ý tưởng** |
| [OutfitTransformer paper](https://openaccess.thecvf.com/content/WACV2023/papers/Sarkar_OutfitTransformer_Learning_Outfit_Representations_for_Fashion_Recommendation_WACV_2023_paper.pdf) | Paper chính thức; không tìm thấy official production repo từ tác giả | Task CP/CIR và cách biểu diễn outfit-level | **Học mô hình/eval**, không phụ thuộc implementation bên thứ ba |

### 3.2 Vì sao không dùng “agent tự do” làm stylist

Một LLM agent tự do có thể trò chuyện hay nhưng không bảo đảm:

- chỉ dùng item có trong tủ;
- giữ anchor item, loại đồ đang giặt hoặc item người dùng vừa từ chối;
- tuân thủ weather/occasion/taught rule;
- không lặp lại bộ vừa gợi ý;
- tạo cùng kết quả hợp lệ khi provider lỗi hoặc đổi model.

Pattern đúng cho SHADOW là:

```text
Tin nhắn người dùng
  -> parse intent + structured delta (LLM, schema-constrained)
  -> cập nhật ConversationState (deterministic validation)
  -> lấy item thật + weather + profile/rules
  -> tạo/rank candidates bằng scorer hiện tại
  -> LLM chỉ chọn trong top-K và viết giải thích
  -> guard lần cuối + response có source/why
```

`ConversationState` tối thiểu nên có `event`, `location/date`, `anchor_ids`, `excluded_ids`, `current_outfit`, `shown_outfit_ids`, `requested_delta` (formal hơn/mát hơn/đổi áo...), `pending_question`, `turn_count`. Không cho model ghi trực tiếp profile hoặc storage; feedback/rule phải đi qua endpoint có schema và validation hiện hữu.

### 3.3 Khi nào mới cần embedding/recommender học máy

- **Tủ cá nhân <100 item:** enumerate theo category, scorer hiện tại và rule-based pruning dễ kiểm chứng hơn vector retrieval.
- **100–1.000 item hoặc nhiều tủ:** FashionSigLIP tạo candidate top-N theo ảnh/text; sau đó vẫn score deterministic.
- **Catalog shop lớn + đủ event feedback:** benchmark RecBole/sequential recommender offline; chỉ promote khi tăng Recall/NDCG và không giảm constraint-validity/diversity.
- **Không fine-tune chỉ từ vài lượt thumbs-up:** dữ liệu quá ít, dễ overfit; profile affinity/time-decay hiện tại phù hợp hơn.

## 4. Virtual try-on: model, repo và API

### 4.1 Ma trận lựa chọn

| Candidate | Khả năng/giới hạn chính | License & tình trạng | Dùng cho SHADOW |
|---|---|---|---|
| [CatVTON](https://github.com/Zheng-Chong/CatVTON) | Nhẹ, worker hiện tại đã port cho MPS; mask/pose khó có thể làm lẹm tay, long-to-short và góc nghiêng | **CC BY-NC-SA 4.0**; không được dùng thương mại | **Giữ demo/research**, không ship thương mại |
| [IDM-VTON](https://github.com/yisol/IDM-VTON) | Chất lượng ảnh tốt nhưng SDXL, DensePose/OpenPose/human parsing nặng; không phù hợp Mac mini interactive | Code/checkpoint **CC BY-NC-SA 4.0** | **Chỉ học/benchmark**; bỏ HF Space khỏi production |
| [StableVITON](https://github.com/rlawjdghek/StableVITON) | Official CVPR 2024, latent diffusion, training/inference code | **CC BY-NC-SA 4.0** | **Chỉ học/benchmark** |
| [OOTDiffusion](https://github.com/levihsu/OOTDiffusion) | Hai-UNet, upper/lower/dress; code/dependency nặng, repository research | License/dependency cần audit đầy đủ; benchmark dùng dataset NC | **Không ưu tiên production** |
| [FASHN VTON v1.5](https://github.com/fashn-AI/fashn-vton-1.5) / [model card](https://huggingface.co/fashn-ai/fashn-vton-1.5) | Maskless pixel-space, tops/bottoms/one-pieces, 576×864, ~1B params/~2 GB weights, khoảng 8 GB VRAM; card nêu ~5 s trên H100. Giới hạn: có thể đổi body, còn vết áo cũ khi long-to-short/bulky-to-slim | Code + weights **Apache-2.0**, cập nhật 2026 | **Pilot self-host thương mại**, nhưng CUDA-oriented; chưa thay ngay MPS worker |
| [FASHN Human Parser](https://github.com/fashn-AI/fashn-human-parser) | SegFormer-B4 dùng trong pipeline FASHN | Chính repo nói kế thừa **NVIDIA Source Code License**, research/evaluation non-commercial | **License blocker**: chứng minh segmentation-free path không dùng parser, thay parser, hoặc xin quyền trước commercial self-host |
| [Leffa](https://github.com/franciszzj/Leffa) | High-fidelity VTON/pose transfer, stack lớn; cần audit weights và base-model dependencies | Code MIT theo repo; commercial status của toàn weight chain không nên suy từ code license | **R&D benchmark**, chưa shortlist production mặc định |
| [Decart try-on examples](https://github.com/DecartAI/tryon-examples) + [Lucy VTON](https://platform.decart.ai/models/lucy-vton) | Realtime WebRTC 720p; official examples có outfit builder, pose/framing check và short-lived token. Pricing hiện hiển thị 2 credits/s standard, 4 credits/s fast | Examples MIT; dịch vụ/model theo ToS riêng; repo hoạt động 2026 | **Reuse cho live**, giữ token/cap phía backend; procurement/privacy review |
| [FASHN API v1.6](https://docs.fashn.ai/api-reference/tryon-v1-6) | Stable, async polling, tops/bottoms/one-pieces, khoảng 5–17 s; 1 credit/output; base64 output rút retention ảnh | API thương mại; lifecycle “Stable” | **Shortlist photo VTON** vì API đơn giản và fashion-specific |
| [Google Vertex AI VTO](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/vto/virtual-try-on-001) | GA từ 01/2026, 1–4 output, cùng aspect ratio, C2PA; hiện $0.06/image theo [pricing](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing) | Managed commercial service; model `virtual-try-on-001` có retirement date 20/01/2027 trong model card | **Shortlist enterprise**, cần abstraction vì vòng đời model ngắn |
| [OpenTryOn](https://github.com/tryonlabs/opentryon) | Adapter/toolkit tiện để thử nhiều backend | Toàn material ghi **CC BY-NC 4.0** | **Chỉ học**, không đưa vào commercial codebase |

### 4.2 Cảnh báo license quan trọng

“Repo/model chính Apache-2.0” **không có nghĩa toàn pipeline dùng thương mại được**. Phải audit cả:

- checkpoint/base model;
- pose detector/human parser/segmentation;
- code vendored và pretrained weights;
- dataset dùng để fine-tune/evaluate;
- ảnh catalog/seed và quyền hình ảnh người mẫu;
- ToS của API hosted.

FASHN v1.5 là ví dụ điển hình: model/card Apache-2.0 nhưng parser chính thức nêu NVIDIA Source Code License non-commercial. Do đó trạng thái đúng là **“commercial pilot pending dependency audit”**, không phải “commercial cleared”.

### 4.3 Kiến trúc VTON đề xuất

Không gắn model cụ thể vào client. Chuẩn hóa backend interface:

```text
TryOnProvider.submit(person_ref, garments, category, options) -> job_id
TryOnProvider.status(job_id) -> queued | running | completed | failed | cancelled
TryOnProvider.cancel(job_id)
TryOnProvider.capabilities() -> photo/live, categories, privacy, limits, version
```

- Photo VTON là durable async job, không giữ HTTP 30–180 giây như route hiện tại.
- Live VTON vẫn cấp client token ngắn hạn; permanent key không bao giờ xuống browser/KMM.
- Kết quả lưu theo `user_id/job_id`, TTL mặc định ngắn, có delete; không dùng URL công khai lâu hạn.
- Provider metadata (`provider`, `model_version`, `seed`, `source`, `generated_at`, retention) đi cùng kết quả để audit và A/B.
- Fallback “mock composite” phải ghi rõ demo; không được trông giống kết quả AI thật mà không có label.

## 5. Dataset và đánh giá

### 5.1 Outfit recommendation/conversation

| Nguồn | Dùng được gì | Ràng buộc |
|---|---|---|
| [Polyvore Outfits](https://huggingface.co/datasets/mvasil/polyvore-outfits) | Compatibility Prediction (AUC), Fill-in-the-Blank, complementary item retrieval Recall@K; có disjoint split tốt hơn để đo generalization | Card vừa gắn CC BY 4.0 vừa ghi restricted academic/non-profit và tác giả không sở hữu copyright ảnh. **Không dùng train thương mại nếu chưa clearance** |
| [Maryland Polyvore dataset](https://github.com/xthan/polyvore-dataset) | Baseline outfit/FITB và reproduction nghiên cứu | URL ảnh gốc đã chết; provenance/copyright ảnh thương mại không sạch |
| ReDial/INSPIRED/OpenDialKG qua [CRSLab](https://github.com/RUCAIBox/CRSLab) | Metric/flow cho CRS và baseline hội thoại | Chủ yếu movie/general domain; không dùng để kết luận stylist tốt |
| [VOGUE](https://github.com/D3Mlab/vogue) | Transcript/annotation fashion conversation để học flow elicitation → nhóm nhỏ item → critique → refinement | Transcript/rating/annotation CC BY 4.0; product image/metadata research-only non-commercial; chỉ 60 hội thoại/20 người |
| [ConvApparel](https://research.google/blog/convapparel-measuring-and-bridging-the-realism-gap-in-user-simulators/) | Hơn 4.000 hội thoại/15.000 turn để nghiên cứu CRS simulator/eval | Google cảnh báo “realism gap”; simulator không thay human evaluation |
| Golden set nội bộ SHADOW | Scenario Việt Nam, thời tiết, đám cưới/đi làm/đi học, đồ đang giặt, anchor, taught rule, follow-up | **Nguồn đánh giá chính**; item/ảnh phải có consent và quyền sử dụng |

Metric đề xuất cho stylist:

- **Safety/invariant:** catalog-only rate = 100%; hard-constraint pass = 100%; anchor/exclusion adherence = 100%; không ghi profile khi chưa có explicit action.
- **Ranking offline:** Recall@K/NDCG@K/MRR khi có ground truth; pairwise preference win rate; coverage và intra-list diversity; repetition rate 7/30 ngày.
- **Conversation:** task success, số turn tới bộ hợp lệ, clarification precision, context carry-over, correction success (“áo đó đang giặt”, “trang trọng hơn”), abandonment.
- **Product:** suggestion open rate, 👍/👎, “Mặc bộ này”, save/share/try-on conversion; đo uplift A/B so với deterministic baseline.
- **Explanation:** factual item/rule/weather citation rate; lý do phải khớp `why` deterministic, không để LLM tự nhận một rule không được áp dụng.
- **Latency/reliability:** p50/p95, provider error, fallback rate, cost/suggestion.

Không dùng LLM-as-judge làm ground truth duy nhất. Có thể dùng để triage, nhưng promotion gate phải gồm assertions deterministic và human pairwise review.

### 5.2 VTON dataset/metric

| Nguồn | Điểm mạnh | License/use |
|---|---|---|
| [VITON-HD](https://github.com/shadow2496/VITON-HD) | 11.647 train + 2.032 test pair, 1024×768, benchmark upper-body chuẩn | Research only, **CC BY-NC 4.0** |
| [DressCode](https://github.com/aimagelab/dress-code) | 53.792 garment, 107.584 ảnh, upper/lower/dress, 1024×768 + pose/parsing | Agreement bắt buộc; repo nói không cấp cho private companies |
| [Garments2Look](https://github.com/ArtmeScienceLab/Garments2Look) | Dataset outfit-level/multi-reference mới, code/data tuyên bố Apache-2.0 | Audit third-party notices và adapter weights; model adapter chưa có license rõ trong snapshot |
| Bộ consented nội bộ | Đại diện người Việt, ảnh điện thoại thật, màu da/body/đồ dài/phụ kiện và catalog thật | **Bắt buộc cho product gate**, có consent, retention và delete |

Metric VTON nên chia thành các lớp:

1. **Paired reconstruction:** LPIPS/SSIM chỉ trên paired set; hữu ích nhưng không phản ánh hoàn toàn try-on khác garment.
2. **Unpaired realism/distribution:** FID/KID ở cấp tập; không dùng để quyết định một ảnh đơn.
3. **Garment fidelity:** embedding similarity/crop retrieval bằng FashionSigLIP/CLIP, màu/logo/pattern OCR, human rating “món đồ có đúng không”.
4. **Identity/body preservation:** face/DINO similarity, pose deviation, vùng ngoài garment thay đổi; kiểm tra riêng mặt, tóc, tay, hình xăm, hijab/phụ kiện.
5. **Operational:** success/reject rate, time-to-first-result, p95 latency, queue time, OOM/error/cancel, cost/job.
6. **Human A/B:** realism, garment fidelity, body preservation, usefulness; báo cả failure rate, không chỉ điểm trung bình.

Test slice bắt buộc: top/bottom/one-piece/outerwear; trơn/sọc/logo/chữ; tối/sáng; long→short và bulky→slim; tay khoanh/chống hông; chính diện/nghiêng; nhiều màu da/body shape; ánh sáng/nền kém. “Tỷ lệ lỗi nghiêm trọng” (đổi mặt, méo tay, sai logo, hở da giả, còn áo cũ) phải là release gate.

## 6. UX, safety và privacy

### 6.1 Conversation UX

Áp dụng [Google PAIR](https://pair.withgoogle.com/guidebook-v2/) và [Microsoft HAX](https://www.microsoft.com/en-us/research/project/hax-toolkit/):

- hỏi ít nhất có thể; nếu message đã có occasion/location/style thì short-circuit tới gợi ý;
- hỏi lại khi thiếu constraint có tác động lớn, không hỏi chuỗi form dài;
- luôn cho thao tác trực tiếp: “đổi áo”, “món này đang giặt”, “mát hơn”, “giữ quần này”, “xóa gu đã học”;
- hiển thị “Vì sao”: weather + occasion + taught rule + taste signal thực sự đã dùng;
- cho người dùng sửa tag item/interpretation của rule trước khi lưu;
- khi AI/provider lỗi, trả deterministic suggestions và giải thích nguồn; không tạo dead-end. PAIR khuyến nghị đường hoàn thành thủ công và feedback/control rõ ràng trong [Errors + Graceful Failure](https://pair.withgoogle.com/chapter/errors-failing/) và [Feedback + Control](https://pair.withgoogle.com/guidebook-v2/chapter/feedback-controls/);
- tránh anthropomorphism quá mức và body-shaming; không suy đoán giới tính, cân nặng, sức khỏe, sắc tộc hoặc “dáng người lý tưởng” từ ảnh;
- VTON có nhãn “Ảnh/video do AI tạo – màu, độ rủ và độ vừa có thể không chính xác”; có nút xem ảnh gốc và báo lỗi.

### 6.2 Security

Theo [OWASP prompt-injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html):

- xem text OCR trong ảnh garment, notes, taught rules và vendor output là **untrusted data**, không phải instruction;
- schema-validate intent/tool arguments; authorization và invariant phải nằm ngoài model;
- parameterized storage/query, không nối chuỗi từ lời người dùng;
- model không được quyền tự xóa/sửa wardrobe, rule, profile hoặc gọi VTON tốn tiền nếu thiếu explicit action;
- rate limit theo user/tenant, cap turn/job/session và ngân sách; log decision metadata nhưng không log base64 ảnh/API key;
- secret không nằm trong prompt/client; live token ngắn hạn có scope và max session như SHADOW đang làm.

### 6.3 Privacy/data governance

Ảnh toàn thân, mặt, camera stream, vị trí, lịch, lịch sử mặc và style profile đều là dữ liệu cá nhân nhạy cảm về mặt sản phẩm. Tại Việt Nam, [Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15](https://vanban.chinhphu.vn/?classid=1&docid=214590&pageid=27160) có hiệu lực từ 01/01/2026; production cần legal review cụ thể, đặc biệt với vendor và chuyển dữ liệu xuyên biên giới.

Yêu cầu tối thiểu:

- consent riêng cho camera/photo upload và cho từng vendor; mô tả mục đích, nơi xử lý, retention;
- data minimization: crop/resize trước upload, bỏ EXIF, không thu audio cho VTON nếu không cần;
- mặc định không lưu raw camera frame; photo/result có TTL; cung cấp list/download/delete và profile reset;
- tenant isolation cho DB/object storage, signed URL ngắn hạn, encryption, audit access;
- không dùng ảnh/hội thoại để train hoặc eval nếu chưa có opt-in riêng;
- policy cho trẻ vị thành niên và intimate/sensitive imagery; moderation ở cả input/output;
- DPA/vendor review và region lựa chọn trước production.

Ví dụ vendor: [FASHN data-retention docs](https://docs.fashn.ai/api-overview/data-retention-privacy) nói request record không tự xóa, URL input được giữ trong record, không có delete endpoint tự phục vụ; base64 input bị xóa sau xử lý (backstop một ngày) và base64 output giảm retention. Vì vậy nếu dùng FASHN, ưu tiên base64 hoặc signed URL cực ngắn, không gửi public permanent URL, và ghi rõ quy trình yêu cầu xóa record.

## 7. Lộ trình triển khai phù hợp kiến trúc SHADOW

### Phase 0 — chốt boundary, license và benchmark

- [ ] Chốt deployment: single-user/family hay multi-tenant; chưa chốt thì không thêm durable conversation memory.
- [ ] Lập SBOM/license matrix cho code + weights + preprocessors + dataset + seed image + vendor ToS.
- [ ] Gắn cờ CatVTON/IDM-VTON/HF Space là non-commercial; không để `auto` route vô tình chọn provider NC trong production.
- [ ] Tạo 50–100 golden scenarios tiếng Việt cho stylist và 100–300 consented image pairs/slices cho VTON.
- [ ] Ghi baseline hiện tại: constraint pass, task success, p95 latency, fallback, human preference và cost.

### Phase 1 — conversational stylist trên lõi hiện tại

- [ ] Thêm `ConversationState` và endpoint/version mới; không phá `POST /api/stylist/suggest` của KMM v1.
- [ ] Parse mỗi turn thành structured delta; validation ngoài LLM.
- [ ] Hỗ trợ follow-up đầu tiên: giữ/loại item, đồ đang giặt, formal/casual/warm/cool hơn, đổi occasion/location/date.
- [ ] Reuse scorer/memory/feedback; không thêm LangGraph hoặc vector DB nếu state machine nhỏ đủ dùng.
- [ ] Test golden multi-turn, cancellation, provider timeout, prompt injection, cross-user isolation.
- [ ] UI hiển thị source/why/rule/learning và cho correction/reset.

### Phase 2 — persistence và personalization an toàn

- [ ] Thay JSON dùng chung bằng user-scoped repository (PostgreSQL) và object storage có signed URL.
- [ ] AuthN/AuthZ, RLS/tenant tests, retention/delete/export/audit.
- [ ] Lưu event có version/schema; profile là derived state có thể rebuild/reset.
- [ ] Chỉ khi catalog đủ lớn: service embedding async với FashionSigLIP + ANN; scorer hiện tại re-rank cuối.
- [ ] A/B against deterministic baseline; không promote nếu invariant <100% hoặc diversity giảm.

### Phase 3 — photo VTON production pilot

- [ ] Đổi route dài thành durable job (`submit/status/cancel`) và provider capability registry.
- [ ] Pilot FASHN API v1.6 và Vertex `virtual-try-on-001` trên cùng consented set; pin model/version.
- [ ] Pilot FASHN v1.5 self-host trên NVIDIA; xác minh bằng dependency trace rằng commercial path không dùng FASHN Human Parser NC, hoặc thay/xin license.
- [ ] Giữ CatVTON Mac mini như research baseline, không production commercial fallback.
- [ ] Thêm input framing/quality gate, output moderation, watermark/AI label, TTL/delete.
- [ ] Promotion gate theo fidelity/identity/failure/p95/cost, không chỉ FID hoặc ảnh đẹp chọn tay.

### Phase 4 — live VTON + KMM

- [ ] Giữ Decart permanent key ở backend; token ngắn hạn, user/session scope, hard duration/cost cap.
- [ ] Native permission/consent thuộc KMM; backend chỉ phát token/capability và audit metadata.
- [ ] KMM nhận job progress/cancel/resume; navigation chỉ mang primitive job/item IDs.
- [ ] Đo network/thermal/battery, reconnect và low-bandwidth fallback trên thiết bị thật.
- [ ] Hiển thị rõ live stream đi tới vendor nào và dừng stream ngay khi rời màn hình.

### Phase 5 — release gate

- [ ] Security/privacy review trên branch diff và data-flow diagram.
- [ ] License counsel/procurement sign-off cho provider/model/dataset thực dùng.
- [ ] Red-team prompt injection đa phương thức, data leakage, unauthorized tool/job và cost exhaustion.
- [ ] Accessibility, graceful failure, manual correction và Vietnamese copy review.
- [ ] Continuous eval theo model/prompt/provider version; rollback được khi quality drift.

### Backlog 12 bước gắn với file/module SHADOW

Đây là thứ tự triển khai đề xuất; tên file mới chỉ là boundary gợi ý, chưa phải yêu cầu tạo abstraction trước khi cần.

| # | Việc làm và tiêu chí xong | File/module chính |
|---:|---|---|
| 1 | **Khóa baseline contract.** Snapshot request/response của suggest, feedback, profile, try-on config/photo/live; test giữ tương thích và ghi p50/p95/fallback hiện tại. | `backend/wardrobe_api.py`, `backend/tests/test_wardrobe_api.py`, `backend/tests/test_wardrobe_stylist.py`, `specs/001-kmm-api-contract/contracts/rest-api.md` |
| 2 | **Định nghĩa conversation state thuần dữ liệu.** Có event/location/date, anchor/excluded/shown/current outfit, pending question, turn/version; serialize được nhưng chưa persist global. | file mới `backend/stylist_conversation.py`; tham khảo pattern dataclass trong `backend/session_types.py` |
| 3 | **Thêm turn endpoint không phá v1.** Input gồm conversation ID/message/optional structured context; output gồm reply, outfits, suggestions/actions và state version; `/api/stylist/suggest` vẫn chạy nguyên. | `backend/wardrobe_api.py`, file mới `backend/tests/test_stylist_conversation.py`, tài liệu contract trong `specs/001-kmm-api-contract/` |
| 4 | **Parse turn thành structured delta.** LLM chỉ trả schema intent; deterministic code reject unknown item/tool/action, cap length/turn và không ghi storage. | `backend/stylist.py`, `backend/providers.py`, `backend/stylist_conversation.py` |
| 5 | **Nối follow-up với guard hiện có.** “Đang giặt/đừng dùng”, “giữ món này”, “trang trọng/mát hơn” phải thay context rồi chạy lại scorer/memory; catalog-only và hard constraints đạt 100% golden tests. | `backend/outfit_scoring.py`, `backend/style_memory.py`, `backend/stylist.py`, tests tương ứng |
| 6 | **Đưa feedback/control vào UI reference.** Quick chips, sửa tag/rule, reset profile, source/why và deterministic fallback; không tạo dead-end khi provider lỗi. | `web/js/screens/stylist.js`, `web/js/wardrobe-api.js`, `web/js/store.js`; KMM map cùng DTO/action, không copy web state |
| 7 | **Đóng user/tenant boundary trước durable memory.** Repository user-scoped, authz trên mọi wardrobe/profile/conversation/media access, delete/export/TTL; migration từ JSON có rollback. | `backend/wardrobe_store.py`, `backend/style_memory.py`, `backend/security.py`, composition trong `backend/main.py`; thêm integration tests cross-user |
| 8 | **Tạo VTON provider contract/capability registry.** Mỗi provider khai photo/live, category, model version, NC/commercial policy, limits và retention; production không được route vào provider NC. | `backend/tryon.py`, config/composition trong `backend/wardrobe_api.py` và `backend/main.py`, `backend/tests/test_wardrobe_tryon.py` |
| 9 | **Chuyển photo VTON thành job.** `submit/status/cancel`, progress, idempotency, timeout/retry có giới hạn; result user-scoped + TTL; route sync cũ chỉ là compatibility shim nếu còn cần. | `backend/tryon.py`, `backend/wardrobe_api.py`; file mới `backend/tryon_jobs.py`; update `specs/001-kmm-api-contract/contracts/rest-api.md` |
| 10 | **Pilot provider thương mại sau feature flag.** FASHN v1.6 và Vertex chạy cùng consented eval set; FASHN v1.5 self-host chỉ bật khi dependency/license trace sạch. CatVTON/IDM/HF Space bị hard-disable trong commercial profile. | provider adapter mới dưới `backend/`; `tryon-worker/` giữ research profile; `NOTICE.md`, `tryon-worker/README.md`, deployment env docs |
| 11 | **Tích hợp native/KMM theo ownership.** KMM xin camera/photo permission, gửi primitive item/job IDs, poll/cancel/resume job và dừng live stream khi rời màn; backend giữ secret/token/cost cap. | contract dưới `specs/001-kmm-api-contract/`; màn KMM tương ứng trong `BaseKMM` khi feature được duyệt; không truyền domain object qua navigation |
| 12 | **Đóng continuous eval/release gate.** Golden conversation + VTON slice, metric report theo model/version, prompt-injection/cross-tenant/cost red-team, privacy/license checklist và rollback drill. | `backend/tests/`, `backend/eval_*`, file eval mới dưới `backend/evals/`, `docs/`, CI; chạy security review trên branch diff |

## 8. Quyết định “reuse” cuối cùng

**Reuse ngay:** deterministic scoring/guards, style memory, FastAPI BFF/provider seam, current feedback APIs, Decart short-lived-token pattern, worker separation.

**Pilot có kiểm soát:** FashionSigLIP cho retrieval lớn; FASHN API/Google Vertex cho photo VTON; Decart cho live; FASHN v1.5 self-host sau dependency/license audit.

**Chỉ học từ:** CRSLab, RecWizard, Complete the Look, OutfitTransformer, What to Wear, Closet, IDM-VTON, StableVITON, OOTDiffusion, OpenTryOn và các public research dataset bị giới hạn.

**Không làm:** thay scorer bằng LLM, thêm agent framework chỉ vì hội thoại, fine-tune từ ít feedback, lưu camera mặc định, quảng cáo VTON là bằng chứng về size/fit, hoặc dùng fallback non-commercial trong commercial deployment.

## 9. Trạng thái triển khai MVP và quality gate (10/11/2026)

M1–M2 đã hiện thực conversation state, parser/fallback, endpoint `/api/stylist/chat`, guided web chat, handoff bằng
primitive item IDs sang live/photo try-on và explicit consent trước khi ghi sở thích. M3 thêm golden evaluator tất định:

- `backend/evals/stylist_conversations.json`: 32 scenario tiếng Việt gồm đi làm/đi học/đám cưới/đi chơi, nóng/mưa/lạnh,
  anchor, đồ đang giặt, taught rule, provider failure, thiếu category và prompt injection;
- `backend/evals/run_stylist_conversations.py`: chạy endpoint thật trong `SHADOW_MODE=mock` với wardrobe tạm cô lập;
- metrics: catalog-only, hard-constraint pass, anchor/exclusion adherence, correction success, turns-to-outfit,
  repeated-outfit rate, fallback rate và unauthorized profile-write rate;
- promotion gate: bốn invariant an toàn phải đạt 100%: catalog-only, hard constraints, anchor/exclusion và không ghi
  preference khi thiếu explicit confirmation.

Quality gate này không dùng LLM-as-judge và không tuyên bố chất lượng thời trang của model thật. `fallback_rate=100%` là
kỳ vọng trong mock run. Trước M4 cần chạy cùng scenario bằng provider thật, bổ sung human pairwise review và báo latency,
cost, provider error theo model/prompt version.

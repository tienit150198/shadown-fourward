# Báo cáo trạng thái triển khai Stylist AI + Virtual Try-on

> **Loại:** reverse-document từ source và bằng chứng kiểm thử  
> **Ngày:** 2026-11-10  
> **Phạm vi:** `shadown-fourward`  
> **Trạng thái:** P0 đã triển khai; P1/P2/P3 được phân loại theo khả năng làm ngay và dependency bên ngoài.

## 1. Tóm tắt điều hành

Luồng hiện tại là:

```text
Tủ đồ -> Stylist chat lấy bối cảnh -> outfit/item IDs
      -> photo try-on hoặc live Decart -> hậu kiểm -> lưu kết quả/fallback
```

Định hướng “input trước prompt” là đúng. Ảnh người, ảnh garment, pose, descriptor và lifecycle provider được kiểm tra trước khi tối ưu prompt. Điều này giảm request lỗi, giảm chi phí và tạo dữ liệu A/B có thể tái lập.

| Nhãn | Ý nghĩa |
|---|---|
| **DONE** | Có code và bằng chứng kiểm thử tương ứng. |
| **SCAFFOLDED** | Có contract/adapter/fixture nhưng chưa chứng minh bằng provider thật. |
| **PENDING** | Cần key, thiết bị, dữ liệu, quyết định privacy/licence hoặc dịch vụ ngoài. |
| **BLOCKED** | Không thể kiểm tra trong môi trường hiện tại; không đồng nghĩa thiết kế sai. |

Kết luận: phần lớn P1 và một phần P2/P3 có thể làm không cần API key. Chỉ quality hình ảnh, latency/cost, WebRTC thực và self-host benchmark phải chờ môi trường tương ứng.

## 2. Căn cứ và giới hạn

Báo cáo đối chiếu:

- `README.md` và source FastAPI/PWA.
- [docs/SYSTEM_DESIGN_ANALYSIS.md](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/docs/SYSTEM_DESIGN_ANALYSIS.md).
- [docs/IMPROVE_STYLIST_VTON_OUTPUT_QUALITY.md](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/docs/IMPROVE_STYLIST_VTON_OUTPUT_QUALITY.md).
- [docs/superpowers/plans/2026-11-10-vton-output-quality-p0.md](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/docs/superpowers/plans/2026-11-10-vton-output-quality-p0.md).
- Backend/frontend tests, E2E harness và các commit `49dd692`, `5ca3cb8`, `ee0bbd9`, `34ddc2a`.

Đây là trạng thái quan sát được từ implementation, không phải xác nhận mọi ý định sản phẩm. Có code không đồng nghĩa đã đạt chất lượng provider thật.

## 3. Kiến trúc hiện tại

| Khối | Trách nhiệm | Bằng chứng |
|---|---|---|
| PWA frontend | Wardrobe, chat, photo/live try-on, progress, compare, save/share | `web/js/screens/wardrobe.js`, `tryon-photo.js`, `tryon-live.js` |
| Wardrobe store | `items.json`, `images/`, `tryon/`, path safety, limit/pruning | [backend/wardrobe_store.py:460](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/backend/wardrobe_store.py:460), [backend/wardrobe_store.py:555](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/backend/wardrobe_store.py:555) |
| Stylist | Chat nhiều lượt, hỏi bối cảnh, scoring/rules/weather/style memory | `backend/stylist_chat.py`, `backend/stylist.py`, `backend/wardrobe_api.py` |
| Photo VTON | Provider chain, upper/lower/overall passes, fallback, output validation | [backend/tryon.py:367](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/backend/tryon.py:367), [backend/tryon.py:427](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/backend/tryon.py:427) |
| Live VTON | Token, SDK connect/setImage/disconnect, combined canvas, idle/cap | [decart-live.js:223](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/web/js/decart-live.js:223), [tryon-live.js:572](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/web/js/screens/tryon-live.js:572) |
| Quality/eval | Pose gate, garment gate, descriptor, output validator, manifest, mock E2E | `web/js/tryon-pose.js`, `backend/image_quality.py`, `backend/evals/`, `web/e2e/` |

## 4. Những phần đã làm

### 4.1. Vertical slice M1–M4

| Hạng mục | Trạng thái | Vì sao làm | Bằng chứng |
|---|---|---|---|
| Handoff outfit từ Stylist sang try-on | **DONE** | Chat phải truyền item IDs tới VTON, không chỉ trả text | `wardrobe_api.py`, `wardrobe.js`, `tryon-photo.js` |
| Conversational Stylist | **DONE** | AI cần dịp, thời tiết, phong cách và ràng buộc | `stylist_chat.py`, `stylist.py` |
| Dạy trợ lý/style memory | **DONE một phần** | Cá nhân hoá cần tái sử dụng; durable identity còn chưa chốt | Stylist rules/memory; P3.1 pending |
| Photo VTON provider chain/fallback | **DONE** | Provider có thể lỗi/quota; UX cần fallback rõ ràng | `tryon.py` |
| Live Decart integration | **DONE ở cấp mock/integration** | Cho phép kiểm tra UX trước key thật | `decart-live.js`, `tryon-live.js` |

### 4.2. P0 chất lượng input/output

1. **Pose gate — DONE.** `tryon-pose.js` kiểm tra framing/landmarks; photo chặn bottom/dress khi thiếu đầu gối, live chặn start/apply khi pose kém. Không thể phục hồi vùng cơ thể bị crop, nên chặn sớm tiết kiệm cost.
2. **Garment photo gate — DONE.** `image_quality.py` và `wardrobe_store.py` cảnh báo ảnh nhỏ, crop sát, nền không phù hợp. Warning chưa hard-block để không làm mất item cũ.
3. **Structured descriptor — DONE.** `garment_attrs.py` chuẩn hoá loại đồ, màu, tay áo, độ dài, fit, confidence và `needs_confirmation`; prompt không còn phụ thuộc tên file mơ hồ.
4. **Output validator — DONE.** `validate_generated_image` chạy sau provider; output decode/format/kích thước lỗi không được coi là success.
5. **Eval manifest — SCAFFOLDED.** `backend/evals/vton_cases.json`, schema và validator tạo case metadata/hash, không commit ảnh riêng tư.
6. **Browser mock E2E — SCAFFOLDED.** `web/e2e/run.mjs` và `tryon-mock.spec.mjs` kiểm tra connect → setImage → disconnect không gọi API trả phí.

### 4.3. Baseline provider cần đo

FASHN hiện hard-code v1.6 `mode=balanced`, `num_samples=1`, JPEG ([backend/tryon.py:503](/Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward/backend/tryon.py:503)). Đây là baseline, chưa phải tham số tối ưu. Kết quả trả provider/seconds/rendered/skipped/fallback để không nhầm mock với ảnh thật. Lưu trữ result có pruning nhưng chưa thay thế user-scoped TTL/delete.

## 5. Bằng chứng kiểm tra

| Kiểm tra | Kết quả | Ý nghĩa |
|---|---:|---|
| Backend regression | **272 passed / 49.62s** | API, wardrobe, try-on, descriptor và quality gate không phá test cũ |
| Frontend unit tests | **7 passed** | Pose, Decart lifecycle và helper JS |
| JS syntax checks | **exit 0** | JS thay đổi và E2E script parse được |
| Eval validator | **valid, 1 case** | Manifest hợp lệ, chưa phải model quality |
| `git diff --check` | **clean** | Không có whitespace error |
| Browser E2E | **SKIP, exit 77** | Thiếu Playwright/browser; harness có nhưng môi trường chưa chạy được |

Lệnh tái hiện:

```bash
cd /Volumes/Data/2024_DuAnWebAppHocTap/Hackathon_Fourward/shadown-fourward
pytest -q backend/tests
node --test web/tests/*.test.mjs
python backend/evals/validate_vton_cases.py
node web/e2e/run.mjs
git diff --check
```

## 6. P1 — Photo VTON

| Mục | Làm ngay không cần key? | Đã có | Pending và nguyên nhân | Bước tiếp theo |
|---|---|---|---|---|
| P1.1 A/B `balanced`/`quality` | **Có harness** | Baseline ở `tryon.py` | Ảnh, cost, latency cần FASHN key/credit | Tách request builder, MockTransport, log mode/seed/samples; chạy paired cases khi có key |
| P1.2 `garment_photo_type` + segmentation retry | **Có** | Garment metadata/descriptor | Chưa biết retry có cải thiện hay không | Propagate config, retry tối đa một lần, test không retry vô hạn |
| P1.3 Multi-pass/order/identity drift | **Có instrumentation** | `plan_garments` upper/lower/overall | Drift cần output thật/evaluator | Ghi pass order, input/output hash, rendered IDs và rollback |
| P1.4 Try-On Max | **Có adapter disabled** | Có API reference | Cần key, quota, cost; docs đang Preview | Feature flag + contract test, không đổi default |
| P1.5 Quality/cost/latency gate | **Có rule/report** | Manifest và metadata | Chưa có số đo thực tế | Threshold provisional, chỉ promote khi đủ quality/cost/latency |

**Vì sao làm P1 trước:** ảnh photo cố định, request snapshot được và kết quả lưu được; đây là nơi dễ đo hơn video. Không nên đổi prompt theo một demo đơn lẻ.

### P1 có thể triển khai ngay

- Tách `build_fashn_request(case, variant)` khỏi HTTP.
- Thêm `experiment_id`, `case_id`, `provider`, mode, seed, photo type, segmentation flag vào metadata.
- Mock timeout/malformed output/retry/fallback.
- Ghép manifest với result thành CSV/Markdown.
- Đặt Try-On Max sau feature flag tắt mặc định.

### P1 bắt buộc chờ key

So sánh ảnh thật, p50/p95 latency, cost per successful render, segmentation-free, identity preservation và garment fidelity.

## 7. P2 — Live Decart

| Mục | Làm ngay? | Đã có | Pending | Bước tiếp theo |
|---|---|---|---|---|
| P2.1 Clean garment/on-model | **Fixture/telemetry** | `setImage`, combined canvas | SDK/key/device thật | Variant flag + paired session IDs |
| P2.2 Prompt hiện tại/20–30 từ | **Có** | Prompt builder/descriptor | Cần video thật và human preference | Snapshot prompt version, A/B cùng fixture |
| P2.3 Single/combined outfit | **Có** | Canvas top/bottom | Chưa biết artefact/occlusion | Canvas fixture, nền/kích thước chuẩn |
| P2.4 Wi-Fi/4G/UDP/relay/device | **Protocol** | Visibility/lifecycle | Cần HTTPS, phone, mạng và SDK thật | Preflight telemetry + test matrix |
| P2.5 Disconnect/cost cap | **Local ledger** | Disconnect/idle/cap | Cần provider dashboard | Đối soát duration ledger với dashboard |

Mock SDK chỉ chứng minh lifecycle; không chứng minh bitrate, WebRTC relay, temporal consistency, latency hay cost. Vì vậy P2 quality phải pending tới khi có key và thiết bị thật.

## 8. P3 — Privacy, evaluator, self-host

| Mục | Có thể làm ngay? | Pending/root cause | Khuyến nghị |
|---|---|---|---|
| Durable VTON jobs + tenant storage | Contract/in-memory | Chưa chốt identity, TTL, delete/export, ownership ảnh | Chốt privacy trước; rồi user-scoped path + cleanup worker |
| FashionSigLIP/local evaluator | Labels/report | Model/dependency/licence/privacy audit | Bắt đầu human pairwise + pose/garment checks |
| Commercial profile | **Có ngay** | Cần policy provider, không cần key | Hard-disable CatVTON/IDM/OOTDiffusion ở commercial route; startup fail-fast |
| Self-host benchmark | Inventory/adapter | Cần GPU/weights/licence/dependency audit | Mock adapter và inventory trước, benchmark sau |

P3.1 pending vì thêm database khi chưa biết ai có quyền xoá/truy cập ảnh là rủi ro privacy, không chỉ là việc kỹ thuật. P3.3 nên làm sớm vì research model không được vô tình đi vào commercial path.

## 9. Root cause pending và cách làm trong lúc chờ

| Nguyên nhân | Không thể kết luận | Việc không cần chờ |
|---|---|---|
| Thiếu API key/credit | Ảnh thật, cost, latency, quality | Mock transport, fixture, request snapshot |
| Thiếu Playwright/browser | Browser E2E xanh | Giữ harness deterministic, chạy trong CI/dev có dependency |
| Thiếu HTTPS/phone/network | WebRTC và device quality | Protocol, telemetry, checklist |
| Thiếu eval cases/labels | “Tốt hơn” một cách khách quan | Mở rộng manifest, pairwise labels, hashes |
| Chưa chốt identity/privacy | Durable storage production | In-memory contract, ADR TTL/delete/export |
| Licence/model chưa audit | Commercial deployment | Hard-disable, licence inventory |

## 10. Repo/tài liệu tham khảo

| Nguồn | Học gì | Quyết định dùng |
|---|---|---|
| [DecartAI/tryon-examples](https://github.com/DecartAI/tryon-examples) và [digital mirror](https://github.com/DecartAI/tryon-examples/blob/main/examples/digital-mirror/README.md) | Live lifecycle, `setImage`, fit validation, combined outfit, prompt/network preflight, portrait crop | Phù hợp nhất với live; dùng qua SDK/API, không copy mù; production cần session/DB và cleanup rõ |
| [FASHN v1.6 API](https://docs.fashn.ai/api-reference/tryon-v1-6) | mode, garment photo type, segmentation-free, seed, samples | Hosted photo baseline nếu key/terms phù hợp |
| [FASHN parameter guide](https://docs.fashn.ai/guides/tryon-parameters-guide) | Fast/quality trade-off, seed variation | Dùng cho A/B, không đổi default theo cảm tính |
| [FASHN Try-On Max](https://docs.fashn.ai/api-reference/tryon-max) | High-resolution quality tier | Pilot flag; docs ghi Preview, cần cost/SLA |
| [FASHN model lifecycle](https://docs.fashn.ai/api-overview/model-lifecycle) và [privacy](https://docs.fashn.ai/api-overview/data-retention-privacy) | Lifecycle, retention/privacy | Bắt buộc review trước production; không giả định model/retention bất biến |
| IDM-VTON | Upper-body research baseline | Tham khảo; non-commercial licence cần audit |
| OOTDiffusion | Upper/lower/dress baseline | Tham khảo; nặng GPU/licence cần audit |
| CatVTON | Offline worker/Mac mini baseline | Chỉ benchmark sau licence/weights audit |
| Leffa/StableVITON | Research quality/eval | Không kéo vào runtime vội |
| OpenTryOn | Provider abstraction | Học adapter boundary; audit licence |
| Marqo FashionSigLIP | Fashion retrieval/fidelity evaluator | Tuỳ chọn sau privacy/licence audit |
| CRSLab | Conversational recommendation evaluation | Tham khảo eval, không phải runtime dependency |

Nguyên tắc: chọn repo theo mức khớp flow và licence, không chỉ số sao; giữ provider boundary trong `backend/tryon.py`; không commit ảnh người dùng.

## 11. Ma trận truy vết

| Yêu cầu | Code | Trạng thái | Bằng chứng | Thiếu |
|---|---|---|---|---|
| AI hỏi bối cảnh/gợi ý | Stylist chat/rules | DONE | Backend/API tests | UX user study |
| Chọn item từ tủ đồ | Store/UI | DONE | Item IDs/metadata | Tenant isolation dài hạn |
| Ảnh người hợp lệ | Pose gate | DONE | JS tests | Threshold tuning device |
| Garment sạch/mô tả rõ | Quality + descriptor | DONE | Backend tests | Classifier chính xác hơn |
| Photo provider chain | `tryon.py` | DONE/SCAFFOLDED | 272 tests/mock | Provider benchmark |
| Live lifecycle | Decart adapter/UI | DONE/SCAFFOLDED | Mock tests | Real browser/network |
| Output không lỗi format | Validator | DONE | Image tests | Semantic fidelity |
| So sánh quality | Manifest | SCAFFOLDED | Valid 1 case | Nhiều case/output/labels |
| Commercial-safe route | Config | PENDING | Chưa hard-disable hoàn toàn | Policy/licence decision |
| User-scoped job/TTL/delete | Durable jobs | PENDING | Chưa bật production | Identity/privacy decision |

## 12. Thứ tự khuyến nghị

### Wave A — không chờ key

1. QL-05: request builder + A/B matrix FASHN.
2. QL-08: pass order, input/output hash, provider metadata.
3. QL-10: manifest, report schema, human pairwise labels.
4. Chạy browser E2E trong môi trường có Playwright.
5. Hard-disable provider research ở commercial profile.
6. Viết ADR identity/tenant/TTL/delete/export.

### Wave B — có key/thiết bị

7. Chạy FASHN A/B/retry/Max và đo quality-cost-latency.
8. Đo multi-pass và identity drift.
9. Chạy Decart clean/model/prompt/canvas paired tests.
10. Test điện thoại thật, HTTPS, Wi-Fi/4G và dashboard cost.

### Wave C — sau bằng chứng

11. Chọn default provider/parameter bằng gate dữ liệu.
12. Pilot evaluator model sau audit.
13. Self-host benchmark sau GPU/weights/licence approval.

## 13. Tiêu chí chuyển sang DONE

- Có contract và test không cần network.
- Có ít nhất một provider run thật cho mục liên quan quality/latency/live.
- Có case ID, input metadata và kết quả tái lập.
- Báo quality, cost, latency, fallback; không chỉ một ảnh đẹp.
- Có quyết định licence/privacy.
- UI disclosure rõ mock/fallback/uncertain.
- Regression pass và `git diff --check` sạch.

```text
input gate
 -> provider response hợp lệ
 -> output validator
 -> garment fidelity + identity/pose threshold
 -> cost/latency budget
 => mới promote default
```

## 14. Rủi ro và kết luận

- Chưa có ngưỡng quality chính thức; product/ML phải thống nhất trade-off.
- Descriptor có thể sai; cần quyết định khi nào bắt buộc người dùng xác nhận.
- Multi-pass có thể làm drift identity; cần lưu pass và rollback.
- Live session có thể phát sinh cost nền; phải đối soát dashboard.
- Ảnh người cần TTL/delete/export trước durable storage.
- Research model không được dùng commercial trước licence audit.

**Kết luận:** code hiện tại đi đúng hướng và P0 đã tạo lớp kiểm soát cần thiết. Không cần chờ key để làm QL-05, QL-08, QL-10, E2E, commercial guardrail và ADR privacy. Chỉ sau khi các harness này hoàn tất mới chạy provider thật và chọn default bằng dữ liệu có thể tái lập.

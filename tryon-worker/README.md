# tryon-worker — Máy chủ "mặc thử" chạy trên Mac mini

Worker này chạy mô hình **CatVTON** trên Mac mini M4 (GPU Apple qua PyTorch MPS). Laptop chạy SHADOW
gọi nó qua mạng LAN khi người dùng bấm **"Ảnh mặc thử"**: gửi ảnh người + ảnh món đồ, nhận về ảnh đã mặc thử.

```
Laptop (SHADOW backend)  ──HTTP LAN──►  Mac mini :8188  (tryon-worker, CatVTON + MPS)
   TRYON_WORKER_URL=http://<ip-mac-mini>:8188
```

---

## Cài đặt từng bước (dành cho người không chuyên)

### Bước 0 — Chuẩn bị
- Mac mini Apple Silicon (M1/M2/M3/M4), macOS 13 trở lên. Nên còn trống **≥ 15 GB** ổ cứng.
- Mac mini và laptop **cùng một mạng Wi-Fi/LAN**.
- Cần Internet trong lần cài đầu (tải ~1 GB thư viện + ~2,8 GB mô hình).

### Bước 1 — Chép thư mục sang Mac mini
Chép cả thư mục `tryon-worker/` sang Mac mini, ví dụ vào `~/tryon-worker`. Cách nào cũng được:
- **AirDrop**: chuột phải thư mục → Chia sẻ → AirDrop → chọn Mac mini (thư mục sẽ nằm ở `~/Downloads`).
- **USB**, hoặc từ laptop: `scp -r tryon-worker <tên-user>@<ip-mac-mini>:~/`
  (cần bật *System Settings → General → Sharing → Remote Login* trên Mac mini).

### Bước 2 — Cài đặt (một lần)
Trên Mac mini mở ứng dụng **Terminal** (Cmd+Space, gõ "Terminal") rồi gõ:
```bash
cd ~/tryon-worker          # hoặc ~/Downloads/tryon-worker nếu chép bằng AirDrop
chmod +x setup_macmini.sh run.sh
./setup_macmini.sh
```
Script sẽ tự:
1. Tìm Python 3.12/3.11; nếu chưa có thì cài `python@3.12` bằng Homebrew
   (nếu chưa có Homebrew, script in ra lệnh cài — dán lệnh đó, nhập mật khẩu máy, rồi chạy lại `./setup_macmini.sh`).
2. Tạo môi trường ảo `.venv` và cài thư viện (PyTorch, diffusers, FastAPI…).
3. Kiểm tra GPU Apple (dòng `MPS available: True` là tốt).
4. Tải trọng số mô hình về `~/.cache/huggingface` (chỉ lần đầu, 5–20 phút tuỳ mạng).
5. Chạy tự kiểm tra và **in ra dòng `TRYON_WORKER_URL=http://…:8188`** để dán vào SHADOW.

Chạy lại `./setup_macmini.sh` bao nhiêu lần cũng an toàn.

### Bước 3 — Chạy server
```bash
./run.sh              # chạy mô hình thật
./run.sh --warmup     # nạp mô hình + chạy thử ngay khi khởi động (yêu cầu đầu tiên đỡ phải chờ)
./run.sh --mock       # không cần mô hình: trả về ảnh ghép đơn giản, để thử kết nối
```
Để cửa sổ Terminal mở trong lúc dùng (Ctrl+C để dừng). `run.sh` dùng `caffeinate` để Mac không ngủ khi server đang chạy.
Lần đầu macOS có thể hỏi *"Cho phép python nhận kết nối đến?"* → chọn **Allow/Cho phép**.

### Bước 4 — Tìm địa chỉ IP của Mac mini
`run.sh` và `setup_macmini.sh` đều in sẵn. Hoặc tự xem:
- *System Settings → Wi-Fi (hoặc Network) → Details… → IP address*, hoặc
- Terminal: `ipconfig getifaddr en0` (Wi-Fi/Ethernet; nếu trống thử `en1`).

Nên vào trang quản trị router đặt **IP cố định (DHCP reservation)** cho Mac mini để địa chỉ không đổi.
Có thể dùng tên máy thay IP: `http://<Tên-Mac-mini>.local:8188` (xem bằng `scutil --get LocalHostName`).

### Bước 5 — Khai báo trên laptop chạy SHADOW
Trong file `.env` của SHADOW (trên laptop) thêm:
```
TRYON_WORKER_URL=http://192.168.1.50:8188     # thay bằng IP của Mac mini
# TRYON_PHOTO_PROVIDER=worker                 # ép dùng worker; mặc định tự chọn worker → hf_space → mock
# TRYON_TOKEN=...                             # chỉ khi đã đặt TRYON_TOKEN trên Mac mini
```
Khởi động lại SHADOW. Kiểm tra từ laptop:
```bash
curl http://192.168.1.50:8188/health
# {"ok":true,"model":"catvton","device":"mps","loaded":false,"warm":false,...}
```
`loaded:false` là bình thường — mô hình chỉ được nạp ở yêu cầu đầu tiên (hoặc khi chạy `--warmup`).

### Thử nhanh không cần SHADOW
Trên Mac mini (hoặc laptop, đổi `--url`):
```bash
.venv/bin/python try_tryon.py nguoi.jpg ao.jpg --category upper --save-mask
.venv/bin/python try_tryon.py nguoi.jpg quan.jpg --category lower --url http://192.168.1.50:8188
```
Kết quả lưu ở `tryon_result.jpg` (và `tryon_result_mask.jpg` = vùng được vẽ lại).

### (Tuỳ chọn) Tự chạy khi Mac mini khởi động
Cách đơn giản: *System Settings → General → Login Items* → thêm một file `.command` chứa
`cd ~/tryon-worker && ./run.sh --warmup` (nhớ `chmod +x` file đó). Đặt Mac mini tự đăng nhập và
*Energy → Prevent automatic sleeping* = bật.

---

## Mẹo chụp ảnh cho kết quả đẹp
- **Ảnh người**: đứng thẳng, chính diện, thấy rõ từ đầu đến ít nhất đầu gối (toàn thân càng tốt), nền gọn, đủ sáng.
  Ảnh sẽ được cắt giữa theo tỉ lệ 3:4 — đứng ở giữa khung.
- **Ảnh đồ**: món đồ trải phẳng hoặc ảnh sản phẩm, nền trơn (PNG nền trong suốt cũng được).
- `category`: `upper` = áo/áo khoác, `lower` = quần/váy ngắn, `overall` = váy liền/đồ bộ.
  Với bộ áo + quần, SHADOW chạy 2 lượt (áo trước, quần sau).

## Tốc độ dự kiến (Mac mini M4 10-core GPU, 24 GB)
> **Chưa đo trên máy thật** — đây là ước lượng từ khối lượng tính toán (UNet SD-1.5 với ảnh người + đồ ghép
> dọc, CFG ×2). Log của server in `s/step` sau mỗi 10 bước; hãy xem con số thật ở đó.

| Độ phân giải (`TRYON_RESOLUTION`) | 30 bước (mặc định) | 20 bước |
|---|---|---|
| 768x1024 (chất lượng gốc) | ~1,5–3 phút | ~1–2 phút |
| 576x768 | ~45–90 giây | ~30–60 giây |
| 384x512 | ~15–30 giây | ~10–20 giây |

- Lần nạp mô hình đầu tiên: ~20–60 giây; yêu cầu đầu tiên chậm hơn (MPS biên dịch kernel) → dùng `--warmup`.
- Tạo mask (SCHP, CPU): ~1–3 giây. Bộ nhớ: ~5–8 GB khi chạy (fp16), ~10–12 GB nếu phải chuyển fp32.
- Mỗi lúc chỉ xử lý **1 yêu cầu**; các yêu cầu khác xếp hàng (tối đa `TRYON_MAX_QUEUE`, quá thì trả 503).
- Muốn nhanh: `TRYON_RESOLUTION=576x768` và/hoặc `TRYON_STEPS=20`. SHADOW cũng có thể gửi `steps` riêng mỗi yêu cầu.

---

## API
`GET /health`
```json
{"ok": true, "model": "catvton", "device": "mps", "loaded": true, "warm": true,
 "busy": false, "queue": 0, "mock": false, "dtype": "float16", "resolution": "768x1024",
 "default_steps": 30, "auth": false, "served": 3, "error": null}
```
`POST /tryon` (header `X-Tryon-Token` nếu có đặt `TRYON_TOKEN`)
```json
{"person_image": "data:image/jpeg;base64,...", "garment_image": "data:image/png;base64,...",
 "category": "upper", "garment_des": "áo sơ mi trắng", "steps": 30, "guidance": 2.5, "seed": 42}
```
→ `200`
```json
{"image": "data:image/jpeg;base64,...", "seconds": 95.3, "width": 768, "height": 1024,
 "queue_seconds": 0.0, "model": "catvton", "device": "mps", "seed": 42, "steps": 30,
 "mask_mode": "schp", "mock": false, "mask": null}
```
- Chỉ `person_image`, `garment_image`, `category` là bắt buộc. Base64 trần (không có `data:`) cũng được nhận.
- `garment_des` được nhận nhưng CatVTON **không dùng** (mô hình không có bộ mã hoá chữ).
- `seed` bỏ trống hoặc `-1` = ngẫu nhiên; seed thực tế được trả lại để tái tạo kết quả.
- Mở rộng (tuỳ chọn): `mask_image` (data URL **cùng kích thước/khung hình với `person_image`**, trắng = vùng vẽ lại,
  bỏ qua mask tự động), `return_mask: true` (trả kèm mask đã dùng, ở độ phân giải đầu ra).
- Mã lỗi: `400` ảnh hỏng/quá lớn/quá nhỏ · `401` sai token · `422` sai tham số hoặc bị bộ lọc an toàn chặn ·
  `503` đang đầy hàng đợi / nạp mô hình lỗi · `500` lỗi khi chạy.

## Biến môi trường
Đặt trong `tryon-worker/.env` (mẫu: `.env.example`) hoặc trước lệnh, ví dụ `TRYON_STEPS=20 ./run.sh`.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PORT` | `8188` | Cổng HTTP |
| `TRYON_TOKEN` | (trống) | Mật khẩu chung, kiểm tra qua header `X-Tryon-Token` (chỉ áp dụng cho `/tryon`) |
| `TRYON_MOCK` | `0` | `1` = không nạp mô hình, trả ảnh ghép đơn giản (test SHADOW) |
| `TRYON_DEVICE` | `auto` | `auto` = mps → cuda → cpu; hoặc `mps`/`cuda`/`cpu` |
| `TRYON_DTYPE` | `auto` | MPS: UNet fp16 (tự chuyển fp32 nếu ra NaN), VAE luôn fp32; `fp32` để ép fp32 |
| `TRYON_STEPS` | `30` | Số bước mặc định (yêu cầu có thể gửi `steps` riêng, 1–100) |
| `TRYON_GUIDANCE` | `2.5` | CFG mặc định (giá trị gốc của CatVTON) |
| `TRYON_RESOLUTION` | `576x768` | Rộng x cao, làm tròn xuống bội số 64 |
| `TRYON_WARMUP` | `0` | `1` = nạp + chạy thử 2 bước khi khởi động |
| `TRYON_MAX_QUEUE` | `4` | Số yêu cầu chờ tối đa (ngoài yêu cầu đang chạy) |
| `TRYON_MAX_IMAGE_MB` | `15` | Giới hạn kích thước mỗi ảnh gửi lên |
| `TRYON_MASKER` | `schp` | `schp` = phân tích người tự động; `box` = hình chữ nhật thô (chỉ để thử) |
| `TRYON_MASKER_DEVICE` | `cpu` | Thiết bị cho SCHP (`cpu` ổn định nhất; `mps` tự lùi về CPU nếu lỗi) |
| `TRYON_SAFETY_CHECK` | `1` | Bộ lọc an toàn Stable Diffusion (như app gốc). `0` = tắt, bớt tải 0,6 GB |
| `TRYON_REPAINT` | `1` | Giữ nguyên điểm ảnh gốc ngoài vùng mask (mặt, nền không bị đổi) |
| `TRYON_ATTN_CHUNK` | auto (`1024` trên MPS) | Số token mỗi lát attention; nhỏ hơn = ít RAM hơn, chậm hơn. `0` = không chia |
| `TRYON_OFFLINE` | `0` | `1` = chỉ dùng trọng số đã tải, không vào mạng |
| `TRYON_MODEL` | `catvton` | `idm` **không hỗ trợ** (xem bên dưới) |
| `HF_TOKEN` | (trống) | Token Hugging Face nếu bị giới hạn tốc độ tải |
| `HF_HOME` | `~/.cache/huggingface` | Nơi lưu trọng số |
| `TRYON_BASE_MODEL`, `TRYON_VAE`, `TRYON_CATVTON_REPO`, `TRYON_ATTN_VERSION` | xem `weights.py` | Đổi nguồn trọng số (nâng cao) |

---

## Đã chọn gì và vì sao

**Mô hình: CatVTON bản có mask** (`zhengchong/CatVTON`, checkpoint `mix-48k-1024` — đúng cái mà `app.py` chính thức dùng).
- Nhẹ: UNet Stable Diffusion 1.5 inpainting (~860M tham số) + 49M tham số attention đã fine-tune,
  **không có text encoder, không có image encoder**. Ở fp16 chỉ cần ~2 GB trọng số → rất hợp 24 GB RAM của M4.
- Chỉ cần `diffusers` + PyTorch thuần, không có kernel CUDA riêng → chạy được trên MPS.
- Đã port lại pipeline (`pipeline_catvton.py`) thay vì clone repo, để: chia nhỏ attention cho MPS, giữ VAE ở fp32,
  sinh số ngẫu nhiên trên CPU (seed tái lập được), tự chuyển fp32 nếu fp16 ra NaN, không phụ thuộc thư mục
  `detectron2/` có sẵn file `.so` Linux trong repo gốc.

**Mask (vùng quần áo cần vẽ lại): SCHP, bỏ DensePose.** Xem `masker.py`.
- `AutoMasker` gốc của CatVTON dùng 3 mô hình: **SCHP-LIP + SCHP-ATR** (phân tích người, PyTorch thuần) và
  **DensePose** (cần **detectron2** — rất khó cài trên macOS arm64; repo CatVTON còn kèm sẵn `_C.cpython-39-x86_64-linux-gnu.so`, không chạy được trên Mac).
- Worker giữ nguyên 2 mô hình SCHP (trọng số lấy từ chính `zhengchong/CatVTON/SCHP`) và giữ nguyên toàn bộ luật
  ghép mask của `cloth_agnostic_mask` (vùng bảo vệ mặt/tóc/phụ kiện/đồ không liên quan, bao lồi, làm mờ, giãn nở).
  Hai việc DensePose làm được thay bằng ước lượng từ nhãn SCHP:
  1. *Bảo vệ bàn tay/bàn chân*: lấy đầu mút của mỗi cánh tay (điểm xa tâm thân nhất) / điểm thấp nhất của mỗi chân,
     bán kính ≈ 0,9 × chiều cao khuôn mặt.
  2. *Vùng thân thể cần phủ* (để đồ dài hơn đồ đang mặc vẫn vẽ được): dùng nhãn áo/quần/tay/chân của SCHP,
     giãn nở giống hệt cách làm với vùng DensePose.
- SCHP chạy trên **CPU** theo mặc định: trên MPS, `AdaptiveAvgPool2d` của mô-đun PSP lỗi với bản đồ 32×32 của ATR
  (MPS yêu cầu kích thước chia hết). CPU M4 chạy ~1–3 giây, không đáng kể so với phần khuếch tán.
- Có thể gửi `mask_image` của riêng bạn, hoặc `TRYON_MASKER=box` để thử nhanh.
- Hạn chế so với bản gốc: tay chống hông/khoanh tay có thể bị vẽ lại một phần; dáng nghiêng khó hơn.

**Kiểu số trên MPS**: bf16 hỗ trợ kém trên MPS nên UNet dùng **fp16**; VAE, latent và scheduler giữ **fp32**.
Nếu kết quả có NaN, worker tự chuyển UNet sang fp32, chạy lại và giữ fp32 cho các lần sau.
Self-attention được **chia lát theo trục query** (`TRYON_ATTN_CHUNK`): CatVTON ghép người + đồ theo chiều dọc
nên ở 768x1024 lớp trên cùng có 24.576 token; ma trận attention đầy đủ cho batch CFG ~10 GB (fp16), vượt giới hạn
4 GB/tensor của MPS. Chia lát giữ đỉnh bộ nhớ < 1 GB mà kết quả toán học không đổi (đã có test).

**Các phương án đã cân nhắc nhưng không chọn**
- *CatVTON Mask-Free* (`zhengchong/CatVTON-MaskFree`, nền `timbrooks/instruct-pix2pix`): không cần mask, nhưng không
  chọn được loại đồ (áo/quần/váy), model card yêu cầu cam kết "không chia sẻ mô hình", và `app_p2p.py` chính thức nạp
  nhầm trọng số từ repo CatVTON thường → khó tin cậy.
- *CatVTON-FLUX* (LoRA cho FLUX.1-Fill-dev 12B): ~24 GB trọng số bf16 + T5, không vừa 24 GB RAM dùng chung,
  và FLUX.1-dev có giấy phép phi thương mại riêng + phải xin quyền tải.

### IDM-VTON (`TRYON_MODEL=idm`) — **không hỗ trợ**, lý do:
1. IDM-VTON đưa **ảnh DensePose vào thẳng UNet** làm đầu vào (không chỉ để làm mask) → bắt buộc detectron2;
   demo còn cần OpenPose + 2 mô hình human-parsing ONNX.
2. Dựa trên **SDXL** với **hai UNet** (UNet thử đồ + "garment UNet" tham chiếu) + 2 text encoder + CLIP ViT-H image
   encoder: ~14 GB trọng số fp16, VAE SDXL phải chạy fp32 (fp16 ra NaN). Gần chạm trần 24 GB bộ nhớ dùng chung.
3. Ước lượng **5–10+ phút/ảnh** trên M4 ở 768x1024 — không dùng được cho trải nghiệm tương tác.
4. Cùng giấy phép CC BY-NC-SA 4.0 nên không có lợi thế giấy phép.
SHADOW đã có sẵn `hf_space` (IDM-VTON trên Hugging Face Space) làm phương án thay thế khi cần chất lượng IDM-VTON.
Đặt `TRYON_MODEL=idm` sẽ khiến server dừng ngay với thông báo giải thích.

---

## Xử lý sự cố
| Hiện tượng | Cách xử lý |
|---|---|
| `MPS available: False` | Đảm bảo Terminal **không** chạy dưới Rosetta và Python là bản arm64 (`python3 -c "import platform;print(platform.machine())"` phải ra `arm64`). Chạy `RECREATE_VENV=1 ./setup_macmini.sh`. |
| Laptop không gọi được `/health` | Cùng mạng chưa? Thử `ping <ip>`; kiểm tra *System Settings → Network → Firewall* cho phép Python; IP có đổi không (đặt DHCP reservation). Trên Mac mini thử `curl http://localhost:8188/health`. |
| `401 invalid or missing X-Tryon-Token` | `TRYON_TOKEN` trên laptop và trong `tryon-worker/.env` phải giống hệt. |
| Tải trọng số lỗi / rất chậm | Chạy lại `./setup_macmini.sh` (tải tiếp phần còn thiếu). Bị giới hạn: tạo token miễn phí ở huggingface.co/settings/tokens, đặt `HF_TOKEN=hf_...` trong `.env`. |
| Ảnh ra đen/xám hoặc log báo NaN | Worker tự chuyển fp32. Nếu vẫn lỗi: `TRYON_DTYPE=fp32`. |
| Hết bộ nhớ / máy chậm hẳn | Giảm `TRYON_ATTN_CHUNK=512`, hoặc `TRYON_RESOLUTION=576x768`. Đóng ứng dụng nặng khác. |
| Lỗi "not implemented for MPS" | `run.sh` đã đặt `PYTORCH_ENABLE_MPS_FALLBACK=1`. Nếu chạy `server.py` trực tiếp thì tự đặt biến này. |
| Áo bị vẽ sai chỗ / lẹm vào tay, mặt | Ảnh người nên chính diện, đứng giữa khung. Gửi `return_mask: true` (hoặc `try_tryon.py --save-mask`) để xem mask. |
| `422 result blocked by safety checker` | Bộ lọc an toàn chặn kết quả (có thể nhầm với đồ bơi/đồ ngắn). Tắt bằng `TRYON_SAFETY_CHECK=0` nếu bạn chấp nhận. |
| `503 worker busy` | Đang có quá nhiều yêu cầu chờ. Đợi hoặc tăng `TRYON_MAX_QUEUE`. |
| `pip` báo xung đột phiên bản | `RECREATE_VENV=1 ./setup_macmini.sh` để cài lại sạch. |

Log nằm ngay trên cửa sổ Terminal đang chạy `run.sh` (dòng `step 10/30 (x.xx s/step)` cho biết tốc độ).

---

## Giấy phép — **chỉ dùng phi thương mại**
| Thành phần | Nguồn | Giấy phép |
|---|---|---|
| CatVTON (trọng số attention + mã pipeline được port) | [Zheng-Chong/CatVTON](https://github.com/Zheng-Chong/CatVTON), [zhengchong/CatVTON](https://huggingface.co/zhengchong/CatVTON) | **CC BY-NC-SA 4.0** (phi thương mại, chia sẻ cùng giấy phép) |
| SCHP (trọng số LIP/ATR, mạng ResNet-101) | [Self-Correction-Human-Parsing](https://github.com/GoGoDuck912/Self-Correction-Human-Parsing) qua repo CatVTON | Mã MIT; trọng số phân phối trong repo CatVTON (CC BY-NC-SA 4.0); dữ liệu huấn luyện LIP/ATR chỉ cho nghiên cứu |
| Stable Diffusion 1.5 inpainting (UNet, scheduler, safety checker) | [stable-diffusion-v1-5/stable-diffusion-inpainting](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting) (bản gốc runwayml; CatVTON dùng bản sao `booksforcharlie/stable-diffusion-inpainting`, trọng số giống hệt) | CreativeML OpenRAIL-M (có danh sách cấm sử dụng) |
| VAE | [stabilityai/sd-vae-ft-mse](https://huggingface.co/stabilityai/sd-vae-ft-mse) | MIT |
| Mã của worker này (`server.py`, `masker.py`…) | SHADOW | Phần port từ CatVTON thuộc CC BY-NC-SA 4.0 |

Vì CatVTON là **CC BY-NC-SA 4.0**, toàn bộ tính năng mặc thử bằng worker này chỉ được dùng cho mục đích
cá nhân/nghiên cứu/demo, **không thương mại**. Ghi nguồn CatVTON khi trình diễn.

---

## Cho lập trình viên
```
server.py            FastAPI: /health, /tryon, hàng đợi 1 việc, token, mock backend
pipeline_catvton.py  CatVTON port cho MPS (attention chia lát, nạp trọng số, NaN → fp32), CatVTONBackend
masker.py            Mask không cần DensePose: SCHP LIP+ATR + luật cloth_agnostic_mask
schp_net.py          Mạng SCHP (ResNet-101 CE2P) PyTorch thuần
imaging.py           Giải mã data URL, resize/crop/pad, repaint (chỉ cần Pillow)
weights.py           Danh sách repo + allow_patterns tải từ Hugging Face
download_weights.py  Tải trước trọng số
try_tryon.py         Client thử (chỉ dùng thư viện chuẩn)
tests/               test_server.py (mock, không cần torch) · test_masker.py · test_pipeline_tiny.py (UNet tí hon)
```
Chạy test: `TRYON_MOCK=1 .venv/bin/python -m pytest tests -q`. Chạy kiểu uvicorn:
`uvicorn server:create_app --factory --host 0.0.0.0 --port 8188` (luôn 1 worker process).
Chỉ cài phần mock (không torch, ví dụ trên laptop): `pip install -r requirements-mock.txt && TRYON_MOCK=1 python server.py`.

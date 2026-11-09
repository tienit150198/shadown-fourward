# Tủ đồ mẫu — nguồn ảnh & giấy phép

Bộ tủ đồ mẫu (`POST /api/wardrobe/seed-demo`) chỉ để demo hackathon. **Chỉ dùng cho demo, phi thương mại.**
Ảnh được chuẩn hoá về JPEG (tối đa 1024px). Nhãn trong `items.json` do `qwen3-vl-8b-instruct` gắn
(`build_seed.py`, 2026-11-09), sau đó sửa tay vài tên/màu cho đúng.

| id | loại | nguồn | giấy phép / ghi chú |
|---|---|---|---|
| demo-ao-blouse-cam-dao | top | Space `yisol/IDM-VTON`, `example/cloth/09290_00.jpg` — ảnh nghiên cứu VITON-HD | CC BY-NC-SA 4.0 (Space); chỉ dùng cho demo, phi thương mại |
| demo-ao-polo-hong | top | Space `yisol/IDM-VTON`, `example/cloth/09176_00.jpg` — VITON-HD | CC BY-NC-SA 4.0; chỉ dùng cho demo, phi thương mại |
| demo-ao-thun-xanh-duong | top | Space `yisol/IDM-VTON`, `example/cloth/09305_00.jpg` — VITON-HD | CC BY-NC-SA 4.0; chỉ dùng cho demo, phi thương mại |
| demo-ao-nhung-xanh | top | Space `yisol/IDM-VTON`, `example/cloth/04469_00.jpg` — VITON-HD | CC BY-NC-SA 4.0; chỉ dùng cho demo, phi thương mại |
| demo-vay-xam-co-trang | dress | Space `Kwai-Kolors/Kolors-Virtual-Try-On`, `assets/cloth/04_dress.png` | Mã nguồn Space Apache-2.0; bản quyền ảnh gốc không ghi rõ — chỉ dùng cho demo, phi thương mại |
| demo-ao-khoac-xanh-reu | outerwear | Space `Kwai-Kolors/Kolors-Virtual-Try-On`, `assets/cloth/02_upper.png` | Như trên — chỉ dùng cho demo, phi thương mại |
| demo-quan-jean-xanh | bottom | Vẽ bằng Pillow (`make_placeholders.py`) | CC0, tự tạo cho SHADOW |
| demo-quan-au-den | bottom | Vẽ bằng Pillow (`make_placeholders.py`) | CC0, tự tạo cho SHADOW |
| demo-chan-vay-be | bottom | Vẽ bằng Pillow (`make_placeholders.py`) | CC0, tự tạo cho SHADOW |
| demo-giay-sneaker-trang | shoes | Vẽ bằng Pillow (`make_placeholders.py`) | CC0, tự tạo cho SHADOW |

Đã cố ý bỏ các ảnh ví dụ có logo thương hiệu / nhân vật (Gucci, Stüssy, Guess, Disney).

Link: https://huggingface.co/spaces/yisol/IDM-VTON/tree/main/example/cloth ·
https://huggingface.co/spaces/Kwai-Kolors/Kolors-Virtual-Try-On/tree/main/assets/cloth ·
VITON-HD: https://github.com/shadow2496/VITON-HD (dataset phi thương mại).

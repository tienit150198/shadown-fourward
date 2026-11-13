"""Men's SUMMER demo wardrobe from a real shop (YaMe.vn) — offline asset builder.

    cd backend/wardrobe_seed_shop && ../../.venv/bin/python build_seed_shop.py            # tải lại ảnh còn thiếu
    cd backend/wardrobe_seed_shop && ../../.venv/bin/python build_seed_shop.py --refresh  # tải lại ảnh + gắn nhãn
    cd backend/wardrobe_seed_shop && ../../.venv/bin/python build_seed_shop.py --update-products  # + đọc lại shop
    cd backend/wardrobe_seed_shop && ../../.venv/bin/python build_seed_shop.py --apply-fixes  # chỉ áp sửa tay

Default (no flag, no API key needed): download every image listed in items.json that is missing
from images/ (images are git-ignored).

--update-products: re-read each product from the shop's public Shopify JSON
(https://yame.vn/products/<handle>.json: name, price, SKU, image URLs) into products_snapshot.json.
--refresh: re-download all images, tag each image ONCE with the app's real Qwen-VL tagger
(stylist.tag_item, loads ../../.env), then apply the hand corrections in SELECTION
(name/category/colours checked against the official product data) and write items.json.

Polite fetching: normal browser User-Agent, ≤1 request/second, public endpoints only, no login.
Product photos © YaMe — internal demo only (see SOURCES.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import subprocess
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from wardrobe_store import to_jpeg  # noqa: E402

SHOP = "https://yame.vn"
BRAND = "YaMe"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
REQUEST_GAP_S = 1.1
ITEMS_PATH = HERE / "items.json"
IMAGES_DIR = HERE / "images"
SNAPSHOT_PATH = HERE / "products_snapshot.json"  # public product data used for items.json

# (id, Shopify handle, image file name on the CDN (flat product shot), hand-checked fields).
# Hand-checked fields override the tagger: name/category/colours always; others where the tagger was off.
SELECTION: list[tuple[str, str, str, dict]] = [
    ("shop-ao-thun-supima-trang", "ao-thun-supima-cotton-thuong-hang-the-minimalist-014-trang-0024688",
     "0024688_thumb_1.jpg",
     {"name": "Áo thun Supima trắng", "category": "top", "colors": ["trắng"], "material": "cotton supima",
      "warmth": 1, "occasions": ["đi học", "đi chơi", "hẹn hò", "du lịch", "đi biển", "ở nhà"],
      "notes": "Áo thun trơn cơ bản: phối quần short kaki, jean hoặc khoác ngoài sơ mi mở cúc.", "description_en": "plain white short-sleeve crew-neck Supima cotton T-shirt with a tiny chest logo"}),
    ("shop-ao-thun-supima-den", "ao-thun-supima-cotton-thuong-hang-the-minimalist-014-den-0024689",
     "0024689_thumb_1.jpg",
     {"name": "Áo thun Supima đen", "category": "top", "colors": ["đen"], "material": "cotton supima",
      "warmth": 1, "occasions": ["đi học", "đi chơi", "hẹn hò", "du lịch", "thể thao", "ở nhà"], "description_en": "plain black short-sleeve crew-neck Supima cotton T-shirt with a tiny chest logo"}),
    ("shop-ao-polo-xanh-duong", "ao-polo-basic-co-gian-thoang-mat-non-branded-056-xanh-duong-0024655",
     "NonBranded056XanhD_ng24655thumb1.jpg",
     {"name": "Áo polo xanh dương", "category": "top", "colors": ["xanh dương"], "material": "cotton pha",
      "style": "thanh lịch",
      "description_en": "steel blue short-sleeve men's polo shirt with two-button placket"}),
    ("shop-ao-so-mi-linen-xanh-dam",
     "ao-so-mi-tay-ngan-vai-det-xuoc-hieu-ung-linen-the-weekend-056-mau-xanh-duong-phom-rong-rai-f4-2625294",
     "TheWeekend056XanhD_ng.jpg",
     {"name": "Sơ mi linen cộc tay xanh đậm", "category": "top", "colors": ["xanh dương đậm"],
      "material": "vải hiệu ứng linen", "warmth": 1,
      "description_en": "navy short-sleeve linen-look button-up shirt with chest pocket, relaxed fit"}),
    ("shop-ao-so-mi-trang-cong-so", "ao-so-mi-tay-ngan-vai-ngai-cuu-khang-khuan-the-ceo-024-trang-0024800",
     "0024800thumb1.jpg",
     {"name": "Sơ mi cộc tay trắng công sở", "category": "top", "colors": ["trắng"], "style": "công sở",
      "warmth": 1, "material": "vải sợi ngải cứu",
      "occasions": ["đi làm", "phỏng vấn", "đám cưới", "dự tiệc", "hẹn hò"],
      "notes": "Sơ mi trắng đứng phom, ít nhăn: mặc với quần tây hoặc kaki dài, sơ vin cho dịp trang trọng.", "description_en": "crisp white short-sleeve button-up dress shirt with point collar"}),
    ("shop-ao-so-mi-cuban-den", "ao-so-mi-tay-ngan-airdry-hieu-ung-linen-mem-min-giai-nhiet-the-no-style-216-den-0024841",
     "TheNoStyle216Den1.jpg",
     {"name": "Sơ mi cổ Cuban đen", "category": "top", "colors": ["đen"], "material": "vải dệt xước",
      "warmth": 1, "description_en": "black short-sleeve Cuban camp-collar button-up shirt, textured fabric"}),
    ("shop-quan-short-kaki-be", "quan-short-kaki-ben-co-gian-the-weekend-030-nau-nhat-0024659",
     "TheWeekend030NauNh_t24659thumb1.jpg",
     {"name": "Quần short kaki nâu nhạt", "category": "bottom", "colors": ["nâu nhạt"], "material": "kaki",
      "warmth": 1, "style": "thường ngày", "occasions": ["đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "beige khaki chino shorts, above the knee"}),
    ("shop-quan-short-the-thao-den", "quan-short-5-inch-vai-mesh-thoang-khi-no-style-m153-den-0024032",
     "24032_thumb_1.jpg",
     {"name": "Quần short thể thao đen", "category": "bottom", "colors": ["đen"], "style": "thể thao",
      "material": "vải lưới mesh", "warmth": 1,
      "occasions": ["thể thao", "đi biển", "ở nhà", "du lịch"],
      "notes": "Quần short thể thao nhanh khô: đi tập, chạy bộ, đi biển — phối áo thun đen hoặc trắng.",
      "description_en": "black 5-inch mesh athletic shorts with drawstring waist"}),
    ("shop-quan-kaki-be",
     "quan-dai-kaki-basic-ong-dung-vai-mem-min-it-nhan-thoai-mai-ca-ngay-phom-om-vua-f2-non-branded-055-be-2625341",
     "NonBranded055Be.jpg",
     {"name": "Quần kaki dài be", "category": "bottom", "colors": ["be"], "material": "kaki", "warmth": 2,
      "occasions": ["đi làm", "phỏng vấn", "hẹn hò", "đi chơi", "du lịch"],
      "description_en": "light beige straight-leg chino trousers"}),
    ("shop-quan-jean-xanh-nhat",
     "quan-jean-mai-nhe-vai-mem-ong-suong-phom-rong-f4-the-original-064-mau-xanh-nhat-2625140",
     "TheOriginal064XanhNh_t.jpg",
     {"name": "Quần jean ống suông xanh nhạt", "category": "bottom", "colors": ["xanh dương nhạt"],
      "material": "jean", "description_en": "light-wash blue straight-leg denim jeans"}),
    ("shop-quan-tay-den", "quan-tay-smart-casual-mem-it-nhan-lung-tuy-chinh-the-ceo-017-den-0024699",
     "The_CEO_017_Den_0024699_Thumb_1.jpg",
     {"name": "Quần tây đen", "category": "bottom", "colors": ["đen"], "style": "công sở", "warmth": 2,
      "material": "vải tây co giãn", "seasons": ["xuân", "hạ", "thu", "đông"],
      "occasions": ["đi làm", "dự tiệc", "đám cưới", "phỏng vấn", "hẹn hò"],
      "description_en": "black slim tailored dress trousers"}),
    ("shop-ao-khoac-chong-nang-be", "ao-khoac-parka-anti-uv-tay-xo-ngon-chong-nang-upf-50-non-branded-048-be-0024913",
     "NonBranded048Be1.jpg",
     {"name": "Áo khoác chống nắng UPF50+ be", "category": "outerwear", "colors": ["be"], "rain_ok": False,
      "material": "thun chống uv", "warmth": 2,
      "occasions": ["đi chơi", "du lịch", "thể thao", "đi biển"],
      "notes": "Áo khoác mỏng chống nắng UPF 50+, có mũ — đi nắng, đi xe máy, đi biển.",
      "description_en": "light beige zip-up hooded sun-protection jacket, thin fabric"}),
    ("shop-ao-khoac-gio-xam", "ao-khoac-gio-parka-sieu-nhe-gap-gon-thong-minh-non-branded-046-xam-dam-0024313",
     "24313thumb9.jpg",
     {"name": "Áo khoác gió gấp gọn xám đậm", "category": "outerwear", "colors": ["xám đậm"],
      "material": "vải dù trượt nước", "warmth": 2,
      "occasions": ["đi làm", "đi chơi", "du lịch", "thể thao"],
      "notes": "Áo gió siêu nhẹ trượt nước, gấp gọn bỏ balo — mang theo khi trời có thể mưa rào.",
      "description_en": "dark grey lightweight packable hooded windbreaker jacket"}),
    ("shop-giay-slip-on-trang", "giay-slip-on-nguyen-ban-014-0021284",
     "GiaySlipOnNguyenB_n014_2.jpg",
     {"name": "Giày slip-on trắng", "category": "shoes", "colors": ["trắng"], "style": "thường ngày",
      "occasions": ["đi làm", "đi học", "đi chơi", "hẹn hò", "du lịch"],
      "notes": "Giày lười trắng dễ phối: quần kaki, jean, short — tránh ngày mưa.", "material": "da tổng hợp",
      "fabric": "leather", "rain_ok": False,
      "description_en": "white leather slip-on shoes with black sole"}),
    ("shop-dep-quai-ngang-nau", "dep-de-trau-slide-slipper-da-suede-mem-y2010-a002-nau-0024506",
     "dep-y2010-a002-nau-1180969037.jpg",
     {"name": "Dép quai ngang da lộn nâu đậm", "category": "shoes", "colors": ["nâu đậm"], "material": "da lộn",
      "notes": "Dép đế trấu êm chân: đi biển, dạo phố cuối tuần với quần short.",
      "style": "thường ngày", "warmth": 1,
      "occasions": ["đi chơi", "du lịch", "đi biển", "ở nhà"],
      "description_en": "brown suede slide sandals with cork footbed"}),
    ("shop-giay-loafer-den", "giay-tay-loafer-real-leather-06-den-0024417",
     "0024417thumb1.jpg",
     {"name": "Giày loafer da đen", "category": "shoes", "colors": ["đen"], "material": "da thật", "rain_ok": True,
      "style": "công sở", "occasions": ["đi làm", "dự tiệc", "đám cưới", "phỏng vấn", "hẹn hò"],
      "notes": "Giày da đen hai khoá: đi với quần tây đen cho đám cưới, phỏng vấn, dự tiệc.",
      "description_en": "black leather double-monk-strap dress shoes"}),
    # ---- mở rộng 2026-11-09: thêm 26 món (áo thun nhiều màu, polo, sơ mi linen/cuban/oxford, short x3,
    # quần dài, áo chống nắng/gió/overshirt/blazer, sneaker, dép, mũ, thắt lưng) --------------------------
    ("shop-ao-thun-supima-navy", "ao-thun-supima-cotton-thuong-hang-the-minimalist-014-xanh-duong-dam-0024692",
     "0024692_Thumb_1.jpg",
     {"name": "Áo thun Supima xanh navy", "category": "top", "colors": ["xanh navy"], "material": "cotton supima",
      "style": "thường ngày", "warmth": 1, "occasions": ["đi học", "đi chơi", "hẹn hò", "du lịch", "ở nhà"],
      "description_en": "plain navy short-sleeve crew-neck Supima cotton T-shirt"}),
    ("shop-ao-thun-supima-xam", "ao-thun-supima-cotton-thuong-hang-the-minimalist-014-xam-nhat-0024691",
     "0024691_Thumb_1.jpg",
     {"name": "Áo thun Supima xám nhạt", "category": "top", "colors": ["xám nhạt"], "material": "cotton supima",
      "style": "thường ngày", "warmth": 1, "occasions": ["đi học", "đi chơi", "du lịch", "thể thao", "ở nhà"],
      "description_en": "plain light grey short-sleeve crew-neck Supima cotton T-shirt"}),
    ("shop-ao-thun-supima-be", "ao-thun-supima-cotton-thuong-hang-the-minimalist-014-kem-0024690",
     "0024690_thumb_1.jpg",
     {"name": "Áo thun Supima be", "category": "top", "colors": ["be"], "material": "cotton supima",
      "style": "thường ngày", "warmth": 1, "occasions": ["đi học", "đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "plain beige short-sleeve crew-neck Supima cotton T-shirt"}),
    ("shop-ao-thun-xanh-la-dam", "ao-thun-flexcool-mat-lanh-the-minimalist-005-xanh-reu-0024324",
     "ao-thun-cool-touch-08-xanh-reu-1174881466.jpg",
     {"name": "Áo thun mát lạnh xanh lá đậm", "category": "top", "colors": ["xanh lá đậm"],
      "material": "vải icevibes mát lạnh", "fabric": "poly", "style": "năng động", "warmth": 1,
      "occasions": ["đi chơi", "du lịch", "thể thao", "đi biển"],
      "description_en": "dark green short-sleeve crew-neck cooling T-shirt"}),
    ("shop-ao-polo-trang", "ao-polo-basic-co-gian-thoang-mat-non-branded-056-trang-0024651",
     "NonBranded056Tr_ng24651thumb1.jpg",
     {"name": "Áo polo trắng", "category": "top", "colors": ["trắng"], "material": "cotton pha", "style": "thanh lịch",
      "warmth": 1, "occasions": ["đi làm", "đi học", "đi chơi", "hẹn hò", "du lịch"],
      "description_en": "white short-sleeve men's polo shirt with two-button placket"}),
    ("shop-ao-polo-den", "ao-polo-cloudtouch-vai-ultra-ribbed-muot-min-clean-fit-phom-om-vua-f2-the-weekend-061-den-2625303", "TheWeekend061Den.jpg",
     {"name": "Áo polo gân đen", "category": "top", "colors": ["đen"], "material": "thun gân", "style": "thanh lịch",
      "warmth": 1, "fit": "slim", "occasions": ["đi làm", "hẹn hò", "dự tiệc", "đi chơi"],
      "description_en": "black slim-fit ribbed short-sleeve polo shirt"}),
    ("shop-ao-so-mi-linen-trang", "ao-so-mi-tay-ngan-vai-det-xuoc-hieu-ung-linen-the-weekend-056-mau-trang-phom-rong-rai-f4-2625292",
     "TheWeekend056Tr_ng.jpg",
     {"name": "Sơ mi linen cộc tay trắng", "category": "top", "colors": ["trắng"], "material": "vải hiệu ứng linen",
      "style": "thanh lịch", "warmth": 1, "occasions": ["đi làm", "đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "white short-sleeve linen-look button-up shirt with chest pocket, relaxed fit"}),
    ("shop-ao-so-mi-cuban-ke-xanh", "ao-so-mi-tay-ngan-airdry-linen-soc-min-thoang-the-no-style-203-trang-xanh-duong-0024973",
     "TheNoStyle203TrangXanhDuong1.jpg",
     {"name": "Sơ mi Cuban kẻ sọc trắng xanh", "category": "top", "colors": ["trắng", "xanh dương"],
      "pattern": "kẻ sọc", "material": "linen pha rayon", "style": "thường ngày", "warmth": 1,
      "occasions": ["đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "white and blue vertical-striped short-sleeve Cuban-collar linen-blend shirt"}),
    ("shop-ao-so-mi-oxford-coc-xanh", "ao-so-mi-tay-ngan-thoang-khi-it-nhan-the-weekend-029-xanh-duong-nhat-0024630",
     "0024630thumb1.jpg",
     {"name": "Sơ mi oxford cộc tay xanh nhạt", "category": "top", "colors": ["xanh dương nhạt"],
      "material": "oxford cotton", "style": "thanh lịch", "warmth": 1,
      "occasions": ["đi làm", "đi học", "hẹn hò", "đi chơi"],
      "description_en": "light blue short-sleeve oxford button-up shirt"}),
    ("shop-ao-so-mi-oxford-dai-xanh", "ao-khoac-so-mi-oxford-thoang-khi-it-nhan-the-no-style-m214-xanh-duong-nhat-0024625",
     "0024625thumb1.jpg",
     {"name": "Sơ mi oxford dài tay xanh nhạt", "category": "top", "colors": ["xanh dương nhạt"],
      "material": "oxford cotton", "style": "công sở", "warmth": 2,
      "occasions": ["đi làm", "phỏng vấn", "đám cưới", "hẹn hò", "dự tiệc"],
      "notes": "Sơ mi dài tay mỏng: xắn tay khi nóng, sơ vin với quần tây/kaki cho dịp lịch sự.",
      "description_en": "light blue long-sleeve oxford button-up shirt"}),
    ("shop-ao-so-mi-trang-dai-cong-so", "ao-so-mi-tay-dai-vai-ngai-cuu-khang-khuan-the-ceo-001-trang-0022987",
     "ao-s-mi-non-iron-01-tr-ng-1174884279.jpg",
     {"name": "Sơ mi trắng dài tay công sở", "category": "top", "colors": ["trắng"], "material": "vải sợi ngải cứu",
      "style": "công sở", "warmth": 2, "formality": 4,
      "occasions": ["đi làm", "phỏng vấn", "đám cưới", "dự tiệc"],
      "notes": "Sơ mi trắng dài tay đứng phom: chuẩn cho đám cưới, phỏng vấn — đi cùng quần tây và giày da.",
      "description_en": "crisp white long-sleeve dress shirt with point collar"}),
    ("shop-quan-short-jean", "quan-dui-jeans-durabletex-mem-lung-cong-chong-ho-the-original-059-mau-xanh-nhat-phom-vua-van-f3-2625047", "TheOriginal059XanhJean871.jpg",
     {"name": "Quần short jean xanh", "category": "bottom", "colors": ["xanh dương"], "material": "jean",
      "style": "thường ngày", "warmth": 1, "occasions": ["đi chơi", "du lịch", "đi biển", "đi học"],
      "description_en": "medium-wash blue denim shorts"}),
    ("shop-quan-short-kaki-navy", "quan-short-kaki-ben-co-gian-the-weekend-030-xanh-duong-dam-0024661",
     "TheWeekend030XanhD_ngD_m24661thumb1.jpg",
     {"name": "Quần short kaki xanh navy", "category": "bottom", "colors": ["xanh navy"], "material": "kaki",
      "style": "thường ngày", "warmth": 1, "occasions": ["đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "navy khaki chino shorts, above the knee"}),
    ("shop-quan-short-linen-ke", "quan-short-kaki-loose-fit-airdry-linen-soc-min-thoang-the-no-style-202-trang-xanh-duong-0024971",
     "TheNoStyle202TrangXanhDuong1.jpg",
     {"name": "Quần short linen kẻ sọc trắng xanh", "category": "bottom", "colors": ["trắng", "xanh dương"],
      "pattern": "kẻ sọc", "material": "vải hiệu ứng linen", "style": "thường ngày", "warmth": 1,
      "occasions": ["đi chơi", "du lịch", "đi biển"],
      "description_en": "white and blue striped linen-look wide shorts"}),
    ("shop-quan-jean-xanh-dam", "quan-jeans-cloudtouch-be-mat-em-ai-giu-phom-dung-dang-the-original-057-mau-xanh-duong-dam-dang-vua-van-f3-0025062", "TheOriginal057XanhDuongDam1.jpg",
     {"name": "Quần jean ống đứng xanh đậm", "category": "bottom", "colors": ["xanh dương đậm"], "material": "jean",
      "style": "thường ngày", "warmth": 3, "formality": 3,
      "occasions": ["đi làm", "đi chơi", "hẹn hò", "đi học", "du lịch"],
      "description_en": "dark indigo straight-leg denim jeans"}),
    ("shop-quan-linen-be", "quan-jogger-relaxed-fit-airdry-vai-hieu-ung-linen-min-mat-the-no-style-218-be-0024928",
     "TheNoStyle218Be1.jpg",
     {"name": "Quần dài linen ống suông be", "category": "bottom", "colors": ["be"], "material": "rayon hiệu ứng linen",
      "style": "thanh lịch", "warmth": 1, "occasions": ["đi chơi", "hẹn hò", "du lịch", "đi biển"],
      "description_en": "beige relaxed straight-leg linen-look drawstring trousers"}),
    ("shop-quan-tay-xam", "quan-tay-smart-casual-it-nhan-the-ceo-018-xam-0024344", "24344_thumb_2.jpg",
     {"name": "Quần tây xám", "category": "bottom", "colors": ["xám"], "material": "vải tây co giãn",
      "style": "công sở", "warmth": 2, "occasions": ["đi làm", "phỏng vấn", "đám cưới", "dự tiệc"],
      "description_en": "light grey slim tailored dress trousers"}),
    ("shop-quan-kaki-navy", "quan-kaki-cloudtouch-vai-mem-em-da-day-mo-rong-thong-minh-thoai-mai-the-minimalist-018-mau-xanh-duong-dam-dang-vua-van-f3-0025012", "TheMinimalist018XanhDuongDam1.jpg",
     {"name": "Quần kaki công sở xanh navy", "category": "bottom", "colors": ["xanh navy"], "material": "kaki",
      "style": "công sở", "warmth": 2, "occasions": ["đi làm", "phỏng vấn", "hẹn hò", "đi chơi"],
      "description_en": "navy straight-leg chino trousers"}),
    ("shop-ao-khoac-chong-nang-navy", "ao-khoac-hoodie-zipper-airdry-vai-pindot-thong-hoi-upf-50-the-minimalist-004-xanh-duong-dam-0024760", "TheMinimalist004XanhDuongDam1.jpg",
     {"name": "Áo khoác chống nắng xanh navy", "category": "outerwear", "colors": ["xanh navy"], "rain_ok": False,
      "material": "thun pindot nhanh khô", "warmth": 2, "uv_protect": True,
      "occasions": ["đi chơi", "du lịch", "thể thao", "đi biển"],
      "notes": "Áo khoác mỏng chống nắng có mũ, nhanh khô — đi nắng, đi xe máy.",
      "description_en": "navy zip-up hooded sun-protection jacket, thin quick-dry fabric"}),
    ("shop-ao-khoac-gio-navy", "ao-khoac-gio-truot-nuoc-seventy-seven-05-xanh-den-0023310", "ao-khoac-seventy-seven-05-xanh-den-1174884642.jpg",
     {"name": "Áo khoác gió xanh navy", "category": "outerwear", "colors": ["xanh navy"],
      "material": "vải dù trượt nước", "warmth": 2, "rain_ok": True,
      "occasions": ["đi làm", "đi chơi", "du lịch", "thể thao"],
      "notes": "Áo gió mỏng trượt nước, cản gió — mang theo khi trời có thể mưa.",
      "description_en": "navy lightweight hooded windbreaker jacket"}),
    ("shop-ao-khoac-overshirt-kaki-be", "ao-so-mi-tay-dai-kaki-co-gian-no-style-m64-be-0023510", "ao-s-mi-no-style-m64-be-1174878757.jpg",
     {"name": "Áo khoác sơ mi kaki be", "category": "outerwear", "subtype": "overshirt", "colors": ["be"],
      "material": "kaki wash", "style": "thường ngày", "warmth": 2, "formality": 3,
      "occasions": ["đi chơi", "hẹn hò", "du lịch", "đi học"],
      "notes": "Overshirt kaki mặc mở cúc ngoài áo thun — sáng sớm, phòng lạnh, ngày râm mát.",
      "description_en": "beige long-sleeve khaki overshirt with two flap chest pockets"}),
    ("shop-ao-blazer-xam", "ao-khoac-blazer-dang-rong-no-style-m67-xam-dam-0023508", "0023508_thumb_1.jpg",
     {"name": "Áo blazer xám đậm", "category": "outerwear", "colors": ["xám đậm"], "material": "vải tây",
      "fabric": "poly",
      "style": "thanh lịch", "warmth": 3, "formality": 5,
      "occasions": ["đám cưới", "dự tiệc", "phỏng vấn", "đi làm", "hẹn hò"],
      "notes": "Blazer một hàng khuy: khoác cho tiệc cưới, phỏng vấn trong phòng máy lạnh.",
      "description_en": "charcoal grey single-breasted blazer"}),
    ("shop-giay-sneaker-be", "giay-sneaker-mien-gio-cat-018-0022533", "GiaySneakerMi_nGioCat018.jpg",
     {"name": "Giày sneaker be", "category": "shoes", "colors": ["be"], "material": "da", "fabric": "leather",
      "rain_ok": True,
      "style": "thường ngày", "occasions": ["đi chơi", "đi học", "du lịch", "hẹn hò", "thể thao"],
      "description_en": "beige low-top lace-up sneakers"}),
    ("shop-dep-slide-den", "dep-sieu-nhe-slide-slipper-y2010-01-den-0023594", "dep-y2010-01-den-1174880279.jpg",
     {"name": "Dép slide siêu nhẹ đen", "category": "shoes", "colors": ["đen"], "material": "eva",
      "fabric": "rubber", "style": "thường ngày", "warmth": 1,
      "occasions": ["đi biển", "ở nhà", "du lịch", "thể thao"],
      "notes": "Dép slide EVA siêu nhẹ, không sợ nước — đi biển, đi bơi, sau buổi tập.",
      "description_en": "black lightweight EVA slide sandals"}),
    ("shop-mu-luoi-trai-be", "non-outdoor-phoi-mau-vai-du-nhan-sieu-nhe-truot-nuoc-chill-mode-on-y2010-208-mau-begie-2625222", "NonOutdoor_Y2010208Tr_ng1.png",
     {"name": "Mũ lưỡi trai dù be", "category": "accessory", "subtype": "cap", "colors": ["kem", "nâu nhạt"],
      "material": "vải dù trượt nước", "style": "năng động", "warmth": 1, "rain_ok": True, "uv_protect": True,
      "occasions": ["đi chơi", "du lịch", "đi biển", "thể thao"],
      "description_en": "cream and khaki water-repellent baseball cap"}),
    ("shop-that-lung-da-den", "day-nit-da-bo-y-matte-black-khoa-tu-dong-y2010-235-0025009", "Y2010235Den1.jpg",
     {"name": "Thắt lưng da đen", "category": "accessory", "subtype": "belt", "colors": ["đen"], "material": "da bò",
      "style": "công sở", "warmth": 1, "formality": 4,
      "occasions": ["đi làm", "đám cưới", "phỏng vấn", "dự tiệc"],
      "description_en": "black matte leather belt with automatic buckle"}),
]

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = REQUEST_GAP_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def fetch_image(url: str) -> bytes:
    """Product photo from the Shopify CDN (cdn.shopify.com), normal browser UA, ≤1 req/s."""
    _throttle()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*;q=0.8"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return response.read()


def fetch_product_json(handle: str) -> dict:
    """Public Shopify product JSON. Uses the `curl` CLI: yame.vn answers Python HTTP/1.1 clients with
    429 (bot heuristic), and we do not try to work around that — curl with a normal UA is answered
    normally, at ≤1 req/s."""
    url = f"{SHOP}/products/{handle}.json"
    for attempt in range(3):  # transient DNS/network errors: retry politely (5 s, 15 s)
        _throttle()
        result = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", "40", "-A", USER_AGENT, "-H", "Accept: application/json", url],
            capture_output=True, check=False,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)["product"]
        print(f"  ! {handle}: curl lỗi {result.returncode}, thử lại")
        time.sleep(5 + attempt * 10)
    raise RuntimeError(f"không đọc được {url}")


def update_snapshot() -> dict:
    """Re-read the selected products from the shop and keep only the fields we use."""
    snapshot = {}
    for item_id, handle, _image, _fixed in SELECTION:
        product = fetch_product_json(handle)
        snapshot[handle] = {
            "title": product["title"],
            "product_type": product.get("product_type"),
            "variants": [{k: v.get(k) for k in ("sku", "price", "compare_at_price", "available", "title")}
                         for v in product.get("variants") or []],
            "images": [img["src"] for img in product.get("images") or []],
        }
        print(f"  dữ liệu {item_id}: {product['title'][:70]}…")
    snapshot = {"fetched_at": time.strftime("%Y-%m-%d"), "source": f"{SHOP}/products/<handle>.json",
                "products": snapshot}
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return snapshot


def save_image(item_id: str, url: str) -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    jpeg = to_jpeg(fetch_image(url), max_side=1024, quality=88)
    tmp = IMAGES_DIR / f".{item_id}.tmp"
    tmp.write_bytes(jpeg)
    tmp.replace(IMAGES_DIR / f"{item_id}.jpg")
    print(f"  ảnh {item_id}.jpg ({len(jpeg) // 1024} KB)")


def download_missing() -> int:
    rows = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))
    missing = [row for row in rows if not (IMAGES_DIR / f"{row['id']}.jpg").is_file()]
    print(f"{len(rows)} món, thiếu {len(missing)} ảnh")
    for row in missing:
        save_image(row["id"], row["image_source_url"])
    return len(missing)


def product_info(product: dict, handle: str, image_name: str) -> dict:
    variants = product.get("variants") or [{}]
    variant = next((v for v in variants if v.get("available")), variants[0])
    image = next((src for src in product["images"] if src.split("?")[0].endswith("/" + image_name)), None)
    if image is None:
        print(f"  ! {handle}: không thấy ảnh {image_name}, dùng ảnh đầu tiên")
        image = product["images"][0]
    sku = str(variant.get("sku") or "")
    return {
        "brand": BRAND,
        "product_name": product["title"],
        "product_url": f"{SHOP}/products/{handle}",
        "product_code": sku[:-3] if len(sku) > 3 else sku,  # SKU without the size suffix
        "price_vnd": int(float(variant.get("price") or 0)),
        "image_source_url": image,
    }


def finish_row(row: dict, *, product_type: str = "") -> dict:
    """Hand fixes applied → derive garment attributes (garment_attrs; hand-set values win, colour from photo)."""
    from garment_attrs import ATTR_FIELDS, fill_attrs

    fixed = next((f for i, _h, _img, f in SELECTION if i == row["id"]), {})
    image = IMAGES_DIR / f"{row['id']}.jpg"
    jpeg = image.read_bytes() if image.is_file() else None
    probe = {k: v for k, v in row.items() if k not in ("color_family", "attrs_v", "color_hex")}
    if product_type:
        probe["product_type"] = product_type
    # rules from the (hand-fixed) name + shop title beat the tagger's guesses; hand-set attributes win last
    filled = fill_attrs(probe, jpeg, mode="rules_first")
    filled.update({k: fixed[k] for k in ATTR_FIELDS if k in fixed})
    filled.pop("product_type", None)
    return filled


async def refresh(only: set[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv(BACKEND.parent / ".env", override=False)
    from garment_attrs import tag_item_with_attrs
    from providers import build_provider_from_env, vision_model

    provider = build_provider_from_env()
    print("mode", provider.mode, "model", vision_model())
    rows = []
    previous = {r["id"]: r for r in json.loads(ITEMS_PATH.read_text(encoding="utf-8"))} if only else {}
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["products"]
    for item_id, handle, image_name, fixed in SELECTION:
        if only and item_id not in only and item_id in previous:
            rows.append(previous[item_id])
            continue
        product = snapshot[handle]
        info = product_info(product, handle, image_name)
        save_image(item_id, info["image_source_url"])
        jpeg = (IMAGES_DIR / f"{item_id}.jpg").read_bytes()
        started = time.monotonic()
        # no hints: the model sees only the photo; shop title/type only feed the deterministic attribute rules
        tags, source = await tag_item_with_attrs(
            provider, jpeg, extra_text={"product_name": info["product_name"],
                                        "product_type": product.get("product_type") or ""})
        print(f"{item_id}: {time.monotonic() - started:.1f}s source={source} tagger -> {tags['name']} | "
              f"{tags['category']} {tags['colors']} w={tags['warmth']} f={tags.get('formality')} "
              f"{tags.get('fabric')} {tags['occasions']}")
        fields = {**tags, **fixed}
        fields["seasons"] = fixed.get("seasons") or ["hạ"] + [s for s in tags.get("seasons", []) if s != "hạ"]
        row = {"id": item_id, **fields, **info, "tagger_name": tags["name"], "tagger_source": source}
        rows.append(finish_row(row, product_type=product.get("product_type") or ""))
    await provider.aclose()
    ITEMS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_sources(rows)
    print(f"đã ghi {ITEMS_PATH.name}: {len(rows)} món")


def apply_fixes() -> None:
    rows = {row["id"]: row for row in json.loads(ITEMS_PATH.read_text(encoding="utf-8"))}
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["products"] if SNAPSHOT_PATH.is_file() else {}
    out = []
    for item_id, handle, _image, fixed in SELECTION:
        ptype = (snapshot.get(handle) or {}).get("product_type") or ""
        out.append(finish_row({**rows[item_id], **fixed}, product_type=ptype))
    ITEMS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_sources(out)
    print(f"đã áp lại sửa tay + thuộc tính cho {len(out)} món")


CATEGORY_VI = {"top": "áo", "bottom": "quần", "outerwear": "áo khoác", "shoes": "giày dép", "accessory": "phụ kiện"}
SOURCES_PATH = HERE / "SOURCES.md"
SOURCES_INTRO = """# Tủ đồ mẫu “đồ nam mùa hè” (YaMe) — nguồn ảnh & dữ liệu

**© YaMe (yame.vn) — ảnh sản phẩm chỉ dùng cho demo nội bộ của nhóm, không phân phối lại.**
Ảnh **không** được commit (`.gitignore`: `backend/wardrobe_seed_shop/images/`); tải lại bằng
`cd backend/wardrobe_seed_shop && ../../.venv/bin/python build_seed_shop.py`.

Dữ liệu lấy ngày {date} từ JSON sản phẩm công khai của cửa hàng Shopify chính thức
`https://yame.vn/products/<handle>.json` (tên, loại, giá VND, SKU, danh sách ảnh — lưu ở `products_snapshot.json`;
danh sách ứng viên chọn từ `https://yame.vn/products.json`), User-Agent trình duyệt bình thường (curl),
≤1 request/giây, không đăng nhập. Ảnh: CDN `cdn.shopify.com` của shop, chọn tay ảnh sản phẩm phẳng nền trắng/xám
nhạt (không ảnh người mẫu, không ảnh infographic), chuẩn hoá JPEG tối đa 1024px. Nhãn do `qwen3-vl-8b-instruct` gắn
(`build_seed_shop.py --refresh`, mỗi ảnh 1 lần, không gợi ý), thuộc tính (màu hex, độ trang trọng, phom, tay, chất
liệu, độ thoáng, đi mưa, chống nắng) suy ra bằng `garment_attrs.py` từ ảnh + tên/loại sản phẩm trên shop, sau đó
sửa tay tên/màu/loại/dịp/thuộc tính theo dữ liệu chính thức (trường `tagger_name` giữ tên AI đặt ban đầu).

Vì sao không phải UNIQLO: trang uniqlo.com (VN và các vùng khác) trả *Access Denied* (Akamai) cho mọi request
không phải trình duyệt thật, còn API `/{{region}}/api/commerce/v5/...` đòi client id lấy từ chính các trang đó —
không vượt rào, nên chuyển sang YaMe (shop đồ nam Việt Nam, Shopify công khai `/products.json`).

| id | loại | tên trên shop | màu | giá (VND) | mã | trang sản phẩm | ảnh gốc |
|---|---|---|---|---|---|---|---|
"""


def write_sources(rows: list[dict]) -> None:
    fetched = "2026-11-09"
    if SNAPSHOT_PATH.is_file():
        fetched = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")).get("fetched_at") or fetched
    lines = [SOURCES_INTRO.format(date=fetched)]
    for row in rows:
        price = f"{row['price_vnd']:,}".replace(",", ".")
        lines.append(f"| {row['id']} | {CATEGORY_VI.get(row['category'], row['category'])} | "
                     f"{row['product_name'].replace('|', '/')} | {', '.join(row['colors'])} | {price} | "
                     f"{row['product_code']} | {row['product_url']} | {row['image_source_url']} |\n")
    SOURCES_PATH.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="tải lại toàn bộ ảnh + gắn nhãn Qwen-VL, ghi items.json")
    parser.add_argument("--update-products", action="store_true",
                        help="đọc lại tên/giá/ảnh từ shop (products_snapshot.json) trước khi --refresh")
    parser.add_argument("--only", default="",
                        help="với --refresh: chỉ tải/gắn nhãn lại các id này (phẩy), giữ nguyên các món khác")
    parser.add_argument("--apply-fixes", action="store_true",
                        help="chỉ áp lại phần sửa tay trong SELECTION lên items.json (không mạng, không AI)")
    args = parser.parse_args()
    if args.apply_fixes:
        apply_fixes()
        return
    if args.update_products or not SNAPSHOT_PATH.is_file():
        update_snapshot()
    if args.refresh or args.update_products or not ITEMS_PATH.is_file():
        asyncio.run(refresh({x.strip() for x in args.only.split(",") if x.strip()} or None))
    else:
        download_missing()


if __name__ == "__main__":
    main()

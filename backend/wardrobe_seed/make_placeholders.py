"""Generate the flat placeholder garment images of the demo wardrobe (Pillow only).

Run once: ../../.venv/bin/python make_placeholders.py  → images/demo-*.jpg (CC0, made for SKI).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "images"
W, H, S = 768, 1024, 2  # draw at 2x then downscale for anti-aliasing


def canvas(bg=(244, 244, 242)):
    image = Image.new("RGB", (W * S, H * S), bg)
    return image, ImageDraw.Draw(image)


def pts(points):
    return [(x * S, y * S) for x, y in points]


def finish(image, name):
    image = image.resize((W, H), Image.LANCZOS).filter(ImageFilter.SMOOTH)
    image.save(OUT / name, quality=90)


def trousers(name, body, dark, light, stitch=None, crease=False):
    image, d = canvas()
    waist = [(214, 150), (554, 150), (560, 210), (208, 210)]
    left = [(208, 210), (384, 210), (392, 330), (352, 900), (226, 900), (196, 420)]
    right = [(384, 210), (560, 210), (572, 420), (542, 900), (416, 900), (376, 330)]
    for poly in (left, right):
        d.polygon(pts(poly), fill=body, outline=dark)
    d.polygon(pts(waist), fill=dark)
    for x in (250, 330, 438, 518):
        d.rectangle(pts([(x, 150), (x + 12, 214)]), fill=body)  # belt loops
    d.line(pts([(384, 210), (384, 330)]), fill=dark, width=3 * S)  # fly
    if stitch:
        d.line(pts([(372, 214), (372, 312), (384, 330)]), fill=stitch, width=2 * S)
        d.arc(pts([(214, 180), (330, 300)]), 0, 90, fill=stitch, width=2 * S)
        d.arc(pts([(438, 180), (554, 300)]), 90, 180, fill=stitch, width=2 * S)
        d.line(pts([(226, 880), (352, 880)]), fill=stitch, width=2 * S)
        d.line(pts([(416, 880), (542, 880)]), fill=stitch, width=2 * S)
    if crease:
        d.line(pts([(296, 230), (289, 890)]), fill=light, width=2 * S)
        d.line(pts([(472, 230), (479, 890)]), fill=light, width=2 * S)
    else:
        d.line(pts([(300, 360), (292, 860)]), fill=light, width=6 * S)
        d.line(pts([(468, 360), (476, 860)]), fill=light, width=6 * S)
    finish(image, name)


def skirt(name, body, dark, light):
    image, d = canvas()
    d.rectangle(pts([(254, 190), (514, 246)]), fill=dark)
    hem_l, hem_r = 150, 618
    d.polygon(pts([(254, 246), (514, 246), (hem_r, 780), (hem_l, 780)]), fill=body, outline=dark)
    for i in range(1, 10):
        top_x = 254 + (514 - 254) * i / 10
        bot_x = hem_l + (hem_r - hem_l) * i / 10
        d.line(pts([(top_x, 250), (bot_x, 778)]), fill=light if i % 2 else dark, width=2 * S)
    finish(image, name)


def sneakers(name):
    image, d = canvas(bg=(226, 228, 232))
    outline = (140, 140, 148)
    for dx, dy in ((120, 250), (150, 560)):
        # side view, heel on the left, toe on the right
        upper = [(dx, dy + 200), (dx + 6, dy + 70), (dx + 40, dy + 40), (dx + 130, dy + 60),
                 (dx + 200, dy + 95), (dx + 330, dy + 120), (dx + 440, dy + 140), (dx + 500, dy + 170),
                 (dx + 515, dy + 200)]
        d.polygon(pts(upper), fill=(252, 252, 252), outline=outline)
        d.rounded_rectangle(pts([(dx - 6, dy + 195), (dx + 522, dy + 235)]), radius=14 * S,
                            fill=(214, 214, 214), outline=outline)
        d.line(pts([(dx, dy + 212), (dx + 516, dy + 212)]), fill=(185, 185, 190), width=3 * S)
        d.polygon(pts([(dx + 420, dy + 137), (dx + 500, dy + 170), (dx + 512, dy + 196), (dx + 400, dy + 196)]),
                  fill=(238, 238, 240), outline=outline)  # toe cap
        d.polygon(pts([(dx + 6, dy + 70), (dx + 40, dy + 40), (dx + 70, dy + 50), (dx + 60, dy + 196),
                       (dx + 2, dy + 196)]), fill=(236, 236, 238), outline=outline)  # heel counter
        for i in range(5):
            x = dx + 170 + i * 40
            y = dy + 88 + i * 9
            d.line(pts([(x, y), (x + 30, y + 22)]), fill=(110, 110, 120), width=5 * S)
            d.line(pts([(x + 30, y), (x, y + 22)]), fill=(150, 150, 160), width=3 * S)
        d.line(pts([(dx + 130, dy + 70), (dx + 380, dy + 190)]), fill=(200, 200, 205), width=4 * S)  # side stripe
    finish(image, name)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    trousers("demo-quan-jean-xanh.jpg", (66, 98, 146), (40, 62, 98), (98, 132, 180), stitch=(201, 162, 90))
    trousers("demo-quan-au-den.jpg", (38, 38, 42), (18, 18, 20), (78, 78, 84), crease=True)
    skirt("demo-chan-vay-be.jpg", (216, 196, 160), (176, 154, 118), (232, 216, 186))
    sneakers("demo-giay-sneaker-trang.jpg")

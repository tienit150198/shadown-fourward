"""Image helpers shared by the real CatVTON backend and the mock backend.

Only depends on Pillow so it can be imported (and tested) without torch.
`resize_and_crop` / `resize_and_padding` are ported from CatVTON `utils.py`
(https://github.com/Zheng-Chong/CatVTON, CC BY-NC-SA 4.0).
"""

from __future__ import annotations

import base64
import binascii
import io
import re

from PIL import Image, ImageFilter, ImageOps

# Decompression-bomb guard: refuse images with more pixels than this *before* decoding them
# (a tiny PNG can declare 50000x50000). Also lowers Pillow's own bomb threshold.
MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[\w/+.-]+)?(?:;[\w=.-]+)*?;base64,(?P<data>.*)$", re.DOTALL)


class ImageDecodeError(ValueError):
    """Raised when a request image cannot be decoded."""


def decode_image(value: str, *, max_bytes: int, field: str = "image") -> Image.Image:
    """Decode a `data:image/...;base64,...` URL (or bare base64) into an RGB image.

    EXIF orientation is applied so phone photos are upright.
    """
    if not isinstance(value, str) or not value.strip():
        raise ImageDecodeError(f"{field}: empty")
    value = value.strip()
    match = _DATA_URL_RE.match(value)
    payload = match.group("data") if match else value
    payload = re.sub(r"\s+", "", payload)
    # Rough pre-check before decoding (base64 is ~4/3 of the raw size).
    if len(payload) * 3 // 4 > max_bytes:
        raise ImageDecodeError(f"{field}: larger than {max_bytes // (1024 * 1024)} MB")
    try:
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageDecodeError(f"{field}: invalid base64 ({exc})") from exc
    if len(raw) > max_bytes:
        raise ImageDecodeError(f"{field}: larger than {max_bytes // (1024 * 1024)} MB")
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception as exc:  # PIL raises many different exception types
        raise ImageDecodeError(f"{field}: not a valid image ({exc})") from exc
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise ImageDecodeError(f"{field}: too many pixels ({img.width}x{img.height})")
    try:
        img.load()
    except Exception as exc:  # PIL raises many different exception types
        raise ImageDecodeError(f"{field}: not a valid image ({exc})") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        # Garment cut-outs often come as transparent PNGs: flatten onto white,
        # which is what CatVTON's garment padding uses as well.
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img.convert("RGB")


def encode_jpeg_data_url(img: Image.Image, quality: int = 92) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def resize_and_crop(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Center-crop to the target aspect ratio, then resize (CatVTON utils.resize_and_crop)."""
    w, h = image.size
    target_w, target_h = size
    if w / h < target_w / target_h:
        new_w = w
        new_h = w * target_h // target_w
    else:
        new_h = h
        new_w = h * target_w // target_h
    image = image.crop(((w - new_w) // 2, (h - new_h) // 2, (w + new_w) // 2, (h + new_h) // 2))
    return image.resize(size, Image.LANCZOS)


def resize_and_padding(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Fit inside the target size and pad with white (CatVTON utils.resize_and_padding)."""
    w, h = image.size
    target_w, target_h = size
    if w / h < target_w / target_h:
        new_h = target_h
        new_w = w * target_h // h
    else:
        new_w = target_w
        new_h = h * target_w // w
    image = image.resize((max(1, new_w), max(1, new_h)), Image.LANCZOS)
    padding = Image.new("RGB", size, (255, 255, 255))
    padding.paste(image, ((target_w - new_w) // 2, (target_h - new_h) // 2))
    return padding


def soften_mask(mask: Image.Image, blur_factor: int = 9) -> Image.Image:
    """Same as diffusers VaeImageProcessor.blur(mask, blur_factor=9) used by CatVTON's app."""
    return mask.convert("L").filter(ImageFilter.GaussianBlur(blur_factor))


def binarize_mask(mask: Image.Image, threshold: int = 128) -> Image.Image:
    return mask.convert("L").point(lambda p: 255 if p >= threshold else 0)


def repaint(result: Image.Image, person: Image.Image, mask: Image.Image) -> Image.Image:
    """Keep original pixels outside the (soft) mask - CatVTON utils.repaint_result, PIL-only."""
    mask = mask.convert("L").resize(result.size)
    person = person.convert("RGB").resize(result.size)
    return Image.composite(result.convert("RGB"), person, mask)


def box_mask(size: tuple[int, int], category: str) -> Image.Image:
    """Crude rectangular agnostic mask. Used by TRYON_MASKER=box and the mock backend.

    Assumes a roughly centered, upright, full-body (or knee-up) photo.
    """
    w, h = size
    boxes = {
        "upper": (0.12, 0.16, 0.88, 0.62),
        "lower": (0.18, 0.45, 0.82, 0.97),
        "overall": (0.12, 0.16, 0.88, 0.97),
    }
    x0, y0, x1, y1 = boxes[category]
    mask = Image.new("L", size, 0)
    mask.paste(255, (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    return mask

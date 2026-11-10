"""Deterministic, offline image quality checks for wardrobe and VTON images.

The checks are deliberately conservative: garment heuristics return warnings rather
than rejecting an upload.  Hard rejection belongs to decode/security validation.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageFilter, ImageStat


class ImageQualityError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


WARNING_MESSAGES = {
    "low_resolution": "Ảnh hơi nhỏ; nên dùng ảnh có cạnh ngắn từ 512 px.",
    "blurred": "Ảnh thiếu chi tiết hoặc bị mờ; hãy chụp rõ họa tiết và đường viền.",
    "underexposed": "Ảnh quá tối; hãy tăng ánh sáng đều lên món đồ.",
    "overexposed": "Ảnh quá sáng; hãy tránh cháy sáng làm mất chi tiết vải.",
    "cropped": "Món đồ chạm mép ảnh; hãy chụp đủ toàn bộ món đồ.",
    "busy_background": "Nền chưa đồng đều; nền trắng hoặc trong suốt sẽ cho kết quả tốt hơn.",
}


def _warning(code: str) -> dict[str, str]:
    return {"code": code, "message": WARNING_MESSAGES[code]}


def _border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    px = image.load()
    stride = max(1, min(width, height) // 160)
    border = [px[x, 0] for x in range(0, width, stride)]
    border += [px[x, height - 1] for x in range(0, width, stride)]
    border += [px[0, y] for y in range(0, height, stride)]
    border += [px[width - 1, y] for y in range(0, height, stride)]
    return border


def _median_rgb(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    return tuple(sorted(pixel[channel] for pixel in pixels)[len(pixels) // 2] for channel in range(3))


def _foreground_bbox(image: Image.Image, background: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    sample = image.copy()
    sample.thumbnail((256, 256))
    sx, sy = sample.size
    scale_x, scale_y = image.width / sx, image.height / sy
    mask = Image.new("L", sample.size)
    pixels = sample.load()
    values = []
    for y in range(sy):
        for x in range(sx):
            red, green, blue = pixels[x, y]
            distance = ((red - background[0]) ** 2 + (green - background[1]) ** 2 + (blue - background[2]) ** 2) ** 0.5
            values.append(255 if distance >= 35 else 0)
    mask.putdata(values)
    box = mask.getbbox()
    if not box:
        return None
    return (
        int(box[0] * scale_x), int(box[1] * scale_y),
        min(image.width, int(box[2] * scale_x)), min(image.height, int(box[3] * scale_y)),
    )


def inspect_garment_image(jpeg: bytes) -> dict[str, Any]:
    """Return conservative garment-photo warnings without rejecting the upload."""
    with Image.open(io.BytesIO(jpeg)) as source:
        image = source.convert("RGB")
    width, height = image.size
    analysis = image.copy()
    analysis.thumbnail((512, 512))
    gray = analysis.convert("L")
    stats = ImageStat.Stat(gray)
    mean = stats.mean[0]
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = ImageStat.Stat(edges).mean[0]

    border = _border_pixels(analysis)
    background = _median_rgb(border)
    deviations = [sum(abs(pixel[i] - background[i]) for i in range(3)) / 3 for pixel in border]
    background_noise = sum(deviations) / max(1, len(deviations))
    bbox = _foreground_bbox(image, background)

    codes: list[str] = []
    if min(width, height) < 512:
        codes.append("low_resolution")
    if edge_mean < 2.0:
        codes.append("blurred")
    if mean < 28:
        codes.append("underexposed")
    elif mean > 242:
        codes.append("overexposed")
    if bbox:
        margin_x = max(2, round(width * 0.015))
        margin_y = max(2, round(height * 0.015))
        if bbox[0] <= margin_x or bbox[1] <= margin_y or bbox[2] >= width - margin_x or bbox[3] >= height - margin_y:
            codes.append("cropped")
    if background_noise > 24:
        codes.append("busy_background")

    warnings = [_warning(code) for code in dict.fromkeys(codes)]
    photo_type = "flat-lay" if bbox and background_noise <= 24 else "unknown"
    score = max(0, 100 - 18 * len(warnings))
    return {
        "status": "pass" if not warnings else "warn",
        "score": score,
        "width": width,
        "height": height,
        "garment_photo_type": photo_type,
        "warnings": warnings,
    }


def validate_generated_image(raw: bytes, *, min_width: int = 384, min_height: int = 512) -> dict[str, Any]:
    """Hard technical gate for provider output; does not judge fashion quality."""
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.verify()
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
    except Exception as error:
        raise ImageQualityError("decode_failed", "Ảnh kết quả không giải mã được.") from error
    width, height = image.size
    if width < min_width or height < min_height:
        raise ImageQualityError("too_small", f"Ảnh kết quả chỉ có {width}×{height} px.")
    analysis = image.copy()
    analysis.thumbnail((512, 512))
    gray = analysis.convert("L")
    gray_stats = ImageStat.Stat(gray)
    mean = gray_stats.mean[0]
    spread = gray_stats.stddev[0]
    if mean < 8 and spread < 3:
        raise ImageQualityError("near_black", "Ảnh kết quả gần như toàn màu đen.")
    if mean > 248 and spread < 3:
        raise ImageQualityError("near_white", "Ảnh kết quả gần như toàn màu trắng.")
    inset_x = max(1, analysis.width // 50)
    inset_y = max(1, analysis.height // 50)
    centre = gray.crop((inset_x, inset_y, analysis.width - inset_x, analysis.height - inset_y))
    if ImageStat.Stat(centre).stddev[0] < 1.5:
        raise ImageQualityError("blank", "Ảnh kết quả không có đủ chi tiết để hiển thị.")
    return {"status": "pass", "width": width, "height": height}

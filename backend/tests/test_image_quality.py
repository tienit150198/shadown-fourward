from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _jpeg(*, size=(640, 800), background=(245, 245, 245), box=(150, 100, 490, 700), fill=(150, 30, 40)) -> bytes:
    image = Image.new("RGB", size, background)
    if box:
        draw = ImageDraw.Draw(image)
        draw.rectangle(box, fill=fill)
        for y in range(box[1] + 20, box[3], 24):
            draw.line((box[0] + 20, y, box[2] - 20, y), fill=(220, 180, 180), width=3)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92)
    return output.getvalue()


def _codes(report: dict) -> set[str]:
    return {warning["code"] for warning in report["warnings"]}


def test_garment_quality_accepts_a_clean_product_photo():
    from image_quality import inspect_garment_image

    report = inspect_garment_image(_jpeg())

    assert report["status"] == "pass"
    assert report["width"] == 640 and report["height"] == 800
    assert report["garment_photo_type"] == "flat-lay"
    assert report["warnings"] == []


def test_garment_quality_warns_for_low_resolution_and_flat_blurry_image():
    from image_quality import inspect_garment_image

    report = inspect_garment_image(_jpeg(size=(240, 320), background=(120, 120, 120), box=None))

    assert report["status"] == "warn"
    assert {"low_resolution", "blurred"} <= _codes(report)


def test_garment_quality_warns_for_extreme_exposure():
    from image_quality import inspect_garment_image

    assert "underexposed" in _codes(inspect_garment_image(_jpeg(background=(4, 4, 4), box=None)))
    assert "overexposed" in _codes(inspect_garment_image(_jpeg(background=(252, 252, 252), box=None)))


def test_garment_quality_warns_when_foreground_touches_canvas_edge():
    from image_quality import inspect_garment_image

    report = inspect_garment_image(_jpeg(box=(0, 80, 520, 740)))

    assert "cropped" in _codes(report)


def test_garment_quality_does_not_call_a_textured_black_or_white_garment_blank():
    from image_quality import inspect_garment_image

    black = inspect_garment_image(_jpeg(fill=(8, 8, 8)))
    white = inspect_garment_image(_jpeg(background=(190, 190, 190), fill=(248, 248, 248)))

    assert "underexposed" not in _codes(black)
    assert "overexposed" not in _codes(white)
    assert "blurred" not in _codes(black) | _codes(white)


def test_generated_image_validator_rejects_corrupt_small_and_blank_outputs():
    import pytest

    from image_quality import ImageQualityError, validate_generated_image

    with pytest.raises(ImageQualityError, match="decode_failed"):
        validate_generated_image(b"not-an-image")
    with pytest.raises(ImageQualityError, match="too_small"):
        validate_generated_image(_jpeg(size=(200, 300)))
    with pytest.raises(ImageQualityError, match="near_black"):
        validate_generated_image(_jpeg(background=(2, 2, 2), box=None))
    with pytest.raises(ImageQualityError, match="near_white"):
        validate_generated_image(_jpeg(background=(254, 254, 254), box=None))
    with pytest.raises(ImageQualityError, match="blank"):
        validate_generated_image(_jpeg(background=(120, 120, 120), box=None))


def test_generated_image_validator_accepts_textured_black_and_white_clothing():
    from image_quality import validate_generated_image

    black = validate_generated_image(_jpeg(fill=(8, 8, 8)))
    white = validate_generated_image(_jpeg(background=(190, 190, 190), fill=(248, 248, 248)))

    assert black["status"] == "pass" and white["status"] == "pass"

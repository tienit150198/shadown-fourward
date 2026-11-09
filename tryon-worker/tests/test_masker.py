"""Mask logic tests on synthetic SCHP label maps (needs numpy + opencv; skipped otherwise)."""

from __future__ import annotations

import os
import sys

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import masker  # noqa: E402
from masker import ATR_MAPPING as A  # noqa: E402
from masker import LIP_MAPPING as L  # noqa: E402

H, W = 1024, 768


def figure():
    """A crude front-facing person: hair, face, top, two arms with hands, pants, legs, shoes."""
    lip = np.zeros((H, W), np.uint8)
    atr = np.zeros((H, W), np.uint8)

    def paint(y0, y1, x0, x1, name):
        lip[y0:y1, x0:x1] = L[name]
        atr[y0:y1, x0:x1] = A[name]

    paint(40, 110, 330, 440, "Hair")
    paint(100, 200, 340, 430, "Face")
    paint(220, 560, 260, 510, "Upper-clothes")
    paint(230, 640, 200, 258, "Right-arm")   # image-left arm, hand at the bottom (y 560-640)
    paint(230, 640, 512, 570, "Left-arm")
    paint(560, 860, 270, 500, "Pants")
    paint(860, 960, 280, 370, "Right-leg")
    paint(860, 960, 400, 490, "Left-leg")
    paint(960, 1010, 270, 375, "Right-shoe")
    paint(960, 1010, 395, 500, "Left-shoe")
    return lip, atr


def frac(mask, y0, y1, x0, x1):
    return float((mask[y0:y1, x0:x1] > 0).mean())


def test_upper_mask():
    lip, atr = figure()
    m = masker.agnostic_mask_from_parsing(lip, atr, "upper")
    assert m.shape == (H, W) and m.dtype == np.uint8 and set(np.unique(m)) <= {0, 255}
    assert frac(m, 260, 520, 300, 470) > 0.98      # torso garment
    assert frac(m, 280, 450, 210, 250) > 0.9       # upper arm (sleeve area)
    assert frac(m, 120, 190, 350, 420) == 0.0      # face protected
    assert frac(m, 650, 840, 300, 470) < 0.05      # pants protected
    assert frac(m, 610, 640, 205, 253) < 0.2       # hand tip protected
    assert frac(m, 965, 1005, 280, 370) == 0.0     # shoes protected


def test_lower_mask():
    lip, atr = figure()
    m = masker.agnostic_mask_from_parsing(lip, atr, "lower")
    assert frac(m, 600, 840, 300, 470) > 0.98      # pants
    assert frac(m, 260, 500, 300, 470) < 0.05      # top protected
    assert frac(m, 120, 190, 350, 420) == 0.0      # face protected
    assert frac(m, 300, 500, 205, 253) < 0.05      # arms protected


def test_overall_mask_covers_both():
    lip, atr = figure()
    m = masker.agnostic_mask_from_parsing(lip, atr, "overall")
    assert frac(m, 260, 520, 300, 470) > 0.98
    assert frac(m, 600, 840, 300, 470) > 0.98
    assert frac(m, 120, 190, 350, 420) == 0.0


def test_empty_parsing_gives_empty_mask():
    z = np.zeros((H, W), np.uint8)
    assert masker.agnostic_mask_from_parsing(z, z, "upper").max() == 0


def test_bad_inputs():
    lip, atr = figure()
    with pytest.raises(ValueError):
        masker.agnostic_mask_from_parsing(lip, atr, "shoes")
    with pytest.raises(ValueError):
        masker.agnostic_mask_from_parsing(lip, atr[:-1], "upper")


def test_hands_estimate_is_at_arm_tips():
    lip, atr = figure()
    hands = masker.estimate_hands_feet(lip, atr)
    assert hands[600:640, 200:258].mean() > 0.5
    assert hands[240:400, 200:258].mean() == 0.0

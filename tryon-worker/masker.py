"""Cloth-agnostic mask generation for CatVTON without detectron2.

CatVTON's official AutoMasker (model/cloth_masker.py) combines three predictions:
  * SCHP-LIP and SCHP-ATR human parsing  (plain PyTorch ResNet-101 -> works on macOS)
  * DensePose R50-FPN                    (needs detectron2 -> painful on macOS arm64; the copy vendored
                                          in the CatVTON repo ships a Linux x86_64 `_C.so`)

This module keeps the SCHP part unchanged and replaces the two things DensePose was used for with
approximations computed from the SCHP label maps:
  1. "hands/feet" strong-protect area -> the distal tip of each arm / lowest tip of each leg segment,
     with a radius proportional to the face height (a hand is roughly face-sized);
  2. "dense body area" (torso/arms/legs coverage that lets a longer garment extend past the current
     clothes) -> SCHP clothing + limb labels, dilated the same way as the DensePose area.
Everything else (protect lists, convex hull, blur/threshold, dilation kernels) follows
`AutoMasker.cloth_agnostic_mask` line by line.

The pure-numpy part (`agnostic_mask_from_parsing`) needs only numpy + OpenCV and is unit-tested; the
SCHP inference (`SCHPMasker`) imports torch lazily.
"""

from __future__ import annotations

import logging
import os

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("tryon.masker")

ATR_MAPPING = {
    "Background": 0, "Hat": 1, "Hair": 2, "Sunglasses": 3,
    "Upper-clothes": 4, "Skirt": 5, "Pants": 6, "Dress": 7,
    "Belt": 8, "Left-shoe": 9, "Right-shoe": 10, "Face": 11,
    "Left-leg": 12, "Right-leg": 13, "Left-arm": 14, "Right-arm": 15,
    "Bag": 16, "Scarf": 17,
}
LIP_MAPPING = {
    "Background": 0, "Hat": 1, "Hair": 2, "Glove": 3,
    "Sunglasses": 4, "Upper-clothes": 5, "Dress": 6, "Coat": 7,
    "Socks": 8, "Pants": 9, "Jumpsuits": 10, "Scarf": 11,
    "Skirt": 12, "Face": 13, "Left-arm": 14, "Right-arm": 15,
    "Left-leg": 16, "Right-leg": 17, "Left-shoe": 18, "Right-shoe": 19,
}

# --- copied from CatVTON model/cloth_masker.py
PROTECT_BODY_PARTS = {
    "upper": ["Left-leg", "Right-leg"],
    "lower": ["Right-arm", "Left-arm", "Face"],
    "overall": [],
}
PROTECT_CLOTH_PARTS = {
    "upper": {"ATR": ["Skirt", "Pants"], "LIP": ["Skirt", "Pants"]},
    "lower": {"ATR": ["Upper-clothes"], "LIP": ["Upper-clothes", "Coat"]},
    "overall": {"ATR": [], "LIP": []},
}
MASK_CLOTH_PARTS = {
    "upper": ["Upper-clothes", "Coat", "Dress", "Jumpsuits"],
    "lower": ["Pants", "Skirt", "Dress", "Jumpsuits"],
    "overall": ["Upper-clothes", "Dress", "Pants", "Skirt", "Coat", "Jumpsuits"],
}
ACCESSORY_PARTS = ["Hat", "Glove", "Sunglasses", "Bag", "Left-shoe", "Right-shoe", "Scarf", "Socks"]

# --- DensePose replacement: SCHP labels standing in for DensePose MASK_DENSE_PARTS
#     (upper: torso + big arms + forearms, lower: thighs + legs, overall: all of them).
DENSE_APPROX_PARTS = {
    "upper": ["Upper-clothes", "Coat", "Dress", "Jumpsuits", "Left-arm", "Right-arm"],
    "lower": ["Pants", "Skirt", "Dress", "Jumpsuits", "Left-leg", "Right-leg"],
    "overall": ["Upper-clothes", "Coat", "Dress", "Jumpsuits", "Pants", "Skirt",
                "Left-arm", "Right-arm", "Left-leg", "Right-leg"],
}

CATEGORIES = ("upper", "lower", "overall")


def part_mask(parts, parse: np.ndarray, mapping: dict) -> np.ndarray:
    """Boolean mask of the given label names (unknown names are ignored, like upstream)."""
    if isinstance(parts, str):
        parts = [parts]
    ids = [mapping[p] for p in parts if p in mapping]
    if not ids:
        return np.zeros(parse.shape, dtype=bool)
    return np.isin(parse, ids)


def both(parts, lip: np.ndarray, atr: np.ndarray) -> np.ndarray:
    return part_mask(parts, lip, LIP_MAPPING) | part_mask(parts, atr, ATR_MAPPING)


def hull_mask(mask_area: np.ndarray) -> np.ndarray:
    """Union of convex hulls of the external contours (uint8 0/255 in, uint8 0/255 out)."""
    _, binary = cv2.threshold(mask_area, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(mask_area)
    for c in contours:
        hull = cv2.convexHull(c)
        out = cv2.fillPoly(np.zeros_like(mask_area), [hull], 255) | out
    return out


def _odd(n: int) -> int:
    n = max(1, n)
    return n if n % 2 == 1 else n + 1


def _limb_tips(limb: np.ndarray, anchor: tuple[float, float], radius: float, mode: str,
               min_area: int) -> np.ndarray:
    """Pixels of each sizeable limb component within `radius` of its tip.

    mode="far": tip = point farthest from `anchor` (torso centre) -> hand.
    mode="low": tip = lowest point -> foot.
    """
    out = np.zeros(limb.shape, dtype=bool)
    if not limb.any():
        return out
    n, labels, stats, _ = cv2.connectedComponentsWithStats(limb.astype(np.uint8), connectivity=8)
    comps = sorted(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True)[:2]
    for comp in comps:
        if stats[comp, cv2.CC_STAT_AREA] < min_area:
            continue
        ys, xs = np.nonzero(labels == comp)
        if mode == "far":
            d = (xs - anchor[0]) ** 2 + (ys - anchor[1]) ** 2
            idx = int(np.argmax(d))
        else:
            idx = int(np.argmax(ys))
        tx, ty = xs[idx], ys[idx]
        near = (xs - tx) ** 2 + (ys - ty) ** 2 <= radius ** 2
        out[ys[near], xs[near]] = True
    return out


def estimate_hands_feet(lip: np.ndarray, atr: np.ndarray) -> np.ndarray:
    """Approximate DensePose 'hands' + 'feet' using SCHP limb labels (see module docstring)."""
    h, w = lip.shape
    face = both("Face", lip, atr)
    if face.any():
        ys = np.nonzero(face.any(axis=1))[0]
        face_h = float(ys[-1] - ys[0] + 1)
    else:
        face_h = 0.08 * h
    radius = float(np.clip(0.9 * face_h, 0.035 * h, 0.12 * h))

    torso = both(["Upper-clothes", "Coat", "Dress", "Jumpsuits"], lip, atr)
    person = ~(part_mask("Background", lip, LIP_MAPPING) & part_mask("Background", atr, ATR_MAPPING))
    ref = torso if torso.sum() > 0.002 * h * w else person
    if ref.any():
        ys, xs = np.nonzero(ref)
        anchor = (float(xs.mean()), float(ys.mean()))
    else:
        anchor = (w / 2.0, h / 2.0)

    min_area = max(16, int(0.0003 * h * w))
    hands = np.zeros((h, w), dtype=bool)
    for side in ("Left-arm", "Right-arm"):
        hands |= _limb_tips(both(side, lip, atr), anchor, radius, "far", min_area)
    feet = np.zeros((h, w), dtype=bool)
    for side in ("Left-leg", "Right-leg"):
        feet |= _limb_tips(both(side, lip, atr), anchor, radius, "low", min_area)
    return hands | feet


def agnostic_mask_from_parsing(lip: np.ndarray, atr: np.ndarray, part: str) -> np.ndarray:
    """Port of AutoMasker.cloth_agnostic_mask with the DensePose inputs approximated.

    lip/atr: HxW uint8 label maps. Returns HxW uint8 mask with values 0/255.
    """
    if part not in CATEGORIES:
        raise ValueError(f"part must be one of {CATEGORIES}, got {part!r}")
    if lip.shape != atr.shape:
        raise ValueError("LIP and ATR parsing maps must have the same shape")
    h, w = lip.shape

    dk = _odd(max(w, h) // 250)
    dilate_kernel = np.ones((dk, dk), np.uint8)
    kernel_size = _odd(max(w, h) // 25)

    # Strong protect area (hands, feet, face)
    hands_feet = estimate_hands_feet(lip, atr).astype(np.uint8)
    hands_feet = cv2.dilate(hands_feet, dilate_kernel, iterations=1).astype(bool)
    hands_protect = hands_feet & both(["Left-arm", "Right-arm", "Left-leg", "Right-leg"], lip, atr)
    face_protect = part_mask("Face", lip, LIP_MAPPING)
    strong_protect = hands_protect | face_protect

    # Weak protect area (hair, irrelevant clothes, body parts, accessories)
    body_protect = both(PROTECT_BODY_PARTS[part], lip, atr)
    hair_protect = both(["Hair"], lip, atr)
    cloth_protect = (part_mask(PROTECT_CLOTH_PARTS[part]["LIP"], lip, LIP_MAPPING)
                     | part_mask(PROTECT_CLOTH_PARTS[part]["ATR"], atr, ATR_MAPPING))
    accessory_protect = both(ACCESSORY_PARTS, lip, atr)
    weak_protect = body_protect | cloth_protect | hair_protect | strong_protect | accessory_protect

    # Mask area
    strong_mask = both(MASK_CLOTH_PARTS[part], lip, atr)
    background = part_mask("Background", lip, LIP_MAPPING) & part_mask("Background", atr, ATR_MAPPING)
    dense = both(DENSE_APPROX_PARTS[part], lip, atr).astype(np.uint8)
    dense = cv2.resize(dense, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_NEAREST)
    dense = cv2.dilate(dense, dilate_kernel, iterations=2)
    dense = cv2.resize(dense, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)

    mask = (~weak_protect & ~background) | dense
    mask = hull_mask(mask.astype(np.uint8) * 255) > 127  # convex hull expands the area
    mask = mask & ~weak_protect
    blurred = cv2.GaussianBlur(mask.astype(np.uint8) * 255, (kernel_size, kernel_size), 0)
    mask = blurred >= 25
    mask = (mask | strong_mask) & ~strong_protect
    mask = cv2.dilate(mask.astype(np.uint8), dilate_kernel, iterations=1)
    return (mask > 0).astype(np.uint8) * 255


# ----------------------------------------------------------------------------- SCHP inference

_SCHP_SETTINGS = {
    "lip": {"input_size": (473, 473), "num_classes": 20},
    "atr": {"input_size": (512, 512), "num_classes": 18},
}
# SCHP was trained on BGR images (cv2.imread) with these BGR-ordered ImageNet statistics.
_BGR_MEAN = np.array([0.406, 0.456, 0.485], dtype=np.float32)
_BGR_STD = np.array([0.225, 0.224, 0.229], dtype=np.float32)


class SCHPParser:
    """One SCHP model (LIP or ATR). Port of CatVTON model/SCHP/__init__.py."""

    def __init__(self, ckpt_path: str, kind: str, device: str = "cpu"):
        import torch

        from schp_net import resnet101

        self.torch = torch
        self.kind = kind
        cfg = _SCHP_SETTINGS[kind]
        self.input_size = cfg["input_size"]
        self.aspect_ratio = self.input_size[1] * 1.0 / self.input_size[0]
        self.model = resnet101(num_classes=cfg["num_classes"])
        self._load(ckpt_path)
        self.model.eval()
        self.device = "cpu"
        self.to(device)

    def to(self, device: str) -> None:
        self.model.to(device)
        self.device = device

    def _load(self, ckpt_path: str) -> None:
        torch = self.torch
        rename_map = {
            "decoder.conv3.2.weight": "decoder.conv3.3.weight",
            "decoder.conv3.3.weight": "decoder.conv3.4.weight",
            "decoder.conv3.3.bias": "decoder.conv3.4.bias",
            "decoder.conv3.3.running_mean": "decoder.conv3.4.running_mean",
            "decoder.conv3.3.running_var": "decoder.conv3.4.running_var",
            "fushion.3.weight": "fushion.4.weight",
            "fushion.3.bias": "fushion.4.bias",
        }
        try:
            ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
        except Exception as exc:  # older pickles may contain non-tensor objects
            log.warning("weights_only load of %s failed (%s); retrying with full unpickling "
                        "(file comes from the pinned official HF repo)", os.path.basename(ckpt_path), exc)
            ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        state_dict = ckpt["state_dict"] if "state_dict" in ckpt else ckpt
        new_state = {}
        for k, v in state_dict.items():
            name = k[7:] if k.startswith("module.") else k
            new_state[rename_map.get(name, name)] = v
        missing, unexpected = self.model.load_state_dict(new_state, strict=False)
        # Upstream also loads non-strictly; anything missing besides BN counters means a bad checkpoint.
        real_missing = [m for m in missing if not m.endswith("num_batches_tracked")]
        if len(real_missing) > 4:
            raise RuntimeError(f"SCHP {self.kind}: checkpoint is missing {len(real_missing)} tensors, "
                               f"e.g. {real_missing[:5]}")
        if real_missing:
            log.warning("SCHP %s: tensors not in checkpoint (left at init): %s", self.kind, real_missing)
        if unexpected:
            log.debug("SCHP %s: ignoring %d unexpected keys", self.kind, len(unexpected))

    def _xywh2cs(self, x, y, w, h):
        center = np.array([x + w * 0.5, y + h * 0.5], dtype=np.float32)
        if w > self.aspect_ratio * h:
            h = w * 1.0 / self.aspect_ratio
        elif w < self.aspect_ratio * h:
            w = h * self.aspect_ratio
        return center, np.array([w, h], dtype=np.float32)

    def __call__(self, image: Image.Image) -> np.ndarray:
        from schp_net import get_affine_transform, transform_logits

        torch = self.torch
        rgb = np.array(image.convert("RGB"))
        bgr = rgb[:, :, ::-1]
        h, w, _ = bgr.shape
        center, scale = self._xywh2cs(0, 0, w - 1, h - 1)
        trans = get_affine_transform(center, scale, 0, self.input_size)
        inp = cv2.warpAffine(bgr, trans, (int(self.input_size[1]), int(self.input_size[0])),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        inp = (inp.astype(np.float32) / 255.0 - _BGR_MEAN) / _BGR_STD
        tensor = torch.from_numpy(np.ascontiguousarray(inp.transpose(2, 0, 1)))[None]
        with torch.inference_mode():
            try:
                out = self.model(tensor.to(self.device))
            except RuntimeError as exc:
                if self.device == "cpu":
                    raise
                # e.g. MPS AdaptiveAvgPool2d needs divisible sizes (ATR: 32x32 -> 3x3 fails)
                log.warning("SCHP %s failed on %s (%s); moving it to CPU", self.kind, self.device, exc)
                self.to("cpu")
                out = self.model(tensor)
            out = torch.nn.functional.interpolate(out, size=self.input_size, mode="bilinear", align_corners=True)
            logits = out[0].permute(1, 2, 0).float().cpu().numpy()
        logits = transform_logits(logits, center, scale, w, h, input_size=self.input_size)
        return np.argmax(logits, axis=2).astype(np.uint8)


class SCHPMasker:
    """SCHP-LIP + SCHP-ATR parsing -> cloth-agnostic mask (PIL 'L', 0/255)."""

    def __init__(self, schp_dir: str, device: str = "cpu"):
        from weights import SCHP_ATR, SCHP_LIP

        self.lip = SCHPParser(os.path.join(schp_dir, SCHP_LIP), "lip", device)
        self.atr = SCHPParser(os.path.join(schp_dir, SCHP_ATR), "atr", device)

    def __call__(self, image: Image.Image, category: str) -> Image.Image:
        lip = self.lip(image)
        atr = self.atr(image)
        mask = agnostic_mask_from_parsing(lip, atr, category)
        if mask.mean() < 255 * 0.005:
            log.warning("SCHP mask almost empty for category=%s (is a person visible?)", category)
        return Image.fromarray(mask)  # uint8 HxW -> mode "L"

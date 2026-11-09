"""Which weights the worker needs and how to fetch them from the Hugging Face Hub.

Only imports huggingface_hub, so `download_weights.py` can run before torch is
usable. Downloads are restricted with `allow_patterns` so we do not pull the
DensePose checkpoint, the FLUX LoRA, the SD text encoder or the 4 GB .ckpt.

Repos (see README "Giấy phép" for licenses):
  * zhengchong/CatVTON                           - CatVTON attention weights + SCHP parsers (CC BY-NC-SA 4.0)
  * stable-diffusion-v1-5/stable-diffusion-inpainting - base UNet/scheduler/safety checker (CreativeML OpenRAIL-M)
  * stabilityai/sd-vae-ft-mse                    - VAE used by CatVTON (MIT)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

CATVTON_REPO = os.environ.get("TRYON_CATVTON_REPO", "zhengchong/CatVTON")
# CatVTON's app.py uses "booksforcharlie/stable-diffusion-inpainting" (a copy made after runwayml removed
# the original). The official community mirror below has identical weights plus fp16 safetensors,
# which halves the download and avoids pickle (.bin) loading.
BASE_REPO = os.environ.get("TRYON_BASE_MODEL", "stable-diffusion-v1-5/stable-diffusion-inpainting")
VAE_REPO = os.environ.get("TRYON_VAE", "stabilityai/sd-vae-ft-mse")

# "mix-48k-1024" is what CatVTON's app.py loads (attn_ckpt_version="mix"), trained at 1024x768.
ATTN_VERSION = os.environ.get("TRYON_ATTN_VERSION", "mix-48k-1024")

SCHP_LIP = "exp-schp-201908261155-lip.pth"
SCHP_ATR = "exp-schp-201908301523-atr.pth"


@dataclass
class WeightPaths:
    catvton: str
    base: str
    vae: str

    @property
    def attention_dir(self) -> str:
        return os.path.join(self.catvton, ATTN_VERSION, "attention")

    @property
    def schp_dir(self) -> str:
        return os.path.join(self.catvton, "SCHP")


def _base_patterns(safety_check: bool) -> list[str]:
    patterns = [
        "model_index.json",
        "scheduler/*",
        "unet/config.json",
        "unet/diffusion_pytorch_model.fp16.safetensors",
    ]
    if BASE_REPO == "booksforcharlie/stable-diffusion-inpainting":
        # That copy only ships fp32 pickles.
        patterns = [p for p in patterns if "fp16" not in p] + ["unet/diffusion_pytorch_model.bin"]
    if safety_check:
        patterns += ["feature_extractor/*", "safety_checker/config.json"]
        if BASE_REPO == "booksforcharlie/stable-diffusion-inpainting":
            patterns += ["safety_checker/pytorch_model.bin"]
        else:
            patterns += ["safety_checker/model.fp16.safetensors"]
    return patterns


def base_uses_fp16_variant() -> bool:
    return BASE_REPO != "booksforcharlie/stable-diffusion-inpainting"


def fetch_weights(*, safety_check: bool = True, offline: bool = False) -> WeightPaths:
    """Download (or locate in the HF cache) everything the CatVTON backend needs."""
    from huggingface_hub import snapshot_download

    token = os.environ.get("HF_TOKEN") or None
    kw = {"local_files_only": offline, "token": token}
    catvton = snapshot_download(
        CATVTON_REPO,
        allow_patterns=[f"{ATTN_VERSION}/attention/*", f"SCHP/{SCHP_LIP}", f"SCHP/{SCHP_ATR}"],
        **kw,
    )
    base = snapshot_download(BASE_REPO, allow_patterns=_base_patterns(safety_check), **kw)
    vae = snapshot_download(VAE_REPO, allow_patterns=["config.json", "diffusion_pytorch_model.safetensors"], **kw)
    paths = WeightPaths(catvton=catvton, base=base, vae=vae)
    missing = [p for p in (paths.attention_dir, os.path.join(paths.schp_dir, SCHP_LIP),
                           os.path.join(paths.schp_dir, SCHP_ATR), os.path.join(base, "unet"))
               if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Weights incomplete, missing: {missing}. Run ./setup_macmini.sh again.")
    return paths

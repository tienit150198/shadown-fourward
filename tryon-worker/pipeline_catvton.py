"""CatVTON inference for Apple Silicon (PyTorch MPS), CUDA or CPU.

Port of `CatVTONPipeline` from https://github.com/Zheng-Chong/CatVTON (model/pipeline.py, model/utils.py,
model/attn_processor.py; CC BY-NC-SA 4.0) with these changes for macOS:

* Self-attention runs through `SlicedAttnProcessor`: identical math to CatVTON's AttnProcessor2_0, but
  the query axis is processed in chunks. CatVTON concatenates person + garment along the height, so at
  768x1024 the top UNet level attends over 96*256 = 24,576 tokens; a full attention matrix for the CFG
  batch would be ~10 GB in fp16 (and MPS also has a 4 GB-per-tensor limit). Chunking keeps peaks < 1 GB.
* Cross-attention is skipped exactly like upstream (SkipAttnProcessor, no text encoder at all).
* CatVTON attention weights are loaded by mapping checkpoint indices onto the UNet's `attn1` modules
  (the checkpoint was saved from a ModuleList of every module whose name contains "attn1"; each
  attention occupies 8 slots: attn1, to_q, to_k, to_v, to_out, to_out.0, to_out.1, processor).
  This is independent of whether the attention processors are nn.Modules.
* dtypes: UNet fp16 on MPS (bf16 is slow/partial on MPS), VAE fp32, latents/scheduler fp32. If the
  output is not finite the backend converts the UNet to fp32 and retries once (and stays on fp32).
* Random numbers come from a CPU torch.Generator so seeds are reproducible across devices.
* Masks come from `masker.SCHPMasker` (SCHP only, no DensePose/detectron2) - see masker.py.
"""

from __future__ import annotations

import gc
import inspect
import logging
import os
import random
import time
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from imaging import binarize_mask, box_mask, repaint, resize_and_crop, resize_and_padding, soften_mask

log = logging.getLogger("tryon.catvton")


class NonFiniteOutput(RuntimeError):
    """The diffusion produced NaN/Inf (typical fp16 overflow)."""


class UnsafeOutput(RuntimeError):
    """The Stable Diffusion safety checker flagged the result."""


# ----------------------------------------------------------------------------- device / dtype

def pick_device(pref: str = "auto") -> str:
    pref = (pref or "auto").strip().lower()
    mps_ok = getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available()
    if pref == "auto":
        if mps_ok:
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    if pref == "mps" and not mps_ok:
        raise RuntimeError("TRYON_DEVICE=mps but torch.backends.mps.is_available() is False "
                           "(needs Apple Silicon, macOS >= 12.3 and an arm64 Python).")
    if pref.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("TRYON_DEVICE=cuda but CUDA is not available")
    return pref


def pick_dtype(device: str, pref: str = "auto") -> torch.dtype:
    pref = (pref or "auto").strip().lower()
    table = {"fp32": torch.float32, "float32": torch.float32, "fp16": torch.float16,
             "float16": torch.float16, "bf16": torch.bfloat16, "bfloat16": torch.bfloat16}
    if pref != "auto":
        if pref not in table:
            raise ValueError(f"Unknown TRYON_DTYPE {pref!r} (use auto|fp16|fp32|bf16)")
        return table[pref]
    if device == "mps":
        return torch.float16
    if device.startswith("cuda"):
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    return torch.float32


def empty_device_cache(device: str) -> None:
    gc.collect()
    if device == "mps" and hasattr(torch, "mps"):
        torch.mps.empty_cache()
    elif device.startswith("cuda"):
        torch.cuda.empty_cache()


# ----------------------------------------------------------------------------- attention processors

class SkipAttnProcessor:
    """CatVTON drops cross-attention entirely (returns its input unchanged)."""

    def __call__(self, attn, hidden_states, encoder_hidden_states=None, attention_mask=None, temb=None,
                 *args, **kwargs):
        return hidden_states


def _sdpa_chunked(query, key, value, attn_mask, chunk: int):
    n = query.shape[-2]
    if chunk <= 0 or n <= chunk:
        return F.scaled_dot_product_attention(query, key, value, attn_mask=attn_mask, dropout_p=0.0,
                                              is_causal=False)
    out = torch.empty_like(query)
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        mask = attn_mask
        if mask is not None and mask.shape[-2] > 1:
            mask = mask[..., start:end, :]
        out[..., start:end, :] = F.scaled_dot_product_attention(
            query[..., start:end, :], key, value, attn_mask=mask, dropout_p=0.0, is_causal=False)
    return out


class SlicedAttnProcessor:
    """CatVTON's AttnProcessor2_0 with the query axis processed in chunks of `query_chunk` tokens."""

    def __init__(self, query_chunk: int = 0):
        self.query_chunk = int(query_chunk)

    def __call__(self, attn, hidden_states, encoder_hidden_states=None, attention_mask=None, temb=None,
                 *args, **kwargs):
        residual = hidden_states
        if attn.spatial_norm is not None:
            hidden_states = attn.spatial_norm(hidden_states, temb)
        input_ndim = hidden_states.ndim
        if input_ndim == 4:
            batch_size, channel, height, width = hidden_states.shape
            hidden_states = hidden_states.view(batch_size, channel, height * width).transpose(1, 2)
        batch_size, sequence_length, _ = (
            hidden_states.shape if encoder_hidden_states is None else encoder_hidden_states.shape
        )
        if attention_mask is not None:
            attention_mask = attn.prepare_attention_mask(attention_mask, sequence_length, batch_size)
            attention_mask = attention_mask.view(batch_size, attn.heads, -1, attention_mask.shape[-1])
        if attn.group_norm is not None:
            hidden_states = attn.group_norm(hidden_states.transpose(1, 2)).transpose(1, 2)

        query = attn.to_q(hidden_states)
        if encoder_hidden_states is None:
            encoder_hidden_states = hidden_states
        elif attn.norm_cross:
            encoder_hidden_states = attn.norm_encoder_hidden_states(encoder_hidden_states)
        key = attn.to_k(encoder_hidden_states)
        value = attn.to_v(encoder_hidden_states)

        inner_dim = key.shape[-1]
        head_dim = inner_dim // attn.heads
        query = query.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        key = key.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        value = value.view(batch_size, -1, attn.heads, head_dim).transpose(1, 2)
        if getattr(attn, "norm_q", None) is not None:
            query = attn.norm_q(query)
        if getattr(attn, "norm_k", None) is not None:
            key = attn.norm_k(key)

        hidden_states = _sdpa_chunked(query, key, value, attention_mask, self.query_chunk)
        hidden_states = hidden_states.transpose(1, 2).reshape(batch_size, -1, attn.heads * head_dim)
        hidden_states = hidden_states.to(query.dtype)
        hidden_states = attn.to_out[0](hidden_states)
        hidden_states = attn.to_out[1](hidden_states)
        if input_ndim == 4:
            hidden_states = hidden_states.transpose(-1, -2).reshape(batch_size, channel, height, width)
        if attn.residual_connection:
            hidden_states = hidden_states + residual
        return hidden_states / attn.rescale_output_factor


def set_catvton_processors(unet, query_chunk: int) -> None:
    """attn1 (self-attention) -> sliced SDPA, attn2 (cross-attention) -> skip. Same as init_adapter()."""
    procs = {}
    for name in unet.attn_processors.keys():
        procs[name] = SlicedAttnProcessor(query_chunk) if name.endswith("attn1.processor") else SkipAttnProcessor()
    unet.set_attn_processor(procs)


def attn1_modules(unet) -> list:
    return [m for name, m in unet.named_modules() if name.endswith("attn1")]


def load_catvton_attention(unet, attention_dir_or_file: str) -> int:
    """Load CatVTON's fine-tuned self-attention weights into the UNet. Returns #modules loaded."""
    from safetensors.torch import load_file

    path = attention_dir_or_file
    if os.path.isdir(path):
        path = os.path.join(path, "model.safetensors")
    state = load_file(path)
    groups: dict[int, dict[str, torch.Tensor]] = {}
    for key, tensor in state.items():
        idx, sub = key.split(".", 1)
        groups.setdefault(int(idx), {})[sub] = tensor
    modules = attn1_modules(unet)
    order = sorted(groups)
    if len(order) != len(modules):
        raise RuntimeError(f"CatVTON checkpoint has {len(order)} attention blocks but the UNet has "
                           f"{len(modules)} attn1 modules - wrong base model?")
    stride = order[1] - order[0] if len(order) > 1 else 1
    if any(b - a != stride for a, b in zip(order, order[1:])):
        raise RuntimeError("Unexpected CatVTON attention checkpoint layout")
    for module, idx in zip(modules, order):
        sd = groups[idx]
        ref = next(module.parameters())
        module.load_state_dict({k: v.to(device=ref.device, dtype=ref.dtype) for k, v in sd.items()}, strict=True)
    return len(modules)


# ----------------------------------------------------------------------------- tensor helpers

def prepare_image(image: Image.Image) -> torch.Tensor:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    return torch.from_numpy(arr.transpose(2, 0, 1).copy())[None] / 127.5 - 1.0


def prepare_mask(mask: Image.Image) -> torch.Tensor:
    arr = np.asarray(mask.convert("L"), dtype=np.float32) / 255.0
    arr = (arr >= 0.5).astype(np.float32)
    return torch.from_numpy(arr)[None, None]


def numpy_to_pil(images: np.ndarray) -> list:
    images = (images * 255).round().astype("uint8")
    return [Image.fromarray(img) for img in images]


# ----------------------------------------------------------------------------- pipeline

class CatVTONPipeline:
    def __init__(self, unet, vae, scheduler, device: str, unet_dtype: torch.dtype,
                 vae_dtype: torch.dtype = torch.float32, attn_chunk: int = 0,
                 safety_checker=None, feature_extractor=None):
        self.device = device
        self.unet = unet.to(device, dtype=unet_dtype).eval()
        self.vae = vae.to(device, dtype=vae_dtype).eval()
        self.scheduler = scheduler
        self.unet_dtype = unet_dtype
        self.vae_dtype = vae_dtype
        self.safety_checker = safety_checker
        self.feature_extractor = feature_extractor
        self.attn_chunk = attn_chunk
        try:  # VAE mid-block attention also sees 12k tokens at 768x1024
            self.vae.set_attn_processor(SlicedAttnProcessor(max(attn_chunk, 4096) if attn_chunk else 0))
        except Exception as exc:  # pragma: no cover - depends on diffusers version
            log.debug("VAE attention processor not replaced: %s", exc)

    @classmethod
    def from_pretrained(cls, paths, device: str, dtype: torch.dtype, attn_chunk: int,
                        safety_check: bool = True) -> "CatVTONPipeline":
        from diffusers import AutoencoderKL, DDIMScheduler, UNet2DConditionModel

        from weights import base_uses_fp16_variant

        variant = "fp16" if base_uses_fp16_variant() else None
        scheduler = DDIMScheduler.from_pretrained(paths.base, subfolder="scheduler")
        vae = AutoencoderKL.from_pretrained(paths.vae, torch_dtype=torch.float32)
        unet = UNet2DConditionModel.from_pretrained(paths.base, subfolder="unet", variant=variant,
                                                    torch_dtype=dtype)
        set_catvton_processors(unet, attn_chunk)
        n = load_catvton_attention(unet, paths.attention_dir)
        log.info("Loaded CatVTON attention weights into %d attention blocks", n)
        safety_checker = feature_extractor = None
        if safety_check:
            from diffusers.pipelines.stable_diffusion.safety_checker import StableDiffusionSafetyChecker
            from transformers import CLIPImageProcessor

            feature_extractor = CLIPImageProcessor.from_pretrained(paths.base, subfolder="feature_extractor")
            sc_dtype = torch.float16 if device != "cpu" else torch.float32
            safety_checker = StableDiffusionSafetyChecker.from_pretrained(
                paths.base, subfolder="safety_checker", variant=variant, torch_dtype=sc_dtype
            ).to(device).eval()
        if device.startswith("cuda"):
            torch.backends.cuda.matmul.allow_tf32 = True
        return cls(unet, vae, scheduler, device, dtype, torch.float32, attn_chunk, safety_checker,
                   feature_extractor)

    def set_unet_dtype(self, dtype: torch.dtype) -> None:
        self.unet.to(dtype=dtype)
        self.unet_dtype = dtype
        empty_device_cache(self.device)

    def _vae_encode(self, image: torch.Tensor, generator) -> torch.Tensor:
        image = image.to(self.device, dtype=self.vae_dtype)
        latent = self.vae.encode(image).latent_dist.sample(generator=generator)
        return latent.float() * self.vae.config.scaling_factor

    def _extra_step_kwargs(self, generator, eta: float) -> dict:
        params = set(inspect.signature(self.scheduler.step).parameters.keys())
        kw = {}
        if "eta" in params:
            kw["eta"] = eta
        if "generator" in params:
            kw["generator"] = generator
        return kw

    @torch.no_grad()
    def __call__(self, image: Image.Image, condition_image: Image.Image, mask: Image.Image,
                 num_inference_steps: int = 50, guidance_scale: float = 2.5,
                 generator: Optional[torch.Generator] = None, eta: float = 1.0,
                 progress=None) -> Image.Image:
        """image/condition_image/mask must already be resized to the same (W, H), multiples of 64."""
        from diffusers.utils.torch_utils import randn_tensor

        if not (image.size == condition_image.size == mask.size):
            raise ValueError("person, garment and mask must have the same size")
        concat_dim = -2  # person on top, garment below (CatVTON concatenates along the height)
        image_t = prepare_image(image).to(self.device)
        cond_t = prepare_image(condition_image).to(self.device)
        mask_t = prepare_mask(mask).to(self.device)
        masked_image = image_t * (mask_t < 0.5)
        masked_latent = self._vae_encode(masked_image, generator)
        condition_latent = self._vae_encode(cond_t, generator)
        mask_latent = F.interpolate(mask_t, size=masked_latent.shape[-2:], mode="nearest")
        del image_t, cond_t, mask_t, masked_image

        masked_latent_concat = torch.cat([masked_latent, condition_latent], dim=concat_dim)
        mask_latent_concat = torch.cat([mask_latent, torch.zeros_like(mask_latent)], dim=concat_dim)
        latents = randn_tensor(masked_latent_concat.shape, generator=generator,
                               device=torch.device(self.device), dtype=torch.float32)
        self.scheduler.set_timesteps(num_inference_steps, device=self.device)
        timesteps = self.scheduler.timesteps
        latents = latents * self.scheduler.init_noise_sigma

        do_cfg = guidance_scale > 1.0
        if do_cfg:
            masked_latent_concat = torch.cat([
                torch.cat([masked_latent, torch.zeros_like(condition_latent)], dim=concat_dim),
                masked_latent_concat,
            ])
            mask_latent_concat = torch.cat([mask_latent_concat] * 2)

        extra = self._extra_step_kwargs(generator, eta)
        t_start = time.perf_counter()
        for i, t in enumerate(timesteps):
            model_in = torch.cat([latents] * 2) if do_cfg else latents
            model_in = self.scheduler.scale_model_input(model_in, t)
            model_in = torch.cat([model_in, mask_latent_concat, masked_latent_concat], dim=1)
            noise_pred = self.unet(model_in.to(self.unet_dtype), t.to(self.device),
                                   encoder_hidden_states=None, return_dict=False)[0].float()
            if do_cfg:
                noise_uncond, noise_cond = noise_pred.chunk(2)
                noise_pred = noise_uncond + guidance_scale * (noise_cond - noise_uncond)
            latents = self.scheduler.step(noise_pred, t, latents, **extra).prev_sample
            if i == 0 and not torch.isfinite(latents).all():
                raise NonFiniteOutput("NaN/Inf after first denoising step")
            if progress is not None:
                progress(i + 1, len(timesteps), time.perf_counter() - t_start)

        if not torch.isfinite(latents).all():
            raise NonFiniteOutput("NaN/Inf in final latents")
        latents = latents.split(latents.shape[concat_dim] // 2, dim=concat_dim)[0]
        latents = latents / self.vae.config.scaling_factor
        decoded = self.vae.decode(latents.to(self.vae_dtype)).sample
        decoded = (decoded / 2 + 0.5).clamp(0, 1)
        arr = decoded.cpu().permute(0, 2, 3, 1).float().numpy()
        if not np.isfinite(arr).all():
            raise NonFiniteOutput("NaN/Inf in decoded image")
        return numpy_to_pil(arr)[0]

    @torch.no_grad()
    def is_nsfw(self, image: Image.Image) -> bool:
        if self.safety_checker is None:
            return False
        dtype = next(self.safety_checker.parameters()).dtype
        clip_input = self.feature_extractor([image], return_tensors="pt").pixel_values.to(self.device, dtype=dtype)
        _, flags = self.safety_checker(images=np.array(image)[None].copy(), clip_input=clip_input)
        return bool(flags[0])


# ----------------------------------------------------------------------------- backend used by server.py

class CatVTONBackend:
    """Owns the pipeline + masker. Not thread-safe: server.py serialises calls with a lock."""

    name = "catvton"

    def __init__(self, *, device_pref: str, dtype_pref: str, size: tuple[int, int], attn_chunk: Optional[int],
                 masker: str, masker_device: str, safety_check: bool, repaint: bool, offline: bool):
        self.device = pick_device(device_pref)
        self.dtype = pick_dtype(self.device, dtype_pref)
        self.size = size
        if attn_chunk is None:
            attn_chunk = 1024 if self.device == "mps" else (0 if self.device.startswith("cuda") else 2048)
        self.attn_chunk = attn_chunk
        self.masker_kind = masker
        self.masker_device = self.device if masker_device == "auto" else masker_device
        self.safety_check = safety_check
        self.repaint = repaint
        self.offline = offline
        self.pipe: Optional[CatVTONPipeline] = None
        self.masker = None
        self.loaded = False

    @property
    def dtype_name(self) -> str:
        return str(self.pipe.unet_dtype if self.pipe else self.dtype).replace("torch.", "")

    def load(self) -> None:
        if self.loaded:
            return
        from weights import fetch_weights

        t0 = time.perf_counter()
        log.info("Loading CatVTON on %s (%s), resolution %dx%d, attention chunk %s",
                 self.device, self.dtype, self.size[0], self.size[1], self.attn_chunk or "off")
        paths = fetch_weights(safety_check=self.safety_check, offline=self.offline)
        self.pipe = CatVTONPipeline.from_pretrained(paths, self.device, self.dtype, self.attn_chunk,
                                                    self.safety_check)
        if self.masker_kind == "schp":
            from masker import SCHPMasker

            self.masker = SCHPMasker(paths.schp_dir, device=self.masker_device)
        self.loaded = True
        log.info("CatVTON ready in %.1fs", time.perf_counter() - t0)

    def make_mask(self, person: Image.Image, category: str) -> Image.Image:
        if self.masker is None:
            return box_mask(person.size, category)
        return self.masker(person, category)

    def run(self, person: Image.Image, garment: Image.Image, category: str, *, steps: int, guidance: float,
            seed: Optional[int], mask: Optional[Image.Image] = None, garment_des: Optional[str] = None
            ) -> tuple[Image.Image, dict]:
        # garment_des is accepted for API compatibility; CatVTON has no text encoder.
        self.load()
        assert self.pipe is not None
        w, h = self.size
        person_r = resize_and_crop(person, (w, h))
        garment_r = resize_and_padding(garment, (w, h))
        t0 = time.perf_counter()
        if mask is not None:
            mask_r = binarize_mask(resize_and_crop(mask.convert("L"), (w, h)))
            mask_mode = "client"
        else:
            mask_r = self.make_mask(person_r, category)
            mask_mode = self.masker_kind
        mask_soft = soften_mask(mask_r, 9)
        t_mask = time.perf_counter() - t0
        if seed is None or seed < 0:
            seed = random.randint(0, 2**31 - 1)

        def progress(i, n, elapsed):
            if i == 1 or i == n or i % 10 == 0:
                log.info("step %d/%d (%.2f s/step)", i, n, elapsed / i)

        def attempt():
            gen = torch.Generator("cpu").manual_seed(int(seed))
            return self.pipe(person_r, garment_r, mask_soft, num_inference_steps=steps, guidance_scale=guidance,
                             generator=gen, progress=progress)

        try:
            result = attempt()
        except NonFiniteOutput as exc:
            if self.pipe.unet_dtype == torch.float32:
                raise
            log.warning("%s with %s UNet - switching UNet to float32 and retrying", exc, self.pipe.unet_dtype)
            self.pipe.set_unet_dtype(torch.float32)
            result = attempt()
        finally:
            empty_device_cache(self.device)

        if self.repaint:
            result = repaint(result, person_r, mask_soft)
        if self.safety_check and self.pipe.is_nsfw(result):
            raise UnsafeOutput("Result blocked by the Stable Diffusion safety checker")
        info = {"seed": int(seed), "steps": steps, "guidance": guidance, "mask_mode": mask_mode,
                "mask_seconds": round(t_mask, 2), "dtype": self.dtype_name, "mask_image": mask_r}
        return result, info

    def warmup(self) -> None:
        """Load and run 2 steps at the real resolution so MPS kernels are compiled before the first user."""
        self.load()
        w, h = self.size
        person = Image.new("RGB", (w, h), (200, 190, 180))
        garment = Image.new("RGB", (w, h), (40, 80, 160))
        if self.masker is not None:
            self.masker(person, "upper")
        mask = soften_mask(box_mask((w, h), "upper"), 9)
        gen = torch.Generator("cpu").manual_seed(0)
        try:
            self.pipe(person, garment, mask, num_inference_steps=2, guidance_scale=2.5, generator=gen)
        except NonFiniteOutput:
            log.warning("Warm-up produced NaN in %s; switching UNet to float32", self.pipe.unet_dtype)
            self.pipe.set_unet_dtype(torch.float32)
            self.pipe(person, garment, mask, num_inference_steps=2, guidance_scale=2.5,
                      generator=torch.Generator("cpu").manual_seed(0))
        finally:
            empty_device_cache(self.device)

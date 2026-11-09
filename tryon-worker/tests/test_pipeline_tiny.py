"""CatVTON pipeline plumbing on a tiny randomly initialised UNet/VAE (CPU, seconds).

Needs torch + diffusers + safetensors (skipped otherwise). Verifies: attention processor setup,
sliced attention == full attention, CatVTON checkpoint index mapping, CFG/concat shapes, seeding,
backend wiring (box mask, repaint, fp16 -> fp32 NaN fallback). Real-weight quality is NOT tested here.
"""

from __future__ import annotations

import os
import sys

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("diffusers")
pytest.importorskip("safetensors")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import pipeline_catvton as pc  # noqa: E402

# Shapes of to_q in zhengchong/CatVTON mix-48k-1024/attention/model.safetensors (indices 0, 8, ..., 120),
# read from the file header. Order = UNet named_modules order: down(6), up(9), mid(1).
CATVTON_ATTN_SHAPES = [320, 320, 640, 640, 1280, 1280, 1280, 1280, 1280, 640, 640, 640, 320, 320, 320, 1280]

SD15_INPAINT_UNET = dict(
    sample_size=64, in_channels=9, out_channels=4, layers_per_block=2,
    block_out_channels=(320, 640, 1280, 1280),
    down_block_types=("CrossAttnDownBlock2D",) * 3 + ("DownBlock2D",),
    up_block_types=("UpBlock2D",) + ("CrossAttnUpBlock2D",) * 3,
    cross_attention_dim=768, attention_head_dim=8,
)


def tiny_unet():
    from diffusers import UNet2DConditionModel

    torch.manual_seed(0)
    return UNet2DConditionModel(
        sample_size=8, in_channels=9, out_channels=4, layers_per_block=2,
        block_out_channels=(32, 32, 64, 64),
        down_block_types=("CrossAttnDownBlock2D",) * 3 + ("DownBlock2D",),
        up_block_types=("UpBlock2D",) + ("CrossAttnUpBlock2D",) * 3,
        cross_attention_dim=32, attention_head_dim=8, norm_num_groups=8,
    )


def tiny_vae():
    from diffusers import AutoencoderKL

    torch.manual_seed(0)
    return AutoencoderKL(
        in_channels=3, out_channels=3, latent_channels=4, layers_per_block=1, norm_num_groups=8,
        block_out_channels=(8, 8, 16, 16),
        down_block_types=("DownEncoderBlock2D",) * 4, up_block_types=("UpDecoderBlock2D",) * 4,
    )


def ddim():
    from diffusers import DDIMScheduler

    return DDIMScheduler(beta_start=0.00085, beta_end=0.012, beta_schedule="scaled_linear", clip_sample=False,
                         set_alpha_to_one=False, steps_offset=1)


def tiny_pipe(chunk=16):
    unet = tiny_unet()
    pc.set_catvton_processors(unet, chunk)
    return pc.CatVTONPipeline(unet, tiny_vae(), ddim(), "cpu", torch.float32, attn_chunk=chunk)


def images(w=128, h=192):
    person = Image.new("RGB", (w, h), (200, 150, 120))
    garment = Image.new("RGB", (w, h), (30, 60, 200))
    mask = Image.new("L", (w, h), 0)
    mask.paste(255, (16, 32, w - 16, h // 2))
    return person, garment, mask


def test_sd15_attn1_order_matches_catvton_checkpoint():
    from accelerate import init_empty_weights
    from diffusers import UNet2DConditionModel

    with init_empty_weights():
        unet = UNet2DConditionModel(**SD15_INPAINT_UNET)
    shapes = [m.to_q.weight.shape[0] for m in pc.attn1_modules(unet)]
    assert shapes == CATVTON_ATTN_SHAPES


def test_processors_installed():
    unet = tiny_unet()
    pc.set_catvton_processors(unet, 8)
    procs = unet.attn_processors
    assert len(procs) == 32
    for name, p in procs.items():
        expected = pc.SlicedAttnProcessor if name.endswith("attn1.processor") else pc.SkipAttnProcessor
        assert isinstance(p, expected), name


def test_sliced_attention_matches_full():
    unet = tiny_unet().eval()
    x = torch.randn(2, 9, 16, 24)
    t = torch.tensor(500)
    pc.set_catvton_processors(unet, 0)
    with torch.no_grad():
        full = unet(x, t, encoder_hidden_states=None, return_dict=False)[0]
    pc.set_catvton_processors(unet, 7)  # odd chunk size -> uneven last chunk
    with torch.no_grad():
        sliced = unet(x, t, encoder_hidden_states=None, return_dict=False)[0]
    assert torch.allclose(full, sliced, atol=1e-5)


def test_load_catvton_attention_mapping(tmp_path):
    from safetensors.torch import save_file

    unet = tiny_unet()
    pc.set_catvton_processors(unet, 0)
    mods = pc.attn1_modules(unet)
    assert len(mods) == 16
    state = {}
    for i, m in enumerate(mods):
        for k, v in m.state_dict().items():
            state[f"{8 * i}.{k}"] = torch.full_like(v, float(i + 1))
    save_file(state, str(tmp_path / "model.safetensors"))
    assert pc.load_catvton_attention(unet, str(tmp_path)) == 16
    for i, m in enumerate(mods):
        assert torch.all(m.to_q.weight == i + 1)
        assert torch.all(m.to_out[0].bias == i + 1)
    # attn2 (cross-attention) untouched
    attn2 = [mod for name, mod in unet.named_modules() if name.endswith("attn2")]
    assert not torch.all(attn2[0].to_q.weight == 1)


def test_load_rejects_wrong_block_count(tmp_path):
    from safetensors.torch import save_file

    unet = tiny_unet()
    m = pc.attn1_modules(unet)[0]
    save_file({f"0.{k}": v.clone() for k, v in m.state_dict().items()}, str(tmp_path / "model.safetensors"))
    with pytest.raises(RuntimeError, match="attention blocks"):
        pc.load_catvton_attention(unet, str(tmp_path))


def test_pipeline_runs_and_is_seeded():
    pipe = tiny_pipe()
    person, garment, mask = images()
    a = pipe(person, garment, mask, num_inference_steps=3, guidance_scale=2.5,
             generator=torch.Generator("cpu").manual_seed(1))
    b = pipe(person, garment, mask, num_inference_steps=3, guidance_scale=2.5,
             generator=torch.Generator("cpu").manual_seed(1))
    c = pipe(person, garment, mask, num_inference_steps=3, guidance_scale=1.0,  # no CFG branch
             generator=torch.Generator("cpu").manual_seed(2))
    assert a.size == (128, 192) and c.size == (128, 192)
    assert list(a.getdata()) == list(b.getdata())


def test_pipeline_rejects_size_mismatch():
    pipe = tiny_pipe()
    person, garment, mask = images()
    with pytest.raises(ValueError):
        pipe(person, garment.resize((64, 64)), mask, num_inference_steps=1)


def make_backend(pipe, **kw):
    args = dict(device_pref="cpu", dtype_pref="fp32", size=(128, 192), attn_chunk=16, masker="box",
                masker_device="cpu", safety_check=False, repaint=True, offline=True)
    args.update(kw)
    b = pc.CatVTONBackend(**args)
    b.pipe = pipe
    b.loaded = True
    return b


def test_backend_run_end_to_end_tiny():
    backend = make_backend(tiny_pipe())
    person = Image.new("RGB", (300, 500), (120, 100, 90))
    garment = Image.new("RGB", (400, 400), (10, 200, 10))
    out, info = backend.run(person, garment, "upper", steps=2, guidance=2.5, seed=None)
    assert out.size == (128, 192)
    assert info["mask_mode"] == "box" and isinstance(info["seed"], int) and info["dtype"] == "float32"
    # repaint keeps the pixels outside the mask identical to the (resized) person
    assert out.getpixel((2, 190)) == (120, 100, 90)


def test_backend_falls_back_to_fp32_on_nan():
    class FakePipe:
        unet_dtype = torch.float16
        calls = 0

        def __call__(self, *a, **k):
            FakePipe.calls += 1
            if self.unet_dtype == torch.float16:
                raise pc.NonFiniteOutput("nan")
            return Image.new("RGB", (128, 192), (1, 2, 3))

        def set_unet_dtype(self, dtype):
            self.unet_dtype = dtype

        def is_nsfw(self, image):
            return False

    pipe = FakePipe()
    backend = make_backend(pipe, repaint=False)
    out, info = backend.run(Image.new("RGB", (200, 300)), Image.new("RGB", (200, 200)), "lower",
                            steps=2, guidance=2.5, seed=5)
    assert FakePipe.calls == 2 and pipe.unet_dtype == torch.float32
    assert info["seed"] == 5 and out.getpixel((0, 0)) == (1, 2, 3)


def test_device_and_dtype_selection():
    assert pc.pick_device("cpu") == "cpu"
    assert pc.pick_device("auto") in ("mps", "cuda", "cpu")
    assert pc.pick_dtype("mps") == torch.float16
    assert pc.pick_dtype("cpu") == torch.float32
    assert pc.pick_dtype("mps", "fp32") == torch.float32
    with pytest.raises(ValueError):
        pc.pick_dtype("cpu", "int8")

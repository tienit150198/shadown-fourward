"""Pre-download all weights into the Hugging Face cache (~/.cache/huggingface by default).

Usage:  .venv/bin/python download_weights.py [--no-safety-check]
Total download is roughly 2.8 GB (UNet fp16 1.7 GB, VAE 0.33 GB, safety checker 0.6 GB,
CatVTON attention 0.2 GB, SCHP parsers 2 x 0.26 GB).
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-safety-check", action="store_true",
                        help="skip the Stable Diffusion safety checker weights (use with TRYON_SAFETY_CHECK=0)")
    args = parser.parse_args()

    import weights

    safety = not args.no_safety_check and os.environ.get("TRYON_SAFETY_CHECK", "1").strip() not in ("0", "false", "no")
    print(f"CatVTON repo : {weights.CATVTON_REPO} ({weights.ATTN_VERSION} + SCHP)")
    print(f"Base model   : {weights.BASE_REPO}")
    print(f"VAE          : {weights.VAE_REPO}")
    print(f"Safety check : {'yes' if safety else 'no'}")
    t0 = time.time()
    try:
        paths = weights.fetch_weights(safety_check=safety)
    except Exception as exc:
        print(f"\nDownload failed: {exc}", file=sys.stderr)
        print("Check the internet connection; if Hugging Face rate-limits you, create a free token at "
              "https://huggingface.co/settings/tokens and run:  HF_TOKEN=hf_xxx ./setup_macmini.sh", file=sys.stderr)
        return 1
    print(f"\nOK in {time.time() - t0:.0f}s")
    print(f"  CatVTON: {paths.catvton}\n  base:    {paths.base}\n  vae:     {paths.vae}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

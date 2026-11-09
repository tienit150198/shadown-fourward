"""Re-tag the demo wardrobe with the real vision model and write items.json (run offline, once).

    cd backend/wardrobe_seed && ../../.venv/bin/python build_seed.py

Loads ../../.env (LLM_BASE_URL / LLM_API_KEY / VISION_MODEL). Category hints keep the
flat placeholder drawings (bottoms, shoes) in the right category. Seeding at runtime only
copies items.json + images (instant, offline).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE.parent.parent / ".env", override=False)

from providers import build_provider_from_env, vision_model  # noqa: E402
from stylist import tag_item  # noqa: E402

SEED = [
    ("demo-ao-blouse-cam-dao", "top"),
    ("demo-ao-polo-hong", "top"),
    ("demo-ao-thun-xanh-duong", "top"),
    ("demo-ao-nhung-xanh", "top"),
    ("demo-quan-jean-xanh", "bottom"),
    ("demo-quan-au-den", "bottom"),
    ("demo-chan-vay-be", "bottom"),
    ("demo-vay-xam-co-trang", "dress"),
    ("demo-ao-khoac-xanh-reu", "outerwear"),
    ("demo-giay-sneaker-trang", "shoes"),
]


async def main() -> None:
    provider = build_provider_from_env()
    print("mode", provider.mode, "model", vision_model())
    rows = []
    for item_id, category in SEED:
        jpeg = (HERE / "images" / f"{item_id}.jpg").read_bytes()
        started = time.monotonic()
        fields, source = await tag_item(provider, jpeg, category=category)
        print(f"{item_id}: {time.monotonic() - started:.1f}s source={source} -> {fields['name']} | "
              f"{fields['colors']} w={fields['warmth']} {fields['occasions']}")
        rows.append({"id": item_id, **fields})
    await provider.aclose()
    (HERE / "items.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())

"""Compatibility entry point used by the wardrobe seed-demo loader.

With no flag this only fetches and validates missing photos, matching the other
build_seed scripts. Pass --seed explicitly to write items to WardrobeStore.
"""

from __future__ import annotations

import sys

from seed import main


if __name__ == "__main__":
    args = sys.argv[1:] or ["--download-only"]
    raise SystemExit(main(args))

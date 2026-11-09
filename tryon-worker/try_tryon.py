"""Send one try-on request to a running worker and save the result (standard library only).

  python try_tryon.py person.jpg garment.jpg                       # http://127.0.0.1:8188, category upper
  python try_tryon.py person.jpg pants.jpg --category lower --url http://192.168.1.50:8188 --steps 20
  python try_tryon.py --health --url http://192.168.1.50:8188
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request


def data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def save_data_url(value: str, path: str) -> None:
    with open(path, "wb") as f:
        f.write(base64.b64decode(value.split(",", 1)[1]))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("person", nargs="?")
    p.add_argument("garment", nargs="?")
    p.add_argument("--url", default=os.environ.get("TRYON_WORKER_URL", "http://127.0.0.1:8188"))
    p.add_argument("--token", default=os.environ.get("TRYON_TOKEN", ""))
    p.add_argument("--category", default="upper", choices=["upper", "lower", "overall"])
    p.add_argument("--steps", type=int)
    p.add_argument("--guidance", type=float)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="tryon_result.jpg")
    p.add_argument("--save-mask", action="store_true", help="also save the mask as <out>_mask.jpg")
    p.add_argument("--health", action="store_true", help="only call GET /health")
    p.add_argument("--timeout", type=float, default=900)
    a = p.parse_args()
    base = a.url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if a.token:
        headers["X-Tryon-Token"] = a.token

    try:
        with urllib.request.urlopen(base + "/health", timeout=10) as r:
            print("health:", json.dumps(json.loads(r.read()), ensure_ascii=False))
    except (urllib.error.URLError, OSError) as exc:
        print(f"Cannot reach {base}/health: {exc}", file=sys.stderr)
        return 2
    if a.health:
        return 0
    if not (a.person and a.garment):
        p.error("person and garment image paths are required (or use --health)")

    body = {"person_image": data_url(a.person), "garment_image": data_url(a.garment), "category": a.category,
            "seed": a.seed, "return_mask": a.save_mask}
    if a.steps:
        body["steps"] = a.steps
    if a.guidance is not None:
        body["guidance"] = a.guidance
    req = urllib.request.Request(base + "/tryon", data=json.dumps(body).encode(), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    save_data_url(res["image"], a.out)
    if res.get("mask"):
        save_data_url(res["mask"], os.path.splitext(a.out)[0] + "_mask.jpg")
    print(f"saved {a.out} ({res['width']}x{res['height']}), worker {res['seconds']}s, "
          f"round-trip {time.time() - t0:.1f}s, seed {res.get('seed')}, device {res.get('device')}, "
          f"mock={res.get('mock')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

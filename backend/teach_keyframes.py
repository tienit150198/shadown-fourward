"""Server-side keyframe selection for Teach (raw frames from the phone → best ≤ 24).

The phone may send up to ~64 raw frames (e.g. one every second, plus one right after each
spoken sentence). The compiler and the self-check work best with frames that show END STATES,
so we keep, in priority order:

1. the first and the last frame (initial state / final result);
2. "speech" frames: the first frame at/after each transcript segment ends (the teacher just
   said what they did — the frame shows the result), plus frames the client tagged "speech";
3. "scene" frames: the picture settled after a change — a frame that is stable w.r.t. a
   neighbour and clearly different from the last settled scene. Metric: on a 64×48 grayscale
   thumbnail, the fraction of pixels whose luminance changed by > 20 levels (robust to small
   brightness drift, sensitive to an object appearing/moving). Client-tagged "scene" also counts;
4. the rest, chosen to spread evenly in time while skipping near-duplicates.

Frames Pillow cannot decode get no luminance (scene detection just skips them).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Sequence

THUMB_SIZE = (64, 48)
PIXEL_DELTA = 20  # luminance change (0-255) for a thumbnail pixel to count as "changed"
STABLE_DIFF = 0.03  # changed fraction vs a neighbour below which the picture is "still"
SCENE_DIFF = 0.025  # changed fraction vs the last settled scene above which it is a new scene
DUP_DIFF = 0.005  # below this two frames are near-duplicates
SPEECH_MATCH_MS = 3000  # a "right after speech" frame must come within 3 s of the segment end


@dataclass
class RawFrame:
    t: float
    payload: Any  # opaque (decoded bytes etc.), passed through
    data: bytes
    reason: str = ""
    luma: list[int] | None = field(default=None, repr=False)


def luma_thumb(data: bytes) -> list[int] | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as img:
            img.draft("L", (THUMB_SIZE[0] * 4, THUMB_SIZE[1] * 4))  # fast JPEG DCT downscale
            small = img.convert("L").resize(THUMB_SIZE)
            return list(small.tobytes())
    except Exception:  # noqa: BLE001 - undecodable / fake image: no scene info
        return None


def luma_diff(a: list[int] | None, b: list[int] | None) -> float | None:
    """Fraction of thumbnail pixels whose luminance changed by more than PIXEL_DELTA."""
    if a is None or b is None or len(a) != len(b) or not a:
        return None
    return sum(1 for x, y in zip(a, b) if abs(x - y) > PIXEL_DELTA) / len(a)


def _evenly(idxs: list[int], cap: int) -> list[int]:
    if len(idxs) <= cap:
        return idxs
    if cap <= 1:
        return idxs[:1]
    return sorted({idxs[round(i * (len(idxs) - 1) / (cap - 1))] for i in range(cap)})


def select_keyframes(
    frames: Sequence[RawFrame],
    *,
    cap: int,
    speech_ends_ms: Sequence[float] = (),
) -> list[RawFrame]:
    """Return ≤ cap frames in time order, each with `reason` set (start/end/speech/scene/interval)."""
    items = sorted(frames, key=lambda f: f.t)
    n = len(items)
    if n == 0:
        return []
    for f in items:
        if f.luma is None:
            f.luma = luma_thumb(f.data)
    reasons: dict[int, str] = {}

    # 2. right after each speech segment ends
    for t1 in sorted(speech_ends_ms):
        k = next((i for i, f in enumerate(items) if f.t >= t1), None)
        if k is not None and items[k].t - t1 <= SPEECH_MATCH_MS:
            reasons.setdefault(k, "speech")
    for i, f in enumerate(items):
        if f.reason == "speech":
            reasons.setdefault(i, "speech")

    # 3. scene settled after a change
    anchor: list[int] | None = items[0].luma
    for i in range(1, n):
        prev_d = luma_diff(items[i].luma, items[i - 1].luma)
        next_d = luma_diff(items[i].luma, items[i + 1].luma) if i + 1 < n else 0.0
        stable = (prev_d is not None and prev_d < STABLE_DIFF) or (next_d is not None and next_d < STABLE_DIFF and i + 1 < n)
        moved = luma_diff(items[i].luma, anchor)
        if stable and moved is not None and moved > SCENE_DIFF:
            reasons.setdefault(i, "scene")
            anchor = items[i].luma
        elif items[i].reason == "scene":
            reasons.setdefault(i, "scene")
            anchor = items[i].luma if items[i].luma is not None else anchor

    def finish(keep: list[int]) -> list[RawFrame]:
        out = []
        for i in sorted(set(keep)):
            f = items[i]
            if i == 0:
                f.reason = "start" if i not in reasons else reasons[i]
            elif i == n - 1 and i not in reasons:
                f.reason = "end"
            else:
                f.reason = reasons.get(i, f.reason or "interval")
            out.append(f)
        return out

    if n <= cap:
        return finish(list(range(n)))

    keep: list[int] = [0, n - 1]
    speech = [i for i in sorted(reasons) if reasons[i] == "speech" and i not in keep]
    keep += _evenly(speech, max(0, cap - len(keep)))
    scene = [i for i in sorted(reasons) if reasons[i] == "scene" and i not in keep]
    keep += _evenly(scene, max(0, cap - len(set(keep))))
    keep = sorted(set(keep))

    # 4. fill: farthest-in-time first, skipping near-duplicates of an already kept neighbour
    pool = [i for i in range(n) if i not in keep]
    while len(keep) < cap and pool:
        def gap(i: int) -> float:
            return min(abs(items[i].t - items[k].t) for k in keep)

        def dup(i: int) -> bool:
            nearest = min(keep, key=lambda k: abs(items[i].t - items[k].t))
            d = luma_diff(items[i].luma, items[nearest].luma)
            return d is not None and d < DUP_DIFF

        pool.sort(key=lambda i: (dup(i), -gap(i)))
        keep.append(pool.pop(0))
    return finish(keep)

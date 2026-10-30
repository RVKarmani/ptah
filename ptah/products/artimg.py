"""Image helpers for the product scout: find the artwork inside a shopping thumbnail and fit its aspect ratio.

Google Shopping thumbnails of prints are usually *mockups* (a framed print leaning against a wall).  A texture
needs only the print itself, so :func:`find_artwork_rect` looks for the closed, high-contrast rectangle (frame /
mat / paper edge) and :func:`crop_artwork` cuts the print out (stripping the frame and mat; the viewer draws its own).
Numpy + Pillow only, deterministic, no network.
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Box = Tuple[int, int, int, int]  # x0, y0, x1, y1 (x1/y1 exclusive), in pixels of the original image

MAX_SIDE = 420          # analysis resolution
EDGE_T = 9.0           # gradient (0..255) that counts as an edge pixel
MIN_AREA = 0.18         # smallest accepted artwork, fraction of the image
NEUTRAL_EDGE = 0.72     # coverage credited to a rectangle side that coincides with the image border


def _load(image) -> "np.ndarray":
    from PIL import Image
    if isinstance(image, Image.Image):
        im = image
    elif isinstance(image, (bytes, bytearray)):
        im = Image.open(io.BytesIO(bytes(image)))
    else:
        im = Image.open(image)
    return im.convert("RGB")


def _grads(rgb: np.ndarray, band: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    """Edge strength per pixel: |mean of the ``band`` pixels after - mean of the ``band`` before| (max over channels).
    Insensitive to blur / JPEG softness up to ``band`` pixels, so a pale paper edge on a white backdrop still shows."""
    f = rgb.astype(np.float32)
    H, W = f.shape[:2]
    cx = np.concatenate([np.zeros((H, 1, 3), np.float32), np.cumsum(f, axis=1)], axis=1)
    cy = np.concatenate([np.zeros((1, W, 3), np.float32), np.cumsum(f, axis=0)], axis=0)
    gx = np.zeros((H, W), np.float32)
    gy = np.zeros((H, W), np.float32)
    for x in range(band, W - band):
        after = (cx[:, x + band + 1] - cx[:, x + 1]) / band
        before = (cx[:, x] - cx[:, x - band]) / band
        gx[:, x] = np.abs(after - before).max(axis=1)
    for y in range(band, H - band):
        after = (cy[y + band + 1] - cy[y + 1]) / band
        before = (cy[y] - cy[y - band]) / band
        gy[y, :] = np.abs(after - before).max(axis=1)
    return gx, gy


def _peaks(score: np.ndarray, k: int, sep: int) -> List[int]:
    order = np.argsort(-score)
    out: List[int] = []
    for i in order:
        if score[i] < 0.25:
            break
        if all(abs(int(i) - j) >= sep for j in out):
            out.append(int(i))
        if len(out) >= k:
            break
    return out


def _cov(strong: np.ndarray, axis_fixed: int, pos: int, lo: int, hi: int, vertical_side: bool) -> float:
    """Fraction of the side (from lo to hi) that is an edge; the edge may sit within +-2 px of ``pos``."""
    if hi - lo < 4:
        return 0.0
    if vertical_side:      # a left/right side at column pos
        band = strong[lo:hi, max(0, pos - 2):pos + 3]
        return float(band.any(axis=1).mean())
    band = strong[max(0, pos - 2):pos + 3, lo:hi]
    return float(band.any(axis=0).mean())


def _best_rect(strong_x: np.ndarray, strong_y: np.ndarray, region: Tuple[int, int, int, int], min_area_frac: float,
               frac_of: float) -> Optional[Tuple[Tuple[int, int, int, int], float]]:
    """Best closed rectangle inside ``region`` (x0,y0,x1,y1).  -> (rect, confidence 0..1) or None."""
    H, W = strong_x.shape
    rx0, ry0, rx1, ry1 = region
    colcov = strong_x[ry0:ry1, :].mean(axis=0)                 # vertical edges: coverage along y
    rowcov = strong_y[:, rx0:rx1].mean(axis=1)
    # a vertical side shows up as a column with a long run of strong horizontal gradient
    cols = [c for c in _peaks(_smooth_max(colcov), 9, max(4, W // 60)) if rx0 <= c < rx1]
    rows = [r for r in _peaks(_smooth_max(rowcov), 9, max(4, H // 60)) if ry0 <= r < ry1]
    cols = sorted(set(cols + [rx0, rx1 - 1]))
    rows = sorted(set(rows + [ry0, ry1 - 1]))
    best = None
    for i, x0 in enumerate(cols):
        for x1 in cols[i + 1:]:
            for j, y0 in enumerate(rows):
                for y1 in rows[j + 1:]:
                    area = (x1 - x0) * (y1 - y0) / float(frac_of)
                    if area < min_area_frac:
                        continue
                    mx, my = max(2, int((x1 - x0) * 0.06)), max(2, int((y1 - y0) * 0.06))
                    sides = []
                    nb = sum([x0 <= 6, x1 >= W - 7, y0 <= 6, y1 >= H - 7])
                    if nb > 2:  # a rectangle that is the image itself says nothing about where the print is
                        continue
                    for pos, is_v, lo, hi, border in ((x0, True, y0 + my, y1 - my, x0 <= 6), (x1, True, y0 + my, y1 - my, x1 >= W - 7),
                                                      (y0, False, x0 + mx, x1 - mx, y0 <= 6), (y1, False, x0 + mx, x1 - mx, y1 >= H - 7)):
                        if border:
                            sides.append(NEUTRAL_EDGE)
                        else:
                            sides.append(_cov(strong_x if is_v else strong_y, 0, pos, lo, hi, is_v))
                    conf = float(np.prod(sides) ** 0.25)
                    mn = min(sides)
                    if mn < 0.35:
                        continue
                    # prefer big rectangles: confidence dominates, area breaks near ties
                    score = conf * (0.55 + 0.45 * min(1.0, area ** 0.5))
                    if best is None or score > best[0]:
                        best = (score, (x0, y0, x1, y1), conf)
    if best is None:
        return None
    return best[1], best[2]


def _smooth_max(v: np.ndarray, r: int = 1) -> np.ndarray:
    out = v.copy()
    for s in range(1, r + 1):
        out[s:] = np.maximum(out[s:], v[:-s])
        out[:-s] = np.maximum(out[:-s], v[s:])
    return out


def find_artwork_rect(image, strip_frame: bool = True) -> Tuple[Box, float]:
    """-> (box in original pixels, confidence 0..1).  Confidence < ~0.5 means "no clear print found" and the box is
    the whole image (a flat product shot is its own artwork)."""
    from PIL import Image
    im = _load(image)
    W0, H0 = im.size
    s = min(1.0, MAX_SIDE / float(max(W0, H0)))
    small = im.resize((max(8, int(round(W0 * s))), max(8, int(round(H0 * s)))), Image.BILINEAR) if s < 1 else im
    rgb = np.asarray(small)
    H, W = rgb.shape[:2]
    gray = rgb.astype(np.float32).mean(axis=2)
    gx, gy = _grads(rgb)
    strong_x, strong_y = gx > EDGE_T, gy > EDGE_T
    got = _best_rect(strong_x, strong_y, (0, 0, W, H), MIN_AREA, float(W * H))
    full: Box = (0, 0, W0, H0)
    if got is None:
        return full, 0.0
    (x0, y0, x1, y1), conf = got
    if x0 <= 6 or y0 <= 6 or x1 >= W - 7 or y1 >= H - 7:
        conf *= 0.78   # a rectangle that runs off the picture is often a room scene, not the print
    if conf < 0.5:
        return full, conf
    if strip_frame:  # strip the frame / mat: a closed rectangle whose sides are within ~13% of the outer one
        w, h = x1 - x0, y1 - y0
        for _ in range(1):
            w, h = x1 - x0, y1 - y0
            mxx, myy = max(3, int(w * 0.13)), max(3, int(h * 0.13))
            inner = _best_inner(strong_x, strong_y, gray, (x0, y0, x1, y1), mxx, myy)
            if inner is None:
                break
            x0, y0, x1, y1 = inner
    inv = 1.0 / s
    box = (int(round(x0 * inv)), int(round(y0 * inv)), min(W0, int(round(x1 * inv))), min(H0, int(round(y1 * inv))))
    return box, conf


def _band_uniform(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int, limit: float = 26.0) -> bool:
    """A frame or mat is a flat strip: its pixel spread is small."""
    if x1 - x0 < 2 or y1 - y0 < 2:
        return False
    band = gray[y0:y1, x0:x1]
    return float(band.std()) < limit


def _best_inner(strong_x, strong_y, gray, outer, mxx, myy) -> Optional[Box]:
    """Strip a frame / mat: per side, take the strongest closed edge inside a thin band whose pixels are flat
    (frames and mats are plain strips).  Sides without such a strip keep the outer edge."""
    x0, y0, x1, y1 = outer
    ylo, yhi = y0 + int((y1 - y0) * 0.1), y1 - int((y1 - y0) * 0.1)
    xlo, xhi = x0 + int((x1 - x0) * 0.1), x1 - int((x1 - x0) * 0.1)

    def pick(cands, strong, lo, hi, vertical, inner_last):
        scored = [(p, _cov(strong, 0, p, lo, hi, vertical)) for p in cands]
        scored = [t for t in scored if t[1] >= 0.6]
        if not scored:
            return None
        top = max(c for _, c in scored)
        near = [t for t in scored if t[1] >= 0.9 * top]
        return (near[-1] if inner_last else near[0])[0]

    a = pick(range(x0 + 3, min(x1, x0 + mxx + 1)), strong_x, ylo, yhi, True, True)
    b = pick(range(max(x0, x1 - mxx - 1), x1 - 3), strong_x, ylo, yhi, True, False)
    c = pick(range(y0 + 3, min(y1, y0 + myy + 1)), strong_y, xlo, xhi, False, True)
    d = pick(range(max(y0, y1 - myy - 1), y1 - 3), strong_y, xlo, xhi, False, False)
    ix, iy = max(2, int((x1 - x0) * 0.045)), max(2, int((y1 - y0) * 0.045))  # default: shave a frame-sized margin
    nx0, ny0, nx1, ny1 = x0 + ix, y0 + iy, x1 - ix, y1 - iy
    # the outer 35 % of a band may hold the drop shadow / wall: judge flatness on the rest
    if a is not None and _band_uniform(gray, x0 + int(0.35 * (a - x0)) + 1, ylo, a - 1, yhi):
        nx0 = a
    if b is not None and _band_uniform(gray, b + 2, ylo, x1 - int(0.35 * (x1 - b)) - 1, yhi):
        nx1 = b
    if c is not None and _band_uniform(gray, xlo, y0 + int(0.35 * (c - y0)) + 1, xhi, c - 1):
        ny0 = c
    if d is not None and _band_uniform(gray, xlo, d + 2, xhi, y1 - int(0.35 * (y1 - d)) - 1):
        ny1 = d
    if (nx1 - nx0) < 0.5 * (x1 - x0) or (ny1 - ny0) < 0.5 * (y1 - y0):
        return None
    return nx0, ny0, nx1, ny1


def crop_artwork(image, box: Optional[Box] = None):
    im = _load(image)
    if box is None:
        box, _ = find_artwork_rect(im)
    return im.crop(box)


# ---------------------------------------------------------------- dimensions

def fit_dims(aspect: float, w_range: Sequence[float], h_range: Sequence[float], target_area: Optional[float] = None) -> Tuple[float, float]:
    """(w, h) in cm with w/h == aspect inside the (w_min, w_max) and (h_min, h_max) ranges.  Picks the largest size that
    fits the ranges, or the one closest to ``target_area`` (cm^2) when given.  If the aspect cannot meet both
    minimums, the minimums are relaxed (never stretched)."""
    wmin, wmax = float(w_range[0]), float(w_range[1])
    hmin, hmax = float(h_range[0]), float(h_range[1])
    aspect = max(0.2, min(5.0, float(aspect)))
    # w = aspect * h; bounds on h from both ranges
    lo = max(hmin, wmin / aspect)
    hi = min(hmax, wmax / aspect)
    if lo > hi:   # infeasible: clamp to the max box (keep the aspect)
        hi = min(hmax, wmax / aspect)
        lo = hi
    if target_area:
        h = (target_area / aspect) ** 0.5
        h = max(lo, min(hi, h))
    else:
        h = hi
    return round(aspect * h, 1), round(h, 1)

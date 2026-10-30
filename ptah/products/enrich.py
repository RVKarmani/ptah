"""Enrichment of shortlisted product listings: colours, materials, light specs, 3D model template.

Everything here is offline and deterministic (Pillow + numpy only).  The outputs follow the
"v2 additions" section of docs/contracts.md and are meant to be merged into an items.json entry:

    extract_colors(image)                       -> {"primary","secondary","accent"} hex | None
    classify_material(title, description)       -> {"primary","secondary"} (contract material names)
    parse_light_spec(title, desc, category)     -> contract `light` dict | None (not a light)
    infer_template(category, title)             -> contract `model` dict
    enrich_listing(item, image, description)    -> item copy with all of the above filled in
"""
from __future__ import annotations

import io
import math
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

# =============================================================================
# text helpers
# =============================================================================

def _norm(text: Optional[str]) -> str:
    """Lower-case, strip accents ("bouclé" -> "boucle"), unify dashes/spaces."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower().replace("–", "-").replace("—", "-").replace("×", "x")
    return re.sub(r"\s+", " ", t)


# category synonyms -> contract template names
_CATEGORY_ALIASES = {
    "couch": "sofa", "loveseat": "sofa", "love_seat": "sofa", "sectional": "sofa", "settee": "sofa",
    "accent_chair": "armchair", "lounge_chair": "armchair", "recliner": "armchair", "arm_chair": "armchair",
    "dining_chair": "chair", "stool": "chair", "office_chair": "chair", "bar_stool": "chair",
    "center_table": "coffee_table", "centre_table": "coffee_table", "cocktail_table": "coffee_table", "ottoman": "coffee_table",
    "end_table": "side_table", "sidetable": "side_table", "accent_table": "side_table",
    "bedside_table": "nightstand", "night_stand": "nightstand", "bedside": "nightstand",
    "writing_desk": "desk", "computer_desk": "desk", "table": "dining_table",
    "tv_unit": "tv_stand", "tv_console": "tv_stand", "media_console": "tv_stand", "tv_cabinet": "tv_stand", "media_unit": "tv_stand",
    "bookcase": "bookshelf", "shelving": "bookshelf", "shelf": "bookshelf", "shelving_unit": "bookshelf", "etagere": "bookshelf",
    "sideboard": "cabinet", "credenza": "cabinet", "dresser": "cabinet", "wardrobe": "cabinet", "storage": "cabinet", "chest": "cabinet",
    "carpet": "rug", "runner": "rug", "area_rug": "rug",
    "standing_lamp": "floor_lamp", "floor_light": "floor_lamp", "standard_lamp": "floor_lamp", "torchiere": "floor_lamp",
    "bedside_lamp": "table_lamp", "nightstand_lamp": "table_lamp", "table_light": "table_lamp", "lamp": "table_lamp",
    "desk_lamp": "table_lamp",
    "pendant_light": "pendant", "pendant_lamp": "pendant", "ceiling_light": "pendant", "ceiling_lamp": "pendant",
    "hanging_lamp": "pendant", "chandelier": "pendant", "ceiling_pendant": "pendant",
    "wall_lamp": "sconce", "wall_light": "sconce", "wall_sconce": "sconce",
    "light_strip": "led_strip", "strip_light": "led_strip", "led": "led_strip", "led_light_strip": "led_strip", "led_tape": "led_strip",
    "picture_lamp": "picture_light", "art_light": "picture_light",
    "painting": "art", "print": "art", "artwork": "art", "wall_art": "art", "canvas": "art", "framed_print": "art",
    "houseplant": "plant", "potted_plant": "plant", "tree": "plant",
    "wall_mirror": "mirror", "floor_mirror": "mirror",
    # v3: owned items and decor
    "electric_guitar": "guitar", "acoustic_guitar": "guitar", "bass_guitar": "guitar", "bass": "guitar",
    "guitar_amp": "amp", "amplifier": "amp", "guitar_amplifier": "amp", "combo_amp": "amp",
    "books": "book_stack", "book": "book_stack", "stack_of_books": "book_stack",
    "record_crate": "vinyl_crate", "record_box": "vinyl_crate", "vinyl_storage": "vinyl_crate", "records": "vinyl_crate",
    "photo_frame": "frame", "picture_frame": "frame",
    "pillow": "cushion", "throw_pillow": "cushion", "scatter_cushion": "cushion", "accent_pillow": "cushion",
    "blanket": "throw", "throw_blanket": "throw",
    "succulent": "small_plant", "desk_plant": "small_plant", "mini_plant": "small_plant",
    "serving_tray": "tray", "decor_tray": "tray",
    "guitar_wall_hanger": "guitar_hanger", "wall_hanger": "guitar_hanger",
}
TEMPLATES = ("sofa", "armchair", "coffee_table", "side_table", "dining_table", "desk", "chair", "bed", "nightstand",
             "bookshelf", "cabinet", "tv_stand", "rug", "floor_lamp", "table_lamp", "pendant", "sconce", "led_strip",
             "picture_light", "plant", "art", "mirror", "generic",
             # v3: owned items and decor
             "guitar", "amp", "guitar_hanger", "book_stack", "vinyl_crate", "vase", "frame", "cushion", "throw", "candle",
             "small_plant", "poster", "tray")


def normalize_category(category: Optional[str]) -> str:
    c = re.sub(r"[\s\-/]+", "_", _norm(category)).strip("_")
    c = _CATEGORY_ALIASES.get(c, c)
    return c if c in TEMPLATES else (c or "generic")


# =============================================================================
# 1. colours
# =============================================================================

ImageLike = Union[str, bytes, bytearray, "os.PathLike", io.IOBase, "PILImage"]  # noqa: F821


def _load_rgb(image) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """-> (rgb uint8 HxWx3, transparent bool HxW) or None."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    try:
        if isinstance(image, Image.Image):
            im = image
        elif isinstance(image, (bytes, bytearray)):
            im = Image.open(io.BytesIO(bytes(image)))
        else:
            im = Image.open(image)
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA")
        im.thumbnail((144, 144))
        arr = np.asarray(im, dtype=np.uint8)
    except Exception:
        return None
    if arr.ndim != 3 or arr.shape[0] < 4 or arr.shape[1] < 4:
        return None
    transparent = (arr[:, :, 3] < 24) if has_alpha else np.zeros(arr.shape[:2], bool)
    return arr[:, :, :3], transparent


def _grow(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Binary flood fill: the 4-connected components of ``allowed`` that touch ``seed``."""
    cur = seed & allowed
    while True:
        nxt = cur.copy()
        nxt[1:, :] |= cur[:-1, :]
        nxt[:-1, :] |= cur[1:, :]
        nxt[:, 1:] |= cur[:, :-1]
        nxt[:, :-1] |= cur[:, 1:]
        nxt &= allowed
        if nxt.sum() == cur.sum():
            return cur
        cur = nxt


def _erode(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    out[1:, :] &= mask[:-1, :]
    out[:-1, :] &= mask[1:, :]
    out[:, 1:] &= mask[:, :-1]
    out[:, :-1] &= mask[:, 1:]
    out[0, :] = out[-1, :] = False
    out[:, 0] = out[:, -1] = False
    return out


def _foreground_mask(rgb: np.ndarray, transparent: np.ndarray, tol: float) -> np.ndarray:
    """True for product pixels: not transparent and not studio background (near-white / near-uniform
    colour connected to the image border)."""
    h, w, _ = rgb.shape
    f = rgb.astype(np.float32)
    border = np.zeros((h, w), bool)
    border[:2, :] = border[-2:, :] = True
    border[:, :2] = border[:, -2:] = True
    bpx = f[border & ~transparent]
    bg = transparent.copy()
    if bpx.size:
        med = np.median(bpx, axis=0)
        dist = np.sqrt(((f - med) ** 2).sum(axis=2))
        near_uniform = dist <= tol
        near_white = (f.min(axis=2) >= 232) & ((f.max(axis=2) - f.min(axis=2)) <= 22)
        cand = near_uniform | near_white
        # only treat the border as a studio backdrop when most of it is uniform/white
        border_ok = (cand & border & ~transparent).sum() >= 0.5 * max(1, (border & ~transparent).sum())
        if border_ok:
            bg |= _grow(border & cand | (border & transparent), cand | transparent)
    return ~bg


def _kmeans(px: np.ndarray, k: int, iters: int = 14) -> Tuple[np.ndarray, np.ndarray]:
    """Deterministic k-means (farthest-point init).  px: Nx3 float32.  -> (centres kx3, labels N)."""
    n = len(px)
    k = max(1, min(k, n))
    centres = [px[0]]
    d = np.linalg.norm(px - centres[0], axis=1)
    for _ in range(1, k):
        centres.append(px[int(np.argmax(d))])
        d = np.minimum(d, np.linalg.norm(px - centres[-1], axis=1))
    c = np.array(centres, dtype=np.float32)
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(iters):
        dist = ((px[:, None, :] - c[None, :, :]) ** 2).sum(axis=2)
        labels = dist.argmin(axis=1)
        new = c.copy()
        for j in range(k):
            m = labels == j
            if m.any():
                new[j] = px[m].mean(axis=0)
        if np.allclose(new, c, atol=0.25):
            c = new
            break
        c = new
    return c, labels


def _hex(c: Sequence[float]) -> str:
    r, g, b = (int(max(0, min(255, round(float(v))))) for v in c)
    return f"#{r:02x}{g:02x}{b:02x}"


def extract_colors(image: "ImageLike", k: int = 5, min_area: float = 0.02) -> Optional[Dict[str, Optional[str]]]:
    """Dominant product colours from a product photo.

    The studio background (near-white / near-uniform pixels connected to the border, or transparent
    pixels) is masked out, the remaining pixels are clustered with a small k-means, similar clusters
    are merged, clusters under ``min_area`` of the product are ignored and the rest is ordered by area.
    Returns ``{"primary","secondary","accent"}`` (hex strings, missing ones None) or None when the image
    cannot be read or has no usable product pixels.
    """
    try:
        loaded = _load_rgb(image)
        if loaded is None:
            return None
        rgb, transparent = loaded
        fg = None
        for tol in (34.0, 12.0):  # a white product on a white backdrop needs the stricter pass
            m = _foreground_mask(rgb, transparent, tol)
            if m.mean() >= 0.03:
                fg = m
                break
        if fg is None:
            return None
        inner = _erode(fg)  # drop the anti-aliased fringe that mixes product and backdrop
        if inner.sum() >= 0.5 * fg.sum() and inner.sum() >= 40:
            fg = inner
        px = rgb[fg].astype(np.float32)
        if len(px) < 20:
            return None
        if len(px) > 6000:
            px = px[np.linspace(0, len(px) - 1, 6000).astype(int)]
        centres, labels = _kmeans(px, k)
        counts = np.bincount(labels, minlength=len(centres)).astype(np.float64)
        # merge near-duplicate clusters (weighted mean)
        groups: List[List[float]] = []  # [weight, r, g, b]
        for c, n in sorted(zip(centres, counts), key=lambda t: -t[1]):
            if n <= 0:
                continue
            for g in groups:
                if np.linalg.norm(np.array(g[1:]) - c) < 38.0:
                    tot = g[0] + n
                    g[1:] = list((np.array(g[1:]) * g[0] + c * n) / tot)
                    g[0] = tot
                    break
            else:
                groups.append([float(n)] + [float(v) for v in c])
        total = sum(g[0] for g in groups)
        groups = [g for g in groups if g[0] / total >= min_area]
        groups.sort(key=lambda g: -g[0])
        if not groups:
            return None
        cols = [_hex(g[1:]) for g in groups[:3]]
        cols += [None] * (3 - len(cols))
        return {"primary": cols[0], "secondary": cols[1], "accent": cols[2]}
    except Exception:
        return None


# =============================================================================
# 2. materials
# =============================================================================

# (material, weight, regex).  Specific phrases come first and are blanked out once matched,
# so "light oak" is light_wood and does not also count as oak.
_MATERIAL_RULES: List[Tuple[str, float, str]] = [
    # woods, specific first
    ("light_wood", 1.0, r"\b(?:light|pale|bleached|whitewashed|natural|blonde|white)\s+(?:oak|wood|ash|pine|timber)\b"),
    ("dark_wood", 1.0, r"\b(?:dark|espresso|black|charred|ebonised|ebonized|smoked)\s+(?:oak|wood|ash|timber|stained|finish)\b"),
    ("walnut", 1.0, r"\b(?:walnut|cherry|sheesham)\b"),
    ("dark_wood", 1.0, r"\b(?:mahogany|ebony|wenge|rosewood|espresso)\b"),
    ("light_wood", 1.0, r"\b(?:acacia|ash|birch|maple|beech|pine|bamboo|rubberwood|poplar|mango(?:\s+wood)?|paulownia|fir)\b"),
    ("oak", 1.0, r"\b(?:oak|teak|elm)\b"),
    ("light_wood", 0.5, r"\b(?:plywood|mdf|particle\s?board|chipboard|engineered\s+wood)\b"),
    ("oak", 0.5, r"\b(?:solid\s+wood|wooden|wood|timber|veneer)\b"),
    # fabrics
    ("boucle", 1.0, r"\b(?:boucle|terry|teddy|sherpa|shearling)\b"),
    ("velvet", 1.0, r"\b(?:velvet|velour|chenille|plush)\b"),
    ("linen", 1.0, r"\b(?:linen|flax)\b"),
    ("wool", 1.0, r"\b(?:wool(?:l?en)?|felt|tweed|cashmere|mohair|kilim)\b"),
    ("cotton", 1.0, r"\b(?:cotton|canvas|denim|chambray|twill|jersey)\b"),
    ("cotton", 0.4, r"\b(?:polyester|microfib(?:er|re)|upholster(?:ed|y)|fabric)\b"),
    ("leather", 1.0, r"\b(?:leather(?:ette)?|suede|nubuck|pu\s+leather|vegan\s+leather|faux\s+leather)\b"),
    ("rattan", 1.0, r"\b(?:rattan|wicker|cane|caned|seagrass|jute|rope|woven|raffia|sisal|abaca|water\s+hyacinth)\b"),
    # metals
    ("black_metal", 1.0, r"\b(?:black|matte?\s+black|graphite|gunmetal)\s+(?:metal|steel|iron|frame|finish|aluminium|aluminum)\b|\b(?:wrought\s+iron|cast\s+iron|iron)\b"),
    ("brass", 1.0, r"\b(?:brushed\s+gold|antique\s+gold|satin\s+gold|brushed\s+brass|antique\s+brass|brass|bronze|copper|gold(?:en|-?tone|\s+tone|\s+finish)?|champagne\s+(?:gold|metal))\b"),
    ("chrome", 1.0, r"\b(?:chrome|chromed|nickel|stainless(?:\s+steel)?|steel|silver(?:-?tone)?|aluminium|aluminum|polished\s+metal|brushed\s+metal)\b"),
    ("black_metal", 0.5, r"\b(?:metal|powder[\s-]?coated)\b"),
    # mineral / other
    ("marble", 1.0, r"\b(?:marble|marbled|calacatta|carrara)\b"),
    ("stone", 1.0, r"\b(?:stone|granite|slate|concrete|cement|travertine|terrazzo|quartz|sandstone|limestone)\b"),
    ("ceramic", 1.0, r"\b(?:ceramic|porcelain|terracotta|stoneware|earthenware|glazed)\b"),
    ("glass", 1.0, r"\b(?:glass|crystal|tempered|frosted|opal|mirrored)\b"),
    ("plastic", 1.0, r"\b(?:plastic|polypropylene|acrylic|resin|abs|pvc|polycarbonate|silicone)\b"),
    ("painted", 1.0, r"\b(?:painted|lacquer(?:ed)?|enamel(?:led|ed)?|gloss(?:y)?\s+finish)\b"),
]
_MATERIAL_RE = [(m, w, re.compile(p)) for m, w, p in _MATERIAL_RULES]


def classify_material(title: Optional[str], description: Optional[str] = "") -> Dict[str, Optional[str]]:
    """Map listing text to the contract's material names.  The title counts triple; ties go to the
    material mentioned first.  -> {"primary": name|None, "secondary": name|None}."""
    score: Dict[str, float] = {}
    first: Dict[str, int] = {}
    pos_base = 0
    for text, weight in ((_norm(title), 3.0), (_norm(description), 1.0)):
        t = text
        for mat, w, rx in _MATERIAL_RE:
            for m in rx.finditer(t):
                score[mat] = score.get(mat, 0.0) + w * weight
                first.setdefault(mat, pos_base + m.start())
            t = rx.sub(lambda mo: " " * (mo.end() - mo.start()), t)
        pos_base += len(text) + 1
    ranked = sorted(score, key=lambda m: (-round(score[m], 3), first[m]))
    return {"primary": ranked[0] if ranked else None, "secondary": ranked[1] if len(ranked) > 1 else None}


# =============================================================================
# 3. lights
# =============================================================================

LIGHT_CATEGORIES = ("pendant", "floor_lamp", "table_lamp", "sconce", "led_strip", "picture_light")

# category -> (layer, kind, default lumens)
_LIGHT_DEFAULTS = {
    "pendant": ("ambient", "point", 900.0),
    "floor_lamp": ("task", "point", 900.0),
    "table_lamp": ("accent", "point", 450.0),
    "sconce": ("accent", "point", 350.0),
    "led_strip": ("accent", "line", 450.0),   # per metre for strips, see below
    "picture_light": ("accent", "spot", 250.0),
}
_TYPICAL_DIMS = {  # w, d, h (cm) used for emit_offset when the caller has no dimensions
    "pendant": (40, 40, 35), "floor_lamp": (40, 40, 160), "table_lamp": (28, 28, 45),
    "sconce": (15, 15, 25), "led_strip": (100, 2, 2), "picture_light": (40, 12, 8),
}
LM_PER_W = {"led": 90.0, "cfl": 55.0, "halogen": 18.0, "incandescent": 14.0}
STRIP_LM_PER_M = 450.0


def _first_int(s: str) -> int:
    return int(s.replace(",", "").replace(" ", ""))


def _detect_color_temp(t: str) -> Tuple[int, bool]:
    """-> (kelvin, estimated)"""
    ks = [int(v) for v in re.findall(r"\b(\d{4})\s*k\b", t) if 1500 <= int(v) <= 7000]
    if ks:
        return min(ks), False  # a tunable range defaults to its warm end
    if re.search(r"\b(?:extra|ultra|very)\s+warm\b|\bcandle(?:light)?\b", t):
        return 2200, False
    if re.search(r"\bwarm\b|\bsoft\s+white\b|\bsoft\s+light\b|\bgolden\s+light\b", t):
        return 2700, False
    if re.search(r"\bdaylight\b|\bcool\s+daylight\b|\bpure\s+white\b", t):
        return 5000, False
    if re.search(r"\bcool\s+white\b|\bcool\b", t):
        return 4500, False
    if re.search(r"\b(?:neutral|natural)\s+white\b|\bneutral\b", t):
        return 4000, False
    return 2700, True


def _detect_watts(t: str, w_cm: float) -> Optional[Tuple[float, str]]:
    """-> (lumens, basis) from a wattage statement, or None."""
    bulb = "led"
    if re.search(r"\bincandescent\b|\btungsten\b", t):
        bulb = "incandescent"
    elif re.search(r"\bhalogen\b", t):
        bulb = "halogen"
    elif re.search(r"\bcfl\b|\bfluorescent\b", t):
        bulb = "cfl"
    # per-metre strips: "12W/m", "12 w per metre"
    m = re.search(r"(\d+(?:\.\d+)?)\s*w\s*(?:/|per)\s*(?:m|meter|metre)\b", t)
    if m:
        return float(m.group(1)) * LM_PER_W[bulb] * (w_cm / 100.0), bulb
    m = re.search(r"\b(\d)\s*x\s*(\d+(?:\.\d+)?)\s*w(?:att)?s?\b", t)
    if m:
        n, watts = int(m.group(1)), float(m.group(2))
        return n * watts * LM_PER_W[bulb], bulb
    m = re.search(r"(max(?:imum)?\.?|up to)\s*(\d+(?:\.\d+)?)\s*w(?:att)?s?\b", t)
    if m:  # socket rating: assume an old-style incandescent equivalent
        return float(m.group(2)) * LM_PER_W["incandescent"], "incandescent"
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:w|watts?)\b", t)
    if m and 0.5 <= float(m.group(1)) <= 300:
        return float(m.group(1)) * LM_PER_W[bulb], bulb
    return None


def _detect_lumens(t: str, w_cm: float, category: str) -> Tuple[Optional[float], bool]:
    """-> (lumens or None, estimated)"""
    m = re.search(r"(\d[\d,]*)\s*(?:lm|lumens?)\s*(?:/|per)\s*(?:m|meter|metre)\b", t)
    if m:
        return _first_int(m.group(1)) * (w_cm / 100.0), False
    m = re.search(r"\b(\d[\d,]*)\s*(?:lm|lumens?)\b", t)
    if m:
        return float(_first_int(m.group(1))), False
    wl = _detect_watts(t, w_cm)
    if wl is not None:
        return wl[0], True
    return None, True


def default_emit_offset(category: str, w: float, d: float, h: float, template: Optional[dict] = None) -> List[float]:
    """Bulb position in item-local cm (x, y up from the item's base, z) estimated from dims and template."""
    params = (template or {}).get("params", {}) if template else {}
    if category == "floor_lamp":
        if params.get("arc"):
            return [0.0, round(h * 0.9, 1), round(d * 0.3, 1)]
        return [0.0, round(h * 0.94, 1), 0.0]
    if category == "table_lamp":
        return [0.0, round(h * 0.8, 1), 0.0]
    if category == "pendant":
        return [0.0, round(h * 0.4, 1), 0.0]
    if category == "sconce":
        return [0.0, round(h * 0.65, 1), round(d * 0.35, 1)]
    if category == "picture_light":
        return [0.0, round(h * 0.5, 1), round(d * 0.5, 1)]
    if category == "led_strip":
        return [0.0, round(h / 2.0, 1), 0.0]
    return [0.0, round(h * 0.8, 1), 0.0]


def parse_light_spec(title: Optional[str], description: Optional[str] = "", category: Optional[str] = None,
                     dims: Optional[Sequence[float]] = None, model: Optional[dict] = None) -> Optional[dict]:
    """Contract ``light`` dict from listing text.  Returns None when ``category`` is not a luminaire.

    * colour temperature: "2700K", "warm white" (2700), "daylight" (5000), ...  default 2700 (estimated)
    * lumens: "800 lumens", "450 lm/m" (strips, scaled by width) or estimated from watts and bulb type
      (LED 90 lm/W, incandescent 14 lm/W) or a per-category default
    * dimmable / RGB (``color_changing``) from keywords
    * layer + kind defaults per category; emit_offset from ``dims`` = (w, d, h) in cm and the model template
    ``estimated`` lists the fields that were guessed rather than read from the text.
    """
    cat = normalize_category(category)
    full = _norm(f"{title or ''} . {description or ''}")
    if cat == "table_lamp" and re.search(r"\b(?:desk|task|reading|clip|clamp|gooseneck|architect)\b", full):
        task_lamp = True
    else:
        task_lamp = False
    if cat not in LIGHT_CATEGORIES:
        return None
    layer, kind, def_lm = _LIGHT_DEFAULTS[cat]
    w_cm, d_cm, h_cm = (list(dims) + [None, None, None])[:3] if dims else _TYPICAL_DIMS[cat]
    td = _TYPICAL_DIMS[cat]
    w_cm, d_cm, h_cm = (w_cm or td[0], d_cm or td[1], h_cm or td[2])
    estimated: List[str] = []

    if task_lamp:
        layer = "task"
    if cat == "floor_lamp" and re.search(r"\b(?:uplight(?:er)?|up-?light|torchiere)\b", full):
        layer = "ambient"
    if cat == "floor_lamp" and re.search(r"\b(?:ambient|mood)\b", full) and not re.search(r"\b(?:reading|task)\b", full):
        layer = "ambient"
    if cat == "sconce" and re.search(r"\b(?:reading|swing[\s-]?arm|task)\b", full):
        layer = "task"
    if cat == "pendant" and re.search(r"\b(?:spot(?:light)?s?|down-?light|track)\b", full):
        kind = "spot"

    ct, ct_est = _detect_color_temp(full)
    if ct_est:
        estimated.append("color_temp_k")
    lumens, lm_est = _detect_lumens(full, w_cm, cat)
    if lumens is None:
        lumens = def_lm * (w_cm / 100.0 if cat == "led_strip" else 1.0)
        estimated.append("lumens")
    elif lm_est:
        estimated.append("lumens")
    nb = re.search(r"\b(\d)[\s-]*(?:light|bulb|lamp|arm|head)s?\b", full)
    if nb and cat in ("pendant", "sconce") and 2 <= int(nb.group(1)) <= 9 and "lumens" in estimated:
        lumens *= int(nb.group(1))
    dimmable = bool(re.search(r"\bdimm?(?:able|er|ing)\b|\badjustable\s+brightness\b|\b3-?way\b", full)) and \
        not re.search(r"\bnon-?\s?dimm?able\b|\bnot\s+dimm?able\b|\bno\s+dimm", full)
    color_changing = bool(re.search(r"\brgb(?:w|ic|cw)?\b|\bcolou?r[\s-]?chang\w*|\bmulti-?colou?r\w*|\b16\s*million\b|\bcolou?r\s+modes?\b", full))

    tmpl = model or infer_template(cat, title or "")
    light = {
        "layer": layer,
        "kind": kind,
        "color_temp_k": int(ct),
        "lumens": int(round(lumens)),
        "dimmable": dimmable,
        "rgb": None,
        "emit_offset": default_emit_offset(cat, float(w_cm), float(d_cm), float(h_cm), tmpl),
    }
    if kind == "spot":
        light["cone_deg"] = 35 if cat == "picture_light" else 60
    if color_changing:
        light["color_changing"] = True
    if estimated:
        light["estimated"] = estimated
    return light


# =============================================================================
# 4. model template
# =============================================================================

def _shade(t: str, default: str) -> str:
    if re.search(r"\b(?:bare|exposed|edison|filament|naked)\b|\bno\s+shade\b|\bshadeless\b", t):
        return "none"
    if re.search(r"\b(?:globe|orb|ball|sphere|opal|spherical|round\s+(?:glass|shade))\b", t):
        return "globe"
    if re.search(r"\b(?:dome|bowl|bell|mushroom|hemisphere|half[\s-]?sphere)\b", t):
        return "dome"
    if re.search(r"\b(?:cone|conical|empire|tapered\s+shade|funnel|cone-shaped)\b", t):
        return "cone"
    if re.search(r"\b(?:drum|cylinder|cylindrical|barrel|tube)\b", t):
        return "drum"
    if re.search(r"\b(?:linen|fabric|pleated|paper|rice\s+paper)\s+shade\b", t):
        return "drum"
    return default


def _legs(t: str, default: str) -> str:
    if re.search(r"\bhair-?pin\b", t):
        return "hairpin"
    if re.search(r"\b(?:no\s+legs|legless|skirted|plinth|pedestal|platform|floor[\s-]?standing|sled)\b", t):
        return "none"
    if re.search(r"\b(?:tapered|splayed|mid-?century|angled|slanted|peg)\b.*\blegs?\b|\blegs?\b.*\b(?:tapered|splayed)\b|\btapered\b", t):
        return "tapered"
    if re.search(r"\b(?:straight|square|cylindrical|turned)\s+legs?\b|\bmetal\s+legs?\b", t):
        return "straight"
    return default


def _shape(t: str, default: str = "rect") -> str:
    if re.search(r"\b(?:round|circular|circle|disc|disk|drum\s+table)\b", t):
        return "round"
    if re.search(r"\b(?:oval|elliptical|ellipse|pill|racetrack)\b", t):
        return "oval"
    if re.search(r"\b(?:arch|arched)\b", t):
        return "arch"
    return default


_SEAT_WORDS = {"one": 1, "single": 1, "two": 2, "double": 2, "three": 3, "four": 4, "five": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def _seats(t: str) -> Optional[int]:
    m = re.search(r"\b(one|two|three|four|five|[1-5])[\s-]*(?:seat(?:er|s)?|seater|person)\b", t)
    if m:
        return _SEAT_WORDS[m.group(1)]
    if re.search(r"\bloveseat\b", t):
        return 2
    return None


def infer_template(category: Optional[str], title: Optional[str] = "") -> dict:
    """Contract ``model`` dict {"template", "params"} from a category and title, e.g.
    ``infer_template("floor_lamp", "Arc floor lamp with dome shade")`` ->
    ``{"template": "floor_lamp", "params": {"shade": "dome", "legs": "none", "arc": True}}``."""
    t = _norm(title)
    cat = normalize_category(category)
    if cat not in TEMPLATES or cat == "generic":
        # last resort: look at the title
        for word, tpl in (("sofa", "sofa"), ("couch", "sofa"), ("armchair", "armchair"), ("pendant", "pendant"), ("sconce", "sconce"),
                          ("floor lamp", "floor_lamp"), ("table lamp", "table_lamp"), ("rug", "rug"), ("bookcase", "bookshelf")):
            if word in t:
                cat = tpl
                break
        else:
            cat = "generic"
    p: Dict[str, object] = {}
    if cat == "sofa":
        p["arms"] = not re.search(r"\barmless\b|\bno\s+arms?\b", t)
        p["legs"] = _legs(t, "tapered")
        seats = _seats(t)
        if seats:
            p["seats"] = seats
        if re.search(r"\bl-?\s?shaped?\b|\bsectional\b|\bcorner\b|\bchaise\b", t):
            p["shape"] = "l_shape"
    elif cat == "armchair":
        p["arms"] = not re.search(r"\barmless\b|\bno\s+arms?\b", t)
        p["legs"] = _legs(t, "tapered")
        if re.search(r"\bswivel\b", t):
            p["swivel"] = True
    elif cat in ("coffee_table", "side_table", "dining_table", "desk"):
        p["shape"] = _shape(t)
        p["legs"] = _legs(t, "straight")
        if re.search(r"\bglass\b", t):
            p["top"] = "glass"
        if cat == "desk" and re.search(r"\bdrawers?\b", t):
            p["drawers"] = True
        if cat == "side_table" and re.search(r"\bnest(?:ing)?\b", t):
            p["nesting"] = True
    elif cat == "chair":
        p["legs"] = _legs(t, "straight")
        p["arms"] = bool(re.search(r"\barm(?:s|chair|rests?)\b", t)) and not re.search(r"\barmless\b", t)
    elif cat == "bed":
        p["headboard"] = "none" if re.search(r"\bno\s+headboard\b|\bheadboard-?less\b", t) else ("upholstered" if re.search(r"\bupholster", t) else "panel")
        p["legs"] = _legs(t, "none")
    elif cat == "nightstand":
        p["legs"] = _legs(t, "straight")
        p["drawers"] = 1 if re.search(r"\bdrawer\b", t) else (2 if re.search(r"\b(?:two|2)\b.*drawers?|\bdrawers\b", t) else 0)
    elif cat == "bookshelf":
        m = re.search(r"\b(\d|two|three|four|five|six)[\s-]*(?:tier|shelf|shelves|level)", t)
        if m:
            word = m.group(1)
            p["shelves"] = int(word) if word.isdigit() else {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[word]
        p["doors"] = bool(re.search(r"\bdoors?\b|\bglass\s+front\b", t))
        p["legs"] = _legs(t, "none")
    elif cat in ("cabinet", "tv_stand"):
        p["doors"] = bool(re.search(r"\bdoors?\b|\bsliding\b|\bcupboard\b", t))
        p["legs"] = _legs(t, "tapered" if cat == "tv_stand" else "none")
    elif cat == "rug":
        p["shape"] = _shape(t)
        if re.search(r"\brunner\b", t):
            p["shape"] = "runner"
    elif cat == "floor_lamp":
        p["shade"] = _shade(t, "drum")
        p["legs"] = "tripod" if re.search(r"\btripod\b", t) else "none"
        if re.search(r"\barc(?:h|ing)?\b", t):
            p["arc"] = True
            if p["shade"] == "drum" and not re.search(r"\bdrum\b|\bcylind", t):
                p["shade"] = "dome"
    elif cat == "table_lamp":
        p["shade"] = _shade(t, "drum")
        p["legs"] = "none"
    elif cat == "pendant":
        p["shade"] = _shade(t, "globe" if not re.search(r"\b(?:dome|cone|drum)\b", t) else "drum")
        if re.search(r"\bcluster\b|\bmulti\b|\bchandelier\b", t):
            p["cluster"] = True
    elif cat == "sconce":
        p["shade"] = _shade(t, "dome")
        if re.search(r"\bswing[\s-]?arm\b|\badjustable\b", t):
            p["arm"] = "swing"
    elif cat == "picture_light":
        p["arm"] = True
    elif cat == "plant":
        kind = "tree"
        for word, k in (("cactus", "cactus"), ("succulent", "cactus"), ("monstera", "leafy"), ("fiddle", "tree"), ("palm", "palm"),
                        ("snake", "upright"), ("sansevieria", "upright"), ("fern", "leafy"), ("olive", "tree"), ("rubber", "tree")):
            if word in t:
                kind = k
                break
        p["kind"] = kind
        p["pot"] = _shape(t, "round")
    elif cat == "art":
        p["frame"] = "none" if re.search(r"\bunframed\b|\bgallery[\s-]?wrapped\b|\bcanvas\b", t) else "framed"
    elif cat == "mirror":
        p["shape"] = _shape(t)
    return {"template": cat, "params": p}


# =============================================================================
# convenience: one call per shortlisted listing
# =============================================================================

def enrich_listing(item: dict, image: Optional["ImageLike"] = None, description: str = "") -> dict:
    """Return a copy of an items.json entry with ``model``, ``colors``, ``material`` and (for luminaires)
    ``light`` filled in.  Existing values are kept."""
    out = dict(item)
    title = item.get("name") or item.get("title") or ""
    cat = item.get("category")
    out.setdefault("model", infer_template(cat, title))
    if "colors" not in out:
        cols = extract_colors(image) if image is not None else None
        out["colors"] = cols or {"primary": None, "secondary": None, "accent": None}
    if "material" not in out:
        out["material"] = classify_material(title, description or item.get("why", ""))
    if "light" not in out:
        dims = (item.get("w"), item.get("d"), item.get("h")) if item.get("w") else None
        spec = parse_light_spec(title, description, cat, dims=dims, model=out["model"])
        if spec is not None:
            out["light"] = spec
    return out

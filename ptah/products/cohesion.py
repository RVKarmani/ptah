"""Cohesion scoring: does a real product FIT the designed room, and do the chosen pieces read as ONE aesthetic?

The room has an aesthetic (design_spec.json: wall colour, accents, materials, avoid list, Qloo decor / ambience tags) and every
shopping candidate is scored against it, offline and deterministically (numpy + Pillow, no LLM, no network):

    palette   CIEDE2000 between the candidate's colours (background / frame removed) and the room palette (walls, floor, accents,
              the designed pieces' colours); saturated colours that match nothing in the room are CLASHES and are punished,
              warm neutrals are tasteful and score well, cool greys / pure black masses do not
    style     title / description words against the aesthetic's positive vocabulary (slot keywords, spec words, Qloo tags), the
              avoid list (chrome, glossy plastic, cool grey ...) and generally off-mood vocabulary (neon, cartoon fan merch ...)
    material  furniture: material words against the spec materials / avoid list and the room's wood families / metals
    scale     size and aspect against the slot
    quality   store quality + how clean the image / cut-out is (never decides alone)

and for sets:

    art set        palettes overlap, mediums are compatible (a photograph next to a cartoon is not), eras agree, chroma matches
    furniture set  a swap may not add a new wood family or a new metal to the room

Everything returns plain dicts (JSON-serialisable) so the breakdown can be stored per candidate and written to a report.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .enrich import _foreground_mask, _kmeans, _load_rgb, classify_material

Lab = Tuple[float, float, float]

# ----------------------------------------------------------------------------------------------------------------- colour maths

def hex_to_rgb(h: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not h or not re.fullmatch(r"#[0-9a-fA-F]{6}", h):
        return None
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def rgb_to_hex(rgb: Sequence[float]) -> str:
    r, g, b = (int(max(0, min(255, round(float(v))))) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_to_lab(rgb: Sequence[float]) -> Lab:
    """sRGB (0..255) -> CIE L*a*b* (D65)."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    x, y, z = m @ c
    x, y, z = x / 0.95047, y / 1.0, z / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(float(x)), f(float(y)), f(float(z))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def hex_to_lab(h: Optional[str]) -> Optional[Lab]:
    rgb = hex_to_rgb(h)
    return rgb_to_lab(rgb) if rgb else None


def ciede2000(l1: Lab, l2: Lab) -> float:
    """CIEDE2000 colour difference (Sharma et al. reference implementation)."""
    L1, a1, b1 = l1
    L2, a2, b2 = l2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    else:
        hbp = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def chroma_hue(lab: Lab) -> Tuple[float, float]:
    return math.hypot(lab[1], lab[2]), math.degrees(math.atan2(lab[2], lab[1])) % 360


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ramp(x: float, good: float, bad: float) -> float:
    """1 at x <= good, 0 at x >= bad, linear between."""
    if x <= good:
        return 1.0
    if x >= bad:
        return 0.0
    return 1.0 - (x - good) / (bad - good)

# ----------------------------------------------------------------------------------------------------------------- room profile

# Qloo decor / ambience tag -> words that a listing of that mood tends to use
TAG_WORDS: Dict[str, List[str]] = {
    "asian inspired": ["japanese", "asian", "zen", "oriental", "ukiyo", "woodblock", "sumi", "kyoto", "tokyo", "bamboo", "washi", "shoji"],
    "classic": ["classic", "vintage", "traditional", "heritage", "timeless", "antique", "old world"],
    "nostalgic": ["vintage", "retro", "nostalgic", "70s", "1970s", "seventies", "antique", "mid-century", "midcentury"],
    "rustic": ["rustic", "natural", "wood", "wooden", "organic", "woven", "handmade", "earthy"],
    "warm": ["warm", "cozy", "earthy", "amber", "golden", "muted"],
    "cozy": ["cozy", "soft", "warm", "linen", "wool", "knit", "textured"],
    "tropical": ["botanical", "leaf", "leaves", "jungle", "tropical", "forest", "plant", "fern", "nature", "green", "moss"],
    "romantic": ["romantic", "soft", "dreamy", "floral"],
    "artistic": ["abstract", "painting", "watercolor", "watercolour", "illustration", "gallery", "fine art"],
    "contemporary": ["modern", "minimal", "minimalist", "clean", "simple"],
    "traditional": ["traditional", "classic", "heritage", "vintage"],
    "music themed": ["vinyl", "music", "concert", "record", "70s", "1970s", "rock", "band"],
    "old world glamour": ["brass", "gold", "antique", "art deco"],
}
# vocabulary that does not belong in this kind of room, for any slot
OFF_MOOD = ["neon", "glitter", "glow in the dark", "cyberpunk", "futuristic", "holographic", "kawaii", "chibi", "cartoon", "meme", "funny",
            "gaming", "graffiti", "pop art", "psychedelic", "trippy", "blacklight", "fluorescent", "hologram", "motivational",
            "quote", "slogan", "typography", "birthday", "gift for", "gifts for", "fan gift", "anime gift", "a.n.i.m.e", "dorm", "teen",
            "kids", "nursery", "baby", "halloween", "christmas", "skull", "synthwave", "vaporwave", "pastel pink", "hot pink"]
# finishes that read cheap / cold against matte, tactile materials
FINISH_BAD = ["glossy", "high gloss", "mirrored", "metal poster", "magnetic", "displate", "acrylic", "laminated", "plexiglass", "chrome",
              "polished steel", "stainless", "rgb"]

WOOD_FAMILY = {"walnut": "dark", "dark_wood": "dark", "oak": "light", "light_wood": "light"}
METALS = {"brass", "black_metal", "chrome"}


@dataclass
class RoomProfile:
    """The aesthetic a candidate is scored against (built from design_spec.json + items.json + room.json)."""
    name: str = "room"
    wall: Optional[str] = None
    floor_hex: str = "#b99366"
    accents: List[str] = field(default_factory=list)
    item_colors: List[str] = field(default_factory=list)
    materials: List[str] = field(default_factory=list)        # contract names
    avoid: List[str] = field(default_factory=list)
    style_words: List[str] = field(default_factory=list)      # positive vocabulary
    subject_words: List[str] = field(default_factory=list)    # inputs from the why_chains (Spirited Away, Fleetwood Mac ...)
    woods: List[str] = field(default_factory=list)            # wood materials of the designed furniture
    metals: List[str] = field(default_factory=list)           # metals of the designed furniture
    targets: List[Dict[str, Any]] = field(default_factory=list)  # {name, hex, lab, weight}

    def ref_chroma(self) -> float:
        """Typical chroma of the room's palette (walls, floor, accents, pieces), weighted."""
        ws = [(t["weight"], chroma_hue(t["lab"])[0]) for t in self.targets]
        tw = sum(w for w, _ in ws) or 1.0
        return sum(w * c for w, c in ws) / tw

    def to_json(self) -> Dict[str, Any]:
        return {"name": self.name, "wall": self.wall, "floor": self.floor_hex, "accents": self.accents, "materials": self.materials,
                "avoid": self.avoid, "style_words": self.style_words, "woods": sorted(set(self.woods)), "metals": sorted(set(self.metals)),
                "palette": [{"name": t["name"], "hex": t["hex"], "weight": t["weight"]} for t in self.targets]}


_SPEC_MATERIAL_ALIASES = {"walnut": "walnut", "oak": "oak", "linen": "linen", "wool": "wool", "brass": "brass", "ceramic": "ceramic",
                          "glass": "glass", "velvet": "velvet", "cotton": "cotton", "leather": "leather", "rattan": "rattan", "stone": "stone",
                          "marble": "marble", "boucle": "boucle"}
_AVOID_ALIASES = {"chrome": ["chrome", "stainless", "nickel", "polished steel"], "glossy plastic": ["plastic", "glossy", "high gloss", "acrylic", "resin"],
                  "cool grey": ["cool grey", "cool gray", "slate grey", "charcoal grey"], "plastic": ["plastic", "acrylic", "resin"]}
_FLOORS = {"herringbone": "#b99366", "mid_oak": "#b48a58", "dark_wood": "#4a3426", "light_oak": "#d8bf96", "concrete": "#9d9b96",
           "carpet": "#b9b1a3", "tile_light": "#e4e0d7", "tile_dark": "#3b3f45"}


def build_profile(spec: Dict[str, Any], items: Sequence[Dict[str, Any]], room: Optional[Dict[str, Any]] = None) -> RoomProfile:
    """Aesthetic profile of a design bundle (uses the DESIGNED colours / materials of the pieces, never the scouted ones)."""
    pal = spec.get("palette") or {}
    fin = (room or {}).get("finishes") or {}
    floor_id = pal.get("floor") or fin.get("floor") or ""
    p = RoomProfile(name=spec.get("concept_name") or "room", wall=pal.get("wall_hex") or fin.get("wall_color"), floor_hex=_FLOORS.get(floor_id, "#b99366"),
                    accents=[a for a in (pal.get("accents") or []) if hex_to_rgb(a)])
    md = spec.get("material_direction") or {}
    p.materials = [_SPEC_MATERIAL_ALIASES.get(m.lower(), m.lower()) for m in (md.get("materials") or [])]
    p.avoid = [a.lower() for a in (md.get("avoid") or [])]
    for it in items:
        sl = it.get("slot") or {}
        if sl.get("category") in ("art", "poster") or it.get("category") in ("art", "poster"):
            continue
        cols = sl.get("colors") or it.get("colors") or {}
        for k in ("primary", "secondary"):
            if hex_to_rgb(cols.get(k)):
                p.item_colors.append(cols[k].lower())
        mats = sl.get("materials") or it.get("material") or {}
        for m in (mats.get("primary"), mats.get("secondary")):
            if m in WOOD_FAMILY:
                p.woods.append(m)
            elif m in METALS:
                p.metals.append(m)
    words: List[str] = []
    for it in items:
        for t in (it.get("why_chain") or {}).get("tags") or []:
            words += TAG_WORDS.get(str(t.get("name", "")).lower(), [])
    for key in ("palette_why", "materials_why", "lighting_why"):
        for t in (spec.get(key) or {}).get("tags") or []:
            words += TAG_WORDS.get(str(t.get("name", "")).lower(), [])
    words += list(p.materials)
    words += ["muted", "matte", "natural", "japanese", "ghibli", "nature", "forest", "mid-century"]
    p.style_words = sorted({w for w in words if len(w) > 2})
    subj: List[str] = []
    for it in items:
        for i in (it.get("why_chain") or {}).get("inputs") or []:
            subj.append(str(i).lower())
    for i in (spec.get("palette_why") or {}).get("inputs") or []:
        subj.append(str(i).lower())
    p.subject_words = sorted(set(subj))

    def add(name: str, hexv: Optional[str], w: float) -> None:
        lab = hex_to_lab(hexv)
        if lab is None:
            return
        for t in p.targets:
            if ciede2000(t["lab"], lab) < 6:   # same colour already in
                t["weight"] = max(t["weight"], w)
                return
        p.targets.append({"name": name, "hex": str(hexv).lower(), "lab": lab, "weight": w})

    add("wall", p.wall, 1.0)
    add("floor", p.floor_hex, 1.0)
    for i, a in enumerate(p.accents):
        add(f"accent{i + 1}", a, 1.0)
    for i, c in enumerate(p.item_colors):
        add(f"piece{i + 1}", c, 0.8)
    add("walnut", "#4a3426", 0.7)
    return p

# ----------------------------------------------------------------------------------------------------------------- image palette

def image_palette(image: Any, k: int = 6, trim: float = 0.03, remove_background: bool = True) -> List[Dict[str, Any]]:
    """Dominant colour clusters ``[{rgb, hex, lab, weight}]`` (weights sum to 1) of an artwork / product photo, studio
    background and a thin outer ring (frame / mat edge) removed."""
    loaded = _load_rgb(image)
    if loaded is None:
        return []
    rgb, transparent = loaded
    h, w, _ = rgb.shape
    keep = np.ones((h, w), bool)
    if remove_background:
        for tol in (34.0, 12.0):
            m = _foreground_mask(rgb, transparent, tol)
            if m.mean() >= 0.05:
                keep = m
                break
    ring = max(1, int(round(min(h, w) * trim)))
    edge = np.zeros_like(keep)
    edge[:ring, :] = edge[-ring:, :] = True
    edge[:, :ring] = edge[:, -ring:] = True
    keep = keep & ~edge
    if keep.sum() < 40:
        keep = np.ones((h, w), bool)
    px = rgb[keep].astype(np.float32)
    if len(px) > 7000:
        px = px[np.linspace(0, len(px) - 1, 7000).astype(int)]
    centres, labels = _kmeans(px, k)
    counts = np.bincount(labels, minlength=len(centres)).astype(np.float64)
    tot = counts.sum() or 1.0
    out = []
    for c, n in sorted(zip(centres, counts), key=lambda t: -t[1]):
        if n / tot < 0.015:
            continue
        out.append({"rgb": [float(v) for v in c], "hex": rgb_to_hex(c), "lab": rgb_to_lab(c), "weight": float(n / tot)})
    s = sum(o["weight"] for o in out) or 1.0
    for o in out:
        o["weight"] /= s
    return out

def background_fraction(image: Any) -> float:
    """Share of the image that is studio backdrop (near-white / uniform, connected to the border): ~0 for a lifestyle / room photo."""
    loaded = _load_rgb(image)
    if loaded is None:
        return 0.0
    rgb, transparent = loaded
    return float(1.0 - _foreground_mask(rgb, transparent, 34.0).mean())

# ----------------------------------------------------------------------------------------------------------------- palette fit

def cluster_fit(lab: Lab, targets: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """How well one colour sits in the room palette: closeness 0..1, whether it is a clash, and why."""
    d, nearest = min(((ciede2000(lab, t["lab"]), t) for t in targets), key=lambda x: x[0])
    L = lab[0]
    C, H = chroma_hue(lab)
    close = _ramp(d, 6.0, 24.0)
    kind = "palette"
    clash = 0.0
    warm = lab[1] >= -3 and lab[2] >= 0
    if C < 14:                               # neutrals
        if L < 20:
            kind, close = "near-black", min(max(close, 0.4 if L >= 12 else 0.25), 0.55)
        elif L > 90:
            kind, close = "off-white", max(close, 0.8)
        elif warm:
            kind, close = "warm neutral", max(close, 0.55)
        else:
            kind, close = "cool grey", min(close, 0.4)
    elif C >= 30 and d > 22:                 # saturated and unlike anything in the room
        clash = _clip01((d - 22) / 16.0) * _clip01((C - 24) / 25.0 + 0.4)
        kind = "clash"
    return {"close": close, "clash": clash, "kind": kind, "deltaE": d, "nearest": nearest["name"], "chroma": C, "hue": H}


def palette_score(clusters: Sequence[Dict[str, Any]], profile: RoomProfile, slot_colors: Optional[Dict[str, Optional[str]]] = None, art: bool = True) -> Dict[str, Any]:
    """0..1 palette fit of a colour set to the room (and, for furniture, to the designed piece's own colours).  ``art``: pictures must stay
    muted (loudness / accent-restraint penalties); a designed furniture piece may itself BE the room's one saturated accent."""
    if not clusters:
        return {"score": 0.0, "note": "no colours extracted", "clusters": []}
    rows, mean_close, clash = [], 0.0, 0.0
    for c in clusters:
        f = cluster_fit(c["lab"], profile.targets)
        mean_close += c["weight"] * f["close"]
        clash += c["weight"] * f["clash"]
        rows.append({"hex": c["hex"], "w": round(c["weight"], 3), "dE": round(f["deltaE"], 1), "near": f["nearest"], "kind": f["kind"],
                     "close": round(f["close"], 2), "clash": round(f["clash"], 2)})
    # loudness / busyness: a muted room takes muted art; four hue families in one picture read as noise
    mean_c = sum(c["weight"] * chroma_hue(c["lab"])[0] for c in clusters)
    ref_c = profile.ref_chroma()
    loud = 0.35 * _clip01((mean_c - (ref_c + 6.0)) / 28.0) if art else 0.0
    fam = {int(chroma_hue(c["lab"])[1] // 50) for c in clusters if c["weight"] >= 0.07 and chroma_hue(c["lab"])[0] > 25}
    busy = 0.08 * max(0, len(fam) - 3)
    # accent restraint: the room has ONE saturated accent (the red chair); a picture with big areas of very saturated colour competes with it
    sat_share = sum(c["weight"] for c in clusters if chroma_hue(c["lab"])[0] > 45)
    if art:
        loud += 1.5 * max(0.0, sat_share - 0.05)
    # echo: a good piece repeats at least one of the room's chromatic colours (wall, accents) instead of only being "not wrong"
    chrom = [t for t in profile.targets if chroma_hue(t["lab"])[0] >= 14 and t["name"] in ("wall",) + tuple(f"accent{i}" for i in range(1, 8))]
    echo_w = sum(c["weight"] for c in clusters if chrom and min(ciede2000(c["lab"], t["lab"]) for t in chrom) < 14.0)
    echo = _clip01(echo_w / 0.18)
    base = 0.75 * mean_close + 0.25 * echo
    out: Dict[str, Any] = {"score": round(_clip01(base - 0.9 * clash - loud - busy), 3), "echo": round(echo, 3), "mean_close": round(mean_close, 3), "clash": round(clash, 3),
                           "loud": round(loud, 3), "saturated_share": round(sat_share, 3), "busy": round(busy, 3), "mean_chroma": round(mean_c, 1), "hue_families": len(fam), "clusters": rows}
    dom = rows[0]   # the dominant colour decides the first impression: a clashing / cool-grey dominant caps the score
    if dom["kind"] in ("clash", "cool grey") and dom["w"] >= 0.3:
        out["score"] = round(min(out["score"], 0.45), 3)
        out["note"] = f"dominant colour {dom['hex']} is a {dom['kind']}"
    if slot_colors:
        sp = hex_to_lab(slot_colors.get("primary"))
        if sp is not None:
            dE = ciede2000(clusters[0]["lab"], sp)
            sec = hex_to_lab(slot_colors.get("secondary"))
            if sec is not None:   # a two-tone product may lead with the designed secondary colour
                dE = min(dE, 1.15 * ciede2000(clusters[0]["lab"], sec) + 3.0)
            out["slot_dE"] = round(dE, 1)
            out["slot_chroma"] = round(chroma_hue(sp)[0], 1)
            out["slot_match"] = round(_ramp(dE, 14.0, 38.0), 3)
            # cover: how much of the product is in the designed colours (neutral trim - black legs, white shade - counts partly);
            # a product that adds a strong colour the design never had would recolour the piece
            designed = [l for l in (hex_to_lab(slot_colors.get(k)) for k in ("primary", "secondary", "accent")) if l is not None]
            cover = 0.0
            for c in clusters:
                d = min(ciede2000(c["lab"], l) for l in designed)
                if d <= 22.0:
                    cover += c["weight"]
                elif chroma_hue(c["lab"])[0] < 12.0:
                    cover += 0.6 * c["weight"]
            out["slot_cover"] = round(cover, 3)
    return out

# ----------------------------------------------------------------------------------------------------------------- text: style / material

def _has(text: str, word: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None


def style_score(title: str, profile: RoomProfile, slot_keywords: Sequence[str] = (), description: str = "") -> Dict[str, Any]:
    """0..1: the words of a listing against the aesthetic's vocabulary, the avoid list and off-mood words."""
    t = f"{title or ''} {description or ''}".lower()
    pos: List[str] = []
    for w in list(profile.style_words) + [x for kw in slot_keywords for x in re.findall(r"[a-z0-9\-]+", kw.lower()) if len(x) > 3]:
        if _has(t, w) and w not in pos:
            pos.append(w)
    neg = [w for w in OFF_MOOD if _has(t, w)]
    avoid: List[str] = []
    for a in profile.avoid:
        for w in _AVOID_ALIASES.get(a, [a]):
            if _has(t, w):
                avoid.append(w)
    finish = [w for w in FINISH_BAD if _has(t, w) and w not in avoid]
    s = 0.55 + min(0.35, 0.09 * len(pos)) - 0.18 * len(neg) - 0.35 * len(avoid) - 0.15 * len(finish)
    return {"score": round(_clip01(s), 3), "positive": pos[:8], "off_mood": neg[:6], "avoid_hits": avoid, "finish": finish[:4]}


def wood_family(m: Optional[str]) -> Optional[str]:
    return WOOD_FAMILY.get(m or "")


_SOFT = ("velvet", "linen", "wool", "cotton", "boucle")


def material_score(title: str, slot_materials: Dict[str, Optional[str]], profile: RoomProfile, description: str = "",
                   intent: Sequence[str] = (), also_ok: Sequence[str] = (), tolerate: Sequence[str] = ()) -> Dict[str, Any]:
    """Furniture / decor material fit: the listing's materials against the spec materials, the avoid list, the slot's own materials
    and the room's wood families / metals.

    ``intent``   words that confirm the listing IS the designed kind of piece when it names no material (a paper lantern, a fiddle-leaf fig,
                 a record crate): the "no material named" doubt is lifted, and a differing classified material is not held against it
    ``also_ok``  materials that count as the designed material (a walnut guitar hanger for a black one)
    ``tolerate`` materials that are not visible on the piece and are ignored (the plastic of an LED strip)"""
    mat = classify_material(title, description)
    prim, sec = mat.get("primary"), mat.get("secondary")
    tl = f"{title or ''} {description or ''}".lower()
    intent_hit = any(_has(tl, w.lower()) for w in intent)
    if prim in tolerate:
        prim, sec = sec, None
    elif sec in tolerate:
        sec = None
    seen = [m for m in (prim, sec) if m]
    allowed = set(profile.materials) | {m for m in (slot_materials or {}).values() if m} | {"light_wood", "dark_wood", "oak", "walnut", "black_metal", "painted"}
    flags: List[str] = []
    hard = False
    if not seen and intent_hit:
        score = 0.85
    elif not seen:
        score = 0.55   # nothing said: cannot confirm
        flags.append("no material named in the listing")
    else:
        score = 0.85
        for m in seen:
            if m in ("chrome", "plastic"):
                score, hard = 0.0, True
                flags.append(f"avoided material: {m}")
            elif m not in allowed and m not in also_ok:
                score -= 0.3
                flags.append(f"off-palette material: {m}")
        want = (slot_materials or {}).get("primary")
        if want and prim and want != prim and (prim in also_ok or intent_hit and not (wood_family(want) and wood_family(prim))):
            pass
        elif want and prim and want != prim:
            wf, pf = wood_family(want), wood_family(prim)
            if wf and pf and wf == pf:
                score -= 0.05
            elif want in _SOFT and prim in _SOFT:
                score -= 0.12
            else:
                score -= 0.3
                flags.append(f"primary material {prim} instead of the designed {want}")
        elif want and want == prim:
            score += 0.1
        for m in seen:
            if m in WOOD_FAMILY and profile.woods and WOOD_FAMILY[m] not in {WOOD_FAMILY[w] for w in profile.woods}:
                score -= 0.35
                flags.append(f"wood family '{WOOD_FAMILY[m]}' not used in the room")
            if m in METALS and profile.metals and m not in profile.metals:
                score -= 0.35
                flags.append(f"metal '{m}' not used in the room")
    return {"score": round(_clip01(score), 3), "primary": prim, "secondary": sec, "flags": flags, "hard_reject": hard}

# ----------------------------------------------------------------------------------------------------------------- scale

_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\"|”|in(?:ch(?:es)?)?|cm)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(\"|”|in(?:ch(?:es)?)?\b|cm\b)?", re.I)


def listed_size_cm(title: str) -> Optional[Tuple[float, float]]:
    m = _DIM_RE.search(title or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    unit = (m.group(3) or "").lower()
    inches = unit.startswith(("in", '"', "”")) or (not unit and max(a, b) <= 40)
    f = 2.54 if inches else 1.0
    a, b = a * f, b * f
    return (a, b) if 8 <= a <= 400 and 8 <= b <= 400 else None


def scale_score(slot_w: Tuple[float, float], slot_h: Tuple[float, float], title: str, aspect: Optional[float] = None, is_art: bool = False) -> Dict[str, Any]:
    """Size / aspect fit.  Art: the print's aspect against the slot's feasible aspect band, listed size against the slot range.
    Furniture: the listed width against the slot range (unknown = neutral)."""
    notes: List[str] = []
    s = 0.7
    ls = listed_size_cm(title)
    if is_art:
        lo, hi = slot_w[0] / slot_h[1], slot_w[1] / slot_h[0]
        if aspect:
            if lo / 1.03 <= aspect <= hi * 1.03:
                s = 0.95
            else:
                gap = math.log(lo / aspect) if aspect < lo else math.log(aspect / hi)
                s = _clip01(0.9 - 2.0 * gap)
                notes.append(f"aspect {aspect:.2f} outside {lo:.2f}-{hi:.2f}")
        if ls:
            a, b = sorted(ls)
            sa, sb = sorted((0.5 * (slot_w[0] + slot_w[1]), 0.5 * (slot_h[0] + slot_h[1])))
            ratio = math.sqrt((a * b) / (sa * sb))
            if ratio < 0.5 or ratio > 1.6:
                s -= 0.1
                notes.append(f"listed {ls[0]:.0f}x{ls[1]:.0f} cm vs slot ~{sa:.0f}x{sb:.0f}")
    elif ls:
        # the piece's longest side against the slot's longest side (width for a console, height for a bookshelf)
        lo, hi = slot_w if slot_w[1] >= slot_h[1] else slot_h
        w = max(ls)
        ok = lo * 0.75 <= w <= hi * 1.3
        s = 0.9 if ok else 0.35
        if not ok:
            notes.append(f"listed size {ls[0]:.0f}x{ls[1]:.0f} cm outside the slot range")
    return {"score": round(_clip01(s), 3), "notes": notes}

# ----------------------------------------------------------------------------------------------------------------- medium / era (art sets)

_MEDIUM_RULES: List[Tuple[str, str]] = [
    ("woodblock", r"woodblock|wood block|ukiyo|hokusai|hiroshige|sumi[- ]?e|ink wash|ink painting|nihonga|woodcut"),
    ("photo", r"photo|photograph|concert|live at|portrait|stevie nicks|candid|group music"),
    ("illustration", r"anime|a\.n\.i\.m\.e|ghibli|miyazaki|chihiro|no[- ]?face|cartoon|manga|illustration|chibi|movie poster|film poster|fan art|fanart"),
    ("botanical", r"botanical|leaf|leaves|fern|monstera|plant|forest|tree|floral|flower|moss|bamboo"),
    ("painting", r"painting|watercolou?r|oil on|gouache|impressionis|landscape"),
    ("graphic", r"abstract|geometric|mid[- ]century|minimal|bauhaus|retro|vintage|70s|1970s|seventies|screen ?print|risograph"),
]
_MEDIUM_COMPAT: Dict[frozenset, float] = {frozenset((a, b)): v for a, b, v in [
    ("woodblock", "painting", 0.92), ("woodblock", "illustration", 0.78), ("woodblock", "graphic", 0.85), ("woodblock", "botanical", 0.9),
    ("woodblock", "photo", 0.4), ("painting", "illustration", 0.78), ("painting", "botanical", 0.9), ("painting", "graphic", 0.82),
    ("painting", "photo", 0.45), ("illustration", "botanical", 0.8), ("illustration", "graphic", 0.78), ("illustration", "photo", 0.3),
    ("botanical", "graphic", 0.85), ("botanical", "photo", 0.5), ("graphic", "photo", 0.62)]}


def medium_of(title: str) -> str:
    t = (title or "").lower()
    for name, rx in _MEDIUM_RULES:
        if re.search(rx, t):
            return name
    return "graphic"


def medium_compat(a: str, b: str) -> float:
    return 1.0 if a == b else _MEDIUM_COMPAT.get(frozenset((a, b)), 0.6)


_HERITAGE = r"vintage|retro|antique|70s|1970s|seventies|classic|traditional|ukiyo|woodblock|hokusai|hiroshige|heritage|mid[- ]century|nostalg"
_CONTEMP = r"modern|contemporary|digital|neon|anime fan|a\.n\.i\.m\.e|fan art|fanart|futur|cyber"


def era_of(title: str) -> str:
    t = (title or "").lower()
    h, c = bool(re.search(_HERITAGE, t)), bool(re.search(_CONTEMP, t))
    return "heritage" if h and not c else ("contemporary" if c and not h else "neutral")


def palette_overlap(a: Sequence[Dict[str, Any]], b: Sequence[Dict[str, Any]]) -> float:
    """0..1: how much two images share a colour world (symmetric, weight-averaged nearest-colour closeness)."""
    if not a or not b:
        return 0.0

    def one(x: Sequence[Dict[str, Any]], y: Sequence[Dict[str, Any]]) -> float:
        tot = 0.0
        for c in x:
            d = min(ciede2000(c["lab"], o["lab"]) for o in y)
            near = _ramp(d, 12.0, 40.0)
            if chroma_hue(c["lab"])[0] < 14:      # neutrals sit with anything warm
                near = max(near, 0.7 if (c["lab"][1] >= -3 and c["lab"][2] >= 0) else 0.4)
            tot += c["weight"] * near
        return tot

    return round(0.5 * (one(a, b) + one(b, a)), 3)


def _mean_chroma(cs: Sequence[Dict[str, Any]]) -> float:
    return sum(c["weight"] * chroma_hue(c["lab"])[0] for c in cs)


def palette_coverage(sets: Sequence[Sequence[Dict[str, Any]]], profile: RoomProfile, min_weight: float = 0.03, max_dE: float = 14.0) -> Dict[str, Any]:
    """Which of the room's chromatic colours (wall, accents) does the SET of pictures echo?  A good wall repeats several, not one."""
    chrom = [t for t in profile.targets if chroma_hue(t["lab"])[0] >= 14 and (t["name"] == "wall" or t["name"].startswith("accent"))]
    hit = []
    for t in chrom:
        if any(c["weight"] >= min_weight and ciede2000(c["lab"], t["lab"]) < max_dE for cl in sets for c in cl):
            hit.append(t["name"])
    return {"echoed": hit, "of": [t["name"] for t in chrom], "score": round(len(hit) / max(1, min(len(chrom), 4)), 3) if chrom else 0.5}


def art_set_score(a: Dict[str, Any], b: Dict[str, Any], profile: Optional[RoomProfile] = None) -> Dict[str, Any]:
    """Do two art pieces feel like a set (and a good wall for THIS room)?  a / b: {"title", "clusters", "w", "h"} (outer size cm)."""
    pal = palette_overlap(a["clusters"], b["clusters"])
    ma, mb = medium_of(a["title"]), medium_of(b["title"])
    med = medium_compat(ma, mb)
    ea, eb = era_of(a["title"]), era_of(b["title"])
    era = 1.0 if ea == eb or "neutral" in (ea, eb) else 0.7
    area_a, area_b = a.get("w", 1) * a.get("h", 1), b.get("w", 1) * b.get("h", 1)
    r = min(area_a, area_b) / max(area_a, area_b)
    size = 0.9 if (r < 0.65 or r > 0.9) else 0.7          # hero + support, or a true pair; not almost-but-not
    sat = _ramp(abs(_mean_chroma(a["clusters"]) - _mean_chroma(b["clusters"])), 8.0, 30.0)   # a vivid piece beside a muted one reads as two rooms
    out: Dict[str, Any] = {"palette_overlap": pal, "medium": [ma, mb], "medium_compat": med, "era": [ea, eb], "era_compat": era, "size_balance": size,
                           "chroma_match": round(sat, 3)}
    if profile is not None:
        cov = palette_coverage([a["clusters"], b["clusters"]], profile)
        out["coverage"] = cov
        total = 0.30 * pal + 0.20 * med + 0.10 * era + 0.05 * size + 0.15 * sat + 0.20 * cov["score"]
    else:
        total = 0.40 * pal + 0.25 * med + 0.10 * era + 0.05 * size + 0.20 * sat
    out["score"] = round(_clip01(total), 3)
    return out

# ----------------------------------------------------------------------------------------------------------------- candidate totals

ART_WEIGHTS = {"palette": 0.42, "style": 0.22, "scale": 0.10, "quality": 0.10, "taste": 0.16}
FURN_WEIGHTS = {"palette": 0.40, "material": 0.25, "style": 0.15, "scale": 0.10, "quality": 0.10}


def taste_score(title: str, profile: RoomProfile, inspired_by: Optional[str]) -> float:
    """How directly the listing speaks to the taste the slot came from (Spirited Away, Fleetwood Mac ...) or the profile's subjects."""
    t = (title or "").lower()
    if inspired_by and _has(t, inspired_by.lower()):
        return 1.0
    if [w for w in profile.subject_words if len(w) > 3 and _has(t, w)]:
        return 0.9
    mood = [w for w in ("japanese", "ghibli", "miyazaki", "tokyo", "70s", "1970s", "vintage", "retro", "botanical", "forest") if _has(t, w)]
    return 0.55 + min(0.3, 0.1 * len(mood))


def score_art(title: str, clusters: Sequence[Dict[str, Any]], profile: RoomProfile, *, slot_w: Tuple[float, float], slot_h: Tuple[float, float],
              aspect: Optional[float], store_q: float, crop_conf: float, min_side: int, inspired_by: Optional[str], keywords: Sequence[str]) -> Dict[str, Any]:
    pal = palette_score(clusters, profile)
    sty = style_score(title, profile, keywords)
    sca = scale_score(slot_w, slot_h, title, aspect, is_art=True)
    q = _clip01(0.45 * store_q + 0.35 * crop_conf + 0.20 * (1.0 if min_side >= 300 else 0.6 if min_side >= 180 else 0.2))
    taste = taste_score(title, profile, inspired_by)
    parts = {"palette": pal["score"], "style": sty["score"], "scale": sca["score"], "quality": round(q, 3), "taste": round(taste, 3)}
    total = sum(ART_WEIGHTS[k] * parts[k] for k in ART_WEIGHTS)
    if crop_conf < 0.6:   # a mock-up photo with the print only vaguely found: the colours include the room it was photographed in
        parts["quality"] = min(parts["quality"], 0.3)
        total = sum(ART_WEIGHTS[k] * parts[k] for k in ART_WEIGHTS)
    caps = []   # a clashing / cool dominant colour or avoided wording cannot be rescued by taste
    if pal["score"] < 0.45:
        total = min(total, 0.55)
        caps.append("palette below 0.45")
    if sca["score"] < 0.3:   # a portrait print cannot hang in a landscape slot: the layout is built for the slot's shape
        total = min(total, 0.5)
        caps.append("aspect does not fit the slot")
    if crop_conf < 0.6:
        total = min(total, 0.62)
        caps.append(f"print not cleanly cut out (confidence {crop_conf:.2f})")
    if sty["avoid_hits"]:
        total = min(total, 0.45)
        caps.append("avoid-list wording: " + ", ".join(sty["avoid_hits"]))
    return {"total": round(total, 3), "parts": parts, "palette": pal, "style": sty, "scale": sca, "caps": caps,
            "medium": medium_of(title), "era": era_of(title)}


def score_furniture(title: str, clusters: Sequence[Dict[str, Any]], profile: RoomProfile, *, slot_colors: Dict[str, Optional[str]],
                    slot_materials: Dict[str, Optional[str]], slot_w: Tuple[float, float], slot_h: Tuple[float, float], store_q: float,
                    keywords: Sequence[str], description: str = "", bg_fraction: Optional[float] = None, intent: Sequence[str] = (),
                    also_ok: Sequence[str] = (), tolerate: Sequence[str] = ()) -> Dict[str, Any]:
    pal = palette_score(clusters, profile, slot_colors, art=False)
    mat = material_score(title, slot_materials, profile, description, intent, also_ok, tolerate)
    sty = style_score(title, profile, keywords, description)
    sca = scale_score(slot_w, slot_h, title, None, is_art=False)
    pal_part = 0.5 * pal["score"] + 0.5 * pal.get("slot_match", 0.6)   # the room palette AND the designed colour (never recolour a designed piece)
    parts = {"palette": round(pal_part, 3), "material": mat["score"], "style": sty["score"], "scale": sca["score"], "quality": round(_clip01(store_q), 3)}
    total = sum(FURN_WEIGHTS[k] * parts[k] for k in FURN_WEIGHTS)
    return {"total": round(total, 3), "parts": parts, "palette": pal, "material": mat, "style": sty, "scale": sca,
            "studio_photo": None if bg_fraction is None else bool(bg_fraction >= 0.12), "bg_fraction": None if bg_fraction is None else round(bg_fraction, 3)}

# ----------------------------------------------------------------------------------------------------------------- swap gate (furniture)

GATE = {"total": 0.76, "palette": 0.74, "slot_dE": 18.0, "accent_dE": 12.0, "slot_cover": 0.72, "material": 0.70, "style": 0.60, "scale": 0.50}


# Small textiles (a throw, a cushion) are sold in a dozen near-identical shades and a photo's colour is only approximate, so their colour
# tolerance is a little wider (accent 12 -> 16 dE, other colours 18 -> 22 dE).  Big pieces (sofa, rug, chair ...) and everything else in the
# gate stay strict: a 16 dE sofa would visibly recolour the room, a 16 dE cushion does not.
SMALL_TEXTILES = frozenset({"throw", "cushion"})
TEXTILE_GATE = {"accent_dE": 16.0, "slot_dE": 22.0}


def gate_for(category: Optional[str] = None) -> Dict[str, float]:
    """The swap gate that applies to a category: the strict GATE, with the wider colour tolerance for small textiles only."""
    return dict(GATE, **TEXTILE_GATE) if category in SMALL_TEXTILES else dict(GATE)


def swap_gate(bd: Dict[str, Any], gate: Optional[Dict[str, float]] = None, category: Optional[str] = None) -> Tuple[bool, List[str]]:
    """STRICT pass / fail for replacing a designed furniture / decor piece with a real product -> (accepted, reasons for rejection).

    ``category`` selects the small-textile colour tolerance (see ``gate_for``); an explicit ``gate`` overrides single limits on top."""
    g = dict(gate_for(category), **(gate or {}))
    why: List[str] = []
    p, pal = bd["parts"], bd["palette"]
    if bd["material"].get("hard_reject"):
        why.append("avoided material (" + "; ".join(bd["material"]["flags"]) + ")")
    if pal.get("note"):
        why.append(pal["note"])
    if bd["style"]["avoid_hits"]:
        why.append("avoid-list wording: " + ", ".join(bd["style"]["avoid_hits"]))
    if pal["score"] < g["palette"]:
        why.append(f"palette fit {pal['score']:.2f} < {g['palette']:.2f}")
    accent = pal.get("slot_chroma", 0) >= 35.0     # a saturated designed colour (the red chair) is the room's accent: it must stay that colour
    limit = g["accent_dE"] if accent else g["slot_dE"]
    if pal.get("slot_dE", 0) > limit:
        why.append(f"dominant colour is {pal['slot_dE']:.0f} dE from the designed {'accent ' if accent else ''}colour (limit {limit:.0f}); would recolour the piece")
    if pal.get("slot_cover", 1.0) < g["slot_cover"]:
        why.append(f"only {pal['slot_cover']:.0%} of the product is in the designed colours (limit {g['slot_cover']:.0%}); the rest would recolour the piece")
    if p["material"] < g["material"]:
        extra = ("; " + "; ".join(bd["material"]["flags"])) if bd["material"]["flags"] else ""
        why.append(f"material fit {p['material']:.2f} < {g['material']:.2f}{extra}")
    if p["style"] < g["style"]:
        why.append(f"style fit {p['style']:.2f} < {g['style']:.2f}")
    if p["scale"] < g["scale"]:
        why.append("size outside the slot: " + "; ".join(bd["scale"]["notes"]))
    if bd.get("studio_photo") is False:
        why.append("lifestyle photo (no clean product shot): the colours cannot be trusted")
    if bd["total"] < g["total"]:
        why.append(f"overall cohesion {bd['total']:.2f} < {g['total']:.2f}")
    return (not why), why


def furniture_set_check(items: Sequence[Dict[str, Any]], profile: RoomProfile) -> Dict[str, Any]:
    """After the swaps: the room's wood families and metals must not grow beyond the designed set (and never include chrome)."""
    woods, metals = set(), set()
    for it in items:
        if (it.get("category") in ("art", "poster")):
            continue
        mats = it.get("material") or {}
        for m in (mats.get("primary"), mats.get("secondary")):
            if m in WOOD_FAMILY:
                woods.add(WOOD_FAMILY[m])
            elif m in METALS:
                metals.add(m)
    base_w = {WOOD_FAMILY[w] for w in profile.woods}
    base_m = set(profile.metals)
    return {"wood_families": sorted(woods), "metals": sorted(metals), "new_wood_family": sorted(woods - base_w), "new_metal": sorted(metals - base_m),
            "ok": not (woods - base_w) and not (metals - base_m) and "chrome" not in metals}

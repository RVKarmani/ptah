"""Surface metrics of the item templates, shared by the planner (and mirrored by ``viewer/src/models.ts``).

The viewer draws every item from a procedural template.  Styling items (cushions, books, a vase, ...) must sit
on the surfaces that the *drawn* furniture really has: the seat of a sofa (not the top of its backrest), a
board of a bookshelf, a table top.  The numbers below are therefore copies of the constants in the viewer
templates (``sofa()`` / ``bookshelf()`` / ``tableLike()``; the TS side exports ``sofaMetrics`` / ``shelfMetrics``
with the same formulas).  If a template changes in the viewer, change it here too.

All coordinates are item-local cm: x to the right, z to the front (item front = +z), y up from the item's base.
A :class:`Surface` is the free rectangle (or circle) ``centre +- size/2`` at height ``y`` above the item's base.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..products.enrich import normalize_category

SURFACE_KINDS = ("top", "seat", "shelf")

# Styling density: how much of a surface may be covered, and how many items one surface (one shelf board) takes.
STYLING = {
    "max_cover": 0.55,                                  # share of the surface area covered by footprints
    "max_items": {"top": 4, "seat": 4, "shelf": 3},    # items per surface / per shelf board
    "gap_cm": 1.5,                                      # minimum gap between two styling items
    "edge_margin_cm": 1.0,                              # keep-out strip along the surface edge
}

ROUND_TEMPLATES = ("vase", "candle", "small_plant", "plant", "table_lamp")
ALIGN_BACK_TEMPLATES = ("cushion",)                      # lean against the back of the surface (sofa back cushions)
NO_TOP_TEMPLATES = ("sofa", "armchair", "art", "mirror", "poster", "frame_wall", "sconce", "picture_light", "led_strip",
                    "rug", "pendant", "guitar", "guitar_hanger")


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def js_round(v: float) -> int:
    """JavaScript Math.round (half up) so Python and the viewer agree on shelf counts."""
    return int(math.floor(v + 0.5))


def params(item: dict) -> dict:
    return ((item.get("model") or {}).get("params")) or {}


def template_of(item: dict) -> str:
    t = (item.get("model") or {}).get("template")
    if t:
        return str(t).lower()
    return normalize_category(item.get("category"))


@dataclass(frozen=True)
class Surface:
    kind: str                 # top | seat | shelf
    level: Optional[int]      # shelf board index from the bottom (0 = lowest), else None
    y: float                  # height of the surface above the item's base
    cx: float                 # surface centre, item-local
    cz: float
    w: float                  # extent along local x
    d: float                  # extent along local z
    clear: Optional[float]    # free height above the surface (None = unbounded)
    shape: str = "rect"       # rect | circle (circle: diameter = min(w, d))

    @property
    def area(self) -> float:
        return math.pi * (min(self.w, self.d) / 2.0) ** 2 if self.shape == "circle" else self.w * self.d


# ---------------------------------------------------------------- sofa / armchair
def sofa_metrics(w: float, d: float, h: float, armchair: bool = False, arms: bool = True, legs: str = "tapered") -> dict:
    lh = 3.0 if legs == "none" else clamp(h * 0.14, 8, 16)
    arm_w = clamp(w * (0.14 if armchair else 0.075), 11, 22) if arms else 0.0
    bt = clamp(d * (0.3 if armchair else 0.26), 14, 28)
    seat_top = h * (0.52 if armchair else 0.5)
    bd = bt * 0.66
    return {
        "leg_h": lh, "arm_w": arm_w, "back_t": bt, "seat_top": seat_top,
        "inner_w": w - 2 * arm_w,
        "arm_top": seat_top + (h - seat_top) * 0.32,
        # front face (at seat level) of the reclined back cushions: the seat is free in front of it
        "back_face_z": -d / 2 + bt + bd * 0.85 - 3,
        "seat_front_z": d / 2 - 0.2,
    }


# ---------------------------------------------------------------- bookshelf
def shelf_metrics(w: float, d: float, h: float) -> dict:
    t, kick, back = 2.4, 6.0, 0.9
    n = max(2, js_round(h / 36.0))
    step = (h - t - kick) / n
    return {"t": t, "kick": kick, "back": back, "n": n, "step": step}


def shelf_level(w: float, d: float, h: float, level: int) -> Optional[Surface]:
    m = shelf_metrics(w, d, h)
    n, step, t, kick, back = m["n"], m["step"], m["t"], m["kick"], m["back"]
    if not 0 <= level < n:
        return None
    y = kick + step * level + (0.0 if level == 0 else 1.0)
    top = (h - t) if level == n - 1 else (kick + step * (level + 1) - 1.0)
    z0, z1 = -d / 2 + back + 0.5, d / 2 - 1.5
    x0, x1 = -w / 2 + t + 1.0, w / 2 - t - 1.0
    return Surface("shelf", level, y, (x0 + x1) / 2, (z0 + z1) / 2, x1 - x0, z1 - z0, top - y)


# ---------------------------------------------------------------- tables & friends
def table_shape(item: dict, tpl: str) -> str:
    w, d = float(item["w"]), float(item["d"])
    default = "round" if tpl == "side_table" and abs(w - d) < 3 else "rect"
    shape = str(params(item).get("shape") or default)
    return "circle" if shape in ("round", "oval") and abs(w - d) < 3 else "rect"


def surfaces(item: dict) -> Dict[str, List[Surface]]:
    """Surfaces an item offers, by kind.  ``top`` is offered by every item that is not a sofa / wall item."""
    tpl = template_of(item)
    w, d, h = float(item["w"]), float(item["d"]), float(item.get("h", 50))
    p = params(item)
    out: Dict[str, List[Surface]] = {}
    if tpl in ("sofa", "armchair"):
        m = sofa_metrics(w, d, h, tpl == "armchair", bool(p.get("arms", True)), str(p.get("legs", "tapered")))
        z0, z1 = m["back_face_z"], m["seat_front_z"]
        out["seat"] = [Surface("seat", None, m["seat_top"], 0.0, (z0 + z1) / 2, m["inner_w"], z1 - z0, None)]
    if tpl == "bookshelf":
        n = shelf_metrics(w, d, h)["n"]
        out["shelf"] = [s for s in (shelf_level(w, d, h, k) for k in range(n)) if s]
    if tpl not in NO_TOP_TEMPLATES:
        shape = table_shape(item, tpl) if tpl in ("coffee_table", "side_table", "dining_table", "desk") else "rect"
        out["top"] = [Surface("top", None, h, 0.0, 0.0, w, d, None, shape)]
    return out


def get_surface(item: dict, kind: Optional[str], level: Optional[int] = None) -> Optional[Surface]:
    """The requested surface; ``kind`` None picks ``top`` (``shelf`` when a level is given)."""
    if kind is None:
        kind = "shelf" if level is not None else "top"
    cands = surfaces(item).get(kind) or []
    if kind == "shelf":
        for s in cands:
            if s.level == (level or 0):
                return s
        return None
    return cands[0] if cands else None


# ---------------------------------------------------------------- footprints of styling items
def footprint_shape(item: dict) -> str:
    tpl = template_of(item)
    if tpl in ROUND_TEMPLATES or str(params(item).get("shape") or "") == "round":
        return "circle"
    return "rect"


def footprint_radius(item: dict) -> float:
    return min(float(item["w"]), float(item["d"])) / 2.0


def footprint_area(item: dict) -> float:
    if footprint_shape(item) == "circle":
        return math.pi * footprint_radius(item) ** 2
    return float(item["w"]) * float(item["d"])


def fits_inside(surface: Surface, cx: float, cz: float, item: dict, margin: float) -> bool:
    """Footprint of ``item`` centred at surface-local (cx, cz) lies inside ``surface`` (minus ``margin``)."""
    w, d = float(item["w"]), float(item["d"])
    circle_item = footprint_shape(item) == "circle"
    if surface.shape == "circle":
        R = min(surface.w, surface.d) / 2.0 - margin
        if circle_item:
            return math.hypot(cx - surface.cx, cz - surface.cz) + footprint_radius(item) <= R + 1e-9
        pts = [(cx + sx * w / 2, cz + sz * d / 2) for sx in (-1, 1) for sz in (-1, 1)]
        return all(math.hypot(px - surface.cx, pz - surface.cz) <= R + 1e-9 for px, pz in pts)
    hw, hd = (footprint_radius(item),) * 2 if circle_item else (w / 2, d / 2)
    return (abs(cx - surface.cx) + hw <= surface.w / 2 - margin + 1e-9
            and abs(cz - surface.cz) + hd <= surface.d / 2 - margin + 1e-9)


def footprints_clear(a: Tuple[float, float, float, float, str], b: Tuple[float, float, float, float, str], gap: float) -> bool:
    """a, b = (cx, cz, w, d, shape).  True when the footprints are at least ``gap`` apart."""
    ax, az, aw, ad, ash = a
    bx, bz, bw, bd, bsh = b
    if ash == "circle" and bsh == "circle":
        return math.hypot(ax - bx, az - bz) >= min(aw, ad) / 2 + min(bw, bd) / 2 + gap - 1e-9
    if ash == "circle" or bsh == "circle":
        (cx, cz, cw, cd, _), (rx, rz, rw, rd, _) = (a, b) if ash == "circle" else (b, a)
        qx = clamp(cx, rx - rw / 2, rx + rw / 2)
        qz = clamp(cz, rz - rd / 2, rz + rd / 2)
        return math.hypot(cx - qx, cz - qz) >= min(cw, cd) / 2 + gap - 1e-9
    return abs(ax - bx) >= (aw + bw) / 2 + gap - 1e-9 or abs(az - bz) >= (ad + bd) / 2 + gap - 1e-9


def drape_side(item: dict) -> Optional[str]:
    """``left`` / ``right`` for a throw draped over the sofa arm on that side (item-local x), else None."""
    v = params(item).get("drape")
    return v if v in ("left", "right") else None

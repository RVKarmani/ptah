"""Approximate lighting analysis of a furnished room (design guidance, NOT a photometric simulation).

    python -m ptah.lighting samples/room.json samples/items.json samples/layout.json -o samples/lighting.json [--mode night|day --hour 9]

What is computed
----------------
* illuminance (lux) on a horizontal 75 cm work plane, 25 cm grid (``lux[row=z][col=x]``, ``None`` outside the floor)
* every luminaire is a set of point sources: ``point`` = shade-dependent distribution (transmission, up/down
  split, cos^n lobe below), ``spot`` = cone with soft edge, ``line`` (LED strip) = several Lambertian points.
  Direct light: E = I(dir) * cos(theta) / d^2 (inverse square, cosine on the plane, no occlusion by furniture).
* a uniform inter-reflection term (flux * rho / (A_surfaces * (1 - rho)), rho from floor finish / wall colour /
  white ceiling, x0.7 for furnishings) so that a lit room is not black outside the direct cones.
* ``--mode day``: adds a simple window term (each window = a 4x3 patch of diffuse sky luminance whose strength
  follows the hour: 0 before 06:00 / after 18:00, peak at noon, ~15 klux outdoors) plus a small bounce.  Lamps
  are off by default in day mode (``--lights on`` to add them).  Issues are always judged on the lamps alone.
* zones: a task zone per seat (sofa / armchair / bed: best of the seat positions) and desk; ambient average.
* focal points (bookshelf, art, plant, bed, tv_stand, and any item with ``"focal": true``, e.g. an owned guitar) are ``accent_lit`` when an accent-layer light is attached to
  them, targets them, aims at them (spot axis) or sits within 100 cm of them.
* issues (codes DIM_TASK_ZONE, NO_AMBIENT, NO_ACCENT_LAYER, UNLIT_FOCAL_POINT, GLARE_RISK), each with a
  ``suggest`` the agent can turn into a product search / mounting constraint.

Targets (approximate design guidance, work plane 75 cm): reading 300 lux, desk work 500 lux, ambient average ~100 lux
at night; a zone counts as ``ok`` from 80 % of its target; ambient counts from 60 % (NO_AMBIENT below).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .layout.constraints import RoomContext
from .layout.geometry import Rect, point_in_polygon_strict, signed_area
from .products.enrich import LIGHT_CATEGORIES, normalize_category, parse_light_spec

PLANE_CM = 75.0
GRID_CM = 25.0
TARGET_READING = 300.0
TARGET_WORK = 500.0
TARGET_AMBIENT = 100.0
OK_FRACTION = 0.8
AMBIENT_OK_FRACTION = 0.6
ACCENT_NEAR_CM = 100.0
BOUNCE_FURNISHING = 0.7

# shade -> (transmission, share of the output going down, exponent n of the cos^n lobe below)
SHADES: Dict[str, Tuple[float, float, float]] = {
    "none": (1.00, 0.50, 0.0),
    "globe": (0.85, 0.50, 0.0),
    "drum": (0.65, 0.55, 1.0),
    "cone": (0.75, 0.70, 2.0),
    "dome": (0.80, 0.80, 3.0),
}
DEFAULT_SHADE = {"pendant": "globe", "floor_lamp": "drum", "table_lamp": "drum", "sconce": "dome"}
FLOOR_REFLECTANCE = {"light_oak": 0.40, "mid_oak": 0.28, "dark_wood": 0.15, "herringbone": 0.30, "concrete": 0.30,
                     "tile_light": 0.55, "tile_dark": 0.12, "carpet": 0.25}
FOCAL_CATEGORIES = ("bookshelf", "art", "plant", "bed", "tv_stand")
SEAT_CATEGORIES = ("sofa", "armchair", "bed")
LAYERS = ("ambient", "task", "accent")


# =============================================================================
# sources
# =============================================================================

@dataclass
class Source:
    item_id: str
    layer: str
    kind: str
    pos: np.ndarray                 # world emitter position(s): (n, 3) cm
    lumens: float                   # total lumens of the luminaire
    shade: str = "none"
    aim: Optional[np.ndarray] = None  # unit vector (world) for spot / line
    cone_deg: float = 60.0
    parent: Optional[str] = None
    target: Optional[str] = None
    side: Optional[str] = None

    @property
    def effective_lumens(self) -> float:
        if self.kind == "point":
            return self.lumens * SHADES[self.shade][0]
        return self.lumens


def _local_to_world(p: dict, lx: float, ly: float, lz: float, as_vector: bool = False) -> np.ndarray:
    r = math.radians(float(p.get("rotation_deg", 0) or 0))
    cs, sn = math.cos(r), math.sin(r)
    x, z = lx * cs + lz * sn, -lx * sn + lz * cs
    if as_vector:
        return np.array([x, ly, z])
    return np.array([float(p["x"]) + x, float(p.get("y", 0) or 0) + ly, float(p["z"]) + z])


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.array([0.0, -1.0, 0.0])


def light_of(item: dict) -> Optional[dict]:
    """The item's ``light`` dict; luminaire categories without one get the category defaults."""
    if item.get("light"):
        return item["light"]
    cat = normalize_category((item.get("model") or {}).get("template") or item.get("category"))
    if cat in LIGHT_CATEGORIES:
        return parse_light_spec(item.get("name"), item.get("why"), cat, dims=(item.get("w"), item.get("d"), item.get("h")),
                                model=item.get("model"))
    return None


def build_sources(items: Dict[str, dict], placements: Dict[str, dict]) -> List[Source]:
    out: List[Source] = []
    for iid, p in placements.items():
        it = items.get(iid)
        if it is None:
            continue
        lt = light_of(it)
        if not lt:
            continue
        cat = normalize_category((it.get("model") or {}).get("template") or it.get("category"))
        kind = lt.get("kind", "point")
        lumens = float(lt.get("lumens") or 0.0)
        if lumens <= 0:
            continue
        off = lt.get("emit_offset") or [0.0, float(it.get("h", 50)) * 0.8, 0.0]
        shade = ((it.get("model") or {}).get("params") or {}).get("shade") or DEFAULT_SHADE.get(cat, "none")
        shade = shade if shade in SHADES else "none"
        layer = lt.get("layer", "accent")
        w = float(it.get("w", 10))
        parent = p.get("parent")
        side = p.get("side")
        aim = None
        if kind == "line":
            n = max(3, int(math.ceil(w / 10.0)))
            xs = np.linspace(-w / 2.0 + w / (2 * n), w / 2.0 - w / (2 * n), n)
            pos = np.array([_local_to_world(p, float(off[0]) + x, float(off[1]), float(off[2])) for x in xs])
            if lt.get("aim"):
                aim = _unit(_local_to_world(p, *[float(v) for v in lt["aim"]], as_vector=True))
            elif side == "back":
                aim = _unit(_local_to_world(p, 0.0, 0.4, -1.0, as_vector=True))
            elif side == "top":
                aim = np.array([0.0, 1.0, 0.0])
            else:
                aim = np.array([0.0, -1.0, 0.0])
        else:
            pos = np.array([_local_to_world(p, float(off[0]), float(off[1]), float(off[2]))])
            if kind == "spot":
                if lt.get("aim"):
                    aim = _unit(_local_to_world(p, *[float(v) for v in lt["aim"]], as_vector=True))
                else:
                    aim = np.array([0.0, -1.0, 0.0])
                    tp = placements.get(lt.get("target") or parent or "")
                    ti = items.get(lt.get("target") or parent or "")
                    if tp is not None and ti is not None:
                        c = np.array([float(tp["x"]), float(tp.get("y", 0) or 0) + float(ti.get("h", 50)) / 2.0, float(tp["z"])])
                        aim = _unit(c - pos[0])
        out.append(Source(iid, layer, kind, pos, lumens, shade, aim, float(lt.get("cone_deg") or 60.0), parent,
                          lt.get("target"), side))
    return out


# =============================================================================
# photometry
# =============================================================================

def _lux_from(src: Source, pts: np.ndarray, min_dist_cm: float = 30.0) -> np.ndarray:
    """Direct illuminance (lux) of one luminaire on a horizontal plane at points ``pts`` (N, 3, cm)."""
    total = np.zeros(len(pts))
    n_pos = len(src.pos)
    for sp in src.pos:
        v = pts - sp                                   # source -> point
        r2 = np.maximum((v ** 2).sum(axis=1), min_dist_cm ** 2)
        r = np.sqrt(r2)
        d = v / r[:, None]
        cos_plane = np.where(v[:, 1] < 0, -d[:, 1], 0.0)   # horizontal receiver, light arriving from above only
        if src.kind == "point":
            T, D, n = SHADES[src.shade]
            phi = src.lumens * T
            down = np.maximum(-d[:, 1], 0.0)
            i_down = phi * D * (n + 1.0) / (2.0 * math.pi) * down ** n
            i_up = phi * (1.0 - D) / (2.0 * math.pi)
            inten = np.where(d[:, 1] < 0, i_down, i_up)
        elif src.kind == "spot":
            half = math.radians(max(5.0, min(src.cone_deg, 160.0)) / 2.0)
            outer = min(math.radians(89.0), half * 1.3)
            i0 = src.lumens / (2.0 * math.pi * (1.0 - math.cos(half)))
            c = d @ src.aim
            t = np.clip((c - math.cos(outer)) / max(1e-6, math.cos(half) - math.cos(outer)), 0.0, 1.0)
            inten = i0 * t
        else:  # line: Lambertian strip, flux split over the sample points
            phi = src.lumens / n_pos
            inten = phi / math.pi * np.maximum(d @ src.aim, 0.0)
        total += inten * cos_plane / (r / 100.0) ** 2
    return total


def _hex_reflectance(hexcol: Optional[str], default: float) -> float:
    try:
        h = hexcol.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
        return float(min(0.85, max(0.1, y * 0.9)))
    except Exception:
        return default


def room_reflectance(room: dict, ctx: RoomContext) -> Tuple[float, float]:
    """-> (effective average reflectance, total surface area m^2)"""
    fin = room.get("finishes") or {}
    rf = FLOOR_REFLECTANCE.get(fin.get("floor"), 0.3)
    rw = _hex_reflectance(fin.get("wall_color") or (fin.get("wall_paint") or {}).get("hex"), 0.6)
    rc = 0.8
    area_f = abs(signed_area(ctx.floor)) / 1e4
    per = sum(f.length for f in ctx.frames) / 100.0
    h = float(room.get("height", 260) or 260) / 100.0
    a_w = per * h
    a_tot = 2.0 * area_f + a_w
    rho = (area_f * rf + area_f * rc + a_w * rw) / a_tot
    return rho * BOUNCE_FURNISHING, a_tot


def bounce_lux(flux_lm: float, rho: float, a_tot_m2: float) -> float:
    """Average inter-reflected illuminance in a diffuse enclosure: Phi * rho / (A * (1 - rho))."""
    return flux_lm * rho / (a_tot_m2 * (1.0 - rho))


# ---- daylight ---------------------------------------------------------------

def outdoor_lux(hour: float) -> float:
    """Very rough overcast-ish horizontal illuminance outdoors: 0 before 06:00 / after 18:00, 15 klux at noon."""
    if hour <= 6.0 or hour >= 18.0:
        return 0.0
    return 15000.0 * math.sin(math.pi * (hour - 6.0) / 12.0) ** 1.3


GLAZING_TRANSMISSION = 0.65
WINDOW_FLUX_FACTOR = 0.35


def daylight_lux(ctx: RoomContext, pts: np.ndarray, hour: float) -> Tuple[np.ndarray, float]:
    """-> (direct window illuminance at pts, flux entering the room in lm).  Each window is a 4 x 3 patch
    grid of a diffuse sky patch of luminance L = 0.5 * E_out / pi * transmission."""
    e_out = outdoor_lux(hour)
    total = np.zeros(len(pts))
    flux = 0.0
    if e_out <= 0:
        return total, 0.0
    lum = 0.5 * e_out / math.pi * GLAZING_TRANSMISSION
    for o in ctx.windows:
        fr = o.frame
        area_m2 = (o.width / 100.0) * (o.height / 100.0)
        flux += e_out * GLAZING_TRANSMISSION * area_m2 * WINDOW_FLUX_FACTOR
        nu, nv = 4, 3
        for a in range(nu):
            for b in range(nv):
                s = o.offset + o.width * (a + 0.5) / nu
                y = o.sill + o.height * (b + 0.5) / nv
                px, pz = fr.point(s, 0.0)
                src = np.array([px, y, pz])
                v = pts - src
                r2 = np.maximum((v ** 2).sum(axis=1), 15.0 ** 2)
                r = np.sqrt(r2)
                d = v / r[:, None]
                cos_w = np.maximum(d[:, 0] * fr.nx + d[:, 2] * fr.nz, 0.0)   # leaving the window plane into the room
                cos_p = np.maximum(-d[:, 1], 0.0)
                patch_m2 = area_m2 / (nu * nv)
                total += lum * patch_m2 * cos_w * cos_p / (r / 100.0) ** 2
    return total, flux


# =============================================================================
# zones, focal points, issues
# =============================================================================

def _cat(item: dict) -> str:
    return normalize_category((item.get("model") or {}).get("template") or item.get("category"))


def _rect(item: dict, p: dict) -> Rect:
    return Rect(p["x"], p["z"], float(item["w"]), float(item["d"]), p.get("rotation_deg", 0))


def seat_points(item: dict, p: dict) -> List[Tuple[float, float]]:
    cat = _cat(item)
    r = _rect(item, p)
    fx, fz = r.front
    ux, uz = r.right
    w, d = float(item["w"]), float(item["d"])
    if cat == "sofa":
        n = max(1, min(3, int(round(w / 65.0))))
        offs = [(i - (n - 1) / 2.0) * (w / n) for i in range(n)]
        fwd = 0.12 * d
    elif cat == "bed":
        offs = [-w / 4.0, w / 4.0] if w >= 130 else [0.0]
        fwd = -0.25 * d
    else:
        offs = [0.0]
        fwd = 0.1 * d
    return [(r.x + ux * o + fx * fwd, r.z + uz * o + fz * fwd) for o in offs]


def desk_points(item: dict, p: dict) -> List[Tuple[float, float]]:
    r = _rect(item, p)
    fx, fz = r.front
    ux, uz = r.right
    w, d = float(item["w"]), float(item["d"])
    offs = [0.0] if w < 90 else [-w / 4.0, 0.0, w / 4.0]
    return [(r.x + ux * o + fx * 0.1 * d, r.z + uz * o + fz * 0.1 * d) for o in offs]


def _cluster(x: float, z: float, rad: float = 20.0) -> np.ndarray:
    return np.array([[x, PLANE_CM, z], [x + rad, PLANE_CM, z], [x - rad, PLANE_CM, z], [x, PLANE_CM, z + rad], [x, PLANE_CM, z - rad]])


def _lamp_total(sources: Sequence[Source], pts: np.ndarray) -> Tuple[np.ndarray, Dict[str, float]]:
    per: Dict[str, float] = {}
    tot = np.zeros(len(pts))
    for s in sources:
        e = _lux_from(s, pts)
        per[s.item_id] = per.get(s.item_id, 0.0) + float(e.mean())
        tot += e
    return tot, per


def _focal_lit(fid: str, fi: dict, fp: dict, sources: Sequence[Source]) -> List[str]:
    fr = _rect(fi, fp)
    y0 = float(fp.get("y", 0) or 0)
    y1 = y0 + float(fi.get("h", 50))
    centre = np.array([fr.x, (y0 + y1) / 2.0, fr.z])
    reach = 0.5 * max(float(fi["w"]), float(fi.get("h", 50))) + 10.0
    by: List[str] = []
    for s in sources:
        if s.layer != "accent":
            continue
        hit = s.parent == fid or s.target == fid
        if not hit and s.kind in ("spot", "line") and s.aim is not None:
            p0 = s.pos.mean(axis=0)
            t = float((centre - p0) @ s.aim)
            if 0 < t <= 300 and np.linalg.norm(centre - p0 - s.aim * t) <= reach and s.kind == "spot":
                hit = True
        if not hit:
            for sp in s.pos:
                gap = fr.distance_to_point(float(sp[0]), float(sp[2]))
                vgap = max(y0 - sp[1], sp[1] - y1, 0.0)
                if gap <= ACCENT_NEAR_CM and vgap <= 120.0:
                    hit = True
                    break
        if hit and s.item_id not in by:
            by.append(s.item_id)
    return by


def is_focal(item: dict) -> bool:
    """Focal point of the room: an explicit ``"focal": true`` flag (e.g. the user's owned guitar) wins over the default
    category list; ``"focal": false`` opts an item of a focal category out."""
    flag = item.get("focal")
    if flag is not None:
        return bool(flag)
    return _cat(item) in FOCAL_CATEGORIES


def _suggest_for_focal(fid: str, cat: str) -> dict:
    if cat in ("guitar", "poster", "frame"):
        return {"category": "picture_light", "attach": fid, "side": "above"}
    if cat == "bookshelf":
        return {"category": "led_strip", "attach": fid, "side": "back"}
    if cat == "art":
        return {"category": "picture_light", "attach": fid, "side": "above"}
    if cat == "tv_stand":
        return {"category": "led_strip", "attach": fid, "side": "back"}
    if cat == "bed":
        return {"category": "sconce", "near": fid}
    return {"category": "table_lamp", "near": fid} if cat != "plant" else {"category": "sconce", "near": fid}


# Layer presets ("scenes"): factor 0..1 per layer, applied to the lumens of every luminaire of that layer.
SCENES: Dict[str, Dict[str, float]] = {
    "day": {"ambient": 0.0, "task": 0.0, "accent": 0.0, "daylight": 1.0},   # lights off, daylight on
    "evening": {"ambient": 0.4, "task": 0.0, "accent": 1.0},
    "reading": {"ambient": 0.3, "task": 1.0, "accent": 0.3},
    "night": {"ambient": 0.0, "task": 0.0, "accent": 0.6},
}
SCENE_ORDER = ("evening", "reading", "night", "day")      # evaluation / issue order
EVENING_AMBIENT_MIN = 50.0                                 # evening: ambient average adequate from ~50 lux (target ~100)
DAYLIGHT_ADEQUATE_LUX = 100.0


def _scaled(sources: Sequence[Source], factors: Dict[str, float]) -> List[Source]:
    return [replace(s, lumens=s.lumens * factors.get(s.layer, 0.0)) for s in sources if factors.get(s.layer, 0.0) > 0.0]


def compute_lighting(room: dict, items: List[dict], layout: dict, mode: str = "night", hour: Optional[float] = None,
                     scene: Optional[str] = None) -> dict:
    """Evaluate every scene (day, evening, reading, night).  The top-level fields repeat one scene for backwards
    compatibility: ``evening`` (default), or ``day`` for ``mode="day"``, or ``scene`` when given."""
    mode = mode if mode in ("night", "day") else "night"
    if hour is None:
        hour = 12.0
    top = scene if scene in SCENES else ("day" if mode == "day" else "evening")
    ctx = RoomContext(room)
    by_item = {str(i["id"]): i for i in items}
    placements = {p["item_id"]: p for p in layout.get("placements", []) if p["item_id"] in by_item}
    sources = build_sources(by_item, placements)

    minx, minz, maxx, maxz = ctx.bbox
    cols, rows = int(math.ceil((maxx - minx) / GRID_CM - 1e-9)), int(math.ceil((maxz - minz) / GRID_CM - 1e-9))
    cx = minx + (np.arange(cols) + 0.5) * GRID_CM
    cz = minz + (np.arange(rows) + 0.5) * GRID_CM
    gx, gz = np.meshgrid(cx, cz)
    pts = np.stack([gx.ravel(), np.full(gx.size, PLANE_CM), gz.ravel()], axis=1)
    inside = np.array([point_in_polygon_strict(float(x), float(z), ctx.floor) for x, z in zip(pts[:, 0], pts[:, 2])])
    rho, a_tot = room_reflectance(room, ctx)
    _, day_flux = daylight_lux(ctx, pts[:1], hour)
    b_day = bounce_lux(day_flux, rho, a_tot)

    counts = {l: sum(1 for s in sources if s.layer == l) for l in LAYERS}
    seats = [(iid, p) for iid, p in placements.items() if _cat(by_item[iid]) in SEAT_CATEGORIES or _cat(by_item[iid]) == "desk"]
    focal_items = [(iid, p) for iid, p in placements.items() if is_focal(by_item[iid])]
    seat_xy = [(x, z) for iid, p in seats if _cat(by_item[iid]) in SEAT_CATEGORIES for (x, z) in seat_points(by_item[iid], p)]

    def evaluate(name: str) -> dict:
        factors = SCENES[name]
        daylight_on = factors.get("daylight", 0.0) > 0
        srcs = _scaled(sources, factors)
        b_lamps = bounce_lux(sum(s.effective_lumens for s in srcs), rho, a_tot)

        def at(p: np.ndarray) -> np.ndarray:
            e = np.zeros(len(p))
            for s in srcs:
                e += _lux_from(s, p)
            if srcs:
                e += b_lamps
            if daylight_on:
                e += daylight_lux(ctx, p, hour)[0] + b_day
            return e

        lux_all = at(pts)
        grid = [[(int(round(float(lux_all[r * cols + c]))) if inside[r * cols + c] else None) for c in range(cols)] for r in range(rows)]
        amb_avg = float(lux_all[inside].mean()) if inside.any() else 0.0
        judged_task = name == "reading"
        zones: List[dict] = []
        for iid, p in seats:
            it = by_item[iid]
            cat = _cat(it)
            if cat in SEAT_CATEGORIES:
                cands, label, target = seat_points(it, p), "reading", TARGET_READING
            else:
                cands, label, target = desk_points(it, p), "work", TARGET_WORK
            best = None
            for (x, z) in cands:
                cl = _cluster(x, z)
                val = float(at(cl).mean())
                if best is None or val > best[0]:
                    best = (val, x, z, cl)
            val, x, z, cl = best
            _, per = _lamp_total(srcs, cl)
            direct = sum(per.values()) or 1.0
            contrib = [sid for sid, e in sorted(per.items(), key=lambda t: -t[1]) if e >= 0.15 * direct]
            status = ("ok" if val >= OK_FRACTION * target else "dim") if judged_task else "info"
            zones.append({"name": f"{label}:{iid}", "kind": label, "item_ids": [iid] + contrib, "layer": "task", "lux": int(round(val)),
                          "target": int(target), "status": status, "center": [round(x, 1), round(z, 1)], "radius_cm": 20})
        amb_ids = sorted({s.item_id for s in sources if s.layer == "ambient"})
        if name == "evening":
            amb_status = "ok" if (amb_avg >= EVENING_AMBIENT_MIN and amb_ids) else "dim"
        else:
            amb_status = "info"
        zones.append({"name": "ambient", "kind": "ambient", "item_ids": amb_ids, "layer": "ambient", "lux": int(round(amb_avg)),
                      "target": int(TARGET_AMBIENT), "status": amb_status})

        focal: List[dict] = []
        if factors.get("accent", 0.0) > 0:
            for iid, p in focal_items:
                by = _focal_lit(iid, by_item[iid], p, srcs)
                focal.append({"item_id": iid, "category": _cat(by_item[iid]), "accent_lit": bool(by), "lit_by": by})

        issues: List[dict] = []
        if name == "reading":
            for z in zones:
                if z["layer"] == "task" and z["status"] == "dim":
                    sid = z["item_ids"][0]
                    sug = ({"category": "table_lamp", "attach": sid, "side": "top"} if z["kind"] == "work"
                           else {"category": "floor_lamp", "near": sid})
                    issues.append({"code": "DIM_TASK_ZONE", "zone": z["name"],
                                   "message": f"{z['name']} gets about {z['lux']} lux in the reading scene (target ~{z['target']}); add or strengthen a task light.",
                                   "suggest": sug})
        if name == "evening":
            if amb_status != "ok":
                anchor = next((i for i, p in placements.items() if _cat(by_item[i]) in ("coffee_table", "dining_table")), None)
                msg = ("No ambient-layer fixture: the room has no general light." if counts["ambient"] == 0 else
                       f"Ambient light is low in the evening scene (about {amb_avg:.0f} lux on average, want >= {EVENING_AMBIENT_MIN:.0f}, target ~{TARGET_AMBIENT:.0f}).")
                issues.append({"code": "NO_AMBIENT", "message": msg, "suggest": {"category": "pendant", "above": anchor or "room_center"}})
        if name in ("evening", "night"):
            if counts["accent"] == 0:
                alts = [_suggest_for_focal(f["item_id"], _cat(by_item[f["item_id"]])) for f in (
                    {"item_id": i} for i, _ in focal_items)]
                first = alts[0] if alts else ({"category": "table_lamp", "near": seats[0][0]} if seats else {"category": "table_lamp"})
                issue = {"code": "NO_ACCENT_LAYER", "message": "No accent light: add one to give the room depth and highlight a focal point.",
                         "suggest": first}
                if len(alts) > 1:
                    issue["alternatives"] = alts[1:]
                issues.append(issue)
            elif name == "evening":
                for f in focal:
                    if not f["accent_lit"]:
                        issues.append({"code": "UNLIT_FOCAL_POINT", "item_id": f["item_id"],
                                       "message": f"{f['item_id']} ({f['category']}) is not accent-lit.",
                                       "suggest": _suggest_for_focal(f["item_id"], f["category"])})
        if name in ("evening", "reading", "night"):
            for s in sources:  # glare is a property of the fixture; only fixtures that are on in this scene count
                if factors.get(s.layer, 0.0) <= 0 or s.kind != "point" or s.shade != "none" or s.lumens < 600:
                    continue
                e = s.pos[0]
                if 80 <= e[1] <= 170 and any(math.hypot(e[0] - x, e[2] - z) <= 200 for x, z in seat_xy):
                    issues.append({"code": "GLARE_RISK", "item_id": s.item_id,
                                   "message": f"{s.item_id} is a bare {s.lumens:.0f} lm source at eye level near seating; add a shade or diffuser.",
                                   "suggest": {"category": "table_lamp" if e[1] < 120 else "floor_lamp", "near": s.item_id, "replace": s.item_id}})
        out = {"factors": {k: v for k, v in factors.items() if k != "daylight"}, "lux": grid,
               "lux_max": int(round(float(lux_all[inside].max()))) if inside.any() else 0,
               "ambient_avg_lux": int(round(amb_avg)), "zones": zones, "focal_points": focal, "issues": issues}
        if daylight_on:
            out["daylight"] = {"hour": hour, "outdoor_lux": int(round(outdoor_lux(hour))), "avg_lux": int(round(amb_avg)),
                               "coverage_pct": int(round(100.0 * float((lux_all[inside] >= DAYLIGHT_ADEQUATE_LUX).mean()))) if inside.any() else 0,
                               "coverage_threshold_lux": int(DAYLIGHT_ADEQUATE_LUX), "informational": True}
        return out

    scenes = {name: evaluate(name) for name in SCENE_ORDER}
    ev, rd = scenes["evening"], scenes["reading"]
    task_zones = [z for z in rd["zones"] if z["layer"] == "task"]
    layers = {"ambient": counts["ambient"] > 0 and ev["zones"][-1]["status"] == "ok",
              "task": counts["task"] > 0 and all(z["status"] == "ok" for z in task_zones),
              "accent": counts["accent"] > 0}
    merged: Dict[tuple, dict] = {}
    for name in ("reading", "evening", "night"):
        for iss in scenes[name]["issues"]:
            key = (iss["code"], iss.get("item_id"), iss.get("zone"))
            if key in merged:
                merged[key]["scenes"].append(name)
            else:
                merged[key] = dict(iss, scenes=[name])
    t = scenes[top]
    out = {
        "mode": "day" if top == "day" else "night",
        "scene": top,
        "grid_cm": GRID_CM,
        "plane_cm": PLANE_CM,
        "origin": [round(minx, 1), round(minz, 1)],
        "size": [cols, rows],
        "lux": t["lux"],
        "lux_max": t["lux_max"],
        "ambient_avg_lux": t["ambient_avg_lux"],
        "zones": t["zones"],
        "focal_points": t["focal_points"] or ev["focal_points"],
        "layers": layers,
        "layer_counts": counts,
        "scenes": scenes,
        "scene_presets": SCENES,
        "lights": [{"item_id": s.item_id, "layer": s.layer, "kind": s.kind, "lumens": int(round(s.lumens)),
                    "pos": [round(float(v), 1) for v in s.pos.mean(axis=0)]} for s in sources],
        "issues": t["issues"],
        "issues_all": list(merged.values()),
        "note": "approximate design guidance, not a photometric simulation (no occlusion, uniform inter-reflection term)",
    }
    out["hour"] = hour
    return out


# =============================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ptah.lighting", description="Approximate lux maps per lighting scene, layers and issues.")
    ap.add_argument("room")
    ap.add_argument("items")
    ap.add_argument("layout")
    ap.add_argument("-o", "--output", default="lighting.json")
    ap.add_argument("--mode", choices=["night", "day"], default="night",
                    help="which scene the top-level fields repeat: night -> evening, day -> day (all scenes are always written)")
    ap.add_argument("--hour", type=float, default=None, help="hour of day for the day scene (default 12)")
    ap.add_argument("--scene", choices=list(SCENES), default=None, help="override the scene used for the top-level fields")
    args = ap.parse_args(argv)
    with open(args.room) as f:
        room = json.load(f)
    with open(args.items) as f:
        items = json.load(f)
    with open(args.layout) as f:
        layout = json.load(f)
    res = compute_lighting(room, items, layout, args.mode, args.hour, args.scene)
    with open(args.output, "w") as f:
        json.dump(res, f, indent=1, separators=(",", ": "))
        f.write("\n")
    print(f"layers {res['layers']}; {len(res['issues_all'])} issue(s) over all scenes -> {args.output}", file=sys.stderr)
    for name in SCENE_ORDER:
        sc = res["scenes"][name]
        zs = ", ".join(f"{z['name']} {z['lux']}/{z['target']} ({z['status']})" for z in sc["zones"])
        extra = f"  daylight coverage {sc['daylight']['coverage_pct']}%" if "daylight" in sc else ""
        print(f"  [{name}] ambient avg {sc['ambient_avg_lux']} lux, max {sc['lux_max']}: {zs}{extra}", file=sys.stderr)
    for i in res["issues_all"]:
        print(f"  {i['code']} ({', '.join(i['scenes'])}): {i['message']}  suggest={i['suggest']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

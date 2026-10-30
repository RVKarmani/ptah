"""Room context, hard rules, soft-constraint scorers and the walkway check.

Soft constraints follow the Holodeck vocabulary (edge, middle, near, far, in_front_of,
side_of, face, center_align, under, + on_top_of) and every scorer returns a value in
[0, 1].  Ergonomic objectives (LayoutVLM / design-principle style), the optional pose
``hint`` and the optional ``zone`` cohesion term are scored through the same interface.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import (
    TOL,
    Rect,
    WallFrame,
    build_wall_frames,
    convex_intersection_area,
    dist_to_boundary,
    point_in_polygon_strict,
    polygon_bbox,
    polygon_centroid,
    rect_gap,
    rect_in_polygon,
    rects_overlap,
    signed_area,
)

VOCAB = ("edge", "middle", "near", "far", "in_front_of", "side_of", "face", "center_align", "under", "on_top_of")
UNARY_TYPES = ("edge", "middle")
PROX_TYPES = ("near", "in_front_of", "side_of", "under", "on_top_of", "center_align")
NEEDS_TARGET = ("in_front_of", "side_of", "center_align", "under", "on_top_of")
ROOM_TARGETS = ("room_center", "room", "center")
IGNORED_TYPES = ("not_blocking_window",)  # always enforced as a hard rule

SEATING_WORDS = ("sofa", "couch", "loveseat", "sectional", "armchair", "chair", "recliner")
FLAT_H = 2.0  # items with h <= this (rugs) may lie under other items and do not block walking
WINDOW_ZONE_DEPTH = 40.0  # hard: tall items may not stand within this distance in front of a window
WINDOW_LIGHT_DEPTH = 120.0  # soft: keep this much clear in front of a window
WALK_HARD = 60.0
WALK_SOFT = 75.0

THRESH = {
    "edge": 0.7, "middle": 0.7, "near": 0.7, "far": 0.7, "in_front_of": 0.8, "side_of": 0.7,
    "face": 0.8, "center_align": 0.8, "under": 0.95, "on_top_of": 0.9, "zone": 0.5, "hint": 0.5,
}
DEFAULT_THRESH = 0.7


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


@dataclass(frozen=True)
class Cons:
    type: str
    target: Optional[str] = None
    weight: float = 1.0
    synthetic: bool = False

    @property
    def name(self) -> str:
        if self.type == "zone":
            return f"zone:{self.target}"
        if self.target and not self.synthetic and self.type in VOCAB and self.target not in ROOM_TARGETS:
            return f"{self.type}:{self.target}"
        return self.type

    @property
    def threshold(self) -> float:
        return THRESH.get(self.type, DEFAULT_THRESH)


@dataclass
class Item:
    id: str
    category: str
    w: float
    d: float
    h: float
    anchor: bool = False
    zone: Optional[str] = None
    hint: Optional[dict] = None
    cons: List[Cons] = field(default_factory=list)

    @property
    def flat(self) -> bool:
        return self.h <= FLAT_H

    @property
    def area(self) -> float:
        return self.w * self.d

    def has(self, ctype: str) -> bool:
        return any(c.type == ctype for c in self.cons)

    def scaled(self, s: float) -> "Item":
        return Item(self.id, self.category, self.w * s, self.d * s, self.h, self.anchor, self.zone, self.hint, self.cons)


@dataclass
class Opening:
    id: str
    type: str
    frame: WallFrame
    offset: float
    width: float
    height: float
    sill: float
    swing: float
    zone: Rect  # door: width x swing ; window: width x 40 cm
    thin: Rect  # flat footprint on the wall (used as a constraint target)
    light: Optional[Rect] = None  # window: width x 120 cm


@dataclass
class Existing:
    id: str
    category: str
    rect: Rect
    h: float


class RoomContext:
    def __init__(self, room: dict):
        self.warnings: List[str] = []
        self.name = room.get("name", "room")
        self.floor: List[Tuple[float, float]] = [(float(p[0]), float(p[1])) for p in room["floor"]]
        self.bbox = polygon_bbox(self.floor)
        self.frames: List[WallFrame] = build_wall_frames(self.floor, room.get("walls"))
        self.frame_by_id = {f.id: f for f in self.frames}
        self.openings: List[Opening] = []
        for o in room.get("openings", []) or []:
            fr = self.frame_by_id.get(o.get("wall"))
            if fr is None:
                self.warnings.append(f"opening {o.get('id')}: unknown wall {o.get('wall')!r}; ignored")
                continue
            typ = o.get("type", "door")
            off, wid = float(o.get("offset", 0)), float(o.get("width", 80))
            swing = float(o.get("swing", 90) or 0)
            if typ == "window":
                zone = fr.zone(off, wid, WINDOW_ZONE_DEPTH)
                light = fr.zone(off, wid, WINDOW_LIGHT_DEPTH)
            else:
                zone = fr.zone(off, wid, swing)
                light = None
            self.openings.append(Opening(
                id=str(o.get("id")), type=typ, frame=fr, offset=off, width=wid,
                height=float(o.get("height", 200)), sill=float(o.get("sill", 90) if o.get("sill") is not None else 90),
                swing=swing, zone=zone, thin=fr.zone(off, wid, 10.0), light=light))
        self.doors = [o for o in self.openings if o.type != "window"]
        self.windows = [o for o in self.openings if o.type == "window"]
        self.existing: List[Existing] = []
        for e in room.get("existing", []) or []:
            self.existing.append(Existing(
                id=str(e.get("id")), category=str(e.get("category", "")),
                rect=Rect(e["x"], e["z"], e["w"], e["d"], e.get("rotation_deg", 0)), h=float(e.get("h", 100))))
        self._build_grid()
        self.center_rect = Rect(self.center[0], self.center[1], 1.0, 1.0, 0.0)
        self._static_targets: Dict[str, Rect] = {}
        for o in self.openings:
            self._static_targets[o.id] = o.thin
        for e in self.existing:
            self._static_targets[e.id] = e.rect

    # ---- distance field to the room boundary ---------------------------
    def _build_grid(self) -> None:
        minx, minz, maxx, maxz = self.bbox
        res = 5.0
        while ((maxx - minx) / res + 1) * ((maxz - minz) / res + 1) > 40000:
            res *= 2
        self.res = res
        self.minx, self.minz = minx, minz
        self.nx = int(math.floor((maxx - minx) / res + 1e-9)) + 1
        self.nz = int(math.floor((maxz - minz) / res + 1e-9)) + 1
        bd = [-1.0] * (self.nx * self.nz)
        poly = self.floor
        for j in range(self.nz):
            pz = minz + j * res
            for i in range(self.nx):
                px = minx + i * res
                d = dist_to_boundary(px, pz, poly)
                if d < 1e-6 or point_in_polygon_strict(px, pz, poly):
                    bd[j * self.nx + i] = d
        self.bd = bd
        best = max(bd)
        cx, cz = polygon_centroid(poly)
        if self.bd_at(cx, cz) >= 0.8 * best:
            self.center = (cx, cz)
        else:
            k = bd.index(best)
            self.center = (minx + (k % self.nx) * res, minz + (k // self.nx) * res)
        self.center_clear = max(0.0, self.bd_at(*self.center))

    def bd_at(self, x: float, z: float) -> float:
        i = int(round((x - self.minx) / self.res))
        j = int(round((z - self.minz) / self.res))
        if i < 0 or j < 0 or i >= self.nx or j >= self.nz:
            return -1.0
        return self.bd[j * self.nx + i]

    def wall_dist(self, rect: Rect) -> float:
        """Approximate distance from a rectangle to the nearest wall (0 if touching)."""
        pts = rect.corners()
        samples = list(pts) + [((pts[i][0] + pts[(i + 1) % 4][0]) / 2, (pts[i][1] + pts[(i + 1) % 4][1]) / 2) for i in range(4)]
        samples.append((rect.x, rect.z))
        best = 1e9
        for x, z in samples:
            v = self.bd_at(x, z)
            if v < 0:
                v = 0.0
            if v < best:
                best = v
        return best

    # ---- targets -----------------------------------------------------
    def static_target(self, tid: Optional[str]) -> Optional[Rect]:
        if tid is None or tid in ROOM_TARGETS:
            return self.center_rect
        return self._static_targets.get(tid)

    def is_static_target(self, tid: Optional[str]) -> bool:
        return tid is None or tid in ROOM_TARGETS or tid in self._static_targets

    # ---- hard rules (static part) -----------------------------------
    def static_reject(self, rect: Rect, h: float) -> Optional[str]:
        minx, minz, maxx, maxz = self.bbox
        x0, z0, x1, z1 = rect.aabb()
        if x0 < minx - TOL or z0 < minz - TOL or x1 > maxx + TOL or z1 > maxz + TOL:
            return "outside_floor"
        for e in self.existing:
            if rects_overlap(rect, e.rect):
                return "existing"
        for o in self.doors:
            if o.swing > 0 and rects_overlap(rect, o.zone):
                return "door_swing"
        if h > FLAT_H:
            for o in self.windows:
                if h > o.sill and rects_overlap(rect, o.zone):
                    return "window"
        if not rect_in_polygon(rect, self.floor, TOL, self.bbox):
            return "outside_floor"
        return None


def overlap_forbidden(a: Rect, a_flat: bool, b: Rect, b_flat: bool) -> bool:
    """Items may not overlap, except a flat item (rug) lying under a non-flat one."""
    if a_flat != b_flat:
        return False
    return rects_overlap(a, b)


# ----------------------------------------------------------------------------
# Soft scorers: fn(ctx, rect, tgt, item) -> float in [0, 1]
# ----------------------------------------------------------------------------

def _frame_of(t: Rect):
    return t.sn, t.cs, t.cs, -t.sn  # fx, fz, ux, uz


def score_edge(ctx: RoomContext, rect: Rect, tgt, item) -> float:
    fx, fz = rect.sn, rect.cs
    bx, bz = rect.x - fx * rect.d / 2.0, rect.z - fz * rect.d / 2.0
    best = 0.0
    for fr in ctx.frames:
        if fr.nx * fx + fr.nz * fz < 0.98:
            continue
        gap = fr.depth_of(bx, bz)
        s = fr.along(bx, bz)
        if gap < -TOL - 2 or s < -TOL or s > fr.length + TOL:
            continue
        v = 1.0 if gap <= 3.0 else clamp01(1.0 - (gap - 3.0) / 27.0)
        if v > best:
            best = v
    return best


def score_middle(ctx: RoomContext, rect: Rect, tgt, item) -> float:
    return clamp01(ctx.wall_dist(rect) / 80.0)


def score_near(ctx, rect, tgt: Rect, item) -> float:
    g = rect_gap(rect, tgt)
    return clamp01(1.0 - (g - 50.0) / 150.0)


def score_far(ctx, rect, tgt: Rect, item) -> float:
    g = rect_gap(rect, tgt)
    return clamp01((g - 80.0) / 120.0)


def _front_geometry(rect: Rect, t: Rect):
    fx, fz, ux, uz = _frame_of(t)
    px, pz = rect.x - t.x, rect.z - t.z
    pf = px * fx + pz * fz
    pu = px * ux + pz * uz
    ef = rect.half_extent_along(fx, fz)
    eu = rect.half_extent_along(ux, uz)
    return pf, pu, ef, eu


def score_in_front_of(ctx, rect, tgt: Rect, item) -> float:
    pf, pu, ef, eu = _front_geometry(rect, tgt)
    if pf <= 0:
        return 0.0
    gap = pf - tgt.d / 2.0 - ef
    if gap < -TOL:
        s_front = clamp01(1.0 + gap / max(ef, 1.0)) * 0.5 if pf > tgt.d / 2.0 else 0.0
    elif gap <= 70.0:
        s_front = 1.0
    else:
        s_front = clamp01(1.0 - (gap - 70.0) / 80.0)
    half_w = tgt.w / 2.0
    s_lat = 1.0 if abs(pu) <= half_w else clamp01(1.0 - (abs(pu) - half_w) / 60.0)
    return s_front * s_lat


def score_side_of(ctx, rect, tgt: Rect, item) -> float:
    pf, pu, ef, eu = _front_geometry(rect, tgt)
    g = abs(pu) - tgt.w / 2.0 - eu
    if g < -TOL:
        return 0.0
    s_gap = 1.0 if g <= 40.0 else clamp01(1.0 - (g - 40.0) / 80.0)
    hbd = tgt.d / 2.0
    s_dep = 1.0 if abs(pf) <= hbd + 0.5 * ef else clamp01(1.0 - (abs(pf) - hbd - 0.5 * ef) / 40.0)
    return s_gap * s_dep


def score_face(ctx, rect, tgt: Rect, item) -> float:
    vx, vz = tgt.x - rect.x, tgt.z - rect.z
    L = math.hypot(vx, vz)
    if L < 1e-6:
        return 1.0
    c = (rect.sn * vx + rect.cs * vz) / L
    ang = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    return 1.0 if ang <= 30.0 else clamp01(1.0 - (ang - 30.0) / 60.0)


def score_center_align(ctx, rect, tgt: Rect, item) -> float:
    fx, fz, ux, uz = _frame_of(tgt)
    px, pz = rect.x - tgt.x, rect.z - tgt.z
    off = min(abs(px * ux + pz * uz), abs(px * fx + pz * fz))
    return 1.0 if off <= 3.0 else clamp01(1.0 - (off - 3.0) / 37.0)


def score_under(ctx, rect, tgt: Rect, item) -> float:
    inter = convex_intersection_area(rect, tgt)
    cover = inter / max(tgt.area, 1e-6)
    off = math.hypot(rect.x - tgt.x, rect.z - tgt.z)
    return clamp01(cover) * (0.9 + 0.1 * clamp01(1.0 - off / max(rect.w / 2.0, 1.0)))


def score_on_top_of(ctx, rect, tgt: Rect, item) -> float:
    return convex_intersection_area(rect, tgt) / max(rect.area, 1e-6)


# ---- ergonomic objectives -----------------------------------------------

def score_table_gap(ctx, rect, tgt: Rect, item) -> float:
    """Coffee-table front edge 35-50 cm from the seating front."""
    pf, pu, ef, eu = _front_geometry(rect, tgt)
    if pf <= 0:
        return 0.0
    gap = pf - tgt.d / 2.0 - ef
    if 35.0 <= gap <= 50.0:
        return 1.0
    if gap < 35.0:
        return clamp01((gap - 10.0) / 25.0)
    return clamp01(1.0 - (gap - 50.0) / 40.0)


def score_tall_wall(ctx, rect, tgt, item) -> float:
    return clamp01(1.0 - ctx.wall_dist(rect) / 60.0)


def score_window_light(ctx, rect, tgt, item) -> float:
    worst = 1.0
    for o in ctx.windows:
        if item.h > o.sill and o.light is not None:
            frac = convex_intersection_area(rect, o.light) / max(rect.area, 1e-6)
            worst = min(worst, 1.0 - frac)
    return clamp01(worst)


def score_seating_face(ctx, rect, tgt, item) -> float:
    return score_face(ctx, rect, ctx.center_rect, item)


def score_zone(ctx, rect, tgt: Sequence[Rect], item) -> float:
    vals = []
    for m in tgt:
        g = rect_gap(rect, m)
        vals.append(1.0 if g <= 150.0 else clamp01(1.0 - (g - 150.0) / 250.0))
    return sum(vals) / len(vals)


def score_hint(ctx, rect, tgt: dict, item) -> float:
    d = math.hypot(rect.x - tgt["x"], rect.z - tgt["z"])
    pos = math.exp(-d / 80.0)
    rot = tgt.get("rotation_deg")
    if rot is None:
        return pos
    diff = abs((rect.rot - rot + 180.0) % 360.0 - 180.0)
    return pos * (1.0 if diff <= 45.0 else 0.7)


SCORERS = {
    "edge": score_edge, "middle": score_middle, "near": score_near, "far": score_far,
    "in_front_of": score_in_front_of, "side_of": score_side_of, "face": score_face,
    "center_align": score_center_align, "under": score_under, "on_top_of": score_on_top_of,
    "ergo:table_gap": score_table_gap, "ergo:tall_wall": score_tall_wall,
    "ergo:window_light": score_window_light, "ergo:seating_face": score_seating_face,
    "zone": score_zone, "hint": score_hint,
}


def is_seating(category: str) -> bool:
    c = (category or "").lower()
    return any(w in c for w in SEATING_WORDS)


def is_coffee_table(category: str) -> bool:
    c = (category or "").lower()
    return "coffee" in c or "ottoman" in c


# ----------------------------------------------------------------------------
# Parsing constraints.json into Items
# ----------------------------------------------------------------------------

def build_items(items_json: List[dict], constraints: dict, ctx: RoomContext) -> List[Item]:
    ids = [str(it["id"]) for it in items_json]
    idset = set(ids)
    constraints = constraints or {}
    for k in constraints:
        if k not in idset:
            ctx.warnings.append(f"constraints for unknown item {k!r} ignored")
    out: List[Item] = []
    for it in items_json:
        iid = str(it["id"])
        spec = constraints.get(iid) or {}
        item = Item(
            id=iid, category=str(it.get("category", "")), w=float(it["w"]), d=float(it["d"]), h=float(it.get("h", 50)),
            anchor=bool(spec.get("anchor", False)), zone=spec.get("zone") or None)
        hint = spec.get("hint")
        if isinstance(hint, dict) and "x" in hint and "z" in hint:
            item.hint = {"x": float(hint["x"]), "z": float(hint["z"]),
                         "rotation_deg": (float(hint["rotation_deg"]) % 360.0) if hint.get("rotation_deg") is not None else None}
        for c in spec.get("constraints", []) or []:
            ctype = c.get("type")
            target = c.get("target")
            if ctype in IGNORED_TYPES:
                continue
            if ctype not in VOCAB:
                ctx.warnings.append(f"{iid}: unknown constraint type {ctype!r} ignored")
                continue
            if target in ROOM_TARGETS:
                target = "room_center"
            if target is None:
                if ctype in NEEDS_TARGET:
                    ctx.warnings.append(f"{iid}: {ctype} needs a target; ignored")
                    continue
                if ctype in ("face", "near", "far"):
                    target = "room_center"
            elif target == iid:
                ctx.warnings.append(f"{iid}: {ctype} targets itself; ignored")
                continue
            elif target not in idset and not ctx.is_static_target(target):
                ctx.warnings.append(f"{iid}: {ctype} target {target!r} not found; ignored")
                continue
            item.cons.append(Cons(ctype, target))
        # ergonomic objectives
        if is_seating(item.category) and not item.has("face"):
            item.cons.append(Cons("ergo:seating_face", None, 0.5, True))
        if is_coffee_table(item.category):
            for c in list(item.cons):
                if c.type == "in_front_of":
                    item.cons.append(Cons("ergo:table_gap", c.target, 0.5, True))
        if item.h > 150 and not item.flat and not item.has("edge"):
            item.cons.append(Cons("ergo:tall_wall", None, 0.5, True))
        if not item.flat and any(item.h > o.sill for o in ctx.windows):
            item.cons.append(Cons("ergo:window_light", None, 0.5, True))
        if item.zone:
            item.cons.append(Cons("zone", item.zone, 0.5, True))
        if item.hint:
            item.cons.append(Cons("hint", None, 0.6, True))
        out.append(item)
    return out


# ----------------------------------------------------------------------------
# Walkway: occupancy-grid BFS (door -> open centre area), clear width = 2 * half
# ----------------------------------------------------------------------------

class Walkway:
    def __init__(self, ctx: RoomContext, half: float = WALK_HARD / 2.0):
        self.ctx = ctx
        self.half = half
        self.res, self.nx, self.nz = ctx.res, ctx.nx, ctx.nz
        self.minx, self.minz = ctx.minx, ctx.minz
        n = self.nx * self.nz
        base = bytearray(n)
        bd = ctx.bd
        for k in range(n):
            base[k] = 0 if bd[k] >= half - 1e-9 else 1
        for e in ctx.existing:
            if e.h > FLAT_H:
                self.dilate(base, e.rect)
        self.base = base
        cx, cz = ctx.center
        rc = max(100.0, 0.6 * ctx.center_clear)
        goal = bytearray(n)
        for j in range(self.nz):
            pz = self.minz + j * self.res
            if abs(pz - cz) > rc:
                continue
            for i in range(self.nx):
                px = self.minx + i * self.res
                if math.hypot(px - cx, pz - cz) <= rc:
                    goal[j * self.nx + i] = 1
        self.goal = goal
        self.disc = [k for k in range(n) if goal[k]]
        self.doors: List[Tuple[List[int], bool, str]] = []
        self.paths: List[List[int]] = []
        self.warnings: List[str] = []
        for o in ctx.doors:
            seeds = self._seeds(o, base)
            forced = False
            if not seeds:
                c = o.frame.point(o.offset + o.width / 2.0, min(o.swing, 45.0) or 45.0)
                seeds = [self._node(*c)]
                forced = True
            p = self._bfs(base, seeds, forced)
            if p is None:
                self.warnings.append(f"door {o.id}: no {2 * half:.0f} cm path to the room centre even when empty; walkway rule waived for it")
                continue
            self.doors.append((seeds, forced, o.id))
            self.paths.append(p)

    # ---- helpers -----------------------------------------------------
    def _node(self, x: float, z: float) -> int:
        i = min(self.nx - 1, max(0, int(round((x - self.minx) / self.res))))
        j = min(self.nz - 1, max(0, int(round((z - self.minz) / self.res))))
        return j * self.nx + i

    def _seeds(self, o: Opening, base: bytearray) -> List[int]:
        z = o.zone
        x0, z0, x1, z1 = z.aabb()
        i0 = max(0, int(math.ceil((x0 - self.minx) / self.res)))
        i1 = min(self.nx - 1, int(math.floor((x1 - self.minx) / self.res)))
        j0 = max(0, int(math.ceil((z0 - self.minz) / self.res)))
        j1 = min(self.nz - 1, int(math.floor((z1 - self.minz) / self.res)))
        out = []
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                k = j * self.nx + i
                if base[k] == 0 and z.distance_to_point(self.minx + i * self.res, self.minz + j * self.res) <= 0.0:
                    out.append(k)
        return out

    def dilate(self, blocked: bytearray, rect: Rect) -> None:
        h = self.half - 1e-9
        res, nx, minx, minz = self.res, self.nx, self.minx, self.minz
        x0, z0, x1, z1 = rect.aabb()
        i0 = max(0, int(math.ceil((x0 - h - minx) / res)))
        i1 = min(nx - 1, int(math.floor((x1 + h - minx) / res)))
        j0 = max(0, int(math.ceil((z0 - h - minz) / res)))
        j1 = min(self.nz - 1, int(math.floor((z1 + h - minz) / res)))
        h2 = h * h
        if rect.aligned:
            cx, cz, hx, hz = rect.x, rect.z, rect.hx, rect.hz
            for j in range(j0, j1 + 1):
                ez = abs(minz + j * res - cz) - hz
                ez = ez if ez > 0 else 0.0
                row = j * nx
                for i in range(i0, i1 + 1):
                    ex = abs(minx + i * res - cx) - hx
                    ex = ex if ex > 0 else 0.0
                    if ex * ex + ez * ez < h2:
                        blocked[row + i] = 1
        else:
            for j in range(j0, j1 + 1):
                pz = minz + j * res
                for i in range(i0, i1 + 1):
                    if rect.distance_to_point(minx + i * res, pz) < h:
                        blocked[j * nx + i] = 1

    def _path_hit(self, path: List[int], rect: Rect) -> bool:
        h = self.half - 1e-9
        nx, res, minx, minz = self.nx, self.res, self.minx, self.minz
        for k in path:
            if rect.distance_to_point(minx + (k % nx) * res, minz + (k // nx) * res) < h:
                return True
        return False

    def _bfs(self, blocked: bytearray, seeds: List[int], forced: bool) -> Optional[List[int]]:
        """Flood from the door until at least half of the free nodes of the open centre
        area (a disc around the room centre) are reached; returns the path to the last one."""
        nx, nz, goal = self.nx, self.nz, self.goal
        free_disc = 0
        for k in self.disc:
            if not blocked[k]:
                free_disc += 1
        need = (free_disc + 1) // 2
        parent: Dict[int, int] = {}
        dq = deque()
        for s in seeds:
            if forced or not blocked[s]:
                parent[s] = -1
                dq.append(s)
        n = nx * nz
        count = 0
        while dq:
            k = dq.popleft()
            if goal[k]:
                count += 1
                if count >= need:
                    path = []
                    while k != -1:
                        path.append(k)
                        k = parent[k]
                    return path
            i = k % nx
            if i > 0:
                m = k - 1
                if m not in parent and not blocked[m]:
                    parent[m] = k
                    dq.append(m)
            if i < nx - 1:
                m = k + 1
                if m not in parent and not blocked[m]:
                    parent[m] = k
                    dq.append(m)
            if k >= nx:
                m = k - nx
                if m not in parent and not blocked[m]:
                    parent[m] = k
                    dq.append(m)
            if k + nx < n:
                m = k + nx
                if m not in parent and not blocked[m]:
                    parent[m] = k
                    dq.append(m)
        if need == 0 and parent:
            return [next(iter(parent))]
        return None

    # ---- public ------------------------------------------------------
    def check(self, blocked: bytearray, paths: List[List[int]], rect: Rect):
        """Would adding ``rect`` keep every door connected?  -> (ok, new_paths, new_blocked|None)."""
        if not self.doors:
            return True, paths, None
        hit = [k for k, p in enumerate(paths) if self._path_hit(p, rect)]
        if not hit:
            return True, paths, None
        nb = bytearray(blocked)
        self.dilate(nb, rect)
        newp = list(paths)
        for k in hit:
            seeds, forced, _ = self.doors[k]
            p = self._bfs(nb, seeds, forced)
            if p is None:
                return False, None, None
            newp[k] = p
        return True, newp, nb

    def apply(self, blocked: bytearray, rect: Rect) -> bytearray:
        nb = bytearray(blocked)
        self.dilate(nb, rect)
        return nb

    def failing_doors(self, rects: Sequence[Rect]) -> List[str]:
        """Door ids that cannot reach the centre given non-flat obstacle rects."""
        blocked = bytearray(self.base)
        for r in rects:
            self.dilate(blocked, r)
        bad = []
        for seeds, forced, did in self.doors:
            if self._bfs(blocked, seeds, forced) is None:
                bad.append(did)
        return bad


# ----------------------------------------------------------------------------
# Independent validation of a finished layout (used by tests and the CLI)
# ----------------------------------------------------------------------------

def hard_violations(room: dict, items_json: List[dict], layout: dict) -> List[str]:
    """Re-check every hard rule on a layout.json; returns human-readable violations.

    Floor items (no ``mount`` field) get the 2D rules; mounted placements (``mount`` set, see mounts.py)
    are checked as 3D boxes against floor items, openings and each other."""
    from .mounts import Box, is_strip, mounted_violations

    ctx = RoomContext(room)
    by_id = {str(i["id"]): i for i in items_json}
    placed = []
    mbox = []
    fbox = []
    for p in layout["placements"]:
        it = by_id[p["item_id"]]
        r = Rect(p["x"], p["z"], it["w"], it["d"], p.get("rotation_deg", 0))
        h = float(it.get("h", 50))
        if p.get("mount") and p["mount"] != "floor":
            y = float(p.get("y", 0) or 0)
            mbox.append(Box(p["item_id"], r, y, y + h, parent=p.get("parent"), collide=not is_strip(it)))
        else:
            placed.append((p["item_id"], r, h))
            fbox.append(Box(p["item_id"], r, 0.0, h))
    out: List[str] = []
    for iid, r, h in placed:
        cause = ctx.static_reject(r, h)
        if cause:
            out.append(f"{iid}: {cause}")
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            a, b = placed[i], placed[j]
            if overlap_forbidden(a[1], a[2] <= FLAT_H, b[1], b[2] <= FLAT_H):
                out.append(f"{a[0]} overlaps {b[0]}")
    wk = Walkway(ctx)
    bad = wk.failing_doors([r for _, r, h in placed if h > FLAT_H])
    for d in bad:
        out.append(f"walkway from {d} blocked")
    if mbox:
        fbox += [Box("existing:" + e.id, e.rect, 0.0, e.h) for e in ctx.existing]
        out += mounted_violations(ctx, float(room.get("height", 260) or 260), fbox, mbox)
    return out

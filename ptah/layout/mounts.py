"""Non-floor placement: on_top_of, wall_mounted, ceiling, attach_to.

The floor solver (solver.py) only sees floor items.  This module splits the items first
(:func:`partition`) and, once the floor items have final poses, derives the poses of the mounted ones
(:func:`apply_mounts`):

* ``on_top_of``   y = top of the target (target.y + target.h); xy centred on the target (shifted aside
                  when another mounted item already sits there).
* ``wall_mounted`` the back of the item flush to a wall at base height ``height``; the position along the
                  wall is searched (soft constraints such as ``near``/``center_align`` steer it).
* ``ceiling``     hangs ``drop`` cm below the ceiling (``drop`` = visible stem/cord between the ceiling and
                  the item's top, so y = room.height - drop - h), centred over ``above`` when given.
* ``attach_to``   pose derived from the target's *final* pose (side: back | under | top | above).

Collision model: mounted items are boxes (footprint x [y, y+h]); they must not intersect floor items,
existing furniture, wall openings (door / window span in 3D) or each other.  Floor items keep the
2D rules of the solver (rugs excepted).  LED strips (category/template ``led_strip`` or ``light.kind ==
"line"``) have no collision at all.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .constraints import FLAT_H, ROOM_TARGETS, SCORERS, Item, RoomContext, THRESH
from .geometry import TOL, Rect, rect_in_polygon, rects_overlap
from .metrics import (
    ALIGN_BACK_TEMPLATES, STYLING, SURFACE_KINDS, Surface, drape_side, fits_inside, footprint_area, footprint_shape,
    footprints_clear, get_surface, template_of,
)

MOUNT_TYPES = ("on_top_of", "wall_mounted", "ceiling", "attach_to")
SOFT_WALL_TYPES = ("near", "far", "side_of", "in_front_of", "center_align")
EPS = 0.5
ATTACH_SIDES = ("back", "under", "top", "above")
DEFAULT_DROP = 30.0
WALL_GAP = 6.0   # minimum clear gap (cm, in the wall plane) between two wall-mounted groups on the same wall


@dataclass
class MountSpec:
    item_id: str
    type: str
    target: Optional[str] = None
    height: Optional[float] = None
    wall: Optional[str] = None
    drop: Optional[float] = None
    side: Optional[str] = None
    soft: List[dict] = field(default_factory=list)  # remaining (non-mount) constraints
    hint: Optional[dict] = None
    surface: Optional[str] = None   # on_top_of: "top" | "seat" | "shelf" (None = the old "top, centred" behaviour)
    level: Optional[int] = None     # on_top_of a bookshelf: board index from the bottom
    zone: Optional[str] = None


@dataclass
class Box:
    id: str
    rect: Rect
    y0: float
    y1: float
    parent: Optional[str] = None
    collide: bool = True


@dataclass
class MountPlan:
    floor_items: List[dict]
    floor_constraints: dict
    specs: Dict[str, MountSpec]
    order: List[str]            # item ids of the original items.json order
    warnings: List[str]
    zones: Dict[str, List[str]] = field(default_factory=dict)   # zone name -> member item ids


# ----------------------------------------------------------------------------
def is_strip(item: dict) -> bool:
    cat = str(item.get("category", "")).lower()
    tmpl = ((item.get("model") or {}).get("template") or "").lower()
    kind = ((item.get("light") or {}).get("kind") or "").lower()
    return "led_strip" in (cat, tmpl) or "strip" in cat or kind == "line"


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def partition(items: List[dict], constraints: dict) -> MountPlan:
    """Split items into floor items and mounted items (with their MountSpec)."""
    constraints = constraints or {}
    ids = [str(i["id"]) for i in items]
    idset = set(ids)
    warnings: List[str] = []
    specs: Dict[str, MountSpec] = {}
    for it in items:
        iid = str(it["id"])
        spec = constraints.get(iid) or {}
        cons = spec.get("constraints", []) or []
        ms = None
        for c in cons:
            t = c.get("type")
            if t not in MOUNT_TYPES or ms is not None:
                continue
            if t in ("on_top_of", "attach_to"):
                tg = c.get("target")
                if tg == iid or tg not in idset:
                    warnings.append(f"{iid}: {t} target {tg!r} is not an item; ignored")
                    continue
                ms = MountSpec(iid, t, target=str(tg), side=c.get("side"), height=_num(c.get("height")))
                if t == "on_top_of":
                    sf, lv = c.get("surface"), c.get("level")
                    if sf is not None and sf not in SURFACE_KINDS:
                        warnings.append(f"{iid}: unknown surface {sf!r} ignored")
                        sf = None
                    if lv is not None:
                        try:
                            lv = int(lv)
                        except (TypeError, ValueError):
                            warnings.append(f"{iid}: level {lv!r} ignored")
                            lv = None
                    if lv is not None and sf is None:
                        sf = "shelf"
                    ms.surface, ms.level = sf, lv
            elif t == "wall_mounted":
                ms = MountSpec(iid, t, height=_num(c.get("height"), 150.0), wall=c.get("wall"))
            elif t == "ceiling":
                above = c.get("above")
                if above is not None and above not in idset and above not in ROOM_TARGETS:
                    warnings.append(f"{iid}: ceiling above {above!r} not found; centred in the room")
                    above = None
                ms = MountSpec(iid, t, target=above if above not in ROOM_TARGETS else None,
                               drop=_num(c.get("drop"), DEFAULT_DROP))
        if ms is not None:
            ms.soft = [c for c in cons if c.get("type") not in MOUNT_TYPES]
            ms.hint = spec.get("hint") if isinstance(spec.get("hint"), dict) else None
            ms.zone = spec.get("zone") or None
            specs[iid] = ms
    # a dependency cycle (a on b, b on a) cannot be resolved; break it by dropping the later member
    for iid in list(specs):
        seen, cur = {iid}, specs[iid]
        while cur.type in ("on_top_of", "attach_to") and cur.target in specs:
            if cur.target in seen:
                warnings.append(f"{iid}: mount dependency cycle; treated as unplaceable")
                break
            seen.add(cur.target)
            cur = specs[cur.target]
    floor_items = [it for it in items if str(it["id"]) not in specs]
    fset = {str(i["id"]) for i in floor_items}
    fcons = {}
    for k, v in constraints.items():
        if k in specs:
            continue
        if k not in idset:
            fcons[k] = v  # let the solver report "unknown item"
            continue
        newc = []
        for c in v.get("constraints", []) or []:
            t = c.get("type")
            if t in MOUNT_TYPES:
                warnings.append(f"{k}: {t} ignored (invalid or unresolved mount)")
                continue
            tg = c.get("target")
            if tg in specs:
                if t in ("near", "far"):
                    # proximity is symmetric: score it from the mounted side (mounted items are placed after the floor)
                    rev = {"type": t, "target": k}
                    if rev not in specs[tg].soft:
                        specs[tg].soft.append(rev)
                else:
                    warnings.append(f"{k}: {t} target {tg!r} is a mounted item; ignored")
                continue
            newc.append(c)
        nv = dict(v)
        nv["constraints"] = newc
        fcons[k] = nv
    zones: Dict[str, List[str]] = {}
    for it in items:
        z = (constraints.get(str(it["id"])) or {}).get("zone")
        if z:
            zones.setdefault(z, []).append(str(it["id"]))
    return MountPlan(floor_items, fcons, specs, ids, warnings, zones)


# ----------------------------------------------------------------------------
def _y_overlap(a: Box, b: Box) -> bool:
    return min(a.y1, b.y1) - max(a.y0, b.y0) > EPS


def box_conflict(a: Box, b: Box) -> bool:
    if not (a.collide and b.collide):
        return False
    if a.parent == b.id or b.parent == a.id:
        return False
    return _y_overlap(a, b) and rects_overlap(a.rect, b.rect)


def opening_span(o) -> Tuple[float, float]:
    return (o.sill, o.sill + o.height) if o.type == "window" else (0.0, o.height)


def opening_conflict(ctx: RoomContext, box: Box) -> Optional[str]:
    if not box.collide:
        return None
    for o in ctx.openings:
        y0, y1 = opening_span(o)
        if min(box.y1, y1) - max(box.y0, y0) > EPS and rects_overlap(box.rect, o.thin):
            return o.id
    return None


def mounted_violations(ctx: RoomContext, room_h: float, floor_boxes: List[Box], mounted: List[Box]) -> List[str]:
    """Independent check of mounted boxes: inside the room and wall height, clear of openings, floor
    items, existing furniture and each other (parent/child pairs and LED strips excepted)."""
    out: List[str] = []
    for i, b in enumerate(mounted):
        if not b.collide:
            continue
        if not rect_in_polygon(b.rect, ctx.floor, TOL, ctx.bbox):
            out.append(f"{b.id}: mounted outside the room")
        if b.y0 < -EPS or b.y1 > room_h + EPS:
            out.append(f"{b.id}: outside the wall height")
        oid = opening_conflict(ctx, b)
        if oid:
            out.append(f"{b.id} overlaps opening {oid}")
        for o in floor_boxes:
            if box_conflict(b, o):
                out.append(f"{b.id} (mounted) overlaps {o.id}")
        for o in mounted[i + 1:]:
            if box_conflict(b, o):
                out.append(f"{b.id} (mounted) overlaps {o.id}")
    return out


# ----------------------------------------------------------------------------
class MountPlanner:
    def __init__(self, ctx: RoomContext, room: dict, items: List[dict], plan: MountPlan, floor_layout: dict):
        self.ctx = ctx
        self.room_h = float(room.get("height", 260) or 260)
        self.items = {str(i["id"]): i for i in items}
        self.plan = plan
        self.specs = plan.specs
        self.warnings: List[str] = []
        self.floor_ids = {p["item_id"] for p in floor_layout["placements"]}
        self.floor_unplaced = {u["item_id"] for u in floor_layout["unplaced"]}
        self.boxes: Dict[str, Box] = {}
        self.rects: Dict[str, Rect] = {}  # soft-target lookup
        self.results: Dict[str, dict] = {}
        self.failed: Dict[str, dict] = {}
        self.wall_ext: Dict[str, tuple] = {}   # wall-mounted root id -> (wall id, s0, s1, y0, y1) of its whole group
        # styling groups: items sharing a surface (parent, kind, level), in items.json order
        self.surface_groups: Dict[tuple, List[str]] = {}
        for iid in plan.order:
            sp = plan.specs.get(iid)
            if sp is not None and sp.type == "on_top_of" and (sp.surface or sp.level is not None):
                self.surface_groups.setdefault(self.group_key(sp), []).append(iid)
        for p in floor_layout["placements"]:
            it = self.items[p["item_id"]]
            r = Rect(p["x"], p["z"], float(it["w"]), float(it["d"]), p.get("rotation_deg", 0))
            h = float(it.get("h", 50))
            self.boxes[p["item_id"]] = Box(p["item_id"], r, 0.0, h)
            self.rects[p["item_id"]] = r
        for e in ctx.existing:
            self.boxes["existing:" + e.id] = Box("existing:" + e.id, e.rect, 0.0, e.h)

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def group_key(spec: MountSpec) -> tuple:
        kind = spec.surface or ("shelf" if spec.level is not None else "top")
        return (spec.target, kind, (spec.level or 0) if kind == "shelf" else None)

    def dims(self, iid: str, scale: float = 1.0) -> Tuple[float, float, float]:
        it = self.items[iid]
        return float(it["w"]) * scale, float(it["d"]) * scale, float(it.get("h", 50))

    def children_of(self, iid: str) -> List[str]:
        return [c for c, s in self.specs.items() if s.type in ("on_top_of", "attach_to") and s.target == iid and c not in self.results]

    def conflicts(self, box: Box, extra: Optional[List[Box]] = None) -> bool:
        if not box.collide:
            return False
        if box.y0 < -EPS or box.y1 > self.room_h + EPS:
            return True
        if not rect_in_polygon(box.rect, self.ctx.floor, TOL, self.ctx.bbox):
            return True
        if opening_conflict(self.ctx, box):
            return True
        for o in self.boxes.values():
            if o.id != box.id and box_conflict(box, o):
                return True
        for o in extra or []:
            if o.id != box.id and box_conflict(box, o):
                return True
        return False

    def make_box(self, iid: str, rect: Rect, y0: float, parent: Optional[str]) -> Box:
        h = float(self.items[iid].get("h", 50))
        return Box(iid, rect, y0, y0 + h, parent=parent, collide=not is_strip(self.items[iid]))

    # ---- derived poses -------------------------------------------------
    def attach_side(self, spec: MountSpec) -> str:
        if spec.side in ATTACH_SIDES:
            return spec.side
        it = self.items[spec.item_id]
        cat = str(it.get("category", "")).lower()
        if "picture" in cat:
            return "above"
        if is_strip(it):
            return "back"
        return "top"

    def derive(self, iid: str, parent: Box, offset: Tuple[float, float] = (0.0, 0.0)) -> Tuple[Rect, float]:
        """Pose (footprint rect, base y) of a derived item (on_top_of / attach_to) from its parent's box."""
        spec = self.specs[iid]
        w, d, h = self.dims(iid)
        P = parent.rect
        fx, fz = P.front
        ux, uz = P.right
        rot = P.rot
        if spec.type == "on_top_of":
            cx, cz = P.x + ux * offset[0] + fx * offset[1], P.z + uz * offset[0] + fz * offset[1]
            return Rect(cx, cz, w, d, rot), parent.y1
        side = self.attach_side(spec)
        y_over = spec.height
        if side == "back":
            k = P.d / 2.0 - d / 2.0
            cx, cz = P.x - fx * k, P.z - fz * k
            y = y_over if y_over is not None else max(0.0, parent.y1 - h)
        elif side == "under":
            cx, cz = P.x, P.z
            y = y_over if y_over is not None else (6.0 if parent.y0 <= EPS else max(0.0, parent.y0 - h))
        elif side == "above":
            k = d / 2.0 - P.d / 2.0
            cx, cz = P.x + fx * k, P.z + fz * k
            y = y_over if y_over is not None else parent.y1 + 3.0
        else:  # top
            cx, cz = P.x, P.z
            y = y_over if y_over is not None else parent.y1
        return Rect(cx, cz, w, d, rot), y

    def top_offsets(self, iid: str, parent: Box) -> List[Tuple[float, float]]:
        """Candidate (right, forward) offsets on the parent's top surface, centre first.  Wide surfaces
        (desks, consoles) prefer the back-left corner so the work area stays free."""
        w, d, _ = self.dims(iid)
        P = parent.rect
        mu, mf = max(0.0, (P.w - w) / 2.0), max(0.0, (P.d - d) / 2.0)
        us = [0.0] + [s * v for v in range(5, int(mu) + 1, 5) for s in (1, -1)]
        fs = [0.0] + [s * v for v in range(5, int(mf) + 1, 5) for s in (1, -1)]
        if mu and mu not in us:
            us += [mu, -mu]
        if mf and mf not in fs:
            fs += [mf, -mf]
        offs = [(u, f) for u in us for f in fs]
        if P.w > 1.8 * w and P.w > 90:
            offs.sort(key=lambda o: ((o[0] + mu) ** 2 + (o[1] + mf) ** 2))
        else:
            offs.sort(key=lambda o: (o[0] ** 2 + o[1] ** 2, abs(o[0]), abs(o[1])))
        return offs

    # ---- soft scoring --------------------------------------------------
    def soft_target(self, tid: Optional[str]) -> Optional[Rect]:
        if tid is None or tid in ROOM_TARGETS:
            return self.ctx.center_rect
        if tid in self.rects:
            return self.rects[tid]
        return self.ctx.static_target(tid)

    def soft_score(self, iid: str, rect: Rect, spec: MountSpec) -> Tuple[float, float, List[str], List[str]]:
        """-> (sum of weighted scores, total weight, satisfied names, violated names)"""
        w, d, h = self.dims(iid)
        item = Item(iid, str(self.items[iid].get("category", "")), w, d, h)
        num = den = 0.0
        sat: List[str] = []
        vio: List[str] = []
        for c in spec.soft:
            t = c.get("type")
            if t not in SOFT_WALL_TYPES:
                continue
            tid = c.get("target")
            tgt = self.soft_target(tid)
            if tgt is None or tid == iid:
                continue
            v = SCORERS[t](self.ctx, rect, tgt, item)
            num += v
            den += 1.0
            name = t if (tid in ROOM_TARGETS or tid is None) else f"{t}:{tid}"
            (sat if v >= THRESH.get(t, 0.7) else vio).append(name)
        if spec.zone:
            members = [self.rects[o] for o in self.plan.zones.get(spec.zone, []) if o != iid and o in self.rects]
            if members:
                v = SCORERS["zone"](self.ctx, rect, members, item)
                num += 0.5 * v
                den += 0.5
                (sat if v >= THRESH["zone"] else vio).append(f"zone:{spec.zone}")
        if spec.hint and "x" in spec.hint and "z" in spec.hint:
            v = SCORERS["hint"](self.ctx, rect, {"x": float(spec.hint["x"]), "z": float(spec.hint["z"]),
                                                 "rotation_deg": spec.hint.get("rotation_deg")}, item)
            num += 0.6 * v
            den += 0.6
            (sat if v >= THRESH["hint"] else vio).append("hint")
        return num, den, sat, vio

    # ---- searches ------------------------------------------------------
    def wall_rects(self, iid: str, spec: MountSpec, scale: float = 1.0):
        w, d, _ = self.dims(iid, scale)
        frames = [self.ctx.frame_by_id[spec.wall]] if spec.wall in self.ctx.frame_by_id else self.ctx.frames
        if spec.wall and spec.wall not in self.ctx.frame_by_id and spec.wall not in self.warnings:
            self.warnings.append(f"{iid}: wall {spec.wall!r} not found; any wall used")
            frames = self.ctx.frames
        for fr in frames:
            if fr.length < w - TOL:
                continue
            s = w / 2.0
            last = fr.length - w / 2.0
            while s <= last + 1e-9:
                yield fr, s, fr.zone(s - w / 2.0, w, d)
                s += 5.0
            if (last - w / 2.0) % 5.0 > 1e-6:
                yield fr, last, fr.zone(last - w / 2.0, w, d)

    def group_boxes(self, iid: str, rect: Rect, y0: float) -> Optional[List[Box]]:
        """Boxes of an item and its derived descendants at a candidate pose (None if a descendant cannot hang)."""
        root = self.make_box(iid, rect, y0, None)
        out = [root]
        stack = [(iid, root)]
        while stack:
            pid, pbox = stack.pop()
            for cid in self.children_of(pid):
                cr, cy = self.derive(cid, pbox)
                cb = self.make_box(cid, cr, cy, pid)
                out.append(cb)
                stack.append((cid, cb))
        return out

    def group_ok(self, boxes: List[Box]) -> bool:
        for i, b in enumerate(boxes):
            if self.conflicts(b, boxes[:i] + boxes[i + 1:]):
                return False
        return True

    def search_wall(self, iid: str, scale: float = 1.0, score: bool = True):
        spec = self.specs[iid]
        y0 = spec.height if spec.height is not None else 150.0
        best = None
        for n, (fr, s, rect) in enumerate(self.wall_rects(iid, spec, scale)):
            boxes = self.group_boxes(iid, rect, y0)
            if not self.group_ok(boxes) or not self.wall_gap_ok(fr, boxes):
                continue
            if not score:
                return (0.0, fr, s, rect, y0, [], [])
            num, den, sat, vio = self.soft_score(iid, rect, spec)
            val = (num / den if den else 0.5) - 0.002 * abs(s - fr.length / 2.0) / 10.0 + 0.00001 * fr.length / 100.0
            if best is None or val > best[0] + 1e-9:
                best = (val, fr, s, rect, y0, sat, vio)
        return best

    def group_extent(self, fr, boxes: List[Box]) -> tuple:
        """(s0, s1, y0, y1): extent of a group of boxes along wall ``fr`` and in height."""
        ss = [fr.along(cx, cz) for b in boxes if b.collide for cx, cz in b.rect.corners()]
        bs = [b for b in boxes if b.collide]
        return min(ss), max(ss), min(b.y0 for b in bs), max(b.y1 for b in bs)

    def wall_gap_ok(self, fr, boxes: List[Box]) -> bool:
        """Wall-mounted groups keep WALL_GAP cm between each other in the wall plane (art vs guitar vs sconce)."""
        if not any(b.collide for b in boxes):
            return True
        s0, s1, y0, y1 = self.group_extent(fr, boxes)
        for wid, a0, a1, b0, b1 in self.wall_ext.values():
            if wid != fr.id:
                continue
            if s0 < a1 + WALL_GAP and a0 < s1 + WALL_GAP and y0 < b1 + WALL_GAP and b0 < y1 + WALL_GAP:
                return False
        return True

    def search_ceiling(self, iid: str):
        spec = self.specs[iid]
        w, d, h = self.dims(iid)
        y0 = self.room_h - (spec.drop if spec.drop is not None else DEFAULT_DROP) - h
        if spec.target and spec.target in self.boxes:
            T = self.boxes[spec.target].rect
            cx, cz, rot = T.x, T.z, T.rot
        else:
            cx, cz = self.ctx.center
            rot = 0.0
        offs = sorted(((dx, dz) for dx in range(-80, 81, 10) for dz in range(-80, 81, 10)), key=lambda o: (o[0] ** 2 + o[1] ** 2, o))
        for dx, dz in offs:
            rect = Rect(cx + dx, cz + dz, w, d, rot)
            boxes = self.group_boxes(iid, rect, y0)
            if self.group_ok(boxes):
                return rect, y0, boxes
        return None

    # ---- committing ----------------------------------------------------
    def record(self, iid: str, box: Box, mount: str, extra: dict, sat: List[str], vio: List[str], score: float) -> None:
        self.boxes[iid] = box
        self.rects[iid] = box.rect
        y = round(box.y0, 1)
        p = {"item_id": iid, "x": round(box.rect.x, 1), "z": round(box.rect.z, 1),
             "rotation_deg": _int_if(round(box.rect.rot, 1)), "y": _int_if(y), "mount": mount}
        p.update(extra)
        p["satisfied"], p["violated"] = sat, vio
        self.results[iid] = {"placement": p, "score": score}

    def fail(self, iid: str, reason: str, max_w: int = 0, max_d: int = 0) -> None:
        self.failed[iid] = {"item_id": iid, "reason": reason, "max_w": max_w, "max_d": max_d}

    # ---- main loop -----------------------------------------------------
    def run(self) -> None:
        pending = [i for i in self.plan.order if i in self.specs]
        claimed: Dict[str, str] = {}
        guard = 0
        while pending and guard < 4 * len(pending) + 20:
            guard += 1
            progressed = False
            for iid in list(pending):
                spec = self.specs[iid]
                if iid in self.results or iid in self.failed:
                    pending.remove(iid)
                    continue
                if spec.type in ("on_top_of", "attach_to"):
                    tgt = spec.target
                    if tgt in self.failed or tgt in self.floor_unplaced:
                        self.fail(iid, f"{spec.type} target {tgt} could not be placed")
                        pending.remove(iid)
                        progressed = True
                        continue
                    if tgt not in self.boxes:
                        continue  # wait for its parent
                    if self.place_derived(iid, spec):
                        progressed = True
                    pending.remove(iid)
                    progressed = True
                elif spec.type == "wall_mounted":
                    self.place_wall(iid, spec)
                    pending.remove(iid)
                    progressed = True
                elif spec.type == "ceiling":
                    if spec.target and spec.target in self.specs and spec.target not in self.boxes and spec.target not in self.failed:
                        continue
                    self.place_ceiling(iid, spec)
                    pending.remove(iid)
                    progressed = True
            if not progressed:
                break
        for iid in pending:
            if iid not in self.failed and iid not in self.results:
                self.fail(iid, "mount target could not be resolved (dependency cycle or missing target)")

    def _finish_group(self, boxes: List[Box], root_id: str) -> None:
        """Record placements for the derived descendants of a searched item."""
        for b in boxes:
            if b.id == root_id:
                continue
            spec = self.specs[b.id]
            self.boxes[b.id] = b
            self.rects[b.id] = b.rect
            if spec.type == "on_top_of":
                sat, vio = self.derived_names(b.id, spec, b)
                self.record(b.id, b, "on_top_of", {"parent": spec.target, "elevation": _int_if(round(b.y0, 1))}, sat, vio, 1.0 if not vio else 0.5)
            else:
                side = self.attach_side(spec)
                self.record(b.id, b, "attach", {"parent": spec.target, "side": side}, [f"attach_to:{spec.target}"], [], 1.0)

    def derived_names(self, iid: str, spec: MountSpec, box: Box):
        tgt = self.boxes[spec.target]
        inter = _overlap_frac(box.rect, tgt.rect)
        name = f"on_top_of:{spec.target}"
        return ([name], []) if inter >= 0.9 else ([], [name])

    def place_wall(self, iid: str, spec: MountSpec) -> None:
        best = self.search_wall(iid)
        if best is None:
            w, d, _ = self.dims(iid)
            lo, hi = 0.0, 1.0
            if self.search_wall(iid, 0.05, score=False) is not None:
                lo = 0.05
                for _ in range(6):
                    mid = (lo + hi) / 2.0
                    if self.search_wall(iid, mid, score=False) is not None:
                        lo = mid
                    else:
                        hi = mid
            self.fail(iid, "no free wall space at that height (walls, openings and tall furniture in the way)",
                      int(math.floor(w * lo)), int(math.floor(d * lo)))
            for c in self.descendants(iid):
                self.fail(c, f"mount parent {iid} could not be placed")
            return
        val, fr, s, rect, y0, sat, vio = best
        boxes = self.group_boxes(iid, rect, y0)
        root = boxes[0]
        self.boxes[iid] = root
        self.rects[iid] = rect
        names = ["wall_mounted"] + sat
        num, den, _, _ = self.soft_score(iid, rect, spec)
        score = (1.0 + num) / (1.0 + den)
        self.record(iid, root, "wall", {"wall": fr.id}, names, vio, score)
        self.wall_ext[iid] = (fr.id,) + self.group_extent(fr, boxes)
        self._finish_group(boxes, iid)

    def descendants(self, iid: str) -> List[str]:
        out, stack = [], [iid]
        while stack:
            p = stack.pop()
            for c, s in self.specs.items():
                if s.type in ("on_top_of", "attach_to") and s.target == p and c not in out:
                    out.append(c)
                    stack.append(c)
        return out

    def place_ceiling(self, iid: str, spec: MountSpec) -> None:
        res = self.search_ceiling(iid)
        if res is None:
            self.fail(iid, "no free ceiling spot (something is in the way below the fixture)")
            for c in self.descendants(iid):
                self.fail(c, f"mount parent {iid} could not be placed")
            return
        rect, y0, boxes = res
        root = boxes[0]
        sat, vio = ["ceiling"], []
        score = 1.0
        if spec.target:
            tgt = self.rects.get(spec.target)
            if tgt is not None:
                off = math.hypot(rect.x - tgt.x, rect.z - tgt.z)
                v = max(0.0, 1.0 - off / 60.0)
                (sat if v >= 0.7 else vio).append(f"ceiling:{spec.target}")
                score = (1.0 + v) / 2.0
        self.record(iid, root, "ceiling", {}, sat, vio, score)
        if spec.target:
            self.results[iid]["placement"]["above"] = spec.target
        self._finish_group(boxes, iid)

    # ---- styling: surfaces (seat / shelf board / table top) -------------
    def surface_siblings(self, tgt_id: str, surf: Surface, pbox: Box) -> List[tuple]:
        """Footprints (parent-local cx, cz, w, d, shape) of everything already standing on this surface."""
        out = []
        for b in self.boxes.values():
            if b.parent != tgt_id or b.id not in self.items or not b.collide:
                continue
            if abs(b.y0 - (pbox.y0 + surf.y)) > 1.0:
                continue
            lx, lz = pbox.rect.to_local(b.rect.x, b.rect.z)
            it = self.items[b.id]
            out.append((lx, lz, float(it["w"]), float(it["d"]), footprint_shape(it)))
        return out

    def ideal_pose(self, iid: str, spec: MountSpec, surf: Surface) -> Tuple[float, float]:
        """Preferred surface-local (u, v): members of a group share the free span evenly; a throw sticks to its arm end."""
        margin, gap = STYLING["edge_margin_cm"], STYLING["gap_cm"]
        members = self.surface_groups.get(self.group_key(spec), [iid])
        lo, hi = surf.cx - surf.w / 2 + margin, surf.cx + surf.w / 2 - margin
        if surf.shape == "circle":
            r = 0.7 * (min(surf.w, surf.d) / 2 - margin)
            lo, hi = surf.cx - r, surf.cx + r
        item = self.items[iid]
        side = drape_side(item)
        fw = float(item["w"])
        if side:
            u = (hi - fw / 2) if side == "right" else (lo + fw / 2)
        else:
            free_lo, free_hi = lo, hi
            for m in members:
                s = drape_side(self.items[m])
                if s == "right":
                    free_hi -= float(self.items[m]["w"]) + gap
                elif s == "left":
                    free_lo += float(self.items[m]["w"]) + gap
            plain = [m for m in members if not drape_side(self.items[m])]
            k, n = plain.index(iid) if iid in plain else 0, max(1, len(plain))
            u = free_lo + (k + 0.5) * (free_hi - free_lo) / n
        fd = float(item["d"])
        v = surf.cz
        if template_of(item) in ALIGN_BACK_TEMPLATES:
            v = surf.cz - surf.d / 2 + margin + fd / 2
        return u, v

    def place_surface(self, iid: str, spec: MountSpec) -> bool:
        tgt = spec.target
        pbox = self.boxes[tgt]
        surf = get_surface(self.items[tgt], spec.surface, spec.level)
        want = (spec.surface or "shelf") + (f" level {spec.level}" if spec.level is not None else "")
        if surf is None:
            self.fail(iid, f"{tgt} has no {want} surface")
            return self._fail_children(iid)
        item = self.items[iid]
        w, d, h = self.dims(iid)
        if surf.clear is not None and h > surf.clear + 1e-6:
            self.fail(iid, f"too tall for {want} of {tgt} ({h:g} cm item, {surf.clear:.0f} cm free)")
            return self._fail_children(iid)
        sibs = self.surface_siblings(tgt, surf, pbox)
        if len(sibs) + 1 > STYLING["max_items"][surf.kind]:
            self.fail(iid, f"styling density cap: {want} of {tgt} already holds {len(sibs)} items (max {STYLING['max_items'][surf.kind]})")
            return self._fail_children(iid)
        used = sum(sw * sd if sh == "rect" else math.pi * (min(sw, sd) / 2) ** 2 for _, _, sw, sd, sh in sibs)
        if used + footprint_area(item) > STYLING["max_cover"] * surf.area + 1e-6:
            self.fail(iid, f"styling density cap: {want} of {tgt} would be more than {int(STYLING['max_cover'] * 100)}% covered")
            return self._fail_children(iid)
        iu, iv = self.ideal_pose(iid, spec, surf)
        margin, gap = STYLING["edge_margin_cm"], STYLING["gap_cm"]
        me = footprint_shape(item)
        us = [surf.cx + k * 1.0 - surf.w / 2 for k in range(int(surf.w) + 1)]
        vs = [surf.cz + k * 1.0 - surf.d / 2 for k in range(int(surf.d) + 1)]
        cands = sorted(((u, v) for u in us for v in vs), key=lambda c: (abs(c[0] - iu) + 2.0 * abs(c[1] - iv), c))
        P = pbox.rect
        fx, fz = P.front
        ux, uz = P.right
        for u, v in cands:
            if not fits_inside(surf, u, v, item, margin):
                continue
            if not all(footprints_clear((u, v, w, d, me), s, gap) for s in sibs):
                continue
            rect = Rect(P.x + ux * u + fx * v, P.z + uz * u + fz * v, w, d, P.rot)
            y0 = pbox.y0 + surf.y
            boxes = self.group_boxes(iid, rect, y0)
            for b in boxes:
                if b.id == iid:
                    b.parent = tgt
            if not self.group_ok(boxes):
                continue
            root = boxes[0]
            names = [f"on_top_of:{tgt}", f"surface:{surf.kind}" + (f"#{surf.level}" if surf.level is not None else "")]
            extra = {"parent": tgt, "elevation": _int_if(round(y0, 1)), "surface": surf.kind}
            if surf.level is not None:
                extra["level"] = surf.level
            self.record(iid, root, "on_top_of", extra, names, [], 1.0)
            self._finish_group(boxes, iid)
            return True
        self.fail(iid, f"no free spot on {want} of {tgt} (styling density / collisions)")
        return self._fail_children(iid)

    def _fail_children(self, iid: str) -> bool:
        for c in self.descendants(iid):
            self.fail(c, f"mount parent {iid} could not be placed")
        return False

    def place_derived(self, iid: str, spec: MountSpec) -> bool:
        if spec.type == "on_top_of" and (spec.surface or spec.level is not None):
            return self.place_surface(iid, spec)
        parent = self.boxes[spec.target]
        if spec.type == "on_top_of":
            offs = self.top_offsets(iid, parent)
        else:
            offs = [(0.0, 0.0)]
        for off in offs:
            rect, y0 = self.derive(iid, parent, off)
            boxes = self.group_boxes(iid, rect, y0)
            for b in boxes:
                if b.id == iid:
                    b.parent = spec.target
            if self.group_ok(boxes):
                root = boxes[0]
                if spec.type == "on_top_of":
                    sat, vio = self.derived_names(iid, spec, root)
                    self.record(iid, root, "on_top_of", {"parent": spec.target, "elevation": _int_if(round(y0, 1))}, sat, vio,
                                1.0 if not vio else 0.5)
                else:
                    self.record(iid, root, "attach", {"parent": spec.target, "side": self.attach_side(spec)},
                                [f"attach_to:{spec.target}"], [], 1.0)
                self._finish_group(boxes, iid)
                return True
        self.fail(iid, f"no free spot for {spec.type.replace('_', ' ')} {spec.target} (collision with other items)")
        for c in self.descendants(iid):
            self.fail(c, f"mount parent {iid} could not be placed")
        return False


def _int_if(v: float):
    return int(v) if float(v) == int(v) else v


def _overlap_frac(a: Rect, b: Rect) -> float:
    from .geometry import convex_intersection_area

    return convex_intersection_area(a, b) / max(a.area, 1e-6)


# ----------------------------------------------------------------------------
def apply_mounts(ctx: RoomContext, room: dict, items: List[dict], plan: MountPlan, layout: dict) -> None:
    """Add mounted placements to ``layout`` (in place): y for every placement, mount/parent/wall/side fields."""
    n_floor = len(plan.floor_items)
    floor_score = float(layout.get("score", 0.0))
    for p in layout["placements"]:
        p.setdefault("y", 0)
    if not plan.specs:
        return
    mp = MountPlanner(ctx, room, items, plan, layout)
    mp.run()
    by_id = {p["item_id"]: p for p in layout["placements"]}
    mounted_scores = 0.0
    for iid in plan.order:
        rec = mp.results.get(iid)
        if rec and "placement" in rec:
            by_id[iid] = rec["placement"]
            mounted_scores += rec["score"]
    layout["placements"] = [by_id[i] for i in plan.order if i in by_id]
    layout["unplaced"] = list(layout["unplaced"]) + [mp.failed[i] for i in plan.order if i in mp.failed]
    total = len(plan.order)
    if total:
        layout["score"] = round(max(0.0, (floor_score * n_floor + mounted_scores) / total), 3)
    warns = list(layout.get("warnings", [])) + plan.warnings + mp.warnings
    if warns:
        layout["warnings"] = warns

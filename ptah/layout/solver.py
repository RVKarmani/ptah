"""Holodeck-style constraint solver: candidate poses + depth-first search with backtracking.

Adapted from Holodeck (Yang et al., CVPR 2024), informed by LayoutVLM (Sun et al., CVPR 2025).
Deterministic: candidates come from fixed grids in a fixed order and the search is bounded by
an expansion budget (the wall-clock limit is only a safety net).
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .constraints import (
    FLAT_H,
    PROX_TYPES,
    SCORERS,
    WALK_SOFT,
    Cons,
    Item,
    RoomContext,
    Walkway,
    build_items,
    overlap_forbidden,
)
from .geometry import TOL, Rect


@dataclass
class Budget:
    time_s: float = 2.0          # safety net for the DFS phase
    max_expansions: int = 260    # primary (deterministic) limit
    anchor_k: int = 10           # anchor candidates tried in the greedy phase
    patience: int = 60           # leaves without improvement before phase B stops
    step: float = 10.0           # grid step (cm)
    fit_time_s: float = 0.8      # extra time allowed for max_w/max_d estimation


@dataclass
class SolveResult:
    layout: dict
    stats: dict


def frange(lo: float, hi: float, step: float) -> List[float]:
    if hi < lo - 1e-9:
        return []
    out = []
    v = lo
    while v <= hi + 1e-9:
        out.append(v)
        v += step
    if out[-1] < hi - 1e-6:
        out.append(hi)
    return out


class State:
    __slots__ = ("rects", "flats", "blocked", "paths", "blocked75", "paths75", "ok75")

    def fork(self) -> "State":
        s = State()
        s.rects = dict(self.rects)
        s.flats = dict(self.flats)
        s.blocked, s.paths = self.blocked, self.paths
        s.blocked75, s.paths75, s.ok75 = self.blocked75, self.paths75, self.ok75
        return s


class Choice:
    __slots__ = ("score", "rect", "paths", "nb", "paths75", "nb75", "ok75")

    def __init__(self, score, rect, paths, nb, paths75, nb75, ok75):
        self.score, self.rect, self.paths, self.nb = score, rect, paths, nb
        self.paths75, self.nb75, self.ok75 = paths75, nb75, ok75


def order_items(items: List[Item]) -> List[Item]:
    ids = {i.id for i in items}
    deps = {i.id: {c.target for c in i.cons if c.target in ids and c.target != i.id and c.type != "zone"} for i in items}
    for i in items:
        if i.anchor:
            deps[i.id] = set()  # anchors are placed first; constraints to later items are checked at the end
    remaining = list(items)
    done = set()
    order: List[Item] = []
    idx = {i.id: n for n, i in enumerate(items)}
    while remaining:
        ready = [i for i in remaining if deps[i.id] <= done] or remaining  # cycle: break by priority
        pick = min(ready, key=lambda i: (not i.anchor, not i.has("edge"), -i.area, idx[i.id]))
        order.append(pick)
        done.add(pick.id)
        remaining.remove(pick)
    return order


class Solver:
    def __init__(self, room: dict, items_json: List[dict], constraints: dict, budget: Optional[Budget] = None,
                 room_name: str = "room.json"):
        self.budget = budget or Budget()
        self.room_name = room_name
        self.ctx = RoomContext(room)
        self.items = build_items(items_json, constraints, self.ctx)
        self.by_id: Dict[str, Item] = {i.id: i for i in self.items}
        self.item_h = {i.id: i.h for i in self.items}
        self.walk = Walkway(self.ctx)
        self.walk75 = Walkway(self.ctx, WALK_SOFT / 2.0)
        self.order = order_items(self.items)
        self.zones: Dict[str, List[str]] = {}
        for i in self.items:
            if i.zone:
                self.zones.setdefault(i.zone, []).append(i.id)
        self.static_cons: Dict[str, List[Cons]] = {}
        self.rel_cons: Dict[str, List[Cons]] = {}
        self.ontop: Dict[str, set] = {}
        for i in self.items:
            st, rl = [], []
            for c in i.cons:
                if c.type == "zone" or (c.target in self.by_id):
                    rl.append(c)
                else:
                    st.append(c)
            self.static_cons[i.id], self.rel_cons[i.id] = st, rl
            self.ontop[i.id] = {c.target for c in i.cons if c.type == "on_top_of"}
        self._static_cache: Dict[str, list] = {}
        self._hint_cache: Dict[str, list] = {}
        self.expansions = 0
        self.leaves = 0
        self.since_improve = 0
        self.best = None
        self.t0 = time.perf_counter()
        self.deadline = self.t0 + self.budget.time_s
        self.max_exp = self.budget.max_expansions
        self.patience_on = False
        self.root_chosen: List[Choice] = []
        self.root_results: Dict[int, tuple] = {}
        self.cur_root = -1
        self.perfect = False

    # ------------------------------------------------------------------
    # scoring helpers
    # ------------------------------------------------------------------
    def eval_cons(self, item: Item, c: Cons, rect: Rect, rects: Dict[str, Rect], final: bool = False) -> Optional[float]:
        t = c.type
        fn = SCORERS[t]
        if t == "zone":
            members = [rects[o] for o in self.zones.get(c.target, []) if o != item.id and o in rects]
            if not members:
                return None
            return fn(self.ctx, rect, members, item)
        if t == "hint":
            return fn(self.ctx, rect, item.hint, item)
        if t in ("edge", "middle", "ergo:tall_wall", "ergo:window_light", "ergo:seating_face"):
            return fn(self.ctx, rect, None, item)
        tid = c.target
        if tid in self.by_id:
            tgt = rects.get(tid)
            if tgt is None:
                return (0.0 if not c.synthetic else None) if final else None
        else:
            tgt = self.ctx.static_target(tid)
            if tgt is None:
                return None
        return fn(self.ctx, rect, tgt, item)

    def static_score(self, item: Item, rect: Rect) -> float:
        s = 0.0
        for c in self.static_cons[item.id]:
            v = self.eval_cons(item, c, rect, {})
            if v is not None:
                s += c.weight * v
        return s

    # ------------------------------------------------------------------
    # candidate generation
    # ------------------------------------------------------------------
    def edge_rects(self, item: Item, step: float = 5.0) -> List[Rect]:
        out = []
        for fr in self.ctx.frames:
            if fr.length < item.w - TOL:
                continue
            for s in frange(item.w / 2.0, fr.length - item.w / 2.0, step):
                cx, cz = fr.point(s, item.d / 2.0)
                out.append(Rect(cx, cz, item.w, item.d, fr.rot))
        return out

    def grid_rects(self, item: Item, step: float) -> List[Tuple[Rect, Rect]]:
        """Footprint pairs (rot r, rot r+180) over the grid, for r in 0 and 90."""
        minx, minz, maxx, maxz = self.ctx.bbox
        out = []
        for swap in (False, True):
            hx, hz = (item.d / 2.0, item.w / 2.0) if swap else (item.w / 2.0, item.d / 2.0)
            r0 = 90.0 if swap else 0.0
            for x in frange(minx + hx, maxx - hx, step):
                for z in frange(minz + hz, maxz - hz, step):
                    out.append((Rect(x, z, item.w, item.d, r0), Rect(x, z, item.w, item.d, r0 + 180.0)))
        return out

    def hint_rects(self, item: Item) -> List[Rect]:
        h = item.hint
        if not h:
            return []
        rots = [0.0, 90.0, 180.0, 270.0]
        if h.get("rotation_deg") is not None:
            snapped = (round(h["rotation_deg"] / 90.0) % 4) * 90.0
            rots.remove(snapped)
            rots.insert(0, snapped)
        out = []
        for dx in range(-40, 41, 10):
            for dz in range(-40, 41, 10):
                for r in rots:
                    out.append(Rect(h["x"] + dx, h["z"] + dz, item.w, item.d, r))
        return out

    def static_list(self, item: Item) -> list:
        """Hard-valid (static) candidates with their cached unary score: [(rect, usum)]."""
        if item.id in self._static_cache:
            return self._static_cache[item.id]
        ctx = self.ctx
        out = []
        seen = set()

        def add(rect):
            key = (round(rect.x, 1), round(rect.z, 1), round(rect.rot, 1))
            if key in seen:
                return
            seen.add(key)
            out.append((rect, self.static_score(item, rect)))

        if item.has("edge"):
            for r in self.edge_rects(item):
                if ctx.static_reject(r, item.h) is None:
                    add(r)
        else:
            for a, b in self.grid_rects(item, self.budget.step):
                if ctx.static_reject(a, item.h) is None:
                    add(a)
                    add(b)
            for r in self.hint_extra(item):
                add(r)
        self._static_cache[item.id] = out
        return out

    def hint_extra(self, item: Item) -> List[Rect]:
        if item.id not in self._hint_cache:
            self._hint_cache[item.id] = [r for r in self.hint_rects(item) if self.ctx.static_reject(r, item.h) is None]
        return self._hint_cache[item.id]

    def derived_list(self, item: Item, prox: List[Cons], state: State) -> list:
        """Candidates built around already-placed targets (Holodeck-style relational placement)."""
        ctx = self.ctx
        out, seen = [], set()

        def add(x, z, rot):
            key = (round(x, 1), round(z, 1), round(rot % 360, 1))
            if key in seen:
                return
            seen.add(key)
            r = Rect(x, z, item.w, item.d, rot)
            if ctx.static_reject(r, item.h) is None:
                out.append((r, self.static_score(item, r)))

        for c in prox:
            T = state.rects[c.target]
            fx, fz, ux, uz = T.sn, T.cs, T.cs, -T.sn
            for dr in (0.0, 180.0, 90.0, 270.0):
                rot = T.rot + dr
                A = Rect(0, 0, item.w, item.d, rot)
                ef = A.half_extent_along(fx, fz)
                eu = A.half_extent_along(ux, uz)
                t = c.type
                if t in ("in_front_of", "near", "center_align"):
                    lats = (0, 10, -10, 20, -20, 40, -40, 70, -70) if t != "center_align" else (0,)
                    for gap in (10, 25, 35, 42, 50, 65):
                        d = T.d / 2.0 + gap + ef
                        for lat in lats:
                            add(T.x + fx * d + ux * lat, T.z + fz * d + uz * lat, rot)
                if t in ("side_of", "near", "center_align"):
                    for sg in (1, -1):
                        for gap in (3, 8, 15, 30, 50):
                            d = T.w / 2.0 + gap + eu
                            for off in (0.0, T.d / 2.0 - ef, -(T.d / 2.0 - ef)):
                                add(T.x + ux * sg * d + fx * off, T.z + uz * sg * d + fz * off, rot)
                if t == "near":
                    for gap in (10, 30):
                        d = T.d / 2.0 + gap + ef
                        add(T.x - fx * d, T.z - fz * d, rot)
                if t in ("under", "on_top_of"):
                    for a in (0, 15, -15, 30, -30):
                        for b in (0, 15, -15, 30, -30):
                            add(T.x + fx * a + ux * b, T.z + fz * a + uz * b, rot)
        for r in self.hint_extra(item):
            key = (round(r.x, 1), round(r.z, 1), round(r.rot, 1))
            if key not in seen:
                seen.add(key)
                out.append((r, self.static_score(item, r)))
        return out

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def candidates(self, item: Item, state: State) -> list:
        placed = state.rects
        active = []
        for c in self.rel_cons[item.id]:
            if c.type == "zone":
                if any(o != item.id and o in placed for o in self.zones.get(c.target, [])):
                    active.append(c)
            elif c.target in placed:
                active.append(c)
        prox = [c for c in active if c.type in PROX_TYPES]
        if item.has("edge"):
            base = self.static_list(item)
        elif prox:
            base = self.derived_list(item, prox, state) or self.static_list(item)
        else:
            base = self.static_list(item)
        flat = item.flat
        ontop = self.ontop[item.id]
        flats = state.flats
        out = []
        for rect, usum in base:
            ok = True
            for pid, pr in placed.items():
                if pid in ontop:
                    continue
                if overlap_forbidden(rect, flat, pr, flats[pid]):
                    ok = False
                    break
            if not ok:
                continue
            s = usum
            for c in active:
                v = self.eval_cons(item, c, rect, placed)
                if v is not None:
                    s += c.weight * v
            out.append((s, rect))
        out.sort(key=lambda t: -t[0])
        return out

    def expand(self, idx: int, state: State, limit: int) -> List[Choice]:
        self.expansions += 1
        item = self.order[idx]
        cands = self.candidates(item, state)
        nms = 60.0 if item.anchor else 20.0
        cap = max(2, (limit + 1) // 2) if limit > 2 else limit
        chosen: List[Choice] = []
        deferred: List[Choice] = []
        picked: List[Rect] = []
        rot_count: Counter = Counter()
        tries = 0
        flat = item.flat
        for sc, rect in cands:
            if len(chosen) >= limit or tries > 150:
                break
            if any(abs(p.rot - rect.rot) < 1.0 and math.hypot(p.x - rect.x, p.z - rect.z) < nms for p in picked):
                continue
            if rot_count[rect.rot] >= cap:
                continue
            tries += 1
            if flat:
                ok, paths, nb = True, state.paths, None
                ok75, paths75, nb75 = state.ok75, state.paths75, None
            else:
                ok, paths, nb = self.walk.check(state.blocked, state.paths, rect)
                if not ok:
                    continue
                if state.ok75:
                    ok75, paths75, nb75 = self.walk75.check(state.blocked75, state.paths75, rect)
                else:
                    ok75, paths75, nb75 = False, None, None
            ch = Choice(sc, rect, paths, nb, paths75, nb75, ok75)
            picked.append(rect)
            rot_count[rect.rot] += 1
            (chosen if ok75 else deferred).append(ch)
        for ch in deferred:
            if len(chosen) >= limit:
                break
            chosen.append(ch)
        return chosen

    def place(self, state: State, item: Item, ch: Choice) -> State:
        st = state.fork()
        st.rects[item.id] = ch.rect
        st.flats[item.id] = item.flat
        if not item.flat:
            st.blocked = ch.nb if ch.nb is not None else self.walk.apply(state.blocked, ch.rect)
            st.paths = ch.paths
            if state.ok75:
                if ch.ok75:
                    st.blocked75 = ch.nb75 if ch.nb75 is not None else self.walk75.apply(state.blocked75, ch.rect)
                    st.paths75 = ch.paths75
                else:
                    st.ok75, st.blocked75, st.paths75 = False, None, None
        return st

    def stop(self) -> bool:
        if self.perfect or self.expansions >= self.max_exp:
            return True
        if time.perf_counter() > self.deadline:
            return True
        return self.patience_on and self.since_improve >= self.budget.patience

    def dfs(self, idx: int, state: State, limits: List[int], phase_b_roots: Optional[List[Choice]] = None) -> None:
        if self.stop():
            return
        if idx == len(self.order):
            self.leaf(state)
            return
        item = self.order[idx]
        if idx == 0 and phase_b_roots is not None:
            chosen = phase_b_roots
        else:
            lim = limits[idx] if idx < len(limits) else 1
            chosen = self.expand(idx, state, lim)
            if idx == 0:
                self.root_chosen = chosen
        if not chosen:
            self.dfs(idx + 1, state, limits)
            return
        for n, ch in enumerate(chosen):
            if idx == 0:
                self.cur_root = n if phase_b_roots is None else self.root_chosen.index(ch)
            self.dfs(idx + 1, self.place(state, item, ch), limits)
            if self.stop():
                return

    # ------------------------------------------------------------------
    def evaluate(self, rects: Dict[str, Rect]) -> dict:
        per_item = {}
        detail = {}
        for item in self.items:
            if item.id not in rects:
                per_item[item.id] = 0.0
                continue
            sat, vio, num, den = [], [], 0.0, 0.0
            for c in item.cons:
                v = self.eval_cons(item, c, rects[item.id], rects, final=True)
                if v is None:
                    continue
                num += c.weight * v
                den += c.weight
                (sat if v >= c.threshold else vio).append(c.name)
            per_item[item.id] = num / den if den else 1.0
            detail[item.id] = (sat, vio)
        score = sum(per_item.values()) / max(1, len(per_item))
        return {"score": score, "detail": detail}

    def leaf(self, state: State) -> None:
        self.leaves += 1
        ev = self.evaluate(state.rects)
        score = ev["score"] - (0.05 if not state.ok75 else 0.0)
        key = (len(state.rects), round(score, 6))
        if self.best is None or key > self.best[0]:
            # the path cache is a shortcut; confirm the winner with a full walkway check
            if self.walk.failing_doors([r for i, r in state.rects.items() if not state.flats[i]]):
                return
            self.best = (key, state, ev)
            self.since_improve = 0
        else:
            self.since_improve += 1
        prev = self.root_results.get(self.cur_root)
        if prev is None or key > prev:
            self.root_results[self.cur_root] = key
        if len(state.rects) == len(self.items) and score >= 0.998:
            self.perfect = True

    def initial_state(self) -> State:
        s = State()
        s.rects, s.flats = {}, {}
        s.blocked, s.paths = bytearray(self.walk.base), list(self.walk.paths)
        s.blocked75, s.paths75, s.ok75 = bytearray(self.walk75.base), list(self.walk75.paths), True
        return s

    def search(self) -> None:
        if not self.order:
            return
        b = self.budget
        root = self.initial_state()
        n = len(self.order)
        # Phase A: greedy completion from each of the top anchor candidates
        self.dfs(0, root, [b.anchor_k])
        if self.perfect:
            return
        # Phase B: backtracking around the best anchor placements
        roots = list(self.root_chosen)
        ranked = sorted(range(len(roots)), key=lambda i: self.root_results.get(i, (-1, -1.0)), reverse=True)
        self.patience_on = True
        self.since_improve = 0
        self.max_exp = min(self.max_exp, self.expansions + max(40, b.max_expansions - self.expansions))
        limits = [2, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2][:n]
        if roots:
            top = [roots[i] for i in ranked[:2]]
            self.dfs(0, root, limits, top)
        else:
            self.dfs(0, root, limits)

    # ------------------------------------------------------------------
    # unplaced items: reason + largest footprint that would fit
    # ------------------------------------------------------------------
    def _first_fit(self, item: Item, state: State, counts: Optional[Counter] = None) -> bool:
        ctx = self.ctx
        if item.has("edge"):
            rects = self.edge_rects(item)
        else:
            rects = []
            for a, b in self.grid_rects(item, self.budget.step):
                rects.append(a)
        flat = item.flat
        for rect in rects:
            cause = ctx.static_reject(rect, item.h)
            if cause:
                if counts is not None:
                    counts[cause] += 1
                continue
            if any(overlap_forbidden(rect, flat, pr, state.flats[pid]) for pid, pr in state.rects.items()):
                if counts is not None:
                    counts["overlap"] += 1
                continue
            if not flat:
                ok, _, _ = self.walk.check(state.blocked, state.paths, rect)
                if not ok:
                    if counts is not None:
                        counts["walkway"] += 1
                    continue
            if counts is not None:
                counts["ok"] += 1
            return True
        return False

    REASONS = {
        "outside_floor": "does not fit inside the floor",
        "existing": "blocked by existing furniture",
        "overlap": "blocked by other placed items",
        "door_swing": "would block a door swing zone",
        "window": "tall item cannot stand in front of a window",
        "walkway": "would block the 60 cm walkway from the door",
    }

    def explain_unplaced(self, item: Item, state: State) -> dict:
        counts: Counter = Counter()
        self._first_fit(item, state, counts)
        counts.pop("ok", None)
        main = counts.most_common(1)[0][0] if counts else "outside_floor"
        text = self.REASONS.get(main, main)
        reason = ("no wall space: " if item.has("edge") else "no free space: ") + text
        reason += f" (tried {sum(counts.values())} poses)"
        # largest scaled footprint that fits
        deadline = time.perf_counter() + self.budget.fit_time_s
        lo, hi = 0.0, 1.0
        if self._first_fit(item.scaled(0.05), state):
            lo = 0.05
            for _ in range(7):
                if time.perf_counter() > deadline:
                    break
                mid = (lo + hi) / 2.0
                if self._first_fit(item.scaled(mid), state):
                    lo = mid
                else:
                    hi = mid
        return {"item_id": item.id, "reason": reason, "max_w": int(math.floor(item.w * lo)), "max_d": int(math.floor(item.d * lo))}

    # ------------------------------------------------------------------
    def run(self) -> SolveResult:
        self.search()
        if self.best is None:
            state = self.initial_state()
            ev = self.evaluate({})
        else:
            _, state, ev = self.best
        placements, unplaced = [], []
        for item in self.items:
            if item.id in state.rects:
                r = state.rects[item.id]
                sat, vio = ev["detail"][item.id]
                rot = round(r.rot, 1)
                p = {"item_id": item.id, "x": round(r.x, 1), "z": round(r.z, 1),
                     "rotation_deg": int(rot) if rot == int(rot) else rot, "satisfied": sat, "violated": vio}
                for tid in self.ontop[item.id]:
                    if tid in self.item_h and tid in state.rects:
                        p["elevation"] = self.item_h[tid]
                placements.append(p)
        for item in self.items:
            if item.id not in state.rects:
                unplaced.append(self.explain_unplaced(item, state))
        score = ev["score"] - (0.05 if not state.ok75 and state.rects else 0.0)
        layout = {"room": self.room_name, "placements": placements, "unplaced": unplaced, "score": round(max(0.0, score), 3)}
        warns = list(self.ctx.warnings) + list(self.walk.warnings)
        if warns:
            layout["warnings"] = warns
        stats = {"runtime_s": round(time.perf_counter() - self.t0, 3), "expansions": self.expansions, "leaves": self.leaves,
                 "walkway_75cm_ok": bool(state.ok75)}
        return SolveResult(layout, stats)


def solve(room: dict, items: List[dict], constraints: dict, budget: Optional[Budget] = None, room_name: str = "room.json") -> SolveResult:
    """Floor items go through the search; mounted items (on_top_of / wall_mounted / ceiling / attach_to,
    see mounts.py) get their poses derived afterwards.  Every placement carries ``y``."""
    from .mounts import apply_mounts, partition

    plan = partition(items, constraints)
    solver = Solver(room, plan.floor_items, plan.floor_constraints, budget, room_name)
    res = solver.run()
    apply_mounts(solver.ctx, room, items, plan, res.layout)
    res.stats["mounted"] = len(plan.specs)
    res.stats["runtime_s"] = round(time.perf_counter() - solver.t0, 3)
    return res


def plan_layout(room: dict, items: List[dict], constraints: dict, budget: Optional[Budget] = None, room_name: str = "room.json") -> dict:
    return solve(room, items, constraints, budget, room_name).layout

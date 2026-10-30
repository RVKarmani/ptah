"""Design spec -> bundle: items / room / constraints for the layout solver, lighting check, summary, viewer copy.

Designed pieces are Sample-quality procedural items (price null, ``designed: true``, ``slot`` = what a shopping search can
target, ``why_chain`` = the taste reasoning). The room geometry and the owned items (TV, guitar, amp) come from the sample
and stay; the zone structure (TV wall focal + secondary music corner) is reused and adapted to the pieces.
"""
from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .design import ROLES, DesignInputs, DesignSpec, PieceSlot, Vocab, build_vocab
from .layout import Budget, hard_violations, solve
from .lighting import compute_lighting

# placement order (= items.json order): base furniture first, then lights and wall pieces, then styling
ID_ORDER = ["main_sofa", "media_console", "TV", "tv_bias_light", "coffee_table", "accent_chair", "rug", "floor_lamp", "bookshelf",
            "floor_plant", "side_table", "table_lamp", "pendant", "shelf_strip", "art_main", "PLIGHT", "sconce", "GUITAR", "AMP",
            "HANGER", "SPOT", "cushion", "throw", "book_stack", "vase", "vinyl_crate", "tray", "candle", "small_plant", "art_secondary"]
OWNED_COMPANIONS = {"tv-1": "TV", "guitar-1": "GUITAR", "amp-1": "AMP", "hanger-1": "HANGER", "spot-1": "SPOT"}

BRASS = "#c9a45c"
BLACK = "#1e1f21"


def _dims_at(pc: PieceSlot, t: float) -> Tuple[int, int, int]:
    d = pc.dims_cm
    assert d is not None
    return tuple(int(round(lo + (hi - lo) * t)) for lo, hi in (d.w, d.d, d.h))  # type: ignore[return-value]


def signals(chain, vocab: Vocab) -> List[str]:
    out: List[str] = []
    for n in chain.inputs:
        hit = vocab.entities.get(n.strip().lower()) or vocab.entities.get(" ".join(n.lower().split()))
        if hit:
            _, kind, aff = hit
            out.append(f"{n} ({kind}" + (f", Qloo affinity {aff:.2f}" if aff is not None else "") + ")")
        else:
            out.append(n)
    for t in chain.tags:
        label = {"decor": "decor tag", "ambience": "ambience tag", "place_tag": "tag of a place you'd love", "discovery_tag": "tag of a pick for you"}[t.kind]
        out.append(f"{t.name} ({label}" + (f", Qloo affinity {t.affinity:.2f}" if t.affinity is not None else "") + ")")
    return out


def chain_dict(chain, vocab: Vocab) -> Dict[str, Any]:
    return {"signals": signals(chain, vocab), "reading": chain.decision, "inputs": chain.inputs,
            "tags": [t.model_dump() for t in chain.tags], "decision": chain.decision}


def wall_boost(spec: DesignSpec) -> float:
    """Dark wall colours absorb light, so a moody palette needs stronger fixtures to read as lit: scale lumens by the wall's relative
    luminance (1.0 for light walls, up to 1.7 for very dark ones)."""
    h = spec.palette.wall_hex.lstrip("#")
    lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) for c in (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return round(min(1.7, max(1.0, (0.4 / max(lum, 0.04)) ** 0.5)), 2)


def _glow(spec: DesignSpec) -> Optional[str]:
    return spec.lighting_mood.accent_glow_hex


def light_for(role: str, dims: Tuple[int, int, int], spec: DesignSpec, accent_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    m = spec.lighting_mood
    k = {"ambient": m.ambient_k, "task": m.task_k, "accent": m.accent_k}
    w, d, h = dims
    base = {
        "floor_lamp": ("task", "point", 1600, [0, max(h - 10, 10), 0]),
        "table_lamp": ("accent", "point", 450, [0, int(h * 0.8), 0]),
        "pendant": ("ambient", "point", 2400, [0, int(h / 2), 0]),
        "sconce": ("accent", "point", 350, [0, 15, 6]),
        "shelf_strip": ("accent", "line", 360, [0, 1, 0]),
        "tv_bias_light": ("accent", "line", 320, [0, 1, 0]),
        "PLIGHT": ("accent", "spot", 300, [0, 4, 6]),
    }.get(role)
    if not base:
        return None
    layer, kind, lm, off = base
    scale = (1.2 if layer in m.emphasis else 0.9) * (wall_boost(spec) if layer != "task" else 1.0)
    out: Dict[str, Any] = {"layer": layer, "kind": kind, "color_temp_k": k[layer], "lumens": int(round(lm * scale / 10) * 10),
                           "dimmable": role != "PLIGHT", "rgb": None, "emit_offset": off}
    if role == "PLIGHT":
        out["cone_deg"] = 40
    if role in ("shelf_strip", "tv_bias_light") and _glow(spec):
        out["rgb"] = _glow(spec)
    return out


def piece_item(pc: PieceSlot, dims: Tuple[int, int, int], spec: DesignSpec, vocab: Vocab, palette_cycle: List[str]) -> Dict[str, Any]:
    role = ROLES[pc.role]
    w, d, h = dims
    c = pc.colors
    colors = {"primary": c.primary, "secondary": c.secondary, "accent": c.accent}
    if pc.role.startswith("art_") or pc.role in ("book_stack", "vinyl_crate"):  # pictures / spines use all three colours: fill from the palette
        colors["secondary"] = colors["secondary"] or palette_cycle[0]
        colors["accent"] = colors["accent"] or palette_cycle[1]
    mats = {"primary": pc.materials.primary, "secondary": pc.materials.secondary}
    glow = _glow(spec)
    if pc.role in ("shelf_strip", "tv_bias_light"):
        colors["primary"] = glow or colors["primary"]
        mats = {"primary": "plastic", "secondary": None}
    params = dict(pc.params)
    if pc.role in ("art_main",):
        params.setdefault("frame", "framed")
    if pc.role == "tv_bias_light" or pc.role == "shelf_strip":
        params = {}
    item: Dict[str, Any] = {
        "id": pc.id, "category": role.category, "name": f"{pc.name} (designed for you)", "w": w, "d": d, "h": h,
        "dims_estimated": True, "image": None, "price": None, "url": None,
        "why": "Designed for you from your taste profile; a real product matching this slot will replace it.",
        "designed": True,
        "model": {"template": role.template, "params": params},
        "colors": colors, "material": mats,
        "why_chain": chain_dict(pc.why_chain, vocab),
        "slot": {
            "role": pc.role, "template": role.template, "category": role.category, "mount": role.mount, "zone": role.zone,
            "size_range_cm": {"w": list(pc.dims_cm.w), "d": list(pc.dims_cm.d), "h": list(pc.dims_cm.h)},  # type: ignore[union-attr]
            "colors": colors, "materials": mats, "params": params, "style_keywords": pc.style_keywords, "inspired_by": pc.inspired_by,
        },
    }
    lt = light_for(pc.role, dims, spec)
    if lt:
        item["light"] = lt
    if pc.role.startswith("art_"):
        item["focal"] = pc.role == "art_main"
    return item


def _companion(spec: DesignSpec, vocab: Vocab, base: Dict[str, Any], name: str, kind: str) -> Dict[str, Any]:
    it = copy.deepcopy(base)
    it.update(name=name, price=None, url=None, designed=True, why="Designed for you: part of the guitar wall.")
    brass = BRASS if "brass" in spec.material_direction.materials else BLACK
    it["why_chain"] = chain_dict(spec.lighting_why if kind == "spot" else spec.materials_why, vocab)
    it["colors"] = {"primary": BLACK, "secondary": None, "accent": None}
    it["material"] = {"primary": "black_metal", "secondary": None}
    if kind == "spot":
        it["light"]["color_temp_k"] = spec.lighting_mood.accent_k
        it["colors"]["primary"] = brass if brass != BLACK else "#2b2b2b"
    return it


def build_items(spec: DesignSpec, vocab: Vocab, base_items: List[Dict[str, Any]], t: Dict[str, float], skip: Optional[set] = None) -> List[Dict[str, Any]]:
    skip = skip or set()
    by_base = {i["id"]: i for i in base_items}
    cycle = list(spec.palette.accents)
    by_role: Dict[str, List[PieceSlot]] = {}
    for pc in spec.pieces:
        by_role.setdefault(pc.role, []).append(pc)
    out: List[Dict[str, Any]] = []
    metal_brass = BRASS if "brass" in spec.material_direction.materials else "#c9a45c"
    for key in ID_ORDER:
        if key in ROLES:
            for pc in by_role.get(key, []):
                if pc.id in skip:
                    continue
                dims = _dims_at(pc, t.get(pc.id, 0.5))
                out.append(piece_item(pc, dims, spec, vocab, cycle))
        elif key == "PLIGHT":
            if any(i["id"] == "art-1" for i in out):
                art = next(i for i in out if i["id"] == "art-1")
                lt = light_for("PLIGHT", (30, 12, 8), spec)
                out.append({"id": "plight-1", "category": "picture_light", "name": "Picture light for the print (designed for you)", "w": 30, "d": 12, "h": 8,
                            "dims_estimated": True, "image": None, "price": None, "url": None, "designed": True,
                            "why": "Designed for you: lights the main print.", "model": {"template": "picture_light", "params": {"arm": True}},
                            "colors": {"primary": metal_brass if "brass" in spec.material_direction.materials else "#2b2b2b", "secondary": None, "accent": None},
                            "material": {"primary": "brass" if "brass" in spec.material_direction.materials else "black_metal", "secondary": None},
                            "light": lt, "why_chain": chain_dict(spec.lighting_why, vocab), "slot": {"role": "picture_light", "template": "picture_light", "style_keywords": ["picture light", "brass" if "brass" in spec.material_direction.materials else "black"]}})
        else:
            sid = next((k for k, v in OWNED_COMPANIONS.items() if v == key), None)
            if sid and sid in by_base:
                it = copy.deepcopy(by_base[sid])
                if sid == "hanger-1":
                    it = _companion(spec, vocab, it, "Wall guitar hanger (designed for you)", "hanger")
                elif sid == "spot-1":
                    it = _companion(spec, vocab, it, "Spotlight for the guitar (designed for you)", "spot")
                out.append(it)
    return out


def build_room(base_room: Dict[str, Any], spec: DesignSpec) -> Dict[str, Any]:
    room = copy.deepcopy(base_room)
    room["name"] = "Living room (designed for you)"
    room["finishes"] = {"floor": spec.palette.floor, "wall_color": spec.palette.wall_hex,
                        "wall_paint": {"name": spec.palette.wall_name, "brand": None, "hex": spec.palette.wall_hex, "url": None, "price": None}}
    room["can_paint"] = True
    return room


# ---------------------------------------------------------------------------------------------------------------- constraints
def _bbox(room: Dict[str, Any]) -> Tuple[float, float]:
    xs = [p[0] for p in room["floor"]]
    zs = [p[1] for p in room["floor"]]
    return max(xs) - min(xs), max(zs) - min(zs)


def build_constraints(items: List[Dict[str, Any]], room: Dict[str, Any], base_constraints: Dict[str, Any]) -> Dict[str, Any]:
    W, D = _bbox(room)
    by_role: Dict[str, List[str]] = {}
    for it in items:
        r = (it.get("slot") or {}).get("role")
        if r:
            by_role.setdefault(r, []).append(it["id"])
    ids = {i["id"] for i in items}
    first = lambda role: (by_role.get(role) or [None])[0]  # noqa: E731
    c: Dict[str, Any] = {}
    con, sofa, table, shelf = first("media_console"), first("main_sofa"), first("coffee_table"), first("bookshelf")
    side, plant, art = first("side_table"), first("floor_plant"), first("art_main")
    cx = W - 130
    for owned in ("tv-1", "guitar-1", "amp-1", "hanger-1", "spot-1"):
        if owned in ids and owned in base_constraints:
            c[owned] = copy.deepcopy(base_constraints[owned])
    if con:
        c[con] = {"anchor": True, "hint": {"x": cx, "z": 20, "rotation_deg": 0}, "constraints": [{"type": "edge"}]}
    if "tv-1" in c and con:
        c["tv-1"] = copy.deepcopy(base_constraints["tv-1"])
    for tl in by_role.get("tv_bias_light", []):
        c[tl] = {"constraints": [{"type": "attach_to", "target": "tv-1", "side": "back"}]}
    if sofa:
        c[sofa] = {"anchor": True, "zone": "conversation", "hint": {"x": cx, "z": D - 92, "rotation_deg": 180},
                   "constraints": [{"type": "face", "target": con or "tv-1"}, {"type": "center_align", "target": con or "tv-1"}]}
    if table and sofa:
        c[table] = {"zone": "conversation", "constraints": [{"type": "middle"}, {"type": "in_front_of", "target": sofa},
                                                              {"type": "near", "target": sofa}, {"type": "center_align", "target": sofa}]}
    for r in by_role.get("rug", []):
        c[r] = {"zone": "conversation", "constraints": [{"type": "under", "target": table}] if table else [{"type": "middle"}]}
    for ch in by_role.get("accent_chair", []):
        tgt = table or sofa
        c[ch] = {"zone": "conversation", "constraints": [{"type": "near", "target": tgt}, {"type": "face", "target": tgt}, {"type": "side_of", "target": tgt}]}
    chair = first("accent_chair")
    for lp in by_role.get("floor_lamp", []):
        # the floor lamp stands between sofa and lounge chair so one reading light serves both seats
        cs = [{"type": "side_of", "target": sofa}, {"type": "near", "target": sofa}] if sofa else [{"type": "edge"}]
        if chair:
            cs.append({"type": "near", "target": chair})
        c[lp] = {"zone": "conversation", "constraints": cs}
    if shelf:
        c[shelf] = {"zone": "music_corner", "hint": {"x": W - 16, "z": 95, "rotation_deg": 270},
                    "constraints": [{"type": "edge"}] + ([{"type": "near", "target": "amp-1"}] if "amp-1" in ids else [])}
    for p in by_role.get("floor_plant", []):
        c[p] = {"constraints": [{"type": "edge"}, {"type": "near", "target": "win1"}]}
    for s in by_role.get("side_table", []):
        c[s] = {"zone": "conversation", "constraints": [{"type": "side_of", "target": sofa}, {"type": "near", "target": sofa}] if sofa else [{"type": "edge"}]}
    for tl in by_role.get("table_lamp", []):
        c[tl] = {"constraints": [{"type": "on_top_of", "target": side or con, "surface": "top"}]}
    for pd in by_role.get("pendant", []):
        c[pd] = {"constraints": [{"type": "ceiling", "above": table or sofa, "drop": 40}]}
    for st in by_role.get("shelf_strip", []):
        c[st] = {"constraints": [{"type": "attach_to", "target": shelf, "side": "back"}] if shelf else []}
    if art:
        c[art] = {"hint": {"x": 0, "z": 180}, "constraints": [{"type": "wall_mounted", "height": 120, "wall": "w4"}]}
        if "plight-1" in ids:
            c["plight-1"] = {"constraints": [{"type": "attach_to", "target": art, "side": "above"}]}
    for sc in by_role.get("sconce", []):
        tgt = plant or sofa
        cs = [{"type": "wall_mounted", "height": 150}]
        if tgt:
            cs += [{"type": "near", "target": tgt}]
            if tgt == plant:
                cs += [{"type": "center_align", "target": tgt}]
        c[sc] = {"constraints": cs}
    for cu in by_role.get("cushion", []) + by_role.get("throw", []):
        c[cu] = {"constraints": [{"type": "on_top_of", "target": sofa, "surface": "seat"}]} if sofa else {"constraints": []}
    for n, bs in enumerate(by_role.get("book_stack", [])):
        c[bs] = {"constraints": [{"type": "on_top_of", "target": shelf, "level": 2 if n == 0 else 1}]}
    for n, v in enumerate(by_role.get("vase", [])):
        c[v] = {"constraints": [{"type": "on_top_of", "target": shelf, "level": 2 if n == 0 else 3}]}
    for vc in by_role.get("vinyl_crate", []):
        c[vc] = {"constraints": [{"type": "on_top_of", "target": shelf, "level": 0}]}
    trays = by_role.get("tray", [])
    for tr in trays:
        c[tr] = {"constraints": [{"type": "on_top_of", "target": table, "surface": "top"}]}
    for cd in by_role.get("candle", []):
        c[cd] = {"constraints": [{"type": "on_top_of", "target": trays[0] if trays else table, "surface": "top"}]}
    for sp in by_role.get("small_plant", []):
        c[sp] = {"constraints": [{"type": "on_top_of", "target": side or table, "surface": "top"}]}
    for n, po in enumerate(by_role.get("art_secondary", [])):
        if n == 0:
            c[po] = {"hint": {"x": 165, "z": 0}, "constraints": [{"type": "wall_mounted", "height": 115, "wall": "w1"}] +
                     ([{"type": "far", "target": "guitar-1"}] if "guitar-1" in ids else [])}
        else:
            c[po] = {"hint": {"x": 0, "z": 95}, "constraints": [{"type": "wall_mounted", "height": 115, "wall": "w4"}] + ([{"type": "near", "target": art}] if art else [])}
    # drop constraints that point at missing targets
    valid = ids | {"win1", "door1", "room_center"} | {e["id"] for e in room.get("existing", [])}
    for iid, spec_c in list(c.items()):
        if iid not in ids:
            del c[iid]
            continue
        spec_c["constraints"] = [x for x in spec_c["constraints"] if x.get("target") is None or x["target"] in valid]
        if "hint" in spec_c and "rotation_deg" not in spec_c["hint"]:
            pass
    return c


# ---------------------------------------------------------------------------------------------------------------- build + solve
def tune_lighting(items: List[Dict[str, Any]], room: Dict[str, Any], layout: Dict[str, Any], notes: List[str]) -> None:
    """Raise lumens of the designed lights (within caps) until the evening ambient and reading task checks pass; dark wall colours
    absorb light, so a moody palette needs stronger fixtures. Remaining issues are reported, not hidden."""
    def by_role(role):
        return [i for i in items if (i.get("slot") or {}).get("role") == role and i.get("light")]
    for _ in range(8):
        lt = compute_lighting(room, items, layout, "night", None, None)
        codes = {i["code"] for i in lt["issues_all"]}
        changed = False
        if "NO_AMBIENT" in codes:
            for it in by_role("pendant"):
                if it["light"]["lumens"] < 4200:
                    it["light"]["lumens"] = int(min(4200, it["light"]["lumens"] * 1.25) // 10 * 10)
                    changed = True
        if "DIM_TASK_ZONE" in codes:
            for it in by_role("table_lamp"):
                if it["light"]["layer"] != "task":
                    it["light"]["layer"] = "task"
                    it["light"]["lumens"] = max(it["light"]["lumens"], 1000)
                    notes.append(f"{it['id']} promoted to a task light so the sofa zone passes the reading check")
                    changed = True
            for it in by_role("floor_lamp"):
                if it["light"]["lumens"] < 2400:
                    it["light"]["lumens"] = int(min(2400, it["light"]["lumens"] * 1.3) // 10 * 10)
                    changed = True
            if not changed:
                for it in by_role("table_lamp"):
                    if it["light"]["lumens"] < 2000:
                        it["light"]["lumens"] = int(min(2000, it["light"]["lumens"] * 1.3) // 10 * 10)
                        changed = True
        if not changed:
            break


def build_and_solve(spec: DesignSpec, vocab: Vocab, room_in: Dict[str, Any], base_items: List[Dict[str, Any]],
                    base_constraints: Dict[str, Any], log=print) -> Dict[str, Any]:
    """Translate, solve, and if designed pieces do not fit, shrink them within their range once, then drop what still cannot be placed."""
    room = build_room(room_in, spec)
    t: Dict[str, float] = {}
    skip: set = set()
    notes: List[str] = []
    for rnd in range(4):
        items = build_items(spec, vocab, base_items, t, skip)
        cons = build_constraints(items, room, base_constraints)
        res = solve(room, items, cons, Budget(time_s=4.0, max_expansions=260, step=10.0), room_name="room.json")
        lay = res.layout
        bad = hard_violations(room, items, lay)
        unplaced = lay.get("unplaced", [])
        designed_ids = {i["id"] for i in items if i.get("designed")}
        todo = [u for u in unplaced if u["item_id"] in designed_ids]
        if not todo and not bad:
            break
        for u in todo:
            iid = u["item_id"]
            if rnd == 0 and t.get(iid, 0.5) > 0:
                t[iid] = 0.0
                notes.append(f"{iid} did not fit at its middle size ({u.get('reason')}); retried at the smallest size in its range")
            else:
                skip.add(iid)
                notes.append(f"{iid} could not be placed ({u.get('reason')}) and was left out")
        log(f"design: round {rnd + 1}: {len(todo)} designed piece(s) unplaced, {len(bad)} hard violations")
    tune_lighting(items, room, lay, notes)
    return {"room": room, "items": items, "constraints": cons, "layout": lay, "hard_violations": bad, "notes": notes, "solve_stats": res.stats}


def write_bundle(out: Path, spec: DesignSpec, vocab: Vocab, built: Dict[str, Any], inp: DesignInputs, meta: Dict[str, Any]) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    dump = lambda name, obj: (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")  # noqa: E731
    dump("design_spec.json", spec.model_dump())
    dump("room.json", built["room"])
    dump("items.json", built["items"])
    dump("constraints.json", built["constraints"])
    dump("layout.json", built["layout"])
    scene = spec.lighting_mood.default_scene
    lighting = compute_lighting(built["room"], built["items"], built["layout"], "day" if scene == "day" else "night", None, scene)
    dump("lighting.json", lighting)
    dump("taste.json", {"style_summary": inp.profile.get("style_summary"), "aesthetic_tags": inp.profile.get("aesthetic_tags", [])})
    (out / "summary.md").write_text(summary_md(spec, built, lighting, meta))
    return lighting


def publish_to_viewer(out: Path, viewer_me: Path) -> None:
    viewer_me.mkdir(parents=True, exist_ok=True)
    for name in ("room.json", "items.json", "layout.json", "lighting.json", "taste.json", "constraints.json"):
        shutil.copyfile(out / name, viewer_me / name)


def summary_md(spec: DesignSpec, built: Dict[str, Any], lighting: Dict[str, Any], meta: Dict[str, Any]) -> str:
    p, m, lm = spec.palette, spec.material_direction, spec.lighting_mood
    L = [f"# Design: {spec.concept_name}", "", spec.concept, "", "## Palette", "",
         f"- Walls: {p.wall_name} `{p.wall_hex}`", f"- Floor: {p.floor}", f"- Accents: {', '.join(p.accents)}",
         f"- Why: {spec.palette_why.decision}  (inputs: {', '.join(spec.palette_why.inputs)}; tags: "
         f"{', '.join(f'{t.name} {t.affinity}' for t in spec.palette_why.tags)})", "",
         "## Materials", "", f"{m.summary}", f"- Materials: {', '.join(m.materials)}", f"- Avoid: {', '.join(m.avoid) or '-'}",
         f"- Why: {spec.materials_why.decision}", "",
         "## Lighting mood", "", lm.description,
         f"- Colour temperature: ambient {lm.ambient_k} K, task {lm.task_k} K, accent {lm.accent_k} K; emphasis: {', '.join(lm.emphasis)}; "
         f"default scene: {lm.default_scene}; accent glow: {lm.accent_glow_hex or 'none'}",
         f"- Why: {spec.lighting_why.decision}", "", "## Pieces (shopping slots)", ""]
    placed = {pl["item_id"] for pl in built["layout"]["placements"]}
    for it in built["items"]:
        if not it.get("designed") or "slot" not in it:
            continue
        s = it["slot"]
        if "size_range_cm" not in s:
            continue
        rg = s["size_range_cm"]
        wc = it["why_chain"]
        L.append(f"### {it['name']}  (`{it['id']}`, {s['role']}{'' if it['id'] in placed else ', NOT PLACED'})")
        L.append(f"- size {rg['w'][0]:g}-{rg['w'][1]:g} x {rg['d'][0]:g}-{rg['d'][1]:g} x {rg['h'][0]:g}-{rg['h'][1]:g} cm "
                 f"(drawn {it['w']} x {it['d']} x {it['h']}); {s['template']} {json.dumps(s['params'])}")
        L.append(f"- colours {it['colors']['primary']} / {it['colors'].get('secondary')} / {it['colors'].get('accent')}; materials "
                 f"{it['material']['primary']} / {it['material'].get('secondary')}; keywords: {', '.join(s['style_keywords'])}"
                 + (f"; inspired by {s['inspired_by']}" if s.get("inspired_by") else ""))
        L.append(f"- why: {' + '.join(wc['signals'])} -> {wc['decision']}")
        L.append("")
    L += ["## Layout", "", f"- placed {len(built['layout']['placements'])} items, score {built['layout'].get('score')}",
          f"- unplaced: {[u['item_id'] for u in built['layout'].get('unplaced', [])] or 'none'}"]
    for n in built["notes"]:
        L.append(f"- {n}")
    if spec.warnings:
        L += ["", "## Repairs applied to the LLM answer", ""] + [f"- {w}" for w in spec.warnings]
    L += ["", "## Lighting check", "", f"- layers: {lighting['layers']}"]
    for name, sc in lighting["scenes"].items():
        L.append(f"- {name}: ambient avg {sc.get('ambient_avg_lux')} lux, issues {[i['code'] for i in sc.get('issues', [])] or 'none'}")
    L += ["", f"LLM calls used: {meta.get('llm_calls', 0)}", ""]
    return "\n".join(L)

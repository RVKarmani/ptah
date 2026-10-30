"""v3: owned items, surface/level placement of decor, styling density, wall clearances, focal lighting."""
import copy
import json
import math
import os

import pytest

from ptah.layout import hard_violations, plan_layout, solve
from ptah.layout.constraints import RoomContext
from ptah.layout.geometry import Rect
from ptah.layout.metrics import STYLING, footprints_clear, get_surface, shelf_level, shelf_metrics, sofa_metrics
from ptah.layout.mounts import WALL_GAP
from ptah.lighting import compute_lighting
from ptah.owned import effective_colors, is_owned, total_cost

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(name):
    with open(os.path.join(ROOT, "samples", name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample():
    room, items, cons = load("room.json"), load("items.json"), load("constraints.json")
    return room, items, cons, solve(room, items, cons).layout


def pl(layout):
    return {p["item_id"]: p for p in layout["placements"]}


def by_id(items):
    return {i["id"]: i for i in items}


def item(iid, cat, w, d, h, **kw):
    return dict({"id": iid, "category": cat, "name": iid, "w": w, "d": d, "h": h}, **kw)


def local(parent_p, parent_it, p):
    r = Rect(parent_p["x"], parent_p["z"], parent_it["w"], parent_it["d"], parent_p["rotation_deg"])
    return r.to_local(p["x"], p["z"])


# ------------------------------------------------------------------ the sample
def test_sample_solves_clean_with_decor(sample):
    room, items, cons, lay = sample
    assert lay["unplaced"] == [] and len(lay["placements"]) == len(items)
    assert hard_violations(room, items, lay) == []
    assert {"guitar-1", "amp-1", "hanger-1", "spot-1", "cushion-1", "cushion-2", "throw-1", "book-stack-1", "vinyl-crate-1",
            "vase-1", "tray-1", "candle-1", "plant-2", "poster-1"} <= set(pl(lay))


def test_cushions_sit_at_seat_height_inside_sofa_footprint(sample):
    room, items, cons, lay = sample
    p, its = pl(lay), by_id(items)
    sofa, sit = p["sofa-1"], its["sofa-1"]
    seat_y = sofa_metrics(sit["w"], sit["d"], sit["h"])["seat_top"]
    assert seat_y < sit["h"] * 0.6                       # the seat, not the top of the backrest
    for cid in ("cushion-1", "cushion-2", "throw-1"):
        c, ci = p[cid], its[cid]
        assert c["y"] == pytest.approx(seat_y, abs=0.11) and c["parent"] == "sofa-1" and c["surface"] == "seat"
        assert c["rotation_deg"] == sofa["rotation_deg"]
        # every corner of the cushion lies inside the sofa footprint
        lx, lz = local(sofa, sit, c)
        assert abs(lx) + ci["w"] / 2 <= sit["w"] / 2 and abs(lz) + ci["d"] / 2 <= sit["d"] / 2
    # the two cushions do not overlap each other or the throw
    boxes = [(*local(sofa, sit, p[c]), its[c]["w"], its[c]["d"], "rect") for c in ("cushion-1", "cushion-2", "throw-1")]
    for i in range(3):
        for j in range(i + 1, 3):
            assert footprints_clear(boxes[i], boxes[j], 0.0)


def test_shelf_items_sit_on_their_boards_without_overlap(sample):
    room, items, cons, lay = sample
    p, its = pl(lay), by_id(items)
    shelf, sh = p["shelf-1"], its["shelf-1"]
    expect = {"vinyl-crate-1": 0, "book-stack-1": 2, "vase-1": 2}
    for iid, level in expect.items():
        c = p[iid]
        surf = shelf_level(sh["w"], sh["d"], sh["h"], level)
        assert c["level"] == level and c["surface"] == "shelf" and c["y"] == pytest.approx(surf.y, abs=0.11)
        assert its[iid]["h"] <= surf.clear
        lx, lz = local(shelf, sh, c)
        assert abs(lx - surf.cx) + its[iid]["w"] / 2 <= surf.w / 2 and abs(lz - surf.cz) + its[iid]["d"] / 2 <= surf.d / 2
    assert p["vinyl-crate-1"]["y"] < p["book-stack-1"]["y"] == p["vase-1"]["y"]
    a = (*local(shelf, sh, p["book-stack-1"]), 24, 17, "rect")
    b = (*local(shelf, sh, p["vase-1"]), 12, 12, "circle")
    assert footprints_clear(a, b, STYLING["gap_cm"] - 1e-6)
    n = shelf_metrics(sh["w"], sh["d"], sh["h"])["n"]
    assert get_surface(sh, "shelf", n) is None                      # no such board


def test_table_top_chain_and_round_top(sample):
    room, items, cons, lay = sample
    p, its = pl(lay), by_id(items)
    assert p["tray-1"]["y"] == its["table-1"]["h"] and p["candle-1"]["y"] == its["table-1"]["h"] + its["tray-1"]["h"]
    side, sit = p["side-1"], its["side-1"]
    for iid in ("lamp-2", "plant-2"):                                # lamp + plant share a 45 cm round top
        lx, lz = local(side, sit, p[iid])
        assert math.hypot(lx, lz) + min(its[iid]["w"], its[iid]["d"]) / 2 <= sit["w"] / 2
    a = (*local(side, sit, p["lamp-2"]), 26, 26, "circle")
    b = (*local(side, sit, p["plant-2"]), 11, 11, "circle")
    assert footprints_clear(a, b, 0.5)


def test_wall_items_do_not_overlap(sample):
    room, items, cons, lay = sample
    ctx = RoomContext(room)
    its = by_id(items)
    groups = {}                                    # wall-mounted root -> every box of the group (children attach to it)
    for q in lay["placements"]:
        if q.get("mount") == "wall":
            groups[q["item_id"]] = [q]
    for q in lay["placements"]:
        if q.get("mount") == "attach" and q.get("parent") in groups:
            groups[q["parent"]].append(q)

    def extent(plist):
        fr = ctx.frame_by_id[plist[0]["wall"]]
        ss, y0, y1 = [], [], []
        for q in plist:
            it = its[q["item_id"]]
            if "strip" in it["category"]:
                continue
            r = Rect(q["x"], q["z"], it["w"], it["d"], q["rotation_deg"])
            ss += [fr.along(cx, cz) for cx, cz in r.corners()]
            y0.append(q["y"])
            y1.append(q["y"] + it["h"])
        return fr.id, min(ss), max(ss), min(y0), max(y1)

    ext = {k: extent(v) for k, v in groups.items()}
    assert {"guitar-1", "art-1", "poster-1", "sconce-1"} <= set(ext)
    ids = sorted(ext)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            wa, a0, a1, ay0, ay1 = ext[a]
            wb, b0, b1, by0, by1 = ext[b]
            if wa != wb:
                continue
            clear = a1 + WALL_GAP <= b0 or b1 + WALL_GAP <= a0 or ay1 + WALL_GAP <= by0 or by1 + WALL_GAP <= ay0
            assert clear, (a, b)


def test_two_wall_items_get_a_gap_when_forced_onto_one_short_wall():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [150, 0], [150, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("a", "art", 60, 3, 80), item("g", "guitar", 33, 6, 98)]
    cons = {"a": {"constraints": [{"type": "wall_mounted", "height": 100, "wall": "w1"}]},
            "g": {"constraints": [{"type": "wall_mounted", "height": 100, "wall": "w1"}]}}
    lay = plan_layout(room, items, cons)
    p = pl(lay)
    assert lay["unplaced"] == [] and hard_violations(room, items, lay) == []
    gap = abs(p["a"]["x"] - p["g"]["x"]) - (60 + 33) / 2
    assert gap >= WALL_GAP - 1e-6
    cons["g"]["constraints"][0]["wall"] = "w1"
    items[0]["w"] = 100                                          # 100 + 33 + gap > 150: the second one cannot hang
    lay = plan_layout(room, items, cons)
    assert [u["item_id"] for u in lay["unplaced"]] == ["g"]


# ------------------------------------------------------------------ guitar, amp, owned
def test_guitar_and_amp_placement(sample):
    room, items, cons, lay = sample
    p = pl(lay)
    g, amp = p["guitar-1"], p["amp-1"]
    assert g["mount"] == "wall" and 90 <= g["y"] <= 110
    assert amp["y"] == 0 and "mount" not in amp
    assert math.hypot(g["x"] - amp["x"], g["z"] - amp["z"]) < 60                 # near
    assert "near:amp-1" in g["satisfied"] and "edge" in amp["satisfied"]
    assert p["hanger-1"]["parent"] == "guitar-1" and p["spot-1"]["parent"] == "guitar-1"


def test_owned_items_excluded_from_cost(sample):
    room, items, cons, lay = sample
    its = by_id(items)
    for iid in ("guitar-1", "amp-1", "tv-1"):
        assert is_owned(its[iid]) and its[iid]["price"] is None and its[iid]["url"] is None
    assert its["tv-1"]["dims_estimated"]               # the TV size is an assumption
    cost = total_cost(items)
    expect = sum(float(i["price"].lstrip("$")) for i in items if not i.get("owned"))
    assert cost == {"symbol": "$", "total": expect, "count": len(items) - 3, "owned": 3}
    # an owned item with a stray price still does not count
    sneaky = copy.deepcopy(items)
    next(i for i in sneaky if i["id"] == "amp-1")["price"] = "$999"
    assert total_cost(sneaky)["total"] == expect
    assert total_cost(items, placed_ids=["guitar-1", "amp-1"])["total"] == 0


def test_user_colors_take_precedence_over_photo_colors(sample):
    its = by_id(sample[1])
    g = its["guitar-1"]
    eff = effective_colors(g)
    assert eff["primary"] == "#e9dfc8" and eff["secondary"] == "#4a2f22"
    assert g["colors"]["primary"] != eff["primary"]                              # the photo colour is overridden
    assert effective_colors({"colors": {"primary": "#111111"}, "user_colors": {"primary": None}})["primary"] == "#111111"


def test_guitar_is_focal_and_flagged_when_its_spotlight_is_removed(sample):
    room, items, cons, lay = sample
    ev = compute_lighting(room, items, lay)["scenes"]["evening"]
    g = next(f for f in ev["focal_points"] if f["item_id"] == "guitar-1")
    assert g["accent_lit"] and "spot-1" in g["lit_by"]
    its = [i for i in items if i["id"] != "spot-1"]
    l2 = copy.deepcopy(lay)
    l2["placements"] = [q for q in l2["placements"] if q["item_id"] != "spot-1"]
    res = compute_lighting(room, its, l2)
    iss = [i for i in res["scenes"]["evening"]["issues"] if i["code"] == "UNLIT_FOCAL_POINT"]
    assert "guitar-1" in [i["item_id"] for i in iss] or "guitar-1" in [f["item_id"] for f in res["scenes"]["evening"]["focal_points"] if f["accent_lit"]]
    # without the flag the guitar is not a focal point at all
    its2 = copy.deepcopy(its)
    next(i for i in its2 if i["id"] == "guitar-1").pop("focal")
    res2 = compute_lighting(room, its2, l2)
    assert "guitar-1" not in [f["item_id"] for f in res2["scenes"]["evening"]["focal_points"]]


# ------------------------------------------------------------------ styling density
def test_density_cap_and_clearance():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("shelf-1", "bookshelf", 80, 32, 180)] + [item(f"b{i}", "book_stack", 14, 14, 10) for i in range(4)] + \
            [item("tall", "vase", 10, 10, 60), item("big", "tray", 70, 28, 3)]
    on = lambda iid, **kw: {"constraints": [dict({"type": "on_top_of", "target": "shelf-1", "level": 1}, **kw)]}
    cons = {"shelf-1": {"anchor": True, "constraints": [{"type": "edge"}]}, "tall": on("tall"), "big": on("big")}
    cons.update({f"b{i}": on(f"b{i}") for i in range(4)})
    lay = plan_layout(room, items, cons)
    un = {u["item_id"]: u["reason"] for u in lay["unplaced"]}
    placed_on_board = [q["item_id"] for q in lay["placements"] if q.get("level") == 1]
    assert len(placed_on_board) <= STYLING["max_items"]["shelf"]
    assert "tall" in un and "too tall" in un["tall"]
    assert "big" in un and "density" in un["big"]                                # 70x28 on a 73x29 board: > 55 % covered
    assert any("density" in r for r in un.values()) and hard_violations(room, items, lay) == []
    shelf = pl(lay)["shelf-1"]
    surf = shelf_level(80, 32, 180, 1)
    for q in lay["placements"]:
        if q.get("level") == 1:
            assert q["y"] == pytest.approx(surf.y, abs=0.11)


def test_unknown_level_is_reported_and_old_on_top_of_still_works():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("shelf-1", "bookshelf", 80, 32, 180), item("v", "vase", 10, 10, 20), item("d", "desk", 120, 60, 75), item("l", "table_lamp", 20, 20, 40)]
    cons = {"shelf-1": {"anchor": True, "constraints": [{"type": "edge"}]},
            "v": {"constraints": [{"type": "on_top_of", "target": "shelf-1", "level": 9}]},
            "d": {"constraints": [{"type": "edge"}]},
            "l": {"constraints": [{"type": "on_top_of", "target": "d"}]}}
    lay = plan_layout(room, items, cons)
    assert [u["item_id"] for u in lay["unplaced"]] == ["v"] and "surface" in lay["unplaced"][0]["reason"]
    assert pl(lay)["l"]["y"] == 75

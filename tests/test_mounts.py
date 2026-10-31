import copy
import json
import math
import os
import time

import pytest

from ptah.layout import hard_violations, plan_layout, solve
from ptah.layout.constraints import RoomContext
from ptah.layout.geometry import Rect, rects_overlap

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(name):
    with open(os.path.join(ROOT, "samples", name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample():
    room, items, cons = load("room.json"), load("items.json"), load("constraints.json")
    res = solve(room, items, cons)
    return room, items, cons, res


def pl(layout):
    return {p["item_id"]: p for p in layout["placements"]}


def item(iid, cat, w, d, h, **kw):
    return dict({"id": iid, "category": cat, "name": iid, "w": w, "d": d, "h": h}, **kw)


def test_sample_places_everything_and_is_fast(sample):
    room, items, cons, res = sample
    lay = res.layout
    assert lay["unplaced"] == [] and len(lay["placements"]) == len(items)
    assert hard_violations(room, items, lay) == []
    assert res.stats["runtime_s"] < 3.0
    assert all("y" in p for p in lay["placements"])
    assert [p["item_id"] for p in lay["placements"]] == [i["id"] for i in items]


def test_lamp_on_side_table(sample):
    room, items, cons, res = sample
    p = pl(res.layout)
    side, lamp = p["side-1"], p["lamp-2"]
    h_side = next(i["h"] for i in items if i["id"] == "side-1")
    assert lamp["y"] == h_side and lamp["elevation"] == h_side
    # the lamp shares the round top with a small plant (styling surface "top"), so it sits on the table but not necessarily dead centre
    assert lamp["rotation_deg"] == side["rotation_deg"] and lamp["surface"] == "top"
    assert math.hypot(lamp["x"] - side["x"], lamp["z"] - side["z"]) + 13 <= 22.5      # 26 cm round lamp base inside the 45 cm top
    assert lamp["mount"] == "on_top_of" and lamp["parent"] == "side-1" and "on_top_of:side-1" in lamp["satisfied"]
    assert side["y"] == 0


def test_mounted_items_do_not_use_floor_space(sample):
    """The floor solver never sees mounted items: they may sit over the rug / coffee table / on a wall."""
    room, items, cons, res = sample
    p = pl(res.layout)
    assert p["pendant-1"]["mount"] == "ceiling"
    # pendant is directly over the coffee table, base = H - drop - h
    pend = next(i for i in items if i["id"] == "pendant-1")
    assert p["pendant-1"]["y"] == room["height"] - 40 - pend["h"]
    assert (p["pendant-1"]["x"], p["pendant-1"]["z"]) == (p["table-1"]["x"], p["table-1"]["z"])
    # hard_violations ignores mounted items in the 2D floor rules but checks them in 3D
    broken = copy.deepcopy(res.layout)
    for q in broken["placements"]:
        if q["item_id"] == "pendant-1":
            q["y"] = 0                      # drop the pendant onto the coffee table
    assert any("pendant-1" in v for v in hard_violations(room, items, broken))


def test_strip_pose_follows_shelf(sample):
    room, items, cons, res = sample
    cons2 = copy.deepcopy(cons)
    cons2["shelf-1"] = {"constraints": [{"type": "edge"}, {"type": "near", "target": "win1"}]}   # a different wall
    res2 = solve(room, items, cons2)
    for r in (res, res2):
        p = pl(r.layout)
        shelf, strip = p["shelf-1"], p["strip-1"]
        sh = next(i for i in items if i["id"] == "shelf-1")
        st = next(i for i in items if i["id"] == "strip-1")
        assert strip["rotation_deg"] == shelf["rotation_deg"] and strip["parent"] == "shelf-1" and strip["mount"] == "attach"
        # behind the shelf: back edges flush, along the shelf's -front axis
        rot = math.radians(shelf["rotation_deg"])
        fx, fz = math.sin(rot), math.cos(rot)
        k = sh["d"] / 2 - st["d"] / 2
        assert strip["x"] == pytest.approx(shelf["x"] - fx * k, abs=0.11)
        assert strip["z"] == pytest.approx(shelf["z"] - fz * k, abs=0.11)
        assert strip["y"] == pytest.approx(sh["h"] - st["h"], abs=0.11)
    assert (pl(res.layout)["shelf-1"]["x"], pl(res.layout)["shelf-1"]["z"]) != (pl(res2.layout)["shelf-1"]["x"], pl(res2.layout)["shelf-1"]["z"])


def test_picture_light_above_art_and_art_on_wall(sample):
    room, items, cons, res = sample
    p = pl(res.layout)
    art, pic = p["art-1"], p["plight-1"]
    a = next(i for i in items if i["id"] == "art-1")
    assert art["mount"] == "wall" and art["y"] == 120 and art["wall"] in {w["id"] for w in room["walls"]}
    assert pic["parent"] == "art-1" and pic["side"] == "above"
    assert pic["y"] == pytest.approx(art["y"] + a["h"] + 3)
    assert pic["rotation_deg"] == art["rotation_deg"]
    # the art's back is flush to the wall: its centre is d/2 from the wall line
    ctx = RoomContext(room)
    fr = ctx.frame_by_id[art["wall"]]
    assert fr.depth_of(art["x"], art["z"]) == pytest.approx(a["d"] / 2, abs=0.11)


def test_sconce_never_on_window_or_door():
    room = load("room.json")
    items = [item("sconce-1", "sconce", 16, 16, 24), item("sconce-2", "sconce", 16, 16, 24)]
    win = RoomContext(room).windows[0]                     # window span derived from room.json (wall w3 = the far z wall)
    wcx, wlo, whi = win.zone.x, win.zone.x - win.zone.w / 2, win.zone.x + win.zone.w / 2
    wall_z = max(p[1] for p in room["floor"])
    for hint_x in (wcx, wlo + 30, whi - 30, wlo - 70):     # centre, near either jamb, and clear of the window
        cons = {"sconce-1": {"constraints": [{"type": "wall_mounted", "height": 150, "wall": "w3"}, {"type": "near", "target": "win1"}],
                             "hint": {"x": hint_x, "z": wall_z - 8}},
                "sconce-2": {"constraints": [{"type": "wall_mounted", "height": 150, "wall": "w1"}, {"type": "near", "target": "door1"}]}}
        lay = plan_layout(room, items, cons)
        ctx = RoomContext(room)
        assert lay["unplaced"] == []
        for q in lay["placements"]:
            r = Rect(q["x"], q["z"], 16, 16, q["rotation_deg"])
            for o in ctx.openings:
                assert not rects_overlap(r, o.thin), (q, o.id)
        assert hard_violations(room, items, lay) == []
    # below the sill the same spot is allowed again (y 40..64 < sill 90)
    cons = {"sconce-1": {"constraints": [{"type": "wall_mounted", "height": 40, "wall": "w3"}, {"type": "center_align", "target": "win1"}]}}
    lay = plan_layout(room, items[:1], cons)
    q = lay["placements"][0]
    assert wlo <= q["x"] <= whi


def test_mounted_items_do_not_overlap_each_other_or_tall_furniture():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("shelf-1", "bookshelf", 100, 30, 200), item("art-1", "art", 60, 4, 60), item("art-2", "art", 60, 4, 60)]
    cons = {"shelf-1": {"anchor": True, "constraints": [{"type": "edge"}], "hint": {"x": 150, "z": 15, "rotation_deg": 0}},
            "art-1": {"constraints": [{"type": "wall_mounted", "height": 120, "wall": "w1"}, {"type": "center_align", "target": "shelf-1"}]},
            "art-2": {"constraints": [{"type": "wall_mounted", "height": 120, "wall": "w1"}, {"type": "center_align", "target": "shelf-1"}]}}
    lay = plan_layout(room, items, cons)
    p = pl(lay)
    assert lay["unplaced"] == []
    assert hard_violations(room, items, lay) == []
    # the shelf (200 tall) blocks its stretch of w1 -> arts hang beside it, never inside it, never on each other
    shelf = Rect(p["shelf-1"]["x"], p["shelf-1"]["z"], 100, 30, 0)
    a1 = Rect(p["art-1"]["x"], p["art-1"]["z"], 60, 4, p["art-1"]["rotation_deg"])
    a2 = Rect(p["art-2"]["x"], p["art-2"]["z"], 60, 4, p["art-2"]["rotation_deg"])
    assert not rects_overlap(a1, shelf) and not rects_overlap(a2, shelf) and not rects_overlap(a1, a2)


def test_unplaceable_mount_reports_reason_and_children():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("art-1", "art", 500, 4, 60), item("light-1", "picture_light", 30, 12, 8), item("lamp-1", "table_lamp", 20, 20, 40)]
    cons = {"art-1": {"constraints": [{"type": "wall_mounted", "height": 120}]},
            "light-1": {"constraints": [{"type": "attach_to", "target": "art-1", "side": "above"}]},
            "lamp-1": {"constraints": [{"type": "on_top_of", "target": "ghost"}]}}   # unknown target -> ordinary floor item
    lay = plan_layout(room, items, cons)
    un = {u["item_id"]: u for u in lay["unplaced"]}
    assert set(un) == {"art-1", "light-1"}
    assert un["art-1"]["reason"] and 0 < un["art-1"]["max_w"] <= 300
    assert "art-1" in un["light-1"]["reason"]
    assert pl(lay)["lamp-1"]["y"] == 0 and any("ghost" in w for w in lay["warnings"])


def test_on_top_of_siblings_and_chain():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [], "existing": []}
    items = [item("desk-1", "desk", 140, 60, 75), item("l1", "table_lamp", 25, 25, 40), item("l2", "table_lamp", 25, 25, 40),
             item("book-1", "generic", 20, 15, 5)]
    cons = {"desk-1": {"anchor": True, "constraints": [{"type": "edge"}]},
            "l1": {"constraints": [{"type": "on_top_of", "target": "desk-1"}]},
            "l2": {"constraints": [{"type": "on_top_of", "target": "desk-1"}]},
            "book-1": {"constraints": [{"type": "on_top_of", "target": "l2"}]}}   # chained: lies on lamp 2's shade
    lay = plan_layout(room, items, cons)
    p = pl(lay)
    assert lay["unplaced"] == [] and hard_violations(room, items, lay) == []
    assert p["l1"]["y"] == p["l2"]["y"] == 75 and (p["l1"]["x"], p["l1"]["z"]) != (p["l2"]["x"], p["l2"]["z"])
    assert p["book-1"]["y"] == 115


def test_led_strip_has_no_collision():
    room = {"name": "r", "height": 260, "floor": [[0, 0], [300, 0], [300, 300], [0, 300]], "openings": [
        {"id": "win1", "type": "window", "wall": "w1", "offset": 100, "width": 100, "height": 120, "sill": 90}], "existing": []}
    items = [item("tv-1", "tv_stand", 120, 40, 50), item("strip-1", "led_strip", 100, 2, 2, light={"kind": "line", "layer": "accent", "lumens": 300})]
    cons = {"tv-1": {"anchor": True, "constraints": [{"type": "edge"}], "hint": {"x": 150, "z": 20, "rotation_deg": 0}},
            "strip-1": {"constraints": [{"type": "attach_to", "target": "tv-1", "side": "under"}]}}
    lay = plan_layout(room, items, cons)
    assert lay["unplaced"] == [] and hard_violations(room, items, lay) == []
    # same pose with a non-strip item of the same size collides with its parent's neighbours / opening
    p = pl(lay)
    assert p["strip-1"]["mount"] == "attach" and p["strip-1"]["x"] == p["tv-1"]["x"]


def test_runtime_with_many_items(sample):
    room, items, cons, _ = sample
    t0 = time.perf_counter()
    solve(room, items, cons)
    assert time.perf_counter() - t0 < 3.0


def test_deterministic_with_mounts(sample):
    room, items, cons, res = sample
    assert json.dumps(res.layout, sort_keys=True) == json.dumps(solve(room, items, cons).layout, sort_keys=True)

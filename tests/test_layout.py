import copy
import json
import os
import time

import pytest

from ptah.layout import Budget, hard_violations, plan_layout, solve
from ptah.layout.constraints import RoomContext, Walkway
from ptah.layout.geometry import Rect, build_wall_frames, rect_in_polygon, rects_overlap

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(name):
    with open(os.path.join(ROOT, "samples", name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample():
    return load("room.json"), load("items.json"), load("constraints.json")


def rect_of(placement, items):
    it = {i["id"]: i for i in items}[placement["item_id"]]
    return Rect(placement["x"], placement["z"], it["w"], it["d"], placement["rotation_deg"]), it


def by_item(layout):
    return {p["item_id"]: p for p in layout["placements"]}


def item(iid, cat, w, d, h):
    return {"id": iid, "category": cat, "name": iid, "w": w, "d": d, "h": h}


def test_sample_room_solves(sample):
    room, items, cons = sample
    t0 = time.perf_counter()
    res = solve(room, items, cons)
    elapsed = time.perf_counter() - t0
    lay = res.layout
    assert elapsed < 3.0
    assert len(lay["placements"]) == len(items) and lay["unplaced"] == []
    assert hard_violations(room, items, lay) == []
    assert 0.0 <= lay["score"] <= 1.0 and lay["score"] > 0.8
    p = by_item(lay)
    # sofa on an edge facing into the room; table in front of it
    assert "edge" in p["sofa-1"]["satisfied"]
    assert "in_front_of:sofa-1" in p["table-1"]["satisfied"]
    assert "under:table-1" in p["rug-1"]["satisfied"]


def test_l_shaped_room(sample):
    _, _, _ = sample
    room = {
        "name": "L room", "height": 250,
        "floor": [[0, 0], [500, 0], [500, 200], [250, 200], [250, 400], [0, 400]],
        "openings": [{"id": "door1", "type": "door", "wall": "w1", "offset": 40, "width": 85, "height": 205, "swing": 90},
                     {"id": "win1", "type": "window", "wall": "w6", "offset": 100, "width": 120, "height": 120, "sill": 90}],
        "existing": [],
    }
    items = [item("sofa-1", "sofa", 200, 90, 80), item("table-1", "coffee_table", 100, 55, 40),
             item("shelf-1", "bookshelf", 90, 30, 180), item("chair-1", "armchair", 80, 80, 85),
             item("rug-1", "rug", 200, 140, 1)]
    cons = {
        "sofa-1": {"anchor": True, "constraints": [{"type": "edge"}]},
        "table-1": {"constraints": [{"type": "in_front_of", "target": "sofa-1"}, {"type": "near", "target": "sofa-1"}]},
        "shelf-1": {"constraints": [{"type": "edge"}]},
        "chair-1": {"constraints": [{"type": "side_of", "target": "table-1"}, {"type": "face", "target": "table-1"}]},
        "rug-1": {"constraints": [{"type": "under", "target": "table-1"}]},
    }
    lay = plan_layout(room, items, cons)
    assert lay["unplaced"] == []
    assert hard_violations(room, items, lay) == []
    poly = [tuple(p) for p in room["floor"]]
    for pl in lay["placements"]:
        r, _ = rect_of(pl, items)
        assert rect_in_polygon(r, poly), pl
        # nothing in the notch (x>250, z>200)
        assert not rects_overlap(r, Rect(375, 300, 250, 200, 0)), pl


def test_too_small_room_reports_max_w():
    room = {"name": "closet", "height": 250, "floor": [[0, 0], [200, 0], [200, 150], [0, 150]], "openings": [], "existing": []}
    items = [item("sofa-1", "sofa", 210, 92, 82)]
    cons = {"sofa-1": {"anchor": True, "constraints": [{"type": "edge"}]}}
    lay = plan_layout(room, items, cons)
    assert lay["placements"] == []
    u = lay["unplaced"][0]
    assert u["item_id"] == "sofa-1"
    assert 0 < u["max_w"] <= 200 and 0 < u["max_d"] < 92
    assert u["reason"]
    # the suggested size really fits
    items2 = [item("sofa-1", "sofa", u["max_w"], u["max_d"], 82)]
    lay2 = plan_layout(room, items2, cons)
    assert len(lay2["placements"]) == 1 and hard_violations(room, items2, lay2) == []


def test_door_swing_zone_respected(sample):
    room, items, cons = sample
    cons = copy.deepcopy(cons)
    # tempt the shelf and the table right into the door's swing zone (x 30..115, z 0..90)
    cons["shelf-1"]["hint"] = {"x": 70, "z": 40, "rotation_deg": 0}
    cons["table-1"]["hint"] = {"x": 70, "z": 45, "rotation_deg": 0}
    lay = plan_layout(room, items, cons)
    zone = Rect(72.5, 45, 85, 90, 0)
    for pl in lay["placements"]:
        r, _ = rect_of(pl, items)
        assert not rects_overlap(r, zone), pl
    assert hard_violations(room, items, lay) == []


def test_window_rule_respected(sample):
    room, items, cons = sample
    cons = copy.deepcopy(cons)
    cons["shelf-1"] = {"constraints": [], "hint": {"x": 210, "z": 340, "rotation_deg": 180}}  # 180 cm shelf in front of the window
    cons["plant-1"] = {"constraints": [], "hint": {"x": 200, "z": 335, "rotation_deg": 180}}
    lay = plan_layout(room, items, cons)
    zone = Rect(210, 340, 160, 40, 0)  # window span x 130..290, 40 cm deep
    heights = {i["id"]: i["h"] for i in items}
    for pl in lay["placements"]:
        r, it = rect_of(pl, items)
        if heights[pl["item_id"]] > 90:
            assert not rects_overlap(r, zone), pl
    assert hard_violations(room, items, lay) == []


def test_deterministic(sample):
    room, items, cons = sample
    a = plan_layout(room, items, cons)
    b = plan_layout(room, items, cons)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_rotation_convention():
    # three.js rotation about +y: 0 faces +z, 90 faces +x
    sq = [[0, 0], [300, 0], [300, 300], [0, 300]]
    frames = {f.id: f for f in build_wall_frames(sq, None)}
    assert frames["w1"].rot == 0      # wall at z=0, back on it -> front +z
    assert frames["w4"].rot == 90     # wall at x=0 -> front +x
    assert frames["w3"].rot == 180 and frames["w2"].rot == 270
    room = {"name": "sq", "floor": sq, "openings": [], "existing": []}
    its = [item("a", "sofa", 100, 50, 80)]
    lay = plan_layout(room, its, {"a": {"constraints": [{"type": "edge"}], "hint": {"x": 150, "z": 25, "rotation_deg": 0}}})
    p = lay["placements"][0]
    assert p["rotation_deg"] == 0 and abs(p["z"] - 25) < 1
    lay = plan_layout(room, its, {"a": {"constraints": [{"type": "edge"}], "hint": {"x": 25, "z": 150, "rotation_deg": 90}}})
    p = lay["placements"][0]
    assert p["rotation_deg"] == 90 and abs(p["x"] - 25) < 1


def test_hint_pulls_item_and_is_optional(sample):
    room, items, cons = sample
    cons = copy.deepcopy(cons)
    base = by_item(plan_layout(room, items, cons))["lamp-1"]
    cons["lamp-1"]["constraints"] = []
    cons["lamp-1"].pop("zone", None)
    no_hint = by_item(plan_layout(room, items, cons))["lamp-1"]
    cons["lamp-1"]["hint"] = {"x": 380, "z": 250, "rotation_deg": 90}
    lay = plan_layout(room, items, cons)
    hinted = by_item(lay)["lamp-1"]
    assert ((hinted["x"] - 380) ** 2 + (hinted["z"] - 250) ** 2) ** 0.5 <= 25
    assert hinted["rotation_deg"] == 90 and "hint" in hinted["satisfied"]
    assert (no_hint["x"], no_hint["z"]) != (hinted["x"], hinted["z"])
    assert "hint" not in base["satisfied"] + base["violated"]
    assert hard_violations(room, items, lay) == []


def test_zone_cohesion():
    room = {"name": "r", "floor": [[0, 0], [600, 0], [600, 400], [0, 400]], "openings": [], "existing": []}
    its = [item("chair-1", "armchair", 80, 80, 85), item("lamp-1", "floor_lamp", 40, 40, 160)]
    cons = {"chair-1": {"zone": "reading", "hint": {"x": 100, "z": 100, "rotation_deg": 0}, "constraints": []},
            "lamp-1": {"zone": "reading", "constraints": []}}
    lay = plan_layout(room, its, cons)
    p = by_item(lay)
    a, _ = rect_of(p["chair-1"], its)
    b, _ = rect_of(p["lamp-1"], its)
    from ptah.layout.geometry import rect_gap
    assert rect_gap(a, b) <= 150
    assert "zone:reading" in p["lamp-1"]["satisfied"]


def test_walkway_width():
    room = {"name": "r", "floor": [[0, 0], [400, 0], [400, 300], [0, 300]], "walls": None,
            "openings": [{"id": "d", "type": "door", "wall": "w1", "offset": 150, "width": 85, "height": 205, "swing": 90}],
            "existing": []}
    room.pop("walls")
    wk = Walkway(RoomContext(room))
    full = Rect(200, 130, 400, 20, 0)
    assert wk.failing_doors([full]) == ["d"]
    gap50 = Rect(175, 130, 350, 20, 0)   # leaves a 50 cm gap at the right wall
    assert wk.failing_doors([gap50]) == ["d"]
    gap80 = Rect(160, 130, 320, 20, 0)   # leaves an 80 cm gap
    assert wk.failing_doors([gap80]) == []


def test_random_scenes_never_violate_hard_rules():
    import random

    rnd = random.Random(7)
    cats = [("sofa", 200, 90, 80), ("coffee_table", 110, 60, 42), ("rug", 200, 140, 1), ("floor_lamp", 40, 40, 160),
            ("bookshelf", 80, 32, 180), ("plant", 45, 45, 120), ("armchair", 80, 80, 85), ("desk", 120, 60, 75)]
    types = ["edge", "middle", "near", "far", "in_front_of", "side_of", "face", "center_align", "under"]
    for _ in range(6):
        W, D = rnd.choice([300, 350, 420]), rnd.choice([260, 320, 380])
        floor = [[0, 0], [W, 0], [W, D // 2], [W // 2, D // 2], [W // 2, D], [0, D]] if rnd.random() < 0.5 else [[0, 0], [W, 0], [W, D], [0, D]]
        room = {"name": "r", "floor": floor, "existing": [],
                "openings": [{"id": "door1", "type": "door", "wall": "w1", "offset": 30, "width": 85, "height": 205, "swing": 90},
                             {"id": "win1", "type": "window", "wall": "w3" if len(floor) == 4 else "w6", "offset": 20, "width": 100, "height": 120, "sill": 90}]}
        its = [dict(item(f"i{k}", c[0], c[1], c[2], c[3])) for k, c in enumerate(rnd.choices(cats, k=rnd.randint(5, 8)))]
        cons = {}
        for it in its:
            cl = []
            for _ in range(rnd.randint(0, 3)):
                ty = rnd.choice(types)
                tg = rnd.choice([x["id"] for x in its if x["id"] != it["id"]] + ["win1"])
                cl.append({"type": ty} if ty in ("edge", "middle") else {"type": ty, "target": tg})
            cons[it["id"]] = {"constraints": cl}
        cons["i0"]["anchor"] = True
        t0 = time.perf_counter()
        lay = plan_layout(room, its, cons)
        assert time.perf_counter() - t0 < 3.0
        assert hard_violations(room, its, lay) == []

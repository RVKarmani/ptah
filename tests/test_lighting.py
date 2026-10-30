import copy
import json
import math
import os

import numpy as np
import pytest

from ptah.lighting import SCENES, Source, _lux_from, bounce_lux, compute_lighting, main, outdoor_lux

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(name):
    with open(os.path.join(ROOT, "samples", name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def sample():
    return load("room.json"), load("items.json"), load("layout.json")


def without(sample, *ids, layer=None):
    room, items, lay = sample
    drop = set(ids)
    if layer:
        drop |= {i["id"] for i in items if (i.get("light") or {}).get("layer") == layer}
    its = [i for i in items if i["id"] not in drop]
    l = copy.deepcopy(lay)
    l["placements"] = [p for p in l["placements"] if p["item_id"] not in drop]
    return compute_lighting(room, its, l)


def codes(res, scene=None):
    src = res["scenes"][scene]["issues"] if scene else res["issues_all"]
    return [i["code"] for i in src]


# ---------------------------------------------------------------- photometry
def test_inverse_square_and_isotropic_intensity():
    # bare isotropic bulb: 1000 lm, half of it downwards -> I = 1000 * 0.5 / (2 pi) cd straight down
    s = Source("x", "task", "point", np.array([[0.0, 175.0, 0.0]]), 1000.0, "none")
    e100 = _lux_from(s, np.array([[0.0, 75.0, 0.0]]))[0]
    assert e100 == pytest.approx(1000 * 0.5 / (2 * math.pi), rel=1e-6)           # 79.6 lux at 1 m
    s2 = Source("x", "task", "point", np.array([[0.0, 275.0, 0.0]]), 1000.0, "none")
    assert _lux_from(s2, np.array([[0.0, 75.0, 0.0]]))[0] == pytest.approx(e100 / 4, rel=1e-6)   # inverse square
    # cosine law: 100 cm off-axis at 100 cm height -> cos45 / (2 * 1 m^2)
    off = _lux_from(s, np.array([[100.0, 75.0, 0.0]]))[0]
    assert off == pytest.approx(e100 * (1 / math.sqrt(2)) ** 3 * 1.0, rel=1e-6)   # I(45deg) uses n=0 -> same I, cos / r^2
    # a source below the plane lights nothing
    low = Source("x", "task", "point", np.array([[0.0, 40.0, 0.0]]), 1000.0, "none")
    assert _lux_from(low, np.array([[0.0, 75.0, 0.0]]))[0] == 0.0


def test_shade_attenuates_and_spot_has_a_cone():
    pt = np.array([[0.0, 75.0, 0.0]])
    bare = Source("x", "task", "point", np.array([[0.0, 175.0, 0.0]]), 1000.0, "none")
    drum = Source("x", "task", "point", np.array([[0.0, 175.0, 0.0]]), 1000.0, "drum")
    assert _lux_from(drum, pt)[0] < 2 * _lux_from(bare, pt)[0]
    assert _lux_from(Source("x", "task", "point", bare.pos, 1000.0, "dome"), pt)[0] > _lux_from(bare, pt)[0]  # dome focuses downwards
    spot = Source("x", "accent", "spot", np.array([[0.0, 175.0, 0.0]]), 500.0, "none", aim=np.array([0.0, -1.0, 0.0]), cone_deg=40.0)
    inside = _lux_from(spot, np.array([[10.0, 75.0, 0.0]]))[0]
    outside = _lux_from(spot, np.array([[200.0, 75.0, 0.0]]))[0]
    assert inside > 100 and outside == 0.0
    # beam of 40 deg: I = 500 / (2 pi (1 - cos 20deg)) ~ 1320 cd -> ~1300 lux at 1 m
    assert inside == pytest.approx(500 / (2 * math.pi * (1 - math.cos(math.radians(20)))) * math.cos(math.atan(0.1)) / (1.0 + 0.01), rel=0.02)


def test_line_source_splits_flux_over_points():
    pos = np.array([[x, 175.0, 0.0] for x in (-20.0, 0.0, 20.0)])
    strip = Source("s", "accent", "line", pos, 300.0, "none", aim=np.array([0.0, -1.0, 0.0]))
    single = Source("s", "accent", "line", pos[1:2], 300.0, "none", aim=np.array([0.0, -1.0, 0.0]))
    pt = np.array([[0.0, 75.0, 0.0]])
    assert _lux_from(strip, pt)[0] == pytest.approx(_lux_from(single, pt)[0], rel=0.1)
    # emits nowhere above its aim hemisphere
    up = Source("s", "accent", "line", pos, 300.0, "none", aim=np.array([0.0, 1.0, 0.0]))
    assert _lux_from(up, pt)[0] == 0.0


def test_bounce_and_daylight_curve():
    assert bounce_lux(3000, 0.5, 70) == pytest.approx(3000 * 0.5 / (70 * 0.5))
    assert outdoor_lux(3) == 0 and outdoor_lux(22) == 0
    assert outdoor_lux(12) > outdoor_lux(8) > 0 and outdoor_lux(8) == pytest.approx(outdoor_lux(16))


# ---------------------------------------------------------------- the sample room
def test_sample_all_scenes_pass(sample):
    res = compute_lighting(*sample)
    assert set(res["scenes"]) == {"day", "evening", "reading", "night"}
    assert res["issues_all"] == [] and res["issues"] == []
    assert res["layers"] == {"ambient": True, "task": True, "accent": True}
    assert res["scene"] == "evening" and res["lux"] == res["scenes"]["evening"]["lux"]
    rd = res["scenes"]["reading"]
    z = next(z for z in rd["zones"] if z["name"] == "reading:sofa-1")
    assert z["status"] == "ok" and z["lux"] >= 0.8 * 300 and z["item_ids"][:2] == ["sofa-1", "lamp-1"]
    ev = res["scenes"]["evening"]
    assert 50 <= ev["ambient_avg_lux"] <= 150
    assert all(f["accent_lit"] for f in ev["focal_points"]) and {f["item_id"] for f in ev["focal_points"]} == {"shelf-1", "plant-1", "art-1", "guitar-1", "tv-1", "console-1"}
    assert res["scenes"]["day"]["daylight"]["informational"] and res["scenes"]["day"]["issues"] == []


def test_grid_shape_and_scene_presets(sample):
    room, items, lay = sample
    res = compute_lighting(room, items, lay)
    cols, rows = res["size"]
    assert (cols, rows) == (17, 15) and res["grid_cm"] == 25 and res["plane_cm"] == 75 and res["origin"] == [0, 0]
    for sc in res["scenes"].values():
        assert len(sc["lux"]) == rows and all(len(r) == cols for r in sc["lux"])
    assert res["scene_presets"] == SCENES
    assert SCENES["evening"] == {"ambient": 0.4, "task": 0.0, "accent": 1.0}
    assert SCENES["reading"] == {"ambient": 0.3, "task": 1.0, "accent": 0.3}
    assert SCENES["night"]["accent"] == 0.6 and SCENES["day"]["ambient"] == 0.0
    # lamps scale by layer: reading has more light at the sofa than evening, night is dimmest of the lamp scenes
    s = res["scenes"]
    assert s["reading"]["lux_max"] > s["evening"]["lux_max"] > s["night"]["lux_max"]
    assert s["night"]["ambient_avg_lux"] < s["evening"]["ambient_avg_lux"] < s["reading"]["ambient_avg_lux"]


def test_lux_none_outside_non_rect_floor():
    room = {"name": "L", "height": 250, "floor": [[0, 0], [300, 0], [300, 100], [100, 100], [100, 300], [0, 300]], "openings": [], "existing": []}
    items = [{"id": "l", "category": "pendant", "w": 40, "d": 40, "h": 30,
              "light": {"layer": "ambient", "kind": "point", "lumens": 1000, "color_temp_k": 2700, "emit_offset": [0, 10, 0]}}]
    lay = {"placements": [{"item_id": "l", "x": 50, "z": 50, "y": 200, "rotation_deg": 0, "mount": "ceiling"}]}
    res = compute_lighting(room, items, lay)
    grid = res["scenes"]["evening"]["lux"]
    assert grid[10][10] is None and grid[0][0] is not None and grid[10][1] is not None


# ---------------------------------------------------------------- issues per scene
def test_removing_reading_lamp_gives_dim_task_zone_in_reading_scene(sample):
    res = without(sample, "lamp-1")
    assert "DIM_TASK_ZONE" in codes(res, "reading")
    assert "DIM_TASK_ZONE" not in codes(res, "evening")
    i = next(i for i in res["issues_all"] if i["code"] == "DIM_TASK_ZONE")
    assert i["suggest"] == {"category": "floor_lamp", "near": "sofa-1"} and i["scenes"] == ["reading"]
    assert res["layers"]["task"] is False


def test_removing_all_accent_lights_gives_no_accent_layer(sample):
    res = without(sample, layer="accent")
    assert "NO_ACCENT_LAYER" in codes(res, "night") and "NO_ACCENT_LAYER" in codes(res, "evening")
    assert res["layers"]["accent"] is False
    i = next(i for i in res["issues_all"] if i["code"] == "NO_ACCENT_LAYER")
    assert i["suggest"]["category"] in ("led_strip", "picture_light", "sconce", "table_lamp")
    assert "attach" in i["suggest"] or "near" in i["suggest"]
    assert i["scenes"] == ["evening", "night"]


def test_task_lights_without_ambient_pass_reading_but_fail_evening(sample):
    res = without(sample, layer="ambient")
    assert [z["status"] for z in res["scenes"]["reading"]["zones"] if z["layer"] == "task"] == ["ok"]
    assert "DIM_TASK_ZONE" not in codes(res, "reading") and codes(res, "reading") == []
    assert "NO_AMBIENT" in codes(res, "evening")
    i = next(i for i in res["issues_all"] if i["code"] == "NO_AMBIENT")
    assert i["suggest"] == {"category": "pendant", "above": "table-1"}
    assert res["layers"]["ambient"] is False and res["layers"]["task"] is True
    assert res["issues"] == res["scenes"]["evening"]["issues"]      # top level = evening scene


def test_low_ambient_without_fixture_flag_still_reported(sample):
    room, items, lay = sample
    its = copy.deepcopy(items)
    for i in its:
        if i["id"] == "pendant-1":
            i["light"]["lumens"] = 300
    res = compute_lighting(room, its, lay)
    assert "NO_AMBIENT" in codes(res, "evening")
    assert "low" in next(i for i in res["issues_all"] if i["code"] == "NO_AMBIENT")["message"]


def test_unlit_focal_point(sample):
    res = without(sample, "strip-1", "spot-1", "tvlight-1")
    i = next(i for i in res["issues_all"] if i["code"] == "UNLIT_FOCAL_POINT" and i["item_id"] == "shelf-1")
    assert i["suggest"] == {"category": "led_strip", "attach": "shelf-1", "side": "back"}
    res = without(sample, "plight-1", "lamp-2")
    ids = {i["item_id"] for i in res["issues_all"] if i["code"] == "UNLIT_FOCAL_POINT"}
    assert "art-1" in ids


def test_glare_risk_for_bare_bulb_at_eye_level(sample):
    room, items, lay = sample
    its = copy.deepcopy(items)
    for i in its:
        if i["id"] == "lamp-2":
            i["model"]["params"]["shade"] = "none"
            i["light"]["lumens"] = 800
            i["light"]["emit_offset"] = [0, 90, 0]       # table lamp bulb at ~140 cm
    res = compute_lighting(room, its, lay)
    g = [i for i in res["issues_all"] if i["code"] == "GLARE_RISK"]
    assert g and g[0]["item_id"] == "lamp-2" and "night" in g[0]["scenes"]


def test_light_defaults_from_category_when_item_has_no_light_dict(sample):
    room, items, lay = sample
    its = copy.deepcopy(items)
    for i in its:
        if i["id"] == "lamp-1":
            i.pop("light")
    res = compute_lighting(room, its, lay)
    assert any(l["item_id"] == "lamp-1" and l["layer"] == "task" for l in res["lights"])


# ---------------------------------------------------------------- day / CLI
def test_day_scene_follows_hour_and_windows(sample):
    room, items, lay = sample
    noon = compute_lighting(room, items, lay, "day", 12.0)
    morning = compute_lighting(room, items, lay, "day", 8.0)
    night = compute_lighting(room, items, lay, "day", 22.0)
    assert noon["scene"] == "day" and noon["mode"] == "day"
    assert noon["scenes"]["day"]["ambient_avg_lux"] > morning["scenes"]["day"]["ambient_avg_lux"] > 0
    assert night["scenes"]["day"]["lux_max"] == 0
    g = noon["lux"]   # window on the bottom wall (z=360): rows near it are brighter than the back of the room
    assert np.mean([v for v in g[13] if v is not None]) > np.mean([v for v in g[1] if v is not None])
    # the lamp scenes do not depend on the hour
    assert noon["scenes"]["reading"]["lux"] == morning["scenes"]["reading"]["lux"]


def test_cli_writes_lighting_json(tmp_path):
    out = tmp_path / "lighting.json"
    base = os.path.join(ROOT, "samples")
    assert main([f"{base}/room.json", f"{base}/items.json", f"{base}/layout.json", "-o", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["scene"] == "evening" and set(data["scenes"]) == {"day", "evening", "reading", "night"}
    out2 = tmp_path / "day.json"
    assert main([f"{base}/room.json", f"{base}/items.json", f"{base}/layout.json", "-o", str(out2), "--mode", "day", "--hour", "9"]) == 0
    d2 = json.loads(out2.read_text())
    assert d2["scene"] == "day" and d2["hour"] == 9.0 and d2["lux"] == d2["scenes"]["day"]["lux"]

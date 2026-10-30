"""Design stage tests: no network, no LLM, no personal data (synthetic inputs only)."""
import copy
import json
import os

import pytest

from ptah.design import ROLES, DesignError, DesignInputs, build_vocab, extract_json, ledger_count, validate_spec
from ptah.design_build import build_and_solve, build_items, build_room

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEMPLATES = {"sofa", "armchair", "coffee_table", "side_table", "dining_table", "desk", "chair", "bed", "nightstand", "bookshelf", "cabinet",
             "tv_stand", "rug", "floor_lamp", "table_lamp", "pendant", "sconce", "led_strip", "picture_light", "plant", "art", "mirror", "generic",
             "tv", "guitar", "guitar_hanger", "amp", "book_stack", "vinyl_crate", "vase", "frame", "cushion", "throw", "candle", "small_plant",
             "tray", "poster"}
CONSTRAINT_TYPES = {"edge", "middle", "near", "far", "in_front_of", "side_of", "face", "center_align", "on_top_of", "under", "wall_mounted",
                    "ceiling", "attach_to", "not_blocking_window"}


def load(name):
    with open(os.path.join(ROOT, "samples", name)) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def inputs():
    profile = {"style_summary": "Synthetic taste.", "aesthetic_tags": ["warm minimal"],
               "themes": [{"name": "Test Theme", "post_count": 3}],
               "entities": [{"name": "Test Film", "type": "movie"}, {"name": "Test Band", "type": "artist"}]}
    brief = {"location": "Testville", "inputs": [{"name": "Test Band", "type": "artist"}],
             "taste": {"decor": [{"name": "Classic", "affinity": 0.9}, {"name": "Rustic", "affinity": 0.8}],
                       "ambience": [{"name": "Warm", "affinity": 0.7}]},
             "inspiration_places": [{"name": "Test Hotel", "kind": "hotel", "affinity": 0.85, "decor_tags": ["Art deco"], "ambience_tags": ["Quiet"]}],
             "brands": [{"name": "Test Brand", "tier": "mid", "affinity": 0.77}],
             "discovery": [{"name": "Test Pick", "type": "movie", "affinity": 0.9, "tags": ["Drama"]}]}
    items = load("items.json")
    return DesignInputs(profile, brief, load("room.json"), [i for i in items if i.get("owned")])


def chain(inputs=("Test Film",), tags=({"kind": "decor", "name": "Classic", "affinity": 0.9},), decision="because"):
    return {"inputs": list(inputs), "tags": list(tags), "decision": decision}


def piece(role, name, primary, secondary=None, accent=None, m1="oak", m2=None, params=None, insp=None, ch=None):
    return {"role": role, "name": name, "colors": {"primary": primary, "secondary": secondary, "accent": accent},
            "materials": {"primary": m1, "secondary": m2}, "params": params or {}, "style_keywords": ["alpha", "beta"],
            "inspired_by": insp, "why_chain": ch or chain()}


def good_spec():
    return {
        "concept_name": "Test", "concept": "A test room.",
        "palette": {"wall_hex": "#3F5A4A", "wall_name": "Moss", "floor": "mid_oak", "accents": ["#c9a45c", "#b5533c", "#e9dfc8"]},
        "palette_why": chain(), "materials_why": chain(("Test Theme",), ({"kind": "decor", "name": "Rustic", "affinity": 0.8},)),
        "material_direction": {"summary": "warm", "materials": ["walnut", "velvet", "brass"], "avoid": []},
        "lighting_mood": {"ambient_k": 2300, "task_k": 2600, "accent_k": 2100, "emphasis": ["accent", "ambient"], "default_scene": "evening",
                          "description": "warm", "accent_glow_hex": "#ffb070"},
        "lighting_why": chain(("Test Band",), ({"kind": "ambience", "name": "Warm", "affinity": 0.7},)),
        "pieces": [
            piece("main_sofa", "Moss sofa", "#7a8f6a", "#4a3426", None, "velvet", "walnut", {"legs": "tapered", "arms": True}),
            piece("media_console", "Walnut sideboard", "#6a4833", "#1e1f21", None, "walnut", "brass", {"legs": "tapered", "doors": 3}),
            piece("tv_bias_light", "Bias light", "#ffb070"),
            piece("coffee_table", "Round table", "#d9c9a8", "#1e1f21", None, "stone", "black_metal", {"shape": "round", "legs": "pedestal"}),
            piece("accent_chair", "Rust chair", "#b5533c", "#4a3426", None, "leather", "walnut"),
            piece("rug", "Indigo rug", "#2f4858", "#c9a45c", None, "wool"),
            piece("floor_lamp", "Arc lamp", "#c9a45c", "#efe6d2", None, "brass", "linen", {"shade": "cone", "arc": True}),
            piece("bookshelf", "Shelf", "#6a4833", None, None, "walnut"),
            piece("floor_plant", "Plant", "#3f7d4e", "#a86f4c", None, "ceramic"),
            piece("side_table", "Side table", "#c9a45c", None, None, "brass", None, {"shape": "round"}),
            piece("table_lamp", "Lamp", "#2f4858", "#efe6d2", None, "ceramic", "linen", {"shade": "drum"}),
            piece("pendant", "Pendant", "#f4efe4", "#c9a45c", None, "glass", "brass", {"shade": "globe"}),
            piece("art_main", "Print", "#d9cdb8", "#3f5a4a", "#b5533c", "light_wood", insp="Test Pick"),
            piece("cushion", "Cushion", "#b5533c", m1="velvet"),
            piece("throw", "Throw", "#8a6f55", "#e6dcc8", m1="wool"),
            piece("vase", "Vase", "#4d6b5a", m1="ceramic", params={"form": "round"}),
            piece("vinyl_crate", "Crate", "#b98d62", "#2f4858", "#b5533c", "light_wood"),
            piece("art_secondary", "Print 2", "#e4ddd0", "#2f4858", "#b5533c", "black_metal", params={"frame": "thin"}, insp="Test Film"),
        ],
    }


def test_valid_spec_is_normalised(inputs):
    spec = validate_spec(good_spec(), build_vocab(inputs))
    assert spec.palette.wall_hex == "#3f5a4a"  # lower-cased
    ids = [p.id for p in spec.pieces]
    assert len(ids) == len(set(ids)) and "sofa-1" in ids
    assert spec.palette_why.tags[0].affinity == 0.9


def test_affinity_is_taken_from_the_brief(inputs):
    raw = good_spec()
    for p in raw["pieces"]:
        for t in p["why_chain"]["tags"]:
            t["affinity"] = None  # omitted: filled from the brief
    spec = validate_spec(raw, build_vocab(inputs))
    assert all(t.affinity == 0.9 for t in spec.pieces[0].why_chain.tags)


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s["pieces"][0]["why_chain"].update(inputs=["Invented Movie"]), "Invented Movie"),
    (lambda s: s["pieces"][0]["why_chain"].update(tags=[{"kind": "decor", "name": "Baroque", "affinity": 0.9}]), "Baroque"),
    (lambda s: s["pieces"][0]["why_chain"].update(tags=[{"kind": "decor", "name": "Classic", "affinity": 0.5}]), "affinity"),
    (lambda s: s["pieces"][0]["why_chain"].update(tags=[{"kind": "ambience", "name": "Classic", "affinity": 0.9}]), "does not exist"),
    (lambda s: s["palette_why"].update(inputs=["Test Hotel", "Not A Place"]), "Not A Place"),
    (lambda s: s["pieces"][-1].update(inspired_by="Made Up Film"), "inspired_by"),
    (lambda s: s["pieces"][12].update(inspired_by=None), "inspired_by is required"),
    (lambda s: s["pieces"].pop(0), "main_sofa"),
    (lambda s: s["pieces"][0].update(role="chaise_longue"), "unknown role"),
    (lambda s: s["palette"].update(floor="marble_floor"), "floor"),
    (lambda s: s["palette"].update(wall_hex="mossy green"), "colour"),
    (lambda s: s["pieces"][0]["materials"].update(primary="unobtainium"), "primary"),
])
def test_ungrounded_or_malformed_spec_is_rejected(inputs, mutate, needle):
    raw = copy.deepcopy(good_spec())
    mutate(raw)
    with pytest.raises(DesignError) as e:
        validate_spec(raw, build_vocab(inputs))
    assert needle.lower() in str(e.value).lower() or any(needle.lower() in x.lower() for x in e.value.errors)


def test_tags_may_come_from_places_and_picks_but_must_exist(inputs):
    raw = good_spec()
    raw["pieces"][0]["why_chain"]["tags"] = [{"kind": "place_tag", "name": "art deco"}, {"kind": "discovery_tag", "name": "Drama"}]
    spec = validate_spec(raw, build_vocab(inputs))
    assert [t.name for t in spec.pieces[0].why_chain.tags] == ["Art deco", "Drama"]
    raw["pieces"][0]["why_chain"]["tags"] = [{"kind": "place_tag", "name": "Gothic"}]
    with pytest.raises(DesignError):
        validate_spec(raw, build_vocab(inputs))


def test_unsupported_params_are_dropped_with_a_warning_and_sizes_clamped(inputs):
    raw = good_spec()
    raw["pieces"][0]["params"] = {"legs": "wheels", "arms": True, "bogus": 1}
    raw["pieces"][0]["dims_cm"] = {"w": [400, 500], "d": [90, 95], "h": [80, 85]}
    spec = validate_spec(raw, build_vocab(inputs))
    assert spec.pieces[0].params == {"arms": True}
    assert spec.pieces[0].dims_cm.w == (230.0, 230.0)
    assert any("clamped" in w for w in spec.warnings) and any("dropped" in w for w in spec.warnings)


def test_extract_json_handles_fences_and_thinking():
    assert extract_json('<think>hm {x}</think>\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(DesignError):
        extract_json("no json here")


@pytest.fixture(scope="module")
def built(inputs):
    vocab = build_vocab(inputs)
    spec = validate_spec(good_spec(), vocab)
    return spec, vocab, build_and_solve(spec, vocab, inputs.room, load("items.json"), load("constraints.json"), log=lambda m: None)


def test_translation_is_contract_valid(built):
    spec, vocab, b = built
    items, room, cons = b["items"], b["room"], b["constraints"]
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    hexre = __import__("re").compile(r"^#[0-9a-fA-F]{6}$")
    designed = [i for i in items if i.get("designed")]
    assert len(designed) >= len(spec.pieces)
    for i in designed:
        assert i["price"] is None and i["url"] is None
        assert i["name"].endswith("(designed for you)")
        assert i["model"]["template"] in TEMPLATES
        assert all(v is None or hexre.match(v) for v in i["colors"].values())
        assert i["why_chain"]["signals"] and i["why_chain"]["reading"]
        assert i["w"] > 0 and i["d"] > 0 and i["h"] > 0
        if i["id"] in {p.id for p in spec.pieces}:
            assert i["slot"]["size_range_cm"] and i["slot"]["style_keywords"]
    for i in items:
        if "light" in i:
            assert i["light"]["layer"] in ("ambient", "task", "accent") and 1800 <= i["light"]["color_temp_k"] <= 4500
    assert room["finishes"]["floor"] == "mid_oak" and room["finishes"]["wall_color"] == "#3f5a4a"
    # owned items untouched
    sample = {i["id"]: i for i in load("items.json")}
    for oid in ("tv-1", "guitar-1", "amp-1"):
        assert next(i for i in items if i["id"] == oid) == sample[oid]
    # constraints: known types, existing targets
    valid = set(ids) | {"win1", "door1", "room_center", "rad1"}
    for iid, c in cons.items():
        assert iid in ids
        for x in c["constraints"]:
            assert x["type"] in CONSTRAINT_TYPES
            assert x.get("target") is None or x["target"] in valid


def test_solver_places_every_piece(built):
    spec, vocab, b = built
    lay = b["layout"]
    assert not lay["unplaced"], lay["unplaced"]
    assert not b["hard_violations"]
    placed = {p["item_id"] for p in lay["placements"]}
    assert {i["id"] for i in b["items"]} == placed


def test_lighting_layers_present(built):
    spec, vocab, b = built
    layers = {i["light"]["layer"] for i in b["items"] if "light" in i}
    assert layers == {"ambient", "task", "accent"}
    # lights use the mood's colour temperatures
    assert {i["light"]["color_temp_k"] for i in b["items"] if i.get("designed") and "light" in i and i["light"]["layer"] == "ambient"} == {2300}


def test_call_ledger_blocks_a_fourth_call(monkeypatch, tmp_path):
    import ptah.design as d
    monkeypatch.setattr(d, "_ledger_path", lambda: tmp_path / "calls.json")
    for _ in range(3):
        d._ledger_add("t", "m")
    assert d.ledger_count() == 3
    with pytest.raises(RuntimeError):
        d.call_llm([{"role": "user", "content": "hi"}], "t")


def test_repair_mode_drops_ungrounded_references_but_never_keeps_them(inputs):
    raw = good_spec()
    raw["pieces"][0]["why_chain"]["inputs"] = ["Invented Movie", "Test Film"]
    raw["pieces"][0]["why_chain"]["tags"] = [{"kind": "decor", "name": "Baroque", "affinity": 0.9}, {"kind": "decor", "name": "Classic", "affinity": 0.1}]
    raw["pieces"].append(piece("sconce", "Ghost", "#c9a45c", ch=chain(("Nobody",), ({"kind": "decor", "name": "Nope", "affinity": None},))))
    with pytest.raises(DesignError):
        validate_spec(copy.deepcopy(raw), build_vocab(inputs))
    with pytest.raises(DesignError):  # the ghost piece keeps nothing grounded: still an error, never silently kept
        validate_spec(copy.deepcopy(raw), build_vocab(inputs), repair=True)
    raw["pieces"].pop()
    spec = validate_spec(raw, build_vocab(inputs), repair=True)
    assert spec.pieces[0].why_chain.inputs == ["Test Film"]
    assert [(t.name, t.affinity) for t in spec.pieces[0].why_chain.tags] == [("Classic", 0.9)]
    assert any("dropped" in w for w in spec.warnings)

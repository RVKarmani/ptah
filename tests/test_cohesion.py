"""Cohesion scoring + furniture swap gate + art set selection.  Offline: synthetic images, no network, no SerpApi."""
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from ptah.products import cohesion as co, curate  # noqa: E402
from ptah.products.scout import Candidate, Slot  # noqa: E402

SPEC = {
    "concept_name": "Moss study",
    "palette": {"wall_hex": "#6f9378", "floor": "herringbone", "accents": ["#b8412f", "#2f4a63", "#c9a45c", "#efe6d2"]},
    "material_direction": {"materials": ["walnut", "linen", "wool", "brass", "ceramic", "glass"], "avoid": ["chrome", "glossy plastic", "cool grey"]},
    "palette_why": {"inputs": ["Spirited Away"], "tags": [{"kind": "decor", "name": "Asian Inspired", "affinity": 0.7}]},
}
ITEMS = [
    {"id": "sofa-1", "category": "sofa", "slot": {"category": "sofa", "colors": {"primary": "#3d6286", "secondary": "#4a3426"}, "materials": {"primary": "linen", "secondary": "walnut"}},
     "why_chain": {"inputs": ["Tokyo"], "tags": [{"name": "Classic", "affinity": 0.8}]}},
    {"id": "console-1", "category": "tv_stand", "slot": {"category": "tv_stand", "colors": {"primary": "#6a4833", "secondary": "#1e1f21"}, "materials": {"primary": "walnut", "secondary": "black_metal"}}},
    {"id": "lamp-1", "category": "floor_lamp", "slot": {"category": "floor_lamp", "colors": {"primary": "#c9a45c", "secondary": "#f1e8d3"}, "materials": {"primary": "brass", "secondary": "linen"}}},
    {"id": "art-1", "category": "art", "slot": {"category": "art", "colors": {"primary": "#ff00ff"}}},   # art colours never enter the room palette
]
ROOM = {"finishes": {"floor": "herringbone", "wall_color": "#6f9378"}}


@pytest.fixture(scope="module")
def prof():
    return co.build_profile(SPEC, ITEMS, ROOM)


def art_palette(colors):
    """Palette of a picture (no backdrop removal: a print is all picture)."""
    return co.image_palette(img(colors), remove_background=False)


def img(colors, size=(120, 90), bg=None):
    """Stripes of ``colors`` (hex), optionally on a white studio backdrop."""
    im = Image.new("RGB", (size[0] + (40 if bg else 0), size[1] + (40 if bg else 0)), bg or (255, 255, 255))
    d = ImageDraw.Draw(im)
    off = 20 if bg else 0
    w = size[0] / len(colors)
    for i, c in enumerate(colors):
        d.rectangle([off + int(i * w), off, off + int((i + 1) * w), off + size[1]], fill=c)
    return im


# ---------------------------------------------------------------------------------------------------------------- colour maths

@pytest.mark.parametrize("a,b,expected", [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),     # Sharma, Wu, Dalal reference pairs
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
])
def test_ciede2000_reference_values(a, b, expected):
    assert co.ciede2000(a, b) == pytest.approx(expected, abs=1e-3)
    assert co.ciede2000(b, a) == pytest.approx(expected, abs=1e-3)


def test_lab_of_known_colours():
    L, a, b = co.rgb_to_lab((255, 255, 255))
    assert L == pytest.approx(100, abs=0.1) and abs(a) < 0.1 and abs(b) < 0.1
    assert co.rgb_to_lab((0, 0, 0))[0] == pytest.approx(0, abs=0.1)


# ---------------------------------------------------------------------------------------------------------------- profile

def test_profile_uses_designed_pieces_only(prof):
    assert prof.wall == "#6f9378" and prof.floor_hex == "#b99366"
    assert "#ff00ff" not in [t["hex"] for t in prof.targets]            # art colours are not room colours
    assert {"walnut", "brass"} <= set(prof.materials) | set(prof.woods) | set(prof.metals)
    assert set(prof.woods) == {"walnut"} and set(prof.metals) == {"black_metal", "brass"}


# ---------------------------------------------------------------------------------------------------------------- palette

def test_in_palette_art_beats_clash(prof):
    good = co.palette_score(art_palette(["#e4dbc4", "#7fa08c", "#2f4a63", "#b8412f"]), prof)
    neon = co.palette_score(art_palette(["#ff00cc", "#00ffee", "#ffee00"]), prof)
    assert good["score"] > 0.65 and neon["score"] < 0.4
    assert neon["clash"] > 0.3


def test_cool_grey_dominant_is_capped(prof):
    grey = co.palette_score(art_palette(["#7a838b", "#6f7880", "#8a9299"]), prof)
    assert grey["score"] <= 0.45 and "cool grey" in grey.get("note", "")


def test_warm_neutrals_are_tasteful(prof):
    warm = co.palette_score(art_palette(["#d9cdb8", "#c8b99f", "#efe6d2"]), prof)
    cool = co.palette_score(art_palette(["#b9c2c9", "#aab4bc", "#c7cfd5"]), prof)
    assert warm["score"] > cool["score"] + 0.2


def test_background_is_ignored(prof):
    on_white = co.image_palette(img(["#6a4833"], bg=(255, 255, 255)))
    assert on_white and on_white[0]["hex"].startswith("#6") and on_white[0]["weight"] > 0.8


def test_loud_saturated_picture_is_punished(prof):
    quiet = co.palette_score(art_palette(["#e4dbc4", "#a9bba4", "#7fa08c"]), prof)
    loud = co.palette_score(art_palette(["#bf2918", "#bf2918", "#e8e0b0"]), prof)
    assert loud["loud"] > quiet["loud"] and loud["score"] < quiet["score"]


def test_slot_match_and_cover_flag_recolouring(prof):
    designed = {"primary": "#b8412f", "secondary": "#4a3426"}                 # red velvet chair, walnut legs
    red = co.palette_score(_fclusters(["#b0432f", "#b0432f", "#3a2a22"]), prof, designed)
    cream = co.palette_score(_fclusters(["#e6dcc8", "#e6dcc8", "#3a2a22"]), prof, designed)
    assert red["slot_dE"] < 12 and red["slot_cover"] > 0.9
    assert cream["slot_dE"] > 20 and cream["slot_cover"] < red["slot_cover"]


# ---------------------------------------------------------------------------------------------------------------- text

def test_style_score_vocabulary(prof):
    good = co.style_score("Japanese woodblock landscape print, vintage muted", prof)
    bad = co.style_score("Neon cartoon anime fan gift poster glossy", prof)
    chrome = co.style_score("Modern chrome floor lamp", prof)
    assert good["score"] > 0.8 and bad["score"] < 0.4
    assert "chrome" in chrome["avoid_hits"] and chrome["score"] < 0.5


def test_material_score(prof):
    ok = co.material_score("Walnut TV stand with brass handles", {"primary": "walnut", "secondary": "black_metal"}, prof)
    chrome = co.material_score("Chrome and glass side table", {"primary": "brass"}, prof)
    plastic = co.material_score("Plastic lamp", {"primary": "brass"}, prof)
    marble = co.material_score("Marble coffee table", {"primary": "oak"}, prof)
    assert ok["score"] >= 0.85 and not ok["hard_reject"]
    assert chrome["hard_reject"] and plastic["hard_reject"] and chrome["score"] == 0
    assert marble["score"] < 0.7 and any("off-palette" in f for f in marble["flags"])


def test_wood_family_and_metal_consistency():
    spec_items = [{"id": "x", "category": "tv_stand", "slot": {"category": "tv_stand", "materials": {"primary": "walnut", "secondary": "brass"}}}]
    p = co.build_profile(SPEC, spec_items, ROOM)
    assert p.woods == ["walnut"] and p.metals == ["brass"]
    light = co.material_score("Light oak sideboard", {"primary": "walnut"}, p)
    black = co.material_score("Black metal frame table", {"primary": "walnut"}, p)
    assert any("wood family" in f for f in light["flags"]) and light["score"] < 0.7
    assert any("metal" in f for f in black["flags"])


def test_listed_size_and_scale():
    assert co.listed_size_cm('Print 16x24 in') == pytest.approx((40.6, 61.0), abs=0.2)
    assert co.listed_size_cm("Print 40x60 cm") == (40.0, 60.0)
    fits = co.scale_score((90, 110), (60, 75), "Print", aspect=1.4, is_art=True)
    wrong = co.scale_score((90, 110), (60, 75), "Print", aspect=0.7, is_art=True)
    assert fits["score"] > 0.9 and wrong["score"] < 0.3 and wrong["notes"]
    small = co.scale_score((150, 180), (40, 55), "Console 40 x 20 cm", is_art=False)
    assert small["score"] < 0.5


# ---------------------------------------------------------------------------------------------------------------- art sets

def _piece(title, colors, w=100.0, h=70.0):
    return {"title": title, "clusters": art_palette(colors), "w": w, "h": h}


def test_medium_and_era():
    assert co.medium_of("Hokusai Ukiyo-e woodblock print") == "woodblock"
    assert co.medium_of("Fleetwood Mac concert photo poster") == "photo"
    assert co.medium_of("Spirited Away anime poster") == "illustration"
    assert co.era_of("Vintage 70s poster") == "heritage" and co.era_of("Modern neon digital art") == "contemporary"
    assert co.medium_compat("woodblock", "woodblock") == 1.0 and co.medium_compat("photo", "illustration") < 0.5


def test_art_set_prefers_matching_pair(prof):
    a = _piece("Japanese woodblock landscape print", ["#e4dbc4", "#7fa08c", "#2f4a63"])
    b = _piece("Japanese woodblock wave print", ["#e6dcc8", "#7fa08c", "#2f4a63"], 50, 60)
    photo = _piece("Black and white concert photo poster", ["#222222", "#d8d8d8", "#111111"], 50, 60)
    loud = _piece("Neon cartoon anime poster", ["#ff00cc", "#00e5ff", "#ffee00"], 50, 60)
    good = co.art_set_score(a, b, prof)
    mixed = co.art_set_score(a, photo, prof)
    clash = co.art_set_score(a, loud, prof)
    assert good["score"] > mixed["score"] + 0.1 and good["score"] > clash["score"] + 0.2
    assert good["medium_compat"] == 1.0 and mixed["medium_compat"] < 0.5
    assert good["coverage"]["echoed"]                                    # the pair repeats room colours


def test_palette_coverage_counts_distinct_room_colours(prof):
    green = art_palette(["#7fa08c"])
    red_blue = art_palette(["#b8412f", "#2f4a63"])
    one = co.palette_coverage([green], prof)
    many = co.palette_coverage([green, red_blue], prof)
    assert many["score"] > one["score"] and len(many["echoed"]) >= 3


# ---------------------------------------------------------------------------------------------------------------- swap gate

def _fclusters(colors):
    return co.image_palette(img(colors, bg=(255, 255, 255)))


def _furn(title, colors, slot_colors, slot_mats, **kw):
    return co.score_furniture(title, _fclusters(colors), ROOM_PROFILE, slot_colors=slot_colors, slot_materials=slot_mats, slot_w=(150, 180),
                              slot_h=(45, 55), store_q=1.0, keywords=["walnut media console", "tapered legs"], bg_fraction=kw.get("bg", 0.4))


ROOM_PROFILE = co.build_profile(SPEC, ITEMS, ROOM)
DESIGNED = {"primary": "#6a4833", "secondary": "#1e1f21"}
MATS = {"primary": "walnut", "secondary": "black_metal"}


def test_gate_accepts_cohesive_product():
    bd = _furn("Walnut Mid-Century TV Stand Media Console with tapered legs 165 x 40 cm", ["#6e4a36", "#6e4a36", "#6e4a36"], DESIGNED, MATS)
    ok, why = co.swap_gate(bd)
    assert ok, why
    assert bd["total"] >= co.GATE["total"]


def test_gate_rejects_recolour():
    cream = _furn("Walnut TV stand media console", ["#e8dcc6", "#e8dcc6", "#e8dcc6"], DESIGNED, MATS)
    ok, why = co.swap_gate(cream)
    assert not ok and any("recolour" in w for w in why)


def test_gate_rejects_clashing_colour_even_if_titled_right():
    pink = _furn("Walnut TV stand media console", ["#ff2fa0", "#ff2fa0"], DESIGNED, MATS)
    ok, why = co.swap_gate(pink)
    assert not ok and any("palette" in w or "clash" in w for w in why)


def test_gate_rejects_avoid_list_materials_and_words():
    chrome = _furn("Chrome and glass TV stand media console", ["#6e4a36"], DESIGNED, MATS)
    plastic = _furn("Plastic TV stand media console", ["#6e4a36"], DESIGNED, MATS)
    for bd in (chrome, plastic):
        ok, why = co.swap_gate(bd)
        assert not ok and any("avoid" in w for w in why)


def test_gate_rejects_lifestyle_photo_and_wrong_size():
    life = _furn("Walnut TV stand media console", ["#6e4a36"], DESIGNED, MATS, bg=0.0)
    ok, why = co.swap_gate(life)
    assert not ok and any("lifestyle" in w for w in why)
    small = _furn("Walnut TV stand media console 40 x 20 cm", ["#6e4a36"], DESIGNED, MATS)
    ok, why = co.swap_gate(small)
    assert not ok and any("size" in w for w in why)


def test_gate_thresholds_are_overridable():
    cream = _furn("Walnut TV stand media console", ["#e8dcc6"], DESIGNED, MATS)
    assert not co.swap_gate(cream)[0]
    assert co.swap_gate(cream, {"palette": 0.0, "slot_dE": 99, "slot_cover": 0.0, "total": 0.0, "material": 0.0, "style": 0.0})[0]


def test_furniture_set_check_blocks_new_wood_family_and_chrome(prof):
    base = [{"id": "a", "category": "tv_stand", "material": {"primary": "walnut", "secondary": "black_metal"}},
            {"id": "b", "category": "floor_lamp", "material": {"primary": "brass", "secondary": "linen"}}]
    assert co.furniture_set_check(base, prof)["ok"]
    light = base + [{"id": "c", "category": "coffee_table", "material": {"primary": "oak"}}]
    chk = co.furniture_set_check(light, prof)
    assert chk["new_wood_family"] == ["light"] and not chk["ok"]
    chrome = base + [{"id": "d", "category": "side_table", "material": {"primary": "chrome"}}]
    assert not co.furniture_set_check(chrome, prof)["ok"]
    art = base + [{"id": "e", "category": "art", "material": {"primary": "oak"}}]
    assert co.furniture_set_check(art, prof)["ok"]                      # art frames are not furniture


# ---------------------------------------------------------------------------------------------------------------- curate

def art_slot(**kw):
    base = dict(id="art-1", role="art_main", category="art", template="art", name="Print", w_range=(90.0, 110.0), h_range=(60.0, 75.0),
                colors={}, materials={}, params={"frame": "framed"}, style_keywords=["japanese landscape"], inspired_by="Spirited Away")
    base.update(kw)
    return Slot(**base)


def cand(title, colors, aspect, price=40.0, crop=1.0, pid=None):
    im = img(colors, size=(int(100 * aspect), 100))
    c = Candidate(result={"title": title, "product_id": pid or title, "source": "Etsy"}, query="q", price=price, image=im, crop_conf=crop, aspect=aspect, evaluated=True)
    c.clusters = co.image_palette(im, remove_background=False)
    return c


def test_aesthetic_queries_come_from_palette_mood_medium(prof):
    qs = curate.aesthetic_queries(prof, art_slot())
    texts = [q["q"] for q in qs]
    assert any("sage green" in t for t in texts) and any("woodblock" in t or "ink wash" in t for t in texts)
    assert all(q["max_price"] >= 100 for q in qs)
    side = curate.aesthetic_queries(prof, art_slot(id="poster-1", template="poster", category="poster", w_range=(40.0, 50.0), h_range=(60.0, 70.0)))
    assert any("70s" in q["q"] for q in side)


def test_rank_and_pick_set_prefers_cohesive_pair(prof):
    main, side = art_slot(), art_slot(id="poster-1", template="poster", category="poster", w_range=(40.0, 50.0), h_range=(60.0, 70.0), inspired_by="Fleetwood Mac")
    pool = [
        cand("Japanese woodblock landscape print", ["#e4dbc4", "#7fa08c", "#2f4a63"], 1.4),
        cand("Neon cartoon anime poster landscape", ["#ff00cc", "#00e5ff", "#ffee00"], 1.4),
        cand("Japanese woodblock wave print", ["#e6dcc8", "#7fa08c", "#2f4a63"], 0.75),
        cand("Black and white concert photo poster", ["#222222", "#d8d8d8", "#111111"], 0.75),
        cand("Japanese woodblock cut-out unsure", ["#e4dbc4", "#7fa08c"], 1.4, crop=0.4),
    ]
    rm, rs = curate.rank_for_slot(pool, main, prof), curate.rank_for_slot(pool, side, prof)
    assert rm[0].title == "Japanese woodblock landscape print" and "Neon" not in rm[0].title
    assert rm[-1].cohesion["total"] < 0.62 or "Neon" in rm[-1].title        # capped / clashing pieces sink
    pairs = curate.pick_art_set(rm, rs, main, side, prof, top=6)
    best = pairs[0]
    assert best["main"].title == "Japanese woodblock landscape print" and best["side"].title == "Japanese woodblock wave print"
    assert all(p["main"].crop_conf >= 0.85 and p["side"].crop_conf >= 0.85 for p in pairs)   # blurry cut-outs are not hung


def test_portrait_print_is_capped_in_landscape_slot(prof):
    pool = [cand("Japanese woodblock print", ["#e4dbc4", "#7fa08c", "#2f4a63"], 0.7)]
    r = curate.rank_for_slot(pool, art_slot(), prof)[0]
    assert r.cohesion["total"] <= 0.5 and "aspect does not fit the slot" in r.cohesion["caps"]


def test_dedupe_by_image_keeps_best_listing(prof):
    a = cand("Best listing", ["#e4dbc4", "#7fa08c"], 1.4)
    b = cand("Same picture other store", ["#e4dbc4", "#7fa08c"], 1.4)
    c = cand("Different picture", ["#2f4a63", "#b8412f"], 1.4)
    out = curate.dedupe_by_image([a, b, c])
    assert [x.title for x in out] == ["Best listing", "Different picture"]


def test_load_thumb_caches_on_disk(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return img(["#6f9378"])
    im1 = curate.load_thumb("https://x/y.jpg", tmp_path, fetch)
    im2 = curate.load_thumb("https://x/y.jpg", tmp_path, fetch)
    assert im1 is not None and im2 is not None and calls == ["https://x/y.jpg"]
    assert curate.load_thumb(None, tmp_path, fetch) is None


def test_designed_accent_piece_must_stay_its_colour():
    red = {"primary": "#b8412f", "secondary": "#4a3426"}
    mats = {"primary": "velvet", "secondary": "walnut"}

    def chair(color):
        return co.score_furniture("Velvet accent chair walnut legs", _fclusters([color, color, "#2a1d16"]), ROOM_PROFILE, slot_colors=red, slot_materials=mats,
                                  slot_w=(70, 80), slot_h=(74, 82), store_q=1.0, keywords=["velvet lounge chair"], bg_fraction=0.4)
    rust, brown = chair("#b04a30"), chair("#7a4a38")
    assert co.swap_gate(rust)[0], co.swap_gate(rust)[1]                    # a red chair may be swapped for a red chair (and is not "loud": it is the accent)
    ok, why = co.swap_gate(brown)
    assert not ok and any("accent" in w for w in why)


# ---------------------------------------------------------------------------------------------------------------- end to end (run_scout --cohesive, offline)

def _mock_print(colors, box, size=(300, 300)):
    """A framed print photographed on a plain wall (the thing artimg cuts out)."""
    im = Image.new("RGB", size, (238, 236, 232))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    d.rectangle([x0 - 6, y0 - 6, x1 + 6, y1 + 6], fill=(20, 20, 20))
    w = (x1 - x0) / len(colors)
    for i, c in enumerate(colors):
        d.rectangle([x0 + int(i * w), y0, x0 + int((i + 1) * w), y1], fill=c)
    return im


def test_cohesive_run_end_to_end(tmp_path, monkeypatch):
    import json
    import types
    import run_scout
    from ptah.products import scout as sc

    src = tmp_path / "design_x_artonly"
    src.mkdir()

    def slot(role, tpl, cat, w, h, colors, mats, kws, insp=None, params=None):
        return {"role": role, "template": tpl, "category": cat, "size_range_cm": {"w": w, "d": [3, 5], "h": h}, "colors": colors, "materials": mats,
                "params": params or {}, "style_keywords": kws, "inspired_by": insp}

    items = [
        {"id": "art-1", "category": "art", "name": "Print (designed for you)", "w": 100, "d": 4, "h": 68, "designed": True, "model": {"template": "art", "params": {"frame": "framed"}},
         "colors": {"primary": "#e8d8b0", "secondary": "#2f4a63", "accent": "#b8412f"}, "material": {"primary": "light_wood"},
         "slot": slot("art_main", "art", "art", [90, 110], [60, 75], {"primary": "#e8d8b0", "secondary": "#2f4a63"}, {"primary": "light_wood"}, ["japanese landscape"], "Spirited Away", {"frame": "framed"})},
        {"id": "poster-1", "category": "poster", "name": "Poster (designed for you)", "w": 45, "d": 3, "h": 65, "designed": True, "model": {"template": "poster", "params": {"frame": "thin"}},
         "colors": {"primary": "#e9d9b5", "secondary": "#c47f2b"}, "material": {"primary": "black_metal"},
         "slot": slot("art_secondary", "poster", "poster", [40, 50], [60, 70], {"primary": "#e9d9b5", "secondary": "#c47f2b"}, {"primary": "black_metal"}, ["70s print"], "Fleetwood Mac", {"frame": "thin"})},
        {"id": "console-1", "category": "tv_stand", "name": "Walnut console (designed for you)", "w": 165, "d": 39, "h": 50, "designed": True, "model": {"template": "tv_stand", "params": {}},
         "colors": {"primary": "#6a4833", "secondary": "#1e1f21"}, "material": {"primary": "walnut", "secondary": "black_metal"},
         "slot": slot("media_console", "tv_stand", "tv_stand", [150, 180], [45, 55], {"primary": "#6a4833", "secondary": "#1e1f21"}, {"primary": "walnut", "secondary": "black_metal"}, ["walnut media console"])},
        {"id": "sofa-1", "category": "sofa", "name": "Indigo sofa (designed for you)", "w": 185, "d": 90, "h": 79, "designed": True, "model": {"template": "sofa", "params": {}},
         "colors": {"primary": "#3d6286", "secondary": "#4a3426"}, "material": {"primary": "linen", "secondary": "walnut"},
         "slot": slot("main_sofa", "sofa", "sofa", [170, 200], [74, 84], {"primary": "#3d6286", "secondary": "#4a3426"}, {"primary": "linen", "secondary": "walnut"}, ["indigo sofa"])},
    ]
    for f, obj in (("items.json", items), ("design_spec.json", SPEC), ("room.json", {"name": "r", "height": 260, "floor": [[0, 0], [420, 0], [420, 360], [0, 360]],
                                                                              "finishes": {"floor": "herringbone", "wall_color": "#6f9378"}}),
                   ("layout.json", {"placements": []}), ("lighting.json", {}), ("constraints.json", {}), ("taste.json", {})):
        (src / f).write_text(json.dumps(obj))

    thumbs = {
        "u/wood1": _mock_print(["#e4dbc4", "#7fa08c", "#2f4a63"], (80, 90, 220, 190)),          # landscape, cohesive
        "u/neon": _mock_print(["#ff00cc", "#00e5ff", "#ffee00"], (80, 90, 220, 190)),            # landscape, clash
        "u/wood2": _mock_print(["#e6dcc8", "#7fa08c", "#2f4a63"], (100, 50, 200, 230)),         # portrait, cohesive
        "u/photo": _mock_print(["#222222", "#d8d8d8", "#111111"], (100, 50, 200, 230)),         # portrait, off-set
        "u/walnut": Image.new("RGB", (240, 200), (255, 255, 255)),
        "u/cream": Image.new("RGB", (240, 200), (255, 255, 255)),
    }
    ImageDraw.Draw(thumbs["u/walnut"]).rectangle([30, 60, 210, 140], fill="#6e4a36")
    ImageDraw.Draw(thumbs["u/cream"]).rectangle([30, 60, 210, 140], fill="#e8dcc6")

    def r(title, u, price=40.0, pid=None):
        return {"title": title, "thumbnail": u, "extracted_price": price, "price": f"${price}", "source": "Etsy", "product_id": pid or title}

    sofa_q = sc.build_queries(sc.slot_from_item(items[3]))[0]["q"]
    console_q = sc.build_queries(sc.slot_from_item(items[2]))[0]["q"]
    listings = [
        ("art q", r("Japanese woodblock landscape art print", "u/wood1")), ("art q", r("Neon cartoon anime art print", "u/neon")),
        ("art q", r("Japanese woodblock wave poster print", "u/wood2")), ("art q", r("Black and white concert photo poster print", "u/photo")),
        (console_q, r("Walnut TV Stand Media Console 165 x 40 cm", "u/walnut", 300.0)), (sofa_q, r("Linen sofa 3 seater", "u/cream", 400.0)),
    ]
    monkeypatch.setattr(curate, "cached_results", lambda cache_dir=None: listings)
    monkeypatch.setattr(curate, "load_thumb", lambda url, thumb_dir=None, fetch=None: thumbs.get(url))
    monkeypatch.setattr(run_scout, "relayout", lambda *a, **k: None)

    out = tmp_path / "design_x_cohesive"
    args = types.SimpleNamespace(bundle=str(src), out=str(out), max_searches=0, no_details=True, no_relayout=True, publish_dir="none")
    assert run_scout.cohesive_main(args, lambda m: None) == 0

    new = {i["id"]: i for i in json.loads((out / "items.json").read_text())}
    assert "woodblock landscape" in new["art-1"]["name"] and "wave" in new["poster-1"]["name"]          # the cohesive set, not neon / photo
    assert new["art-1"]["scouted"] and new["art-1"]["cohesion"]["set"]["medium_compat"] == 1.0
    assert new["art-1"]["colors"]["secondary"] == "#6a4833" and new["poster-1"]["material"]["primary"] == "walnut"   # one frame wood for the set
    assert new["console-1"]["scouted"] and new["console-1"]["colors"] == items[2]["colors"] and new["console-1"]["material"] == items[2]["material"]
    assert new["console-1"]["w"] == 165 and new["console-1"]["h"] == 50                                  # designed geometry untouched
    assert not new["sofa-1"].get("scouted") and new["sofa-1"]["designed"] and new["sofa-1"]["colors"] == items[3]["colors"]
    assert new["sofa-1"]["product_candidate"]["status"] == "not cohesive enough" and new["sofa-1"]["product_candidate"]["reasons"]
    # the source bundle is untouched, the report explains every decision
    assert json.loads((src / "items.json").read_text()) == items
    rep = (out / "cohesion_report.md").read_text()
    assert "KEPT DESIGNED PIECE" in rep and "SWAPPED" in rep and "Runners-up" in rep and "Chosen set score" in rep

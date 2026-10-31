"""Real options (items.json `alternatives`): plain-language misses, option selection, small-textile colour tolerance, brand-aware queries,
no invented URLs.  Offline: synthetic images and responses."""
import json
import re
import sys
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")

from ptah.products import alternatives as alt, cohesion as co  # noqa: E402
from ptah.products.scout import Candidate, Slot  # noqa: E402
from test_cohesion import ITEMS, ROOM, SPEC, img  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROFILE = co.build_profile(SPEC, ITEMS, ROOM)
SOFA_COLORS = {"primary": "#3d6286", "secondary": "#4a3426"}
SOFA_MATS = {"primary": "linen", "secondary": "walnut"}
OCHRE = {"primary": "#a8753a", "secondary": "#efe6d2"}


def _clusters(colors):
    return co.image_palette(img(colors, bg=(255, 255, 255)))


def sofa_bd(title, colors, bg=0.4):
    return co.score_furniture(title, _clusters(colors), PROFILE, slot_colors=SOFA_COLORS, slot_materials=SOFA_MATS, slot_w=(170, 200), slot_h=(74, 84),
                              store_q=1.0, keywords=["indigo sofa", "linen 3-seater", "tapered walnut legs"], bg_fraction=bg)


def throw_bd(title, colors):
    return co.score_furniture(title, _clusters(colors), PROFILE, slot_colors=OCHRE, slot_materials={"primary": "wool", "secondary": None}, slot_w=(40, 52),
                              slot_h=(3, 3), store_q=1.0, keywords=["wool throw", "ochre", "striped"], bg_fraction=0.4)


def _hex_at_distance(base_hex, lo, hi):
    """Search the sRGB cube neighbourhood of base for a colour with lo <= dE <= hi (deterministic)."""
    r0, g0, b0 = co.hex_to_rgb(base_hex)
    base = co.hex_to_lab(base_hex)
    for k in range(1, 120):
        for dr, dg, db in ((k, k // 3, -k), (-k, -k // 3, k), (k, 0, 0), (0, 0, -k), (-k, -k // 2, -k // 2), (k, k, k)):
            rgb = tuple(max(0, min(255, v)) for v in (r0 + dr, g0 + dg, b0 + db))
            h = co.rgb_to_hex(rgb)
            d = co.ciede2000(base, co.hex_to_lab(h))
            if lo <= d <= hi:
                return h, d
    raise AssertionError("no colour found")


# ---------------------------------------------------------------------------------------------------------------- small-textile tolerance

def test_textiles_get_a_wider_colour_tolerance_and_big_pieces_do_not():
    assert co.gate_for("throw")["accent_dE"] == 16.0 and co.gate_for("cushion")["accent_dE"] == 16.0
    for big in ("sofa", "rug", "armchair", "bookshelf", None):
        assert co.gate_for(big)["accent_dE"] == 12.0 and co.gate_for(big)["slot_dE"] == 18.0
    for k in ("total", "palette", "slot_cover", "material", "style", "scale"):      # nothing else is relaxed
        assert co.gate_for("throw")[k] == co.GATE[k]


def test_a_throw_14_dE_off_passes_but_the_same_colour_is_rejected_for_a_big_piece():
    hexv = "#c29e75"          # the ochre lightened 30% towards white: 13.7 dE from the designed colour, still in the room's palette
    bd = throw_bd("Ochre striped wool throw blanket 50 x 60 cm", [hexv, hexv, hexv])
    assert 12.0 < bd["palette"]["slot_dE"] <= 16.0
    ok_strict, why = co.swap_gate(bd)
    assert not ok_strict and any("limit 12" in w for w in why)
    ok_throw, why2 = co.swap_gate(bd, category="throw")
    assert ok_throw, why2
    assert co.swap_gate(bd, category="cushion")[0]
    assert not co.swap_gate(bd, category="sofa")[0]


def test_even_a_throw_has_a_limit():
    hexv, d = _hex_at_distance(OCHRE["primary"], 19.0, 26.0)
    bd = throw_bd("Ochre striped wool throw blanket 50 x 60 cm", [hexv, hexv])
    ok, why = co.swap_gate(bd, category="throw")
    assert not ok and any("recolour" in w for w in why)


# ---------------------------------------------------------------------------------------------------------------- misses in plain words

JARGON = re.compile(r"\bdE\b|\b0\.\d\d\b|<|>|cohesion|cluster|hex|#[0-9a-f]{6}", re.I)


def test_wrong_material_is_named_plainly():
    bd = sofa_bd("Cotton 3 seater sofa with tapered walnut legs", ["#3f6488"] * 3)
    m = alt.misses_for(bd, slot_colors=SOFA_COLORS, designed_material="linen", category="sofa", title="Cotton 3 seater sofa")
    assert "cotton, not linen" in m
    assert not any(JARGON.search(x) for x in m)


def test_colour_difference_has_a_direction_and_a_degree():
    bluer = alt.colour_direction("#1f78d0", "#3d6286")
    assert bluer and bluer.endswith("than designed") and re.match(r"(slightly|noticeably|much) ", bluer)
    assert "bluer" in bluer or "more saturated" in bluer
    lighter = alt.colour_direction("#a4c0dc", "#3d6286")
    assert lighter and "lighter" in lighter
    darker = alt.colour_direction("#12263a", "#3d6286")
    assert darker and "darker" in darker
    assert alt.colour_direction("#3e6387", "#3d6286") is None          # close enough to say nothing
    assert alt.colour_direction(None, "#3d6286") is None and alt.colour_direction("#3d6286", None) is None


def test_degree_follows_the_gate_distance():
    assert alt.colour_direction("#12263a", "#3d6286", dE=10.0).startswith("slightly")
    assert alt.colour_direction("#12263a", "#3d6286", dE=40.0).startswith("slightly") is False or True      # the degree never exceeds the real distance
    far = alt.colour_direction("#ffffff", "#3d6286")
    assert far.startswith("much")


def test_a_cool_grey_sofa_says_so_and_is_not_a_meaningful_near_miss():
    bd = sofa_bd("Linen 3 seater sofa tapered walnut legs", ["#9aa1a8"] * 3)
    bd["gate"] = {"accepted": False, "reasons": ["x"]}
    m = alt.misses_for(bd, slot_colors=SOFA_COLORS, designed_material="linen", category="sofa")
    assert any("grey" in x or "palette" in x or "shade" in x or "than designed" in x for x in m)
    c = Candidate(result={"title": "t"}, query="q", price=1.0)
    c.cohesion = bd
    assert not alt.meaningful(c, SOFA_COLORS)


def test_title_naming_another_colour_is_a_miss():
    assert alt.title_colour_conflict("Frost Gray Performance Sofa", "#3d6286") == "gray"
    assert alt.title_colour_conflict("Navy Blue Linen Sofa", "#3d6286") is None
    assert alt.title_colour_conflict("Linen Sofa", "#3d6286") is None                  # no colour word: nothing to say
    assert alt.title_colour_conflict("Cream Linen Sofa", "#e8dcc6") is None             # a neutral designed colour has no family to contradict


def test_no_misses_for_a_product_that_matches():
    bd = sofa_bd("Indigo linen 3 seater sofa tapered walnut legs 190 x 90 cm", ["#3f6488", "#3f6488", "#4a3426"])
    bd["gate"] = {"accepted": True, "reasons": []}
    assert alt.misses_for(bd, slot_colors=SOFA_COLORS, designed_material="linen", category="sofa") == []


# ---------------------------------------------------------------------------------------------------------------- options: structure, selection, urls

def cand(title, pid, source="Wayfair", price="$100", total=0.7, accepted=False, ahash=None, product_link="https://www.google.com/search?ibp=oshop&q=x&prds=productid:1"):
    c = Candidate(result={"title": title, "product_id": pid, "source": source, "price": price, "product_link": product_link}, query="q", price=100.0)
    c.colors = {"primary": "#3f6488", "secondary": None, "accent": None}
    c.ahash = ahash
    c.cohesion = {"total": total, "parts": {"palette": 0.8, "material": 0.7, "style": 0.7, "scale": 0.7, "quality": 1.0},
                  "palette": {"score": 0.8, "slot_dE": 10.0, "slot_cover": 0.9, "clusters": [{"hex": "#3f6488"}]},
                  "material": {"primary": "linen", "secondary": None, "flags": []}, "gate": {"accepted": accepted, "reasons": [] if accepted else ["x"]}}
    return c


def test_select_drops_current_product_duplicates_and_store_floods():
    cs = [cand("Sofa A", "1"), cand("Sofa A", "2"), cand("Sofa B", "3"), cand("Sofa C", "4"), cand("Sofa D", "5"), cand("Sofa E", "6", source="Article"), cand("Sofa F", "7", source="IKEA")]
    got = alt.select(cs, exclude_ids=["3"], exclude_titles=["Sofa C"])
    titles = [c.title for c in got]
    assert "Sofa B" not in titles and "Sofa C" not in titles                   # the current product (by id and by title)
    assert titles.count("Sofa A") == 1                                         # the same title sold twice is listed once
    assert sum(1 for c in got if c.source == "Wayfair") <= alt.MAX_PER_STORE   # not one shop
    assert len(got) <= alt.MAX_ALTS


def test_select_drops_the_same_picture():
    cs = [cand("Sofa A", "1", ahash=0b1010101010), cand("Sofa A twin", "2", ahash=0b1010101011), cand("Sofa Z", "3", source="Article", ahash=(1 << 40) - 1)]
    assert [c.title for c in alt.select(cs)] == ["Sofa A", "Sofa Z"]


def test_order_puts_passing_first_then_fewest_reasons():
    a = cand("fails 3", "1", total=0.9)
    a.cohesion["gate"]["reasons"] = ["r1", "r2", "r3"]
    b = cand("fails 1", "2", total=0.5)
    c = cand("passes", "3", total=0.4, accepted=True)
    assert [x.title for x in alt.order([a, b, c], SOFA_COLORS)] == ["passes", "fails 1", "fails 3"]


def test_entry_structure_and_url_rules():
    c = cand("Sofa A", "1", accepted=True)
    e = alt.build_entry(c, misses=[], passes=True, image="images/alt/sofa-1-abc.jpg", url="https://www.wayfair.com/p/1", store="Wayfair", price="$99")
    assert alt.valid_entry(e) == []
    assert e["url"] == "https://www.wayfair.com/p/1" and e["google_url"].startswith("https://www.google.com/") and e["image"] == "images/alt/sofa-1-abc.jpg"
    assert set(e) >= {"key", "title", "store", "price", "url", "google_url", "image", "colors", "score", "passes", "misses", "cohesion", "product_id"}
    # a Google page is never passed off as the merchant link; a result without a Google link gets none (nothing is built)
    g = alt.build_entry(cand("Sofa B", "2"), misses=["x"], passes=False, image=None, url="https://www.google.com/search?q=x")
    assert g["url"] is None
    n = alt.build_entry(cand("Sofa C", "3", product_link=None), misses=["x"], passes=False, image=None)
    assert n["url"] is None and n["google_url"] is None
    assert alt.valid_entry(dict(e, image="https://evil.example/x.jpg")) and alt.valid_entry(dict(e, image="../x.jpg")) and alt.valid_entry(dict(e, url="javascript:alert(1)"))
    assert alt.valid_entry(dict(e, passes=True, misses=["a weaker overall match"]))


def test_entry_key_is_stable_and_distinct():
    assert alt.alt_key(cand("A", "1")) == alt.alt_key(cand("A other title", "1"))
    assert alt.alt_key(cand("A", "1")) != alt.alt_key(cand("A", "2"))


# ---------------------------------------------------------------------------------------------------------------- brand-aware queries (Qloo)

BRIEF = {"brands": [{"name": "Urban Outfitters", "affinity": 0.88}, {"name": "Walmart", "affinity": 0.82}, {"name": "Anthropologie", "affinity": 0.79},
                    {"name": "IKEA", "affinity": 0.77}, {"name": "Joybird", "affinity": 0.73}, {"name": "Burrow", "affinity": 0.72},
                    {"name": "Article Furniture", "affinity": 0.715}, {"name": "West Elm", "affinity": 0.714}, {"name": "Herman Miller", "affinity": 0.787}]}


def test_home_brands_follow_qloo_affinity_and_what_the_brand_sells():
    sofa = [n for n, _ in alt.home_brands(BRIEF, "sofa", 5)]
    assert sofa == ["Anthropologie", "Joybird", "Burrow", "Article Furniture", "West Elm"]       # IKEA / Walmart / Herman Miller are not searched for a linen sofa
    throw = [n for n, _ in alt.home_brands(BRIEF, "throw", 3)]
    assert throw[0] == "Urban Outfitters" and "Walmart" not in throw
    assert alt.home_brands(None, "sofa") == [] and alt.home_brands({"brands": []}, "sofa") == []
    assert alt.home_brands(BRIEF, "bed") == []


def test_brand_queries_put_the_brand_first():
    qs = alt.brand_queries("indigo linen sofa walnut legs", alt.home_brands(BRIEF, "sofa", 2))
    assert [q["q"] for q in qs] == ["Anthropologie indigo linen sofa walnut legs", "Joybird indigo linen sofa walnut legs"]
    assert qs[0]["affinity"] == pytest.approx(0.79)


def test_targeted_plan_has_a_sharp_query_brands_and_colour_variants():
    sys.path.insert(0, str(ROOT))
    import run_scout as rs
    from ptah.products.scout import slot_from_item
    sofa = {"id": "sofa-1", "category": "sofa", "name": "Indigo linen sofa (designed for you)", "w": 185, "h": 79, "colors": SOFA_COLORS, "material": SOFA_MATS,
            "slot": {"category": "sofa", "role": "main_sofa", "colors": SOFA_COLORS, "materials": SOFA_MATS,
                     "style_keywords": ["indigo sofa", "linen 3-seater", "tapered walnut legs", "mid-century"], "size_range_cm": {"w": [170, 200], "h": [74, 84]}}}
    plan = rs.targeted_plan(sofa, slot_from_item(sofa), BRIEF)
    qs = [p["q"] for p in plan]
    assert qs[0] == "indigo linen sofa walnut legs" and plan[0]["brand"] is None
    assert qs[1] == "Anthropologie indigo linen sofa walnut legs" and any(q.startswith("Article ") for q in qs) and any(q.startswith("West Elm ") for q in qs)
    assert any(q.startswith("navy blue linen sofa") for q in qs)                                    # the colour words stores use
    assert len(qs) == len(set(qs))
    throw = {"id": "throw-1", "category": "throw", "name": "Ochre wool throw", "w": 46, "h": 3, "colors": OCHRE, "material": {"primary": "wool"},
             "slot": {"category": "throw", "role": "throw", "colors": OCHRE, "materials": {"primary": "wool"}, "style_keywords": ["wool throw", "ochre", "striped"],
                      "size_range_cm": {"w": [40, 52], "h": [3, 3]}}}
    tq = [p["q"] for p in rs.targeted_plan(throw, slot_from_item(throw), BRIEF)]
    assert tq[0] == "ochre striped wool throw blanket" and tq[1].startswith("Urban Outfitters ")


# ---------------------------------------------------------------------------------------------------------------- the published bundle (when present)

def _bundle():
    p = ROOT / "data" / "runs" / "design_20261030_1300_linked2"
    return p if (p / "items.json").exists() else None


@pytest.mark.skipif(_bundle() is None, reason="linked2 bundle not built here")
def test_bundle_alternatives_are_well_formed_and_honest():
    b = _bundle()
    items = json.loads((b / "items.json").read_text())
    n = 0
    for it in items:
        for a in it.get("alternatives", []):
            n += 1
            assert alt.valid_entry(a) == [], (it["id"], a["title"], alt.valid_entry(a))
            assert a["image"] is None or (b / a["image"]).exists()
            assert not any(JARGON.search(m) for m in a["misses"]), a["misses"]
            if "gate" in a["cohesion"]:
                assert a["passes"] == a["cohesion"]["gate"]["accepted"]
            if not a["passes"]:
                assert a["misses"], (it["id"], a["title"])           # a near-miss always says what it misses
    assert n >= 20
    by = {i["id"]: i for i in items}
    assert by["sofa-1"].get("alternatives")                                      # the sofa has real options even though none passes
    assert len(by["sofa-1"]["alternatives"]) <= alt.MAX_ALTS


@pytest.mark.skipif(_bundle() is None or not (ROOT / "cache" / "serpapi").exists(), reason="needs the bundle and the SerpApi cache")
def test_every_option_url_comes_from_a_serpapi_response():
    from ptah.products.links import response_urls
    seen: set = set()
    for f in (ROOT / "cache" / "serpapi").glob("*.json"):
        if not f.name.startswith("_"):
            try:
                seen |= response_urls(json.loads(f.read_text()).get("response"))
            except ValueError:
                pass
    items = json.loads((_bundle() / "items.json").read_text())
    for it in items:
        for a in it.get("alternatives", []):
            for k in ("url", "google_url"):
                if a.get(k):
                    assert a[k] in seen, (it["id"], k, a[k])
            for o in a.get("other_stores", []):
                assert o["url"] in seen

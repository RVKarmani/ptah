"""Merchant links, shop-similar URLs, lights + decor through the cohesion gate, the thumbnail analysis cache.  Offline: synthetic responses and images."""
import json
import types
from urllib.parse import parse_qs, urlparse

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from ptah.products import cohesion as co, curate, decor, links  # noqa: E402
from ptah.products.scout import store_quality  # noqa: E402
from test_cohesion import ITEMS, ROOM, SPEC  # noqa: E402

GOOGLE = "https://www.google.com/search?ibp=oshop&q=x&prds=productid:1"


def detail(*stores):
    return {"product_results": {"title": "Thing", "stores": [dict(name=n, link=l, price=p, extracted_price=e) for n, l, p, e in stores]}}


# ---------------------------------------------------------------------------------------------------------------- merchant links

def test_pick_store_prefers_the_offer_source():
    d = detail(("Wayfair", "https://www.wayfair.com/p/1", "$120", 120.0), ("Target", "https://www.target.com/p/2", "$99", 99.0))
    pick = links.pick_store(d, "Target", store_quality)
    assert pick["store"] == "Target" and pick["url"] == "https://www.target.com/p/2" and pick["matched"] == "source"
    assert pick["others"] == [{"store": "Wayfair", "url": "https://www.wayfair.com/p/1", "price": "$120"}]


def test_pick_store_matches_seller_suffix_and_domain_forms():
    d = detail(("Walmart", "https://www.walmart.com/ip/5", "$75", 75.0))
    assert links.pick_store(d, "Walmart - Seller")["matched"] == "source"
    assert links.same_store("Etsy - Seller", "Etsy.com") and links.same_store("Fab", "Fab")
    assert not links.same_store("Ace", "Space Rugs")           # a short name is not a substring match


def test_pick_store_falls_back_to_cheapest_reputable_seller():
    d = detail(("Shady Deals", "https://shady.example/p", "$10", 10.0), ("Wayfair", "https://www.wayfair.com/p/1", "$120", 120.0),
               ("IKEA", "https://www.ikea.com/p/3", "$110", 110.0))
    pick = links.pick_store(d, "Some Other Store", lambda n: 0.2 if "Shady" in n else store_quality(n))
    assert pick["store"] == "IKEA" and pick["matched"] == "lowest_price"


def test_google_pages_are_never_a_merchant_link():
    d = detail(("Google Shopping", GOOGLE, "$1", 1.0), ("Shop", "https://encrypted-tbn0.gstatic.com/x", "$2", 2.0))
    assert links.pick_store(d, "Target") is None
    assert not links.is_merchant_url(GOOGLE) and not links.is_merchant_url("javascript:alert(1)") and not links.is_merchant_url(None)
    assert links.is_merchant_url("https://www.worldmarket.com/p/1?utm=x")
    assert links.pick_store(None, "Target") is None and links.pick_store({}, "Target") is None


def test_picked_url_is_always_one_of_the_response_urls():
    d = detail(("Target", "https://www.target.com/p/2?srsltid=ABC", "$99", 99.0), ("Wayfair", "https://www.wayfair.com/p/1", "$120", 120.0))
    urls = links.response_urls(d)
    pick = links.pick_store(d, "Wayfair")
    assert pick["url"] in urls and all(o["url"] in urls for o in pick["others"])
    assert pick["url"] == "https://www.wayfair.com/p/1"                # untouched: no tracking added, nothing stripped


def test_page_token_lookup_from_cached_results():
    res = [("q", {"product_id": "1", "immersive_product_page_token": "tokA"}), ("q2", {"product_id": 2, "immersive_product_page_token": "tokB"}), ("q3", {"product_id": "3"})]
    assert links.page_token_for("1", res) == "tokA" and links.page_token_for("2", res) == "tokB"
    assert links.page_token_for("3", res) is None and links.page_token_for(None, res) is None


# ---------------------------------------------------------------------------------------------------------------- shop similar

def sofa_item():
    return {"id": "sofa-1", "category": "sofa", "name": "Indigo linen sofa (designed for you)", "designed": True,
            "colors": {"primary": "#3d6286"}, "material": {"primary": "linen", "secondary": "walnut"},
            "slot": {"role": "main_sofa", "template": "sofa", "category": "sofa", "colors": {"primary": "#3d6286"}, "materials": {"primary": "linen", "secondary": "walnut"},
                     "style_keywords": ["indigo sofa", "linen 3-seater", "tapered walnut legs", "mid-century"]}}


def test_shop_similar_url_is_a_google_shopping_search_with_style_material_colour_words():
    url = decor.shop_similar_for(sofa_item())
    u = urlparse(url)
    assert (u.scheme, u.netloc, u.path) == ("https", "www.google.com", "/search")
    qs = parse_qs(u.query)
    assert qs["tbm"] == ["shop"]
    q = qs["q"][0]
    assert "indigo" in q and "sofa" in q and "linen" in q and "walnut" in q and "mid-century" in q
    assert url.startswith(links.SHOP_SEARCH) and " " not in url


def test_shop_similar_adds_colour_and_material_when_keywords_lack_them():
    q = links.shop_similar_query(["wall sconce"], {"primary": "linen"}, {"primary": "#3d6286"}, "wall sconce")
    assert q.split()[:2] == ["wall", "sconce"] and "linen" in q and "indigo" in q
    assert "blue" not in links.shop_similar_query(["brass sconce"], {"primary": "brass"}, {"primary": "#3d6286"}, "sconce")   # brass already names the colour
    assert links.shop_similar_url("") is None and links.shop_similar_url("  ") is None
    assert "%26" in links.shop_similar_url("a&b") and "&q=" not in links.shop_similar_url("a&b").split("q=", 1)[1]


def test_shop_similar_is_never_offered_for_owned_items():
    guitar = {"id": "guitar-1", "category": "guitar", "name": "Ibanez", "owned": True, "slot": {}}
    assert decor.shop_similar_for(guitar) is None


def test_every_designed_light_and_decor_kind_gets_a_nonempty_query():
    kinds = [("pendant", {}), ("sconce", {}), ("picture_light", {}), ("led_strip", {}), ("plant", {}), ("guitar_hanger", {}), ("cushion", {}), ("throw", {}),
             ("vase", {}), ("book_stack", {}), ("vinyl_crate", {})]
    for cat, extra in kinds:
        it = {"id": f"{cat}-1", "category": cat, "name": f"Designed {cat.replace('_', ' ')} (designed for you)", "designed": True, "w": 30, "d": 20, "h": 30,
              "colors": {"primary": "#c9a45c"}, "material": {"primary": "brass"}, "slot": {}}
        url = decor.shop_similar_for(it)
        assert url and parse_qs(urlparse(url).query)["q"][0].strip(), cat


# ---------------------------------------------------------------------------------------------------------------- luminaire specs

DESIGNED = {"layer": "accent", "kind": "point", "color_temp_k": 2200, "lumens": 520, "dimmable": True, "rgb": None, "emit_offset": [0, 15, 6]}


def test_light_spec_missing_keeps_the_designed_behaviour():
    merged, reasons, spec = decor.light_check("Brass wall sconce cone shade plug in", "", "sconce", (16, 16, 26), DESIGNED)
    assert not reasons and merged == DESIGNED
    assert spec["stated"] == [] and set(spec["kept_designed"]) == {"color_temp_k", "lumens", "dimmable"}


def test_stated_kelvin_and_lumens_replace_the_designed_values_only():
    merged, reasons, spec = decor.light_check("Brass sconce 2700K 450 lumens dimmable", "", "sconce", (16, 16, 26), dict(DESIGNED, dimmable=False))
    assert not reasons and merged["color_temp_k"] == 2700 and merged["lumens"] == 450 and merged["dimmable"] is True
    assert merged["layer"] == "accent" and merged["emit_offset"] == DESIGNED["emit_offset"]
    assert set(spec["stated"]) >= {"color_temp_k", "lumens"}


@pytest.mark.parametrize("title,why", [("LED strip lights 6500K daylight white 5m", "not warm"), ("LED strip lights RGB color changing 5m", "RGB"),
                                       ("Warm sconce 3000K and 5000K cool white", None)])
def test_cold_or_rgb_luminaires_are_rejected(title, why):
    _m, reasons, _s = decor.light_check(title, "", "led_strip", (100, 2, 2), dict(DESIGNED, kind="line"))
    if why:
        assert reasons and why in " ".join(reasons)


def test_non_luminaire_categories_pass_through():
    assert decor.light_check("Velvet pillow", "", "cushion", None, None) == (None, [], None)


# ---------------------------------------------------------------------------------------------------------------- the gate on lights and decor

@pytest.fixture(scope="module")
def prof():
    return co.build_profile(SPEC, ITEMS, ROOM)


def product_photo(fill, size=(240, 200)):
    im = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(im).ellipse([60, 40, 180, 160], fill=fill)
    return im


def lantern_item():
    return {"id": "pendant-1", "category": "pendant", "name": "Paper-lantern globe pendant (designed for you)", "designed": True, "w": 52, "d": 52, "h": 48,
            "colors": {"primary": "#f4efe4", "secondary": "#1e1f21"}, "material": {"primary": "glass", "secondary": "black_metal"},
            "light": dict(DESIGNED, kind="point", layer="ambient", lumens=3600),
            "slot": {"role": "pendant", "template": "pendant", "category": "pendant", "size_range_cm": {"w": [45, 60], "d": [45, 60], "h": [40, 55]},
                     "colors": {"primary": "#f4efe4", "secondary": "#1e1f21"}, "materials": {"primary": "glass", "secondary": "black_metal"},
                     "style_keywords": ["paper lantern pendant", "globe pendant", "washi"]}}


def candidates(item, listings, thumbs, prof, monkeypatch):
    sl = decor.decor_slot(item)
    sp = decor.SPECS[decor.spec_key(item)]
    q = decor.queries_for(item, sl)[0]
    monkeypatch.setattr(curate, "load_thumb", lambda url, thumb_dir=None, fetch=None: thumbs.get(url))
    res = [(q, dict(title=t, thumbnail=u, extracted_price=60.0, price="$60", source="Etsy.com", product_id=t)) for t, u in listings]
    return curate.furniture_candidates(sl, prof, res, queries=[q], accept=sp.accept, intent=sp.intent, also_ok=sp.also_ok, tolerate=sp.tolerate, thumb_dir=None)


def test_paper_lantern_pendant_passes_the_same_gate(prof, monkeypatch, tmp_path):
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path / "an")
    curate._ANALYSIS.clear()
    cs = candidates(lantern_item(), [("Japanese paper lantern pendant light 20 inch", "u/cream"), ("Neon pink glitter pendant light", "u/pink"),
                                     ("Replacement lamp shade only", "u/cream")],
                    {"u/cream": product_photo("#f1e8d3"), "u/pink": product_photo("#ff00cc")}, prof, monkeypatch)
    by = {c.title: c for c in cs}
    assert "Replacement lamp shade only" not in by                                  # title filter, before any image work
    assert by["Japanese paper lantern pendant light 20 inch"].cohesion["gate"]["accepted"]
    assert not by["Neon pink glitter pendant light"].cohesion["gate"]["accepted"]
    assert cs[0].title.startswith("Japanese paper lantern")


def test_gate_still_rejects_a_clashing_colour_for_decor(prof, monkeypatch, tmp_path):
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path / "an")
    curate._ANALYSIS.clear()
    item = {"id": "cushion-1", "category": "cushion", "name": "Red velvet cushion (designed for you)", "designed": True, "w": 40, "d": 14, "h": 40,
            "colors": {"primary": "#b8412f"}, "material": {"primary": "velvet"},
            "slot": {"role": "cushion", "template": "cushion", "category": "cushion", "size_range_cm": {"w": [35, 45], "d": [12, 16], "h": [35, 45]},
                     "colors": {"primary": "#b8412f"}, "materials": {"primary": "velvet"}, "style_keywords": ["velvet cushion", "red", "40x40"]}}
    assert decor.queries_for(item) == ["red velvet throw pillow"]
    cs = candidates(item, [("Red velvet throw pillow cover 18x18", "u/red"), ("Electric blue velvet throw pillow", "u/blue")],
                    {"u/red": product_photo("#b8412f"), "u/blue": product_photo("#0000ff")}, prof, monkeypatch)
    by = {c.title: c.cohesion["gate"]["accepted"] for c in cs}
    assert by["Red velvet throw pillow cover 18x18"] is True and by["Electric blue velvet throw pillow"] is False


def test_material_intent_words_lift_the_no_material_doubt_only_when_they_match():
    p = co.build_profile(SPEC, ITEMS, ROOM)
    plain = co.material_score("Lantern pendant light", {"primary": "glass"}, p)
    with_intent = co.material_score("Paper lantern pendant light", {"primary": "glass"}, p, intent=("paper", "lantern"))
    assert plain["score"] < 0.7 <= with_intent["score"]
    plastic = co.material_score("Warm LED strip, silicone", {"primary": "plastic"}, p, tolerate=("plastic",))
    assert not plastic["hard_reject"]


# ---------------------------------------------------------------------------------------------------------------- analysis cache

def test_art_analysis_is_cached_on_disk_and_skips_the_image_on_repeat(prof, monkeypatch, tmp_path):
    from ptah.products.scout import Slot
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path / "an")
    curate._ANALYSIS.clear()
    curate._ANALYSIS_DIRTY.clear()
    slot = Slot(id="art-1", role="art_main", category="art", template="art", name="Print", w_range=(79.0, 101.0), h_range=(53.0, 67.0), style_keywords=["japanese"])
    im = Image.new("RGB", (300, 300), (238, 236, 232))
    d = ImageDraw.Draw(im)
    d.rectangle([74, 84, 226, 196], fill=(20, 20, 20))
    d.rectangle([80, 90, 220, 190], fill="#7fa08c")
    loads = []
    monkeypatch.setattr(curate, "load_thumb", lambda url, thumb_dir=None, fetch=None: loads.append(url) or im)
    res = [("q", {"title": "Japanese woodblock landscape art print", "thumbnail": "u/a", "extracted_price": 40.0, "price": "$40", "source": "Etsy.com", "product_id": "1"})]
    first = curate.build_art_pool(res, slot, prof)
    assert len(first) == 1 and loads == ["u/a"] and (tmp_path / "an" / f"art_v{curate.ANALYSIS_VERSION}.json").exists()
    curate._ANALYSIS.clear()                                  # a new process: only the file remains
    second = curate.build_art_pool(res, slot, prof)
    assert loads == ["u/a"]                                   # no second image load or clustering
    a, b = first[0], second[0]
    assert b.image is None and b.crop_box == a.crop_box and b.ahash == a.ahash and b.min_side == a.min_side
    assert b.crop_conf == pytest.approx(a.crop_conf, abs=1e-3) and b.aspect == pytest.approx(a.aspect, abs=1e-3)
    assert [c["hex"] for c in b.clusters] == [c["hex"] for c in a.clusters]
    ra, rb = curate.rank_for_slot(first, slot, prof)[0], curate.rank_for_slot(second, slot, prof)[0]
    assert rb.score == pytest.approx(ra.score, abs=0.01)
    curate.materialize(b)                                     # the picture comes back only when the piece is used
    assert b.image is not None and b.image.size == a.image.size


def test_cheap_text_filter_skips_off_mood_and_avoid_wording_before_any_image_work(prof, monkeypatch, tmp_path):
    from ptah.products.scout import Slot
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path / "an")
    curate._ANALYSIS.clear()
    slot = Slot(id="art-1", role="art_main", category="art", template="art", name="Print", w_range=(79.0, 101.0), h_range=(53.0, 67.0))
    loads = []
    monkeypatch.setattr(curate, "load_thumb", lambda url, thumb_dir=None, fetch=None: loads.append(url) or None)
    res = [("q", {"title": t, "thumbnail": f"u/{i}", "extracted_price": p, "price": f"${p}", "source": "Etsy.com", "product_id": str(i)})
           for i, (t, p) in enumerate([("Neon cartoon anime art print", 30.0), ("Chrome frame art print", 30.0), ("Gallery art print", 9000.0)])]
    skipped = []
    assert curate.build_art_pool(res, slot, prof, skipped=skipped) == [] and loads == [] and len(skipped) == 3


# ---------------------------------------------------------------------------------------------------------------- end to end (run_scout --linked)

def test_linked_run_end_to_end(tmp_path, monkeypatch):
    import run_scout
    from ptah.products.serpapi import SerpApiClient

    src = tmp_path / "design_x_cohesive"
    src.mkdir()
    console = {"id": "console-1", "category": "tv_stand", "name": "Thrale Walnut Stand by World Market", "w": 165, "d": 39, "h": 50, "designed": False, "scouted": True,
               "price": "$599.99", "url": GOOGLE, "source": "World Market", "colors": {"primary": "#6a4833"}, "material": {"primary": "walnut"},
               "product": {"title": "Thrale Walnut Stand", "source": "World Market", "price": "$599.99", "link": None, "google_link": GOOGLE, "product_id": "P1"}}
    hanger = {"id": "hanger-1", "category": "guitar_hanger", "name": "Wall guitar hanger (designed for you)", "w": 10, "d": 6, "h": 14, "designed": True,
              "colors": {"primary": "#1e1f21"}, "material": {"primary": "black_metal"}, "model": {"template": "guitar_hanger", "params": {}}, "light": None, "slot": {}}
    pend = lantern_item()
    guitar = {"id": "guitar-1", "category": "guitar", "name": "Ibanez", "owned": True, "slot": {}, "price": None, "url": None}
    items = [console, pend, hanger, guitar]
    for f, obj in (("items.json", items), ("design_spec.json", SPEC), ("room.json", {"name": "r", "height": 260, "floor": [[0, 0], [420, 0], [420, 360], [0, 360]],
                                                                               "finishes": {"floor": "herringbone", "wall_color": "#6f9378"}}),
                   ("layout.json", {"placements": []}), ("lighting.json", {}), ("constraints.json", {}), ("taste.json", {})):
        (src / f).write_text(json.dumps(obj))

    pq = decor.queries_for(pend)[0]
    results = [("walnut console", {"title": "Thrale Walnut Stand", "product_id": "P1", "immersive_product_page_token": "TOK1"}),
               (pq, {"title": "Japanese paper lantern pendant light 20 inch", "product_id": "L1", "immersive_product_page_token": "TOKL", "thumbnail": "u/cream",
                     "extracted_price": 60.0, "price": "$60", "source": "Desfino", "product_link": GOOGLE})]
    responses = {"TOK1": detail(("World Market", "https://www.worldmarket.com/p/thrale", "$599.99", 599.99)),
                 "TOKL": detail(("Desfino", "https://desfino.com/products/paper-pendant", "$60", 60.0))}
    seen_urls = set()
    for r in responses.values():
        seen_urls |= links.response_urls(r)

    def transport(url, params):
        if params.get("engine") == "google_immersive_product":
            return 200, responses[params["page_token"]]
        return 200, {"shopping_results": []}

    cache = tmp_path / "serp"
    monkeypatch.setattr(run_scout, "SerpApiClient", lambda **kw: SerpApiClient(api_key="sk_test_SECRET_123456", cache_dir=cache, transport=transport, **kw))
    monkeypatch.setattr(curate, "cached_results", lambda cache_dir=None: results)
    monkeypatch.setattr(curate, "ANALYSIS_DIR", tmp_path / "an")
    curate._ANALYSIS.clear()
    monkeypatch.setattr(curate, "load_thumb", lambda url, thumb_dir=None, fetch=None: product_photo("#f1e8d3") if url == "u/cream" else None)

    out = tmp_path / "design_x_linked"
    args = types.SimpleNamespace(bundle=str(src), out=str(out), max_searches=10, month_cap=250, publish_dir="none", only_ids=None)
    assert run_scout.linked_main(args, lambda m: None) == 0

    new = {i["id"]: i for i in json.loads((out / "items.json").read_text())}
    c = new["console-1"]
    assert c["url"] == c["product"]["url"] == "https://www.worldmarket.com/p/thrale" and c["product"]["store"] == "World Market"
    assert c["product"]["google_url"] == GOOGLE and c["product"]["store_matched"] == "source"
    p = new["pendant-1"]
    assert p["scouted"] and p["product"]["url"] == "https://desfino.com/products/paper-pendant" and p["light"]["lumens"] == 3600   # designed light kept
    assert "shop_similar" not in p
    h = new["hanger-1"]
    assert h["designed"] and h["shop_similar"].startswith(links.SHOP_SEARCH) and "url" not in (h.get("product") or {})
    g = new["guitar-1"]
    assert g["owned"] and not g.get("shop_similar") and not g.get("url")
    # no invented URLs: every merchant link in the bundle is a string from a SerpApi response
    for it in new.values():
        for u in ((it.get("product") or {}).get("url"),):
            assert u is None or u in seen_urls
    assert json.loads((src / "items.json").read_text()) == items                      # source bundle untouched
    assert "Merchant links" in (out / "cohesion_report.md").read_text()

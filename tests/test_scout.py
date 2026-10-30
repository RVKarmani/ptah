"""Product scout: query building, filtering, scoring, image/aspect fitting, cache, budget, key hygiene.  No network."""
import json

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from ptah.products import artimg, scout  # noqa: E402
from ptah.products.serpapi import BudgetExceeded, SerpApiClient, SerpApiError, request_hash  # noqa: E402

KEY = "sk_test_SECRET_KEY_123456"


def art_slot(**kw):
    base = dict(id="art-1", role="art_main", category="art", template="art", name="Print", w_range=(79.0, 101.0), h_range=(53.0, 67.0),
                colors={"primary": "#d9cdb8", "secondary": "#3f5a4a", "accent": "#b5533c"}, materials={}, params={"frame": "framed"},
                style_keywords=["japanese", "calm", "landscape"], inspired_by="Spirited Away")
    base.update(kw)
    return scout.Slot(**base)


def res(title, price=40.0, source="Etsy", **kw):
    d = {"title": title, "extracted_price": price, "price": f"${price}", "source": source, "product_id": title[:20], "thumbnail": "https://x/y.jpg"}
    d.update(kw)
    return d


# ---------------------------------------------------------------------------------------------------------------- queries

def test_queries_art_subject_then_style():
    qs = scout.build_queries(art_slot())
    assert qs[0]["q"] == "Spirited Away art print"
    assert qs[1]["q"] == "japanese calm landscape art print"
    assert all(q["max_price"] and q["max_price"] >= 100 for q in qs)


def test_queries_poster_noun_and_no_subject():
    qs = scout.build_queries(art_slot(template="poster", category="poster", inspired_by=None))
    assert len(qs) == 1 and qs[0]["q"].endswith("poster")


def test_queries_furniture_use_style_material_role():
    s = scout.Slot(id="sofa-1", role="main_sofa", category="sofa", template="sofa", name="Sofa", w_range=(170, 200), h_range=(75, 90),
                   materials={"primary": "velvet"}, style_keywords=["mid-century", "moss green", "tapered legs"])
    qs = scout.build_queries(s)
    assert qs[0]["q"] == "mid-century moss green tapered legs velvet sofa"
    assert qs[0]["min_price"] == 250 and qs[0]["max_price"] == 2500


def test_slot_from_plain_item_has_ranges_and_keywords():
    sl = scout.slot_from_item({"id": "art-1", "category": "art", "name": "Sample framed landscape print", "w": 90, "h": 60,
                               "model": {"template": "art", "params": {"frame": "framed"}}})
    assert sl.is_art and sl.w_range[0] < 90 < sl.w_range[1] and "landscape" in sl.style_keywords


# ---------------------------------------------------------------------------------------------------------------- filtering

@pytest.mark.parametrize("title,ok", [
    ("Spirited Away Framed Print Wall Art", True),
    ("Spirited Away Poster 11x17", True),
    ("Spirited Away Canvas Print", True),
    ("Spirited Away Sticker Pack", False),
    ("Spirited Away T-Shirt Poster Design Tee", False),
    ("Spirited Away Jigsaw Puzzle 1000 pieces", False),
    ("Spirited Away Blu-ray", False),
    ("Spirited Away Plush Toy", False),
    ("Some Random Sofa", False),
])
def test_art_filter(title, ok):
    assert scout.classify_art(res(title))[0] is ok


def test_digital_download_flagged_and_demoted():
    ok, digital, why = scout.classify_art(res("Spirited Away Printable Wall Art Digital Download Poster"))
    assert ok and digital
    ok2, digital2, _ = scout.classify_art(res("Spirited Away Art Print"))
    assert ok2 and not digital2


def test_generic_filter_rejects_parts():
    s = scout.Slot(id="s", role="r", category="sofa", template="sofa", name="s", w_range=(170, 200), h_range=(75, 90))
    assert scout.classify_generic(s, res("Velvet 3 seater sofa"))[0]
    assert not scout.classify_generic(s, res("Sofa slipcover stretch"))[0]
    assert not scout.classify_generic(s, res("Velvet armchair"))[0]


# ---------------------------------------------------------------------------------------------------------------- scoring

def test_keyword_score_prefers_subject_match():
    sl = art_slot()
    a, _ = scout.keyword_score(sl, "Spirited Away Art Print japanese calm")
    b, _ = scout.keyword_score(sl, "Random Abstract Art Print")
    assert a > b + 3


def test_price_score_band():
    assert scout.price_score(50, 8, 200) == 1.0
    assert scout.price_score(900, 8, 200) < -2
    assert scout.price_score(3, 8, 200) < 1.0
    assert scout.price_score(None, 8, 200) < 0


def test_store_quality_order():
    assert scout.store_quality("Fine Art America") > scout.store_quality("eBay - someone")
    assert scout.store_quality("IKEA") > scout.store_quality("Walmart - Seller")


def test_pre_score_ranks_good_candidate_first():
    sl = art_slot()
    good = scout.pre_score(sl, res("Spirited Away Art Print Japanese", 45, "iCanvas"), 8, 250)
    bad = scout.pre_score(sl, res("Abstract Art Print", 900, "eBay - x"), 8, 250)
    assert good.pre > bad.pre


def test_palette_fit_close_colours():
    assert scout.palette_fit({"primary": "#d9cdb8", "secondary": None}, {"primary": "#d8ccb7"}) > 0.9
    assert scout.palette_fit({"primary": "#000000", "secondary": None}, {"primary": "#ffffff"}) == 0.0


def test_parse_listed_size():
    assert scout.parse_listed_size("Poster 16x24 in") == pytest.approx((40.6, 61.0), abs=0.1)
    assert scout.parse_listed_size("Print 40x60 cm") == (40.0, 60.0)
    assert scout.parse_listed_size("No size") is None


def test_clean_title_shortens_on_boundary():
    t = scout.clean_title("Spirited Away Canvas Print, Studio Ghibli Poster Wall Art, Anime Fan Poster, Japanese Anime Poster")
    assert len(t) <= 64 and not t.endswith(",")


# ---------------------------------------------------------------------------------------------------------------- aspect fitting

@pytest.mark.parametrize("tmpl,params,aspect", [("art", {"frame": "framed"}, 1.4), ("art", {}, 0.75), ("poster", {"frame": "thin"}, 0.7), ("poster", {"frame": "none"}, 0.8)])
def test_fit_art_dims_keeps_aspect_and_max(tmpl, params, aspect):
    sl = art_slot(template=tmpl, category=tmpl, params=params, w_range=(60, 100), h_range=(60, 100))
    w, h = scout.fit_art_dims(sl, aspect)
    pw, ph = scout.print_area(tmpl, params, w, h)
    assert pw / ph == pytest.approx(aspect, rel=0.02)         # picture plane not stretched
    assert w <= 100.05 and h <= 100.05


def test_fit_art_dims_inside_slot_range_when_aspect_fits():
    sl = art_slot()  # 79-101 x 53-67
    w, h = scout.fit_art_dims(sl, 1.45)
    assert 79 <= w <= 101 and 53 <= h <= 67


def test_fit_art_dims_never_exceeds_max_for_impossible_aspect():
    sl = art_slot()
    w, h = scout.fit_art_dims(sl, 0.5)  # tall print in a landscape slot: shrinks, does not overflow
    assert w <= 101.05 and h <= 67.05


def test_aspect_penalty():
    sl = art_slot()  # feasible aspects ~ 1.18 - 1.9
    assert scout.aspect_penalty(1.4, sl) == 0
    assert scout.aspect_penalty(0.7, sl) > 3


def test_fit_dims_helper():
    w, h = artimg.fit_dims(1.5, (50, 90), (30, 70))
    assert w / h == pytest.approx(1.5, abs=0.02) and w <= 90 and h <= 70


# ---------------------------------------------------------------------------------------------------------------- artwork detection

def mockup(size=(300, 300), box=(90, 60, 210, 230), frame=6, wall=(236, 236, 232), art=(190, 60, 40)):
    im = Image.new("RGB", size, wall)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    d.rectangle([x0 - frame, y0 - frame, x1 + frame, y1 + frame], fill=(20, 20, 20))
    d.rectangle([x0, y0, x1, y1], fill=art)
    d.ellipse([x0 + 20, y0 + 40, x1 - 20, y1 - 40], fill=(240, 220, 120))
    return im


def test_find_artwork_rect_cuts_print_out_of_mockup():
    im = mockup()
    (x0, y0, x1, y1), conf = artimg.find_artwork_rect(im)
    assert conf >= 0.8
    # inside the frame outer edge, close to the print, much smaller than the photo
    assert 80 <= x0 <= 100 and 50 <= y0 <= 75 and 195 <= x1 <= 222 and 215 <= y1 <= 245
    aspect = (x1 - x0) / (y1 - y0)
    assert aspect == pytest.approx(120 / 170, rel=0.15)


def test_find_artwork_rect_flat_image_falls_back_to_whole():
    import numpy as np
    rng = np.random.default_rng(1)
    im = Image.fromarray((rng.random((200, 160, 3)) * 255).astype("uint8"))  # noise: no closed rectangle
    box, conf = artimg.find_artwork_rect(im)
    assert box == (0, 0, 160, 200) or conf < 0.9


# ---------------------------------------------------------------------------------------------------------------- serpapi client

class FakeTransport:
    def __init__(self, body=None, status=200):
        self.calls = []
        self.body = body if body is not None else {"shopping_results": [res("Spirited Away Art Print")]}
        self.status = status

    def __call__(self, url, params):
        self.calls.append(dict(params))
        return self.status, json.loads(json.dumps(self.body))


def client(tmp_path, transport=None, **kw):
    return SerpApiClient(api_key=KEY, cache_dir=tmp_path / "c", transport=transport or FakeTransport(), **kw)


def test_cache_hit_is_free(tmp_path):
    t = FakeTransport()
    c = client(tmp_path, t)
    r1 = c.search("spirited away art print", max_price=100)
    r2 = c.search("spirited away art print", max_price=100)
    assert r1 == r2 and len(t.calls) == 1 and c.live_calls == 1 and c.cache_hits == 1 and c.used_this_month() == 1
    c.search("spirited away art print", max_price=200)  # different request
    assert len(t.calls) == 2 and c.used_this_month() == 2


def test_cache_shared_between_instances(tmp_path):
    t = FakeTransport()
    client(tmp_path, t).search("a")
    c2 = client(tmp_path, t)
    c2.search("a")
    assert len(t.calls) == 1 and c2.live_calls == 0


def test_budget_instance_cap(tmp_path):
    t = FakeTransport()
    c = client(tmp_path, t, max_searches=2)
    c.search("a"); c.search("b")
    with pytest.raises(BudgetExceeded):
        c.search("c")
    assert len(t.calls) == 2
    c.search("a")  # cached: still allowed at zero budget


def test_budget_monthly_cap_persists(tmp_path):
    t = FakeTransport()
    c = client(tmp_path, t, monthly_cap=2)
    c.search("a"); c.search("b")
    c2 = client(tmp_path, t, monthly_cap=2)
    with pytest.raises(BudgetExceeded):
        c2.search("zzz")


def test_zero_budget_makes_no_call(tmp_path):
    t = FakeTransport()
    with pytest.raises(BudgetExceeded):
        client(tmp_path, t, max_searches=0).search("a")
    assert not t.calls


def test_missing_key_is_an_error_not_a_call(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    monkeypatch.setattr("ptah.products.serpapi.load_dotenv", lambda *a, **k: None)
    t = FakeTransport()
    c = SerpApiClient(api_key=None, cache_dir=tmp_path / "c", transport=t)
    with pytest.raises(SerpApiError):
        c.search("a")
    assert not t.calls


def test_key_never_written_to_cache_budget_or_errors(tmp_path):
    body = {"shopping_results": [res("x")], "search_metadata": {"json_endpoint": f"https://serpapi.com/searches/a.json?api_key={KEY}"},
            "serpapi_pagination": {"next": f"https://serpapi.com/search.json?engine=google_shopping&api_key={KEY}&start=40"}, "api_key": KEY}
    c = client(tmp_path, FakeTransport(body))
    c.search("a")
    for f in (tmp_path / "c").iterdir():
        txt = f.read_text()
        assert KEY not in txt, f.name
    assert "api_key" not in (tmp_path / "c").joinpath(f"{request_hash({'engine': 'google_shopping', 'q': 'a', 'gl': 'us', 'hl': 'en', 'google_domain': 'google.com'})}.json").read_text().replace('api_key=***', '')
    assert request_hash({"q": "a", "api_key": KEY}) == request_hash({"q": "a"})


def test_transport_error_message_is_scrubbed(tmp_path):
    def boom(url, params):
        raise RuntimeError(f"connection failed for {url}?api_key={params['api_key']}")
    c = SerpApiClient(api_key=KEY, cache_dir=tmp_path / "c", transport=boom)
    with pytest.raises(SerpApiError) as e:
        c.search("a")
    assert KEY not in str(e.value) and KEY not in repr(e.value)


def test_api_error_response_raises_and_is_not_cached(tmp_path):
    c = client(tmp_path, FakeTransport({"error": "Invalid API key."}, status=401))
    with pytest.raises(SerpApiError):
        c.search("a")
    assert c.cached({"engine": "google_shopping", "q": "a", "gl": "us", "hl": "en", "google_domain": "google.com"}) is None


def test_empty_results_are_a_normal_cached_response(tmp_path):
    t = FakeTransport({"error": "Google hasn't returned any results for this query."})
    c = client(tmp_path, t)
    assert c.results(c.search("nothing")) == []
    c.search("nothing")
    assert len(t.calls) == 1


# ---------------------------------------------------------------------------------------------------------------- end to end (fake search + fake images)

def test_scout_slot_end_to_end(tmp_path, monkeypatch):
    results = [
        res("Spirited Away Sticker Pack", 5),
        res("Spirited Away Art Print japanese calm", 45, "iCanvas", thumbnail="u1"),
        res("Abstract Poster Print", 30, "Etsy", thumbnail="u2"),
        res("Spirited Away Printable Digital Download Print", 3, "Etsy", thumbnail="u3"),
    ]
    t = FakeTransport({"shopping_results": results})
    c = client(tmp_path, t, max_searches=5)
    monkeypatch.setattr(scout, "fetch_image", lambda url, session=None, timeout=30: mockup((300, 260), (70, 50, 230, 200)))
    r = scout.scout_slot(art_slot(), c, details=False)
    assert r.chosen is not None and "Spirited Away Art Print" in r.chosen.title
    assert not r.chosen.digital and r.chosen.crop_conf >= 0.8
    assert r.dims and r.dims[0] <= 101.05 and r.dims[1] <= 67.05
    assert any("Sticker" in t for t, _ in r.rejected)
    assert c.live_calls <= 2

"""Design brief: filters sent to Qloo, section shapes, why-chains, seed resolution. Synthetic fixtures only."""
import json

import pytest

from ptah import brief, qloo
from qloo_fakes import KEY, FakeQloo, eid


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeQloo()
    monkeypatch.setenv("QLOO_API_KEY", KEY)
    monkeypatch.setenv("PTAH_QLOO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(qloo, "_http_get", f)
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    qloo.reset(500)
    return f


INPUTS = [{"qloo_id": "IN-1", "name": "Input One", "type": "artist"},
          {"qloo_id": "IN-2", "name": "Input Two", "type": "movie"}]


def insight_requests(fake, **match):
    out = []
    for path, p, _ in fake.requests:
        if path == "/v2/insights" and all(p.get(k) == v for k, v in match.items()):
            out.append(p)
    return out


def test_inspiration_filters(fake):
    decor = [{"tag_id": f"urn:tag:decor:qloo:{n}", "name": n, "affinity": 0.9} for n in ("d1", "d2", "d3", "d4")]
    places = brief.inspiration_places(["IN-1"], decor, "Testville", {"IN-1": "Input One"})
    reqs = insight_requests(fake, **{"filter.type": "urn:entity:place"})
    assert len(reqs) == len(brief.INSPIRATION_CATEGORIES) * brief.TOP_DECOR_FOR_PLACES   # category x top decor tags
    for p in reqs:
        cat, decor_tag = p["filter.tags"].split(",")
        assert cat in brief.INSPIRATION_CATEGORIES.values() and decor_tag.startswith("urn:tag:decor:qloo:d")
        assert p["operator.filter.tags"] == "intersection" and p["filter.location.query"] == "Testville"
        excl = set(p["filter.exclude.tags"].split(","))
        assert set(brief.LANDMARK_TAGS) <= excl and set(brief.QUICK_SERVICE_TAGS) <= excl
    assert "urn:tag:decor:qloo:d4" not in " ".join(p["filter.tags"] for p in reqs)         # only the top 3
    assert places and all(p["images"] and p["why"] and p["why"][0]["name"] == "Input One" for p in places)
    assert {p["kind"] for p in places} == set(brief.INSPIRATION_CATEGORIES)


def test_inspiration_dedupes_and_ranks_by_score(fake):
    decor = [{"tag_id": "urn:tag:decor:qloo:a", "name": "A", "affinity": 0.9}]
    places = brief.inspiration_places(["IN-1"], decor, "Testville", {})
    ids = [p["qloo_id"] for p in places]
    assert len(ids) == len(set(ids))
    assert [p["score"] for p in places] == sorted((p["score"] for p in places), reverse=True)
    assert all(p["matched_decor"] == ["a"] for p in places)       # fake places carry decor tag :a
    assert all(p["score"] == round(p["affinity"] + brief.MATCH_BONUS, 3) for p in places)


def test_shop_filters(fake):
    decor = [{"tag_id": "urn:tag:decor:qloo:a", "name": "A", "affinity": 0.9}]
    shops = brief.local_shops(["IN-1"], decor, "Testville", {"IN-1": "Input One"})
    reqs = insight_requests(fake, **{"filter.type": "urn:entity:place"})
    assert len(reqs) == len(brief.SHOP_CATEGORIES)
    for p in reqs:
        assert p["filter.tags"] in brief.SHOP_CATEGORIES.values() and "operator.filter.tags" not in p
        assert set(brief.BIG_BOX_TAGS + brief.RETAIL_NOISE_TAGS) <= set(p["filter.exclude.tags"].split(","))
        assert p["filter.location.query"] == "Testville"
    assert shops and all("shop_kind" in s and "kind" not in s and s["address"] for s in shops)
    assert not any(t for t in brief.BIG_BOX_TAGS if "hotel" in t)


def test_no_name_blocklists_in_filters():
    import inspect
    src = inspect.getsource(brief)
    for chain in ("Macy", "BJ's", "Starbucks", "Walmart", "Dunkin"):
        assert chain not in src


def test_brand_ranking_uses_results_filter_and_reports_unresolved(fake):
    fake.search_results["Known Brand"] = ["Known Brand", "Decoy"]
    fake.search_results["Fuzzy"] = ["Totally Different"]
    fake.search_results["Gone"] = []
    ranked, unresolved = brief.brand_ranking(["IN-1", "IN-2"], {"IN-1": "Input One"},
                                             {"Known Brand": "mid", "Fuzzy": "luxury", "Gone": "budget"})
    req = insight_requests(fake, **{"filter.type": "urn:entity:brand"})[0]
    assert req["filter.results.entities"] == eid("Known Brand")
    assert req["signal.interests.entities"] == "IN-1,IN-2"
    assert ranked[0]["tier"] == "mid" and ranked[0]["catalog_name"] == "Known Brand" and ranked[0]["why"]
    assert set(unresolved) == {"Fuzzy", "Gone"}                                   # near-exact name check rejected it


def test_brand_catalog_file_is_names_only_and_sized():
    cat = brief.load_catalog()
    assert 30 <= len(cat) <= 50 and all(isinstance(n, str) and isinstance(t, str) for n, t in cat.items())
    assert len(set(cat.values())) >= 3                              # several price points


def test_discovery_excludes_inputs(fake):
    out = brief.discovery(["IN-1", "IN-2"], {"IN-1": "Input One", "IN-2": "Input Two"})
    for typ in brief.DISCOVERY_TYPES:
        req = insight_requests(fake, **{"filter.type": f"urn:entity:{typ}"})[0]
        assert req["filter.exclude.entities"] == "IN-1,IN-2"
    assert {d["type"] for d in out} == set(brief.DISCOVERY_TYPES)
    assert all(d["why"] for d in out)


def test_taste_analysis_why_from_single_entity_analyses(fake):
    names = {"IN-1": "Input One", "IN-2": "Input Two"}
    t = brief.taste_analysis(INPUTS, names)
    assert set(t) == {"decor", "ambience"}
    top = t["decor"][0]
    assert top["name"] == "A" and top["affinity"] == 0.9
    assert top["why"] and {w["name"] for w in top["why"]} <= set(names.values())
    # one group analysis + one per entity, per kind
    assert len(insight_requests(fake, **{"filter.type": "urn:tag"})) == 2 * (1 + len(INPUTS))
    brief_no_why = brief.taste_analysis(INPUTS, names, with_why=False)
    assert "why" not in brief_no_why["decor"][0]


def test_build_brief_shape_and_every_item_has_why(fake):
    b = brief.build_brief(INPUTS, "Testville", catalog={"Some Brand": "mid"})
    assert set(b) >= {"location", "inputs", "taste", "inspiration_places", "local_shops", "brands",
                      "discovery", "qloo", "skipped_inputs", "brands_unresolved"}
    assert b["location"] == "Testville" and [i["name"] for i in b["inputs"]] == ["Input One", "Input Two"]
    for section in ("inspiration_places", "local_shops", "brands", "discovery"):
        assert b[section], section
        assert all(i["why"] and i["why"][0]["name"] in ("Input One", "Input Two") for i in b[section]), section
    for kind in ("decor", "ambience"):
        assert all(t["why"] for t in b["taste"][kind])
    json.dumps(b)                                                   # serialisable
    assert KEY not in json.dumps(b)
    assert b["qloo"]["network_calls"] == qloo.calls_used()


def test_build_brief_accepts_bare_ids_and_rejects_empty(fake):
    b = brief.build_brief(["IN-1"], "Testville", tag_why=False, catalog={})
    assert b["inputs"][0]["qloo_id"] == "IN-1"
    with pytest.raises(ValueError):
        brief.build_brief([], "Testville")


def test_seed_from_music_resolves_dedupes_and_skips(fake, tmp_path):
    music = [{"title": "t1", "artists": ["Alpha Act", "Beta Act"]},
             {"title": "t2", "artists": ["Alpha Act", "Gamma Act", "Alpha  Act!"]}]
    p = tmp_path / "music.json"
    p.write_text(json.dumps(music))
    fake.search_results["Beta Act"] = ["Completely Other Name"]        # fails the sanity check
    fake.search_results["Alpha  Act!"] = ["Alpha Act"]                  # near-identical -> duplicate id
    ents, skipped = brief.seed_from_music(p)
    assert [e["name"] for e in ents] == ["Alpha Act", "Gamma Act"]
    assert {s["name"] for s in skipped} == {"Beta Act", "Alpha  Act!"}
    assert all(e["type"] == "artist" for e in ents)


def test_entities_from_profile_orders_by_confidence_and_rejects_stubs(tmp_path):
    prof = {"entities": [{"qloo_id": "A", "name": "Low", "type": "movie", "evidence": "e", "confidence": 0.2},
                         {"qloo_id": "B", "name": "High", "type": "brand", "evidence": "e", "confidence": 0.9}]}
    p = tmp_path / "p.json"
    p.write_text(json.dumps(prof))
    assert [e["name"] for e in brief.entities_from_profile(p)] == ["High", "Low"]
    prof["entities"][0]["qloo_id"] = "stub_123"
    p.write_text(json.dumps(prof))
    with pytest.raises(SystemExit):
        brief.entities_from_profile(p)

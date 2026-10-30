"""Qloo client: request building, parsing, why-chains, cache, call cap, key hygiene. No network."""
import json
import logging

import pytest

from ptah import qloo
from qloo_fakes import KEY, FakeQloo, eid


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeQloo()
    monkeypatch.setenv("QLOO_API_KEY", KEY)
    monkeypatch.setenv("PTAH_QLOO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(qloo, "_http_get", f)
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    qloo.reset(50)
    return f


def test_search_request_and_parse(fake):
    out = qloo.search("Some Band", "artist")
    path, params, key = fake.requests[0]
    assert path == "/search"
    assert params["query"] == "Some Band" and params["types"] == "urn:entity:artist"
    assert key == KEY                      # sent as a header value only
    assert out[0]["qloo_id"] == eid("Some Band") and out[0]["score"] == 1.0 and out[0]["type"] == "artist"


@pytest.mark.parametrize("hint,urn", [("brand", "urn:entity:brand"), ("movie", "urn:entity:movie"),
                                      ("designer", "urn:entity:person"), ("tv_show", "urn:entity:tv_show"),
                                      ("urn:entity:place", "urn:entity:place")])
def test_type_urn_per_type(hint, urn):
    assert qloo.type_urn(hint) == urn


def test_unknown_type_rejected():
    with pytest.raises(qloo.QlooError):
        qloo.type_urn("furniture")


def test_insights_params_per_type(fake):
    qloo.insights_entities("brand", ["A", "B"], take=7)
    qloo.insights_entities("place", ["A"], extra={"filter.tags": "t1,t2", "operator.filter.tags": "intersection"})
    (_, brand, _), (_, place, _) = fake.requests
    assert brand["filter.type"] == "urn:entity:brand" and brand["signal.interests.entities"] == "A,B"
    assert brand["take"] == "7" and brand["feature.explainability"] == "true"
    assert place["filter.type"] == "urn:entity:place" and place["operator.filter.tags"] == "intersection"


@pytest.mark.parametrize("kind,ns", [("decor", "urn:tag:decor:qloo"), ("ambience", "urn:tag:ambience:qloo")])
def test_taste_tags_params(fake, kind, ns):
    tags = qloo.taste_tags(["A", "B"], kind)
    _, p, _ = fake.requests[0]
    assert p["filter.type"] == "urn:tag" and p["filter.tag.types"] == ns
    assert p["filter.parents.types"] == "urn:entity:place" and p["signal.interests.entities"] == "A,B"
    assert tags[0]["tag_id"].startswith(ns) and tags[0]["affinity"] == 0.9


def test_parse_entity_and_compact():
    raw = FakeQloo.entity("Hotel Fake", 0.812345, ["ID-ONE", "ID-TWO"])
    e = qloo.parse_entity(raw, {"ID-ONE": "One", "ID-TWO": "Two"})
    assert e["affinity"] == 0.812 and e["images"] == ["https://img.example/x.jpg"]
    assert e["decor_tags"] == ["A"] and e["ambience_tags"] == ["Cozy"] and e["category"] == "Hotel"
    assert [w["name"] for w in e["why"]] == ["One", "Two"]
    c = qloo.compact(e)
    assert set(c) >= {"qloo_id", "name", "type", "affinity", "tags", "why"} and "images" not in c


def test_why_chain_orders_names_and_truncates():
    expl = {"signal.interests.entities": [{"entity_id": "x", "score": 0.1}, {"entity_id": "y", "score": 0.4},
                                          {"entity_id": "z", "score": 0.2}, {"entity_id": "w", "score": 0.15}]}
    why = qloo.why_chain(expl, {"Y": "Why Y", "z": "Zed"})
    assert [w["name"] for w in why] == ["Why Y", "Zed", "w"]   # case-insensitive lookup, falls back to id
    assert qloo.why_chain(None) == [] and qloo.why_chain({}) == []


def test_name_similarity_rejects_shared_surname_only():
    assert qloo.name_similarity("Vishal and Sheykhar", "Vishal-Shekhar") >= 0.85
    assert qloo.name_similarity("Akshaye Khanna", "Usha Khanna") < 0.85
    assert qloo.name_similarity("Zara Home", "Karaca Home") < 0.85


def test_cache_hit_costs_nothing_and_survives_reset(fake, tmp_path):
    qloo.search("Cached", "artist")
    assert qloo.calls_used() == 1
    qloo.search("Cached", "artist")
    assert qloo.calls_used() == 1 and len(fake.requests) == 1
    qloo.reset(50)                                   # new run: disk cache still serves it
    qloo.search("Cached", "artist")
    assert qloo.calls_used() == 0 and len(fake.requests) == 1
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1


def test_call_cap(fake):
    qloo.reset(2)
    qloo.search("a", "artist")
    qloo.search("b", "artist")
    with pytest.raises(qloo.CallCapReached):
        qloo.search("c", "artist")
    out = qloo.qloo_search("d", "artist")            # tools degrade to an error dict
    assert out[0]["error"].startswith("Qloo call cap reached")
    qloo.search("a", "artist")                        # cache hits still work at the cap
    assert qloo.calls_used() == 2


def test_retries_then_success_and_hard_failure(fake):
    fake.statuses = [429, 503, 200]
    assert qloo.search("retry", "artist")
    assert len(fake.requests) == 3 and qloo.calls_used() == 1   # retries are one logical call
    fake.statuses = [500, 500, 500, 500]
    with pytest.raises(qloo.QlooError):
        qloo.search("always down", "artist")
    fake.statuses = [401]
    with pytest.raises(qloo.QlooError, match="HTTP 401"):
        qloo.search("unauthorized", "artist")
    assert len(fake.requests) == 3 + 4 + 1                      # 401 is not retried


def test_key_never_in_cache_logs_or_errors(fake, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    qloo.search("hygiene", "artist")
    qloo.insights_entities("place", ["A"])
    fake.statuses = [401]
    with pytest.raises(qloo.QlooError) as ei:
        qloo.search("bad", "artist")
    assert KEY not in str(ei.value) and KEY not in caplog.text
    for f in (tmp_path / "cache").glob("*.json"):
        assert KEY not in f.read_text()
    assert all(KEY not in url for url in (r[0] + json.dumps(r[1]) for r in fake.requests))


def test_stub_mode_only_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.setattr(qloo, "ROOT", tmp_path)       # no .env here
    assert qloo.is_stub()
    for out in (qloo.qloo_search("x", "artist"), qloo.qloo_related(["a"], "brand"), qloo.qloo_taste_tags(["a"])):
        assert out and all(o["stub"] is True for o in out)
    monkeypatch.setenv("QLOO_API_KEY", "k")
    assert not qloo.is_stub()


def test_key_loaded_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    (tmp_path / ".env").write_text('# c\nQLOO_API_KEY="from-dotenv"\n')
    monkeypatch.setattr(qloo, "ROOT", tmp_path)
    assert qloo.api_key() == "from-dotenv"


def test_llm_tools_real_shapes(fake):
    s = qloo.qloo_search("Some Band", "artist")
    assert s[0]["qloo_id"] == eid("Some Band")
    r = qloo.qloo_related([s[0]["qloo_id"]], "brand")
    assert r[0]["why"][0]["name"] == "Some Band"
    t = qloo.qloo_taste_tags([s[0]["qloo_id"]], "ambience")
    assert t[0]["tag_id"].startswith("urn:tag:ambience:qloo")
    for fn in (qloo.qloo_search, qloo.qloo_related, qloo.qloo_taste_tags):
        assert "Args:" in fn.__doc__ and "Returns:" in fn.__doc__

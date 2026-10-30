"""Guardrails against invented Qloo entities: id format, run provenance, name/type checks, search fallback,
429 handling, run logs. No network, no LLM; synthetic ids only."""
import json

import pytest
from dspy.adapters.utils import parse_value
from dspy.primitives.code_interpreter import FinalOutput
from pydantic import ValidationError

from ptah import profile_check, qloo, taste
from ptah.runlog import RunDir
from ptah.schemas import QlooEntity, TasteProfile
from qloo_fakes import KEY, FakeQloo

REAL = "3FC08B56-962C-41E4-82C6-1374C1B2435C"        # well-formed uuid used as a "returned by Qloo" id
UNSEEN = "0A1B2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D"      # well-formed but never returned
FAKE = "r1s02ow2-5u9u-4k3j-9x8y-zzzz1234abcd"         # the free model's fake-id shape


@pytest.fixture(autouse=True)
def clean_run():
    qloo.end_run()
    yield
    qloo.end_run()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeQloo()
    monkeypatch.setenv("QLOO_API_KEY", KEY)
    monkeypatch.setenv("PTAH_QLOO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(qloo, "_http_get", f)
    sleeps: list[float] = []
    monkeypatch.setattr(qloo.time, "sleep", sleeps.append)
    f.sleeps = sleeps
    qloo.reset(50)
    return f


def entity(**kw) -> dict:
    return {"qloo_id": REAL, "name": "Some Band", "type": "artist", "evidence": "e", "confidence": 0.9, **kw}


def profile(*ents) -> dict:
    return {"entities": list(ents), "aesthetic_tags": [], "themes": [], "style_summary": ""}


def seen_run(*ents):
    """Start a run in which `qloo` 'returned' these (id, name, type) entities."""
    qloo.start_run()
    qloo.register([{"qloo_id": i, "name": n, "type": t} for i, n, t in ents])


# ------------------------------------------------------------------ (a) id format

def test_uuid_normalized_to_uppercase():
    assert QlooEntity(**entity(qloo_id=REAL.lower())).qloo_id == REAL
    assert QlooEntity(**entity(qloo_id=f"  {REAL.lower()} ")).qloo_id == REAL


@pytest.mark.parametrize("bad", [FAKE, "ID-123", "stub_abc", "", "3FC08B56962C41E482C61374C1B2435C",
                                 "3FC08B56-962C-41E4-82C6-1374C1B2435"])
def test_non_uuid_rejected(bad):
    with pytest.raises(ValidationError, match="Qloo entity UUID"):
        QlooEntity(**entity(qloo_id=bad))


# ------------------------------------------------------------------ (b)(c) provenance, name, type

def test_loading_outside_a_run_passes():
    assert not qloo.run_active()
    p = TasteProfile.model_validate(profile(entity(qloo_id=UNSEEN, name="Anything", type="whatever")))
    assert p.entities[0].qloo_id == UNSEEN


def test_seen_id_passes_and_unseen_well_formed_id_fails():
    seen_run((REAL, "Some Band", "artist"))
    assert QlooEntity(**entity()).qloo_id == REAL
    with pytest.raises(ValidationError, match="qloo_search"):
        QlooEntity(**entity(qloo_id=UNSEEN))
    qloo.end_run()
    QlooEntity(**entity(qloo_id=UNSEEN))              # run over: loads again


def test_name_mismatch_rejected_but_small_variants_pass():
    seen_run((REAL, "Norway Park Орех", "place"), (UNSEEN, "Ludwig Göransson", "artist"))
    with pytest.raises(ValidationError, match="does not match Qloo's name 'Norway Park"):
        QlooEntity(**entity(name="Norway", type="place"))
    assert QlooEntity(**entity(qloo_id=UNSEEN, name="Ludwig Goransson")).name == "Ludwig Goransson"
    assert QlooEntity(**entity(qloo_id=REAL, name="norway park орех", type="place"))


@pytest.mark.parametrize("etype,qtype,ok", [
    ("artist", "artist", True), ("movie", "artist", False), ("place", "destination", False),
    ("destination", "destination", True), ("country", "destination", True), ("city", "destination", True),
    ("designer", "person", True), ("director", "person", True), ("brand", "place", False),
])
def test_type_consistency(etype, qtype, ok):
    seen_run((REAL, "Some Band", qtype))
    if ok:
        QlooEntity(**entity(type=etype))
    else:
        with pytest.raises(ValidationError, match="type"):
            QlooEntity(**entity(type=etype))


# ------------------------------------------------------------------ (f) through dspy's parse_value / RLM path

@pytest.mark.parametrize("bad_id", [FAKE, UNSEEN])
def test_parse_value_rejects_fake_and_unseen_ids(bad_id):
    seen_run((REAL, "Some Band", "artist"))
    with pytest.raises((ValueError, ValidationError)):            # what RLM._process_final_output catches
        parse_value(profile(entity(qloo_id=bad_id)), TasteProfile)
    ok = parse_value(profile(entity(qloo_id=REAL.lower())), TasteProfile)
    assert ok.entities[0].qloo_id == REAL


def test_rlm_final_output_returns_type_error_to_the_model():
    seen_run((REAL, "Some Band", "artist"))
    rlm = taste.build_program()
    parsed, err = rlm._process_final_output(FinalOutput({"profile": profile(entity(qloo_id=UNSEEN))}), ["profile"])
    assert parsed is None and err.startswith("[Type Error]") and "qloo_search" in err
    parsed, err = rlm._process_final_output(FinalOutput({"profile": profile(entity(qloo_id=FAKE))}), ["profile"])
    assert parsed is None and "[Type Error]" in err and "UUID" in err
    parsed, err = rlm._process_final_output(FinalOutput({"profile": profile(entity())}), ["profile"])
    assert err is None and parsed["profile"].entities[0].qloo_id == REAL


def test_prompt_rules_present():
    low = taste.INSTRUCTIONS.lower()
    assert "never write tool outputs" in low and "destination" in low and "real tool output" in low


# ------------------------------------------------------------------ (e) post-run check

def test_post_run_check_drops_and_explains():
    seen = {REAL: {"name": "Some Band", "types": ["artist"]}, UNSEEN: {"name": "Norway Park Орех", "types": ["place"]}}
    prof = profile(entity(), entity(qloo_id=FAKE, name="X"), entity(qloo_id=UNSEEN, name="Norway", type="place"),
                   entity(qloo_id="1" * 8 + "-1111-1111-1111-" + "1" * 12, name="Y"),
                   entity(qloo_id=REAL.lower(), confidence=0.5))
    clean, dropped = profile_check.check_profile(prof, seen)
    assert [e["name"] for e in clean["entities"]] == ["Some Band"] and clean["entities"][0]["confidence"] == 0.9
    reasons = " | ".join(r for d in dropped for r in d["reasons"])
    assert "not a Qloo entity UUID" in reasons and "does not match" in reasons and "was not returned" in reasons
    assert len(dropped) == 4 and "duplicate qloo_id" in reasons


# ------------------------------------------------------------------ tools: registry, call log, ids

def test_tools_record_provenance_and_log_calls(fake):
    fake.search_by_type[("Some Band", "urn:entity:artist")] = [("Some Band", REAL)]
    qloo.start_run()
    out = qloo.qloo_search("Some Band", "artist")
    assert out[0]["qloo_id"] == REAL and out[0]["match"] == "exact"
    assert QlooEntity(**entity())                                    # now verified
    rel = qloo.qloo_related([REAL.lower()], "brand")                 # lowercase id is normalised
    assert rel and "error" not in rel[0]
    bad = qloo.qloo_related([UNSEEN], "brand")
    assert "never returned" in bad[0]["error"]
    assert "not valid" in qloo.qloo_taste_tags([FAKE])[0]["error"]
    qloo.qloo_search("Some Band", "artist")                          # second time: from cache
    snap = qloo.end_run()
    assert REAL in snap["seen"] and snap["seen"][REAL]["types"] == ["artist"]
    assert [c["tool"] for c in snap["calls"]] == ["qloo_search", "qloo_related", "qloo_related", "qloo_taste_tags",
                                                  "qloo_search"]
    assert snap["calls"][0]["cache_hit"] is False and snap["calls"][-1]["cache_hit"] is True
    assert snap["calls"][0]["returned"][0]["qloo_id"] == REAL
    rel_ids = {r["qloo_id"] for r in rel}
    assert rel_ids <= set(snap["seen"])                              # related results are registered too


def test_registry_from_cache_roundtrip(fake, tmp_path):
    fake.search_by_type[("Some Band", "urn:entity:artist")] = [("Some Band", REAL)]
    qloo.search("Some Band", "artist")
    qloo.insights_entities("brand", [REAL])
    reg = qloo.registry_from_cache(tmp_path / "cache")
    assert reg[REAL]["name"] == "Some Band" and "artist" in reg[REAL]["types"]
    assert any("brand" in v["name"] for v in reg.values())


# ------------------------------------------------------------------ search robustness

@pytest.mark.parametrize("hint,types", [("country", "urn:entity:destination"), ("City", "urn:entity:destination"),
                                        ("destination", "urn:entity:destination"), ("designer", "urn:entity:person"),
                                        ("director", "urn:entity:director"), ("video_game", "urn:entity:videogame"),
                                        ("film", "urn:entity:movie")])
def test_type_hints_map_to_valid_search_types(fake, hint, types):
    qloo.qloo_search("Thing", hint)
    assert fake.requests[0][1]["types"] == types


def test_typed_search_without_good_match_falls_back_to_untyped(fake):
    fake.search_by_type[("Norway", "urn:entity:place")] = ["Norway Park Орех", "Norway Inn"]
    fake.search_by_type[("Norway", "")] = [("Norway", UNSEEN), "Norway Inn"]
    out = qloo.qloo_search("Norway", "place")
    assert out[0]["name"] == "Norway"
    assert out[0]["score"] == 1.0 and out[0]["match"] == "exact" and "note" in out[0]
    assert [r[1].get("types") for r in fake.requests] == ["urn:entity:place", None]
    assert [r["name"] for r in out].count("Norway Inn") == 1       # merged without duplicates
    assert [r["score"] for r in out] == sorted((r["score"] for r in out), reverse=True)


def test_good_typed_match_does_not_retry(fake):
    qloo.qloo_search("Some Band", "artist")
    assert len(fake.requests) == 1


def test_unknown_hint_searches_untyped(fake):
    out = qloo.qloo_search("Thing", "furniture")
    assert "types" not in fake.requests[0][1] and out[0]["name"] == "Thing"


# ------------------------------------------------------------------ 429 handling

def test_retry_after_header_respected(fake):
    fake.statuses = [429, 200]
    fake.retry_headers = {"Retry-After": "7"}
    assert qloo.search("slow", "artist")
    assert 7.0 in fake.sleeps and qloo.stats()["n429"] == 1 and qloo.stats()["retries"] == 1


def test_backoff_has_jitter_and_grows_and_stops_after_four_tries(fake):
    fake.statuses = [429] * 10
    with pytest.raises(qloo.QlooError, match="after 4 attempts"):
        qloo.search("blocked", "artist")
    assert len(fake.requests) == 4 and qloo.stats()["n429"] == 4
    waits = [w for w in fake.sleeps if w >= 0.7]
    assert len(waits) == 3
    for i, w in enumerate(waits):
        assert 0.75 * 2 ** i <= w <= 1.5 * 2 ** i
    assert len(set(waits)) == 3


def test_throttle_spaces_live_requests(fake, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(qloo.time, "monotonic", lambda: clock[0])
    qloo._state["last_request"] = 0.0
    qloo.search("a", "artist")
    n = len(fake.sleeps)
    qloo.search("b", "artist")                                        # immediately after: must wait ~MIN_INTERVAL
    assert len(fake.sleeps) == n + 1 and fake.sleeps[-1] == pytest.approx(qloo.MIN_INTERVAL_S)


# ------------------------------------------------------------------ run logs

def test_run_dir_tees_output_and_writes_json(tmp_path, capsys):
    import sys
    with RunDir(tmp_path) as run:
        print("hello out")
        print("hello err", file=sys.stderr)
        run.write_json("x.json", {"a": 1})
    log = (run.path / "run.log").read_text()
    assert "hello out" in log and "hello err" in log
    assert json.loads((run.path / "x.json").read_text()) == {"a": 1}
    assert "hello out" in capsys.readouterr().out
    assert RunDir(tmp_path, now=None).path != run.path                # no collisions within the same minute


# ------------------------------------------------------------------ run_taste.py end to end (no LLM)

class _Pred:
    def __init__(self, profile):
        self.profile, self.trajectory = profile, [{"reasoning": "r", "code": "c", "output": "o"}]


class _LM:
    history = [1, 2, 3]


def _run_with(monkeypatch, tmp_path, extraction):
    import argparse

    import run_taste
    from ptah import runlog
    monkeypatch.setattr(runlog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(taste, "run_extraction", extraction)
    args = argparse.Namespace(qloo_cap=5, out=tmp_path / "taste_profile.json")
    return run_taste.real_run(args), tmp_path / "runs"


def test_real_run_writes_all_logs_and_drops_bad_entities(monkeypatch, tmp_path):
    good = QlooEntity(**entity())
    bad = QlooEntity.model_construct(qloo_id=UNSEEN, name="Ghost", type="artist", evidence="e", confidence=0.5)

    def extraction(cap):
        qloo.start_run()
        qloo.register([{"qloo_id": REAL, "name": "Some Band", "type": "artist"}])
        qloo._run["calls"].append({"tool": "qloo_search", "args": {}, "returned": [], "cache_hit": False})
        reg = qloo.end_run()
        return {"profile": TasteProfile(entities=[good, bad]), "prediction": _Pred(None), "registry": reg,
                "lms": {"main": _LM(), "sub": _LM()}}
    status, runs = _run_with(monkeypatch, tmp_path, extraction)
    (run,) = list(runs.iterdir())
    assert status == 0
    names = {p.name for p in run.iterdir()}
    assert names == {"run.log", "trajectory.json", "qloo_calls.json", "profile.raw.json", "profile.json", "summary.md"}
    assert len(json.loads((run / "profile.raw.json").read_text())["entities"]) == 2
    kept = json.loads((run / "profile.json").read_text())["entities"]
    assert [e["name"] for e in kept] == ["Some Band"]
    assert json.loads((tmp_path / "taste_profile.json").read_text()) == json.loads((run / "profile.json").read_text())
    summary = (run / "summary.md").read_text()
    assert "Ghost" in summary and "was not returned" in summary and "LLM calls: 6" in summary


def test_real_run_failure_still_writes_logs(monkeypatch, tmp_path):
    def extraction(cap):
        raise RuntimeError("model fell over")
    status, runs = _run_with(monkeypatch, tmp_path, extraction)
    (run,) = list(runs.iterdir())
    assert status == 1 and not (tmp_path / "taste_profile.json").exists()
    assert "model fell over" in (run / "run.log").read_text()
    assert "failed" in (run / "summary.md").read_text() and (run / "qloo_calls.json").exists()

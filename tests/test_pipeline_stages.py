"""The real taste / brief stage functions against the synthetic Qloo transport (no network, no LLM)."""
import json

import pytest

from ptah import events, pipeline, qloo
from ptah.pipeline import Ctx, JobConfig, StageError
from pipeline_fakes import instagram_zip
from qloo_fakes import KEY, FakeQloo


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeQloo()
    monkeypatch.setenv("QLOO_API_KEY", KEY)
    monkeypatch.setenv("PTAH_QLOO_CACHE_DIR", str(tmp_path / "qcache"))
    monkeypatch.setattr(qloo, "_http_get", f)
    monkeypatch.setattr(qloo.time, "sleep", lambda s: None)
    monkeypatch.setattr(pipeline, "TASTE_CACHE", tmp_path / "taste_cache")
    qloo.reset(500)
    return f


def ctx_for(tmp_path, **cfg):
    sink = events.JobSink("j", None, min_interval=0)
    c = Ctx(tmp_path / "job", JobConfig(**cfg), sink)
    sink.set_plan(pipeline.plan_for(c.cfg))
    sink.emit("stage_start", stage="taste")
    return c


def test_taste_from_typed_favourites(fake, tmp_path):
    c = ctx_for(tmp_path, loves=[{"name": "Alpha Band", "type": "artist"}, {"name": "Beta Film", "type": "movie"}, {"name": "Alpha Band"}])
    with events.bind(c.sink):
        out = pipeline.stage_taste(c)
    prof = json.loads(c.profile_path.read_text())
    assert [e["name"] for e in prof["entities"]] == ["Alpha Band", "Beta Film"]                  # duplicate collapsed
    assert prof["entities"][0]["qloo_id"] and prof["style_summary"].startswith("A taste shaped by Alpha Band")
    assert prof["aesthetic_tags"]                                                               # decor tags from Qloo's taste analysis
    assert out["loves_resolved"] == 2
    assert any(e["type"] == "activity_start" and e["service"] == "qloo" for e in c.sink.events)  # the pulsing-Qloo hook fires per live request
    assert KEY not in json.dumps(c.sink.events)


def test_unknown_favourite_warns_and_no_taste_is_an_error(fake, tmp_path):
    fake.search_results["Nope"] = []
    c = ctx_for(tmp_path, loves=[{"name": "Nope"}])
    with events.bind(c.sink), pytest.raises(StageError) as ei:
        pipeline.stage_taste(c)
    assert ei.value.code == "no_taste" and c.warnings and "Nope" in c.warnings[0]


def test_taste_requires_qloo_key(tmp_path, monkeypatch):
    monkeypatch.setattr(qloo, "api_key", lambda: None)
    c = ctx_for(tmp_path, loves=[{"name": "x"}])
    with pytest.raises(StageError) as ei:
        pipeline.stage_taste(c)
    assert ei.value.code == "qloo_not_configured"


def prep_ingest(c):
    c.input.mkdir(parents=True, exist_ok=True)
    instagram_zip(c.input / "instagram.zip")
    import ingest_instagram
    ingest_instagram.ingest(c.input / "instagram.zip", c.ingest_dir)


def test_rlm_result_is_cached_by_content(fake, tmp_path, monkeypatch):
    calls = []

    def fake_rlm(ctx):
        calls.append(1)
        return {"entities": [{"qloo_id": "E1", "name": "Rlm Thing", "type": "brand", "evidence": "e", "confidence": 0.8}],
                "aesthetic_tags": ["warm"], "themes": [{"name": "t", "post_count": 3, "example_post_ids": []}], "style_summary": "S"}

    monkeypatch.setattr(pipeline, "_run_rlm", fake_rlm)
    c = ctx_for(tmp_path, has_zip=True)
    prep_ingest(c)
    with events.bind(c.sink):
        assert pipeline.stage_taste(c)["entities"] == 1
        c2 = ctx_for(tmp_path / "second", has_zip=True)
        prep_ingest(c2)
        out = pipeline.stage_taste(c2)
    assert calls == [1] and out["cached"] is True                       # same posts -> no second LLM run


def test_rlm_failure_falls_back_to_favourites_then_music(fake, tmp_path, monkeypatch):
    def broken(ctx):
        raise StageError("The AI model is not set up on this server.", code="llm_not_configured")

    monkeypatch.setattr(pipeline, "_run_rlm", broken)
    c = ctx_for(tmp_path / "a", has_zip=True, loves=[{"name": "Gamma"}])
    prep_ingest(c)
    with events.bind(c.sink):
        out = pipeline.stage_taste(c)
    assert out["entities"] == 1 and any("skipped" in w for w in c.warnings)
    c2 = ctx_for(tmp_path / "b", has_zip=True)                          # no favourites: the saved music artist seeds the profile
    prep_ingest(c2)
    with events.bind(c2.sink):
        pipeline.stage_taste(c2)
    assert json.loads(c2.profile_path.read_text())["entities"][0]["name"] == "Some Band" and c2.warnings


def test_brief_stage_reports_progress_and_qloo_activity(fake, tmp_path):
    c = ctx_for(tmp_path, loves=[{"name": "Alpha Band"}])
    with events.bind(c.sink):
        pipeline.stage_taste(c)
        c.sink.emit("stage_start", stage="brief")
        out = pipeline.stage_brief(c)
    assert out["places"] and out["decor_tags"] and c.brief_path.exists()
    labels = [e["label"] for e in c.sink.events if e["type"] == "stage_progress" and e["stage"] == "brief"]
    assert "Finding shops near New York City" in labels
    assert sum(1 for e in c.sink.events if e["type"] == "activity_start" and e["stage"] == "brief") == out["qloo_calls"]


# ---------------------------------------------------------------------------------------------------------------- design (needs the recorder's private run data)
_P = pipeline.ROOT / "data"
_NEEDS = [_P / "taste_profile.json", _P / "brief.json", _P / "runs" / "design_20261030_1300_cohesive" / "design_spec.json"]


@pytest.mark.skipif(not all(p.exists() for p in _NEEDS), reason="private recorded run data (data/) not present")
def test_design_stage_owned_items_and_cannot_paint(tmp_path, monkeypatch):
    from ptah import design
    c = ctx_for(tmp_path, owned={"sample": False, "items": [{"name": "Standing desk", "category": "desk", "w": 140, "d": 70, "h": 75}]},
                prefs={"budget": 3000, "can_paint": False, "location": "X"})
    shutil = __import__("shutil")
    shutil.copy(_NEEDS[0], c.profile_path)
    shutil.copy(_NEEDS[1], c.brief_path)

    def fake_generate(inp, cache_dir, force=False, log=print):
        return design.validate_spec(json.loads(_NEEDS[2].read_text()), design.build_vocab(inp)), {"llm_calls": 0, "cached": True}

    monkeypatch.setattr(design, "generate_spec", fake_generate)
    with events.bind(c.sink):
        out = pipeline.stage_design(c)
    items = json.loads((c.design_dir / "items.json").read_text())
    assert any(i.get("owned") and i["name"] == "Standing desk" for i in items)                   # user-owned piece reaches the room
    lay = json.loads((c.design_dir / "layout.json").read_text())
    assert any(p["item_id"] == "owned-1" for p in lay["placements"]) or any(u["item_id"] == "owned-1" for u in lay.get("unplaced", []))
    room = json.loads((c.design_dir / "room.json").read_text())
    assert room["can_paint"] is False and room["finishes"]["wall_paint"] is None and room["finishes"]["wall_color"] == "#f2efe8"
    assert json.loads((c.design_dir / "taste.json").read_text())["budget"] == 3000
    assert out["cached"] is True and out["pieces"] > 5

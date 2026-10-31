"""Pipeline runner with fake stages (+ the real ingest stage on a synthetic export)."""
import json

import pytest

from ptah import events, pipeline
from ptah.pipeline import Cancelled, JobConfig, StageError, run_job
from pipeline_fakes import FAKES, instagram_zip


def sink_for(tmp_path, cfg):
    s = events.JobSink("job1", tmp_path / "events.jsonl", weights=pipeline.weights_for(cfg), min_interval=0)
    s.set_plan(pipeline.plan_for(cfg))
    return s


def types(s):
    return [(e["type"], e["stage"]) for e in s.events]


def test_happy_path_events_progress_and_bundle(tmp_path):
    cfg = JobConfig(loves=[{"name": "x"}])
    s = sink_for(tmp_path, cfg)
    res = run_job(tmp_path, cfg, s, stages=FAKES)
    assert res["ok"] and not res["partial"]
    assert s.closed and s.events[-1]["type"] == "result" and s.events[-1]["progress"] == 1.0
    prog = [e["progress"] for e in s.events]
    assert prog == sorted(prog)                                            # monotonic overall progress
    assert [e["seq"] for e in s.events] == list(range(1, len(s.events) + 1))
    started = [e["stage"] for e in s.events if e["type"] == "stage_start"]
    assert started == ["taste", "brief", "design", "products", "layout", "lighting"]      # no ingest without an upload
    first = s.events[0]
    assert first["detail"]["plan"][0]["stage"] == "taste"
    services = {e["service"] for e in s.events if e["type"] == "activity_start"}
    assert services == {"qloo", "llm", "serpapi"}
    for n in ("room.json", "items.json", "layout.json", "lighting.json", "taste.json", "images/art-1.jpg"):
        assert (tmp_path / "viewer" / n).exists()
    assert (tmp_path / "events.jsonl").read_text().count("\n") == len(s.events)


def test_ingest_stage_real_and_zip_deleted(tmp_path):
    (tmp_path / "input").mkdir()
    instagram_zip(tmp_path / "input" / "instagram.zip", n_posts=7)
    cfg = JobConfig(has_zip=True)
    s = sink_for(tmp_path, cfg)
    stages = {**FAKES, "taste": FAKES["taste"]}
    run_job(tmp_path, cfg, s, stages=stages)
    end = next(e for e in s.events if e["type"] == "stage_end" and e["stage"] == "ingest")
    assert end["detail"]["posts"] == 7 and end["detail"]["music_tracks"] == 1
    assert (tmp_path / "work" / "ingest" / "posts.jsonl").exists()
    assert not (tmp_path / "input" / "instagram.zip").exists()               # raw export is not kept
    assert [e["stage"] for e in s.events if e["type"] == "stage_start"][0] == "ingest"


def test_html_export_gives_friendly_error(tmp_path):
    (tmp_path / "input").mkdir()
    instagram_zip(tmp_path / "input" / "instagram.zip", html=True)
    cfg = JobConfig(has_zip=True)
    s = sink_for(tmp_path, cfg)
    res = run_job(tmp_path, cfg, s, stages=FAKES)
    assert not res["ok"]
    last = s.events[-1]
    assert last["type"] == "error" and "JSON" in last["label"] and last["detail"]["code"] == "bad_export" and last["detail"]["retryable"] is False
    assert s.closed


def test_hard_stage_failure_ends_with_error_and_no_traceback_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO_API_KEY", "sk-leakme-123456")

    def boom(ctx):
        raise RuntimeError("request failed for sk-leakme-123456")

    cfg = JobConfig(loves=[{"name": "x"}])
    s = sink_for(tmp_path, cfg)
    res = run_job(tmp_path, cfg, s, stages={**FAKES, "brief": boom})
    assert not res["ok"] and s.events[-1]["type"] == "error"
    blob = json.dumps(s.events)
    assert "leakme" not in blob and "Traceback" not in blob
    assert s.events[-1]["detail"]["stage"] == "brief"
    assert (tmp_path / "work" / "error.txt").exists()                      # the scrubbed traceback is kept on disk, not sent


def test_soft_stage_failure_gives_partial_result(tmp_path):
    def nope(ctx):
        raise StageError("We could not find real products.", code="no_products")

    cfg = JobConfig(loves=[{"name": "x"}])
    s = sink_for(tmp_path, cfg)
    res = run_job(tmp_path, cfg, s, stages={**FAKES, "products": nope})
    assert res["ok"] and res["partial"]
    last = s.events[-1]
    assert last["type"] == "result" and last["detail"]["partial"] is True and last["detail"]["skipped"] == ["products"]
    assert any(e["type"] == "stage_end" and e["stage"] == "products" and e["detail"].get("skipped") for e in s.events)
    assert (tmp_path / "viewer" / "items.json").exists()


def test_resume_skips_finished_stages(tmp_path):
    calls = []

    def counting(name, fn):
        def inner(ctx):
            calls.append(name)
            return fn(ctx)
        return inner

    def boom(ctx):
        raise StageError("Qloo is not answering right now.", code="qloo_unavailable")

    cfg = JobConfig(loves=[{"name": "x"}])
    stages = {k: counting(k, v) for k, v in FAKES.items()}
    s1 = sink_for(tmp_path, cfg)
    assert not run_job(tmp_path, cfg, s1, stages={**stages, "design": boom})["ok"]
    assert calls == ["taste", "brief"]
    # second attempt: same events file continues, finished stages are replayed as cached
    s2 = events.JobSink("job1", tmp_path / "events.jsonl", weights=pipeline.weights_for(cfg), min_interval=0)
    s2.preload(events.JobSink.load("job1", tmp_path / "events.jsonl").events)
    n_before = len(s2.events)
    assert run_job(tmp_path, cfg, s2, stages=stages, resume=True)["ok"]
    assert calls == ["taste", "brief", "design", "products", "layout", "lighting"]
    cached = [e for e in s2.events[n_before:] if e["type"] == "stage_end" and e["detail"].get("cached")]
    assert [e["stage"] for e in cached] == ["taste", "brief"]
    seqs = [e["seq"] for e in s2.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert s2.events[-1]["type"] == "result"


def test_cancel_stops_the_job(tmp_path):
    flag = {"stop": False}

    def slow(ctx):
        flag["stop"] = True
        ctx.progress(0.5)
        return {}

    cfg = JobConfig(loves=[{"name": "x"}])
    s = sink_for(tmp_path, cfg)
    res = run_job(tmp_path, cfg, s, stages={**FAKES, "taste": slow}, cancelled=lambda: flag["stop"])
    assert not res["ok"] and s.events[-1]["type"] == "error" and s.events[-1]["detail"]["code"] == "cancelled"


def test_room_validation_and_owned_normalisation():
    ok = json.loads((pipeline.SAMPLES / "room.json").read_text())
    assert pipeline.validate_room(ok)["height"] == 260
    for bad in ({}, {"floor": [[0, 0]], "walls": []}, {"floor": [[0, 0], [5, 0], [5, 5]], "walls": [{"id": "w", "start": [0, 0], "end": [5, 0]}]}):
        with pytest.raises(StageError):
            pipeline.validate_room(bad)
    items = pipeline.normalize_owned([{"name": "Desk", "category": "Desk!", "w": 140, "d": 70, "h": 75}, {"name": "x", "w": "junk"}, "nope"])
    assert items[0]["owned"] and items[0]["category"] == "desk_" and items[0]["w"] == 140 and len(items) == 1


def test_sample_rooms_listed():
    r = pipeline.sample_rooms()
    assert "sample-living-room" in r and r["sample-living-room"]["width_cm"] == 580 and r["sample-living-room"]["depth_cm"] == 480


def test_dependencies_of_real_stages_import():
    # the real stage functions must at least be importable and registered
    assert set(pipeline.STAGES) == {"ingest", "taste", "brief", "design", "products", "layout", "lighting"}

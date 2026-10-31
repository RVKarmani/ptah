"""Event bus: ordering, throttling, job scoping, progress maths, secrets, DSPy callback. No network, no LLM."""
import json
import threading
import time

import dspy
import pytest
from dspy.utils.dummies import DummyLM

from ptah import events
from ptah.events import JobSink


def make(tmp_path=None, plan=("ingest", "taste", "brief", "design", "products", "layout", "lighting"), **kw):
    return JobSink("job1", (tmp_path / "events.jsonl") if tmp_path else None, plan=list(plan), **kw)


def test_seq_ordering_and_jsonl(tmp_path):
    s = make(tmp_path)
    s.emit("stage_start", stage="ingest", label="Reading")
    s.emit("log", label="hello")
    s.emit("stage_end", stage="ingest")
    s.close()
    assert [e["seq"] for e in s.events] == [1, 2, 3]
    lines = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [l["seq"] for l in lines] == [1, 2, 3] and lines[0]["job_id"] == "job1"
    assert set(lines[0]) >= {"job_id", "seq", "ts", "type", "stage", "progress", "stage_progress", "service", "label", "detail", "duration_ms"}


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        make().emit("nope")


def test_weights_and_monotonic_progress():
    s = make()
    shares = {x["stage"]: x["weight"] for x in s.plan_info()}
    assert shares["taste"] == pytest.approx(0.45) and shares["ingest"] == pytest.approx(0.05) and shares["brief"] == pytest.approx(0.15)
    assert shares["layout"] + shares["lighting"] == pytest.approx(0.08) and sum(shares.values()) == pytest.approx(1.0)
    s.min_interval = 0
    last = 0.0
    for st in s._plan:
        s.emit("stage_start", stage=st)
        for f in (0.2, 0.1, 0.7):                       # a backwards value must never move the bar back
            ev = s.emit("stage_progress", stage=st, stage_progress=f)
            assert ev["progress"] >= last
            last = ev["progress"]
        ev = s.emit("stage_end", stage=st)
        assert ev["progress"] >= last
        last = ev["progress"]
    assert last == pytest.approx(1.0)
    assert s.emit("result", label="done")["progress"] == 1.0


def test_plan_without_ingest_renormalises():
    s = make(plan=["taste", "brief", "design", "products", "layout", "lighting"])
    assert sum(x["weight"] for x in s.plan_info()) == pytest.approx(1.0)


def test_progress_throttled_with_trailing_flush():
    s = make(min_interval=0.1)
    s.emit("stage_start", stage="taste")
    for i in range(500):
        s.emit("stage_progress", stage="taste", stage_progress=i / 500)
    time.sleep(0.25)                                    # trailing flush keeps the latest value
    prog = [e for e in s.events if e["type"] == "stage_progress"]
    assert 1 <= len(prog) <= 3
    assert prog[-1]["stage_progress"] >= 0.99
    # sustained: never above 10 events per second
    s2 = make(min_interval=0.1)
    s2.emit("stage_start", stage="taste")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.55:
        s2.emit("stage_progress", stage="taste", stage_progress=0.5)
        time.sleep(0.001)
    time.sleep(0.15)
    assert len([e for e in s2.events if e["type"] == "stage_progress"]) <= 8


def test_stage_end_flushes_pending_progress_before_it():
    s = make(min_interval=5.0)
    s.emit("stage_start", stage="taste")
    s.emit("stage_progress", stage="taste", stage_progress=0.1)
    s.emit("stage_progress", stage="taste", stage_progress=0.9)       # throttled
    s.emit("stage_end", stage="taste")
    types = [e["type"] for e in s.events]
    assert types == ["stage_start", "stage_progress", "stage_progress", "stage_end"]


def test_scoping_by_job_and_noop_without_sink():
    a, b = make(), make()
    b.job_id = "job2"
    assert events.current() is None
    events.emit("log", label="nobody listens")            # no-op
    with events.activity("qloo", "x"):
        pass
    with events.bind(a):
        events.log("to a")
        with events.bind(b):
            events.log("to b")
        events.log("to a again")
    assert [e["label"] for e in a.events] == ["to a", "to a again"]
    assert [e["label"] for e in b.events] == ["to b"]
    assert events.current() is None


def test_helpers_reach_the_sink_from_other_threads():
    s = make()
    with events.bind(s):
        t = threading.Thread(target=lambda: events.log("from thread"))
        t.start()
        t.join()
    assert [e["label"] for e in s.events] == ["from thread"]


def test_suppressed_blocks_events():
    s = make()
    with events.bind(s), events.suppressed():
        events.log("hidden")
        with events.activity("qloo", "hidden"):
            pass
    assert s.events == []


def test_activity_pair_and_error():
    s = make()
    with events.bind(s):
        with events.activity("qloo", "Qloo: lookup", {"attempt": 1}):
            pass
        with pytest.raises(RuntimeError):
            with events.activity("serpapi", "Shopping search: sofa"):
                raise RuntimeError("boom")
    a0, a1, b0, b1 = s.events
    assert (a0["type"], a1["type"], a0["service"]) == ("activity_start", "activity_end", "qloo")
    assert a0["id"] == a1["id"] and a1["duration_ms"] is not None and a1["detail"]["ok"] is True
    assert b1["detail"]["ok"] is False and b1["service"] == "serpapi"


def test_detail_is_small_and_secret_free():
    d = events.clean_detail({"api_key": "k", "X-Api-Key": "k", "token": "t", "ok": 1, "long": "x" * 1000, "nested": {"password": "p", "a": [1, 2]}})
    assert "api_key" not in d and "X-Api-Key" not in d and "token" not in d
    assert len(d["long"]) < 210 and d["nested"] == {"a": [1, 2]}


def test_scrub_removes_env_secrets(monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "sk-supersecret-123")
    assert "supersecret" not in events.scrub("failed: https://x?key=sk-supersecret-123")


def test_since_wait_and_load(tmp_path):
    s = make(tmp_path)
    s.emit("log", label="a")
    assert [e["seq"] for e in s.since(0)] == [1] and s.since(1) == []
    threading.Timer(0.05, lambda: s.emit("log", label="b")).start()
    assert [e["seq"] for e in s.wait(1, 2.0)] == [2]
    s.close()
    loaded = JobSink.load("job1", tmp_path / "events.jsonl")
    assert loaded.closed and len(loaded.events) == 2


def test_preload_continues_sequence(tmp_path):
    s = make(tmp_path)
    s.emit("stage_start", stage="ingest")
    s.emit("stage_end", stage="ingest")
    s.close()
    s2 = make(tmp_path)
    s2.preload(JobSink.load("job1", tmp_path / "events.jsonl").events)
    ev = s2.emit("log", label="again")
    assert ev["seq"] == 3 and ev["progress"] >= 0.05


def test_creep_nudges_progress():
    s = make(min_interval=0)
    s.emit("stage_start", stage="design")
    with events.bind(s), events.creep(0.3, 0.1, 0.7, "x", stage="design"):
        time.sleep(1.3)
    vals = [e["stage_progress"] for e in s.events if e["type"] == "stage_progress"]
    assert len(vals) >= 2 and vals == sorted(vals) and 0.1 < vals[-1] <= 0.7


def test_find_iteration():
    assert events.find_iteration({"args": (), "kwargs": {"iteration": "3/20", "x": 1}}) == (3, 20)
    assert events.find_iteration({"kwargs": {"iteration": "oops"}}) is None


def test_dspy_callback_emits_llm_activity_and_rlm_iterations():
    s = make(min_interval=0)
    s.emit("stage_start", stage="taste")
    cb = events.make_dspy_callback()

    class Step(dspy.Signature):
        q: str = dspy.InputField()
        iteration: str = dspy.InputField()
        a: str = dspy.OutputField()

    pred = dspy.Predict(Step)
    with events.bind(s), dspy.context(lm=DummyLM([{"a": "x"}, {"a": "y"}]), callbacks=[cb]):
        pred(q="hi", iteration="2/10")
        pred(q="hi", iteration="3/10")
    acts = [e for e in s.events if e["type"].startswith("activity")]
    assert [e["type"] for e in acts] == ["activity_start", "activity_end"] * 2 and all(e["service"] == "llm" for e in acts)
    prog = [e for e in s.events if e["type"] == "stage_progress"]
    assert prog and prog[-1]["detail"]["iteration"] == 3 and prog[-1]["stage_progress"] > prog[0]["stage_progress"]

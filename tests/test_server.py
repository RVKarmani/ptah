"""HTTP API through FastAPI's TestClient: uploads, jobs, SSE, bundle files, demo replay, delete, limits. Fake stages only."""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from ptah import qloo
from pipeline_fakes import FAKES, instagram_zip, multipart
from qloo_fakes import KEY, FakeQloo
from server import app as appmod
from server import demo as demo_mod
from server.jobs import JobManager


@pytest.fixture
def env(tmp_path, monkeypatch):
    mgr = JobManager(tmp_path / "jobs", stages=dict(FAKES))
    client = TestClient(appmod.create_app(mgr, serve_viewer=False))
    yield client, mgr, tmp_path
    mgr.stop()


def post(client, parts):
    body, ctype = multipart(parts)
    return client.post("/api/jobs", content=body, headers={"content-type": ctype})


def wait_status(client, jid, want=("done", "error", "cancelled"), timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/jobs/{jid}").json()
        if d["status"] in want:
            return d
        time.sleep(0.05)
    raise AssertionError(f"job still {d['status']}")


def read_sse(client, url, headers=None, limit=500, stop=("result", "error")):
    evs, ids = [], []
    with client.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("id: "):
                ids.append(int(line[4:]))
            elif line.startswith("data: "):
                evs.append(json.loads(line[6:]))
                if evs[-1]["type"] in stop or len(evs) >= limit:
                    break
    return evs, ids


def test_zip_job_end_to_end_with_sse_and_files(env):
    client, mgr, tmp = env
    z = instagram_zip(tmp / "ig.zip")
    r = post(client, [("instagram", "ig.zip", z.read_bytes()), ("loves", None, json.dumps([{"name": "Some Band", "type": "artist"}]).encode()),
                      ("preferences", None, b'{"budget": 2500, "can_paint": false}')])
    assert r.status_code == 201, r.text
    j = r.json()
    jid = j["job_id"]
    assert len(jid) == 16 and j["events_url"] == f"/api/jobs/{jid}/events" and j["viewer_url"].endswith(f"/api/jobs/{jid}/viewer/")
    st = wait_status(client, jid)
    assert st["status"] == "done" and st["progress"] == 1.0 and st["inputs"]["instagram"] and st["inputs"]["preferences"]["budget"] == 2500
    assert st["plan"][0]["stage"] == "ingest" and st["viewer_url"]
    evs, ids = read_sse(client, f"/api/jobs/{jid}/events")
    assert ids == list(range(1, len(ids) + 1)) and [e["seq"] for e in evs] == ids
    assert evs[-1]["type"] == "result" and evs[-1]["progress"] == 1.0
    assert {"job_id", "seq", "ts", "type", "progress", "stage_progress"} <= set(evs[0])
    assert all(None not in e.values() for e in evs)                                    # null fields are omitted on the wire
    # bundle files
    for n in ("room.json", "items.json", "layout.json", "lighting.json", "taste.json"):
        assert client.get(f"/api/jobs/{jid}/viewer/{n}").status_code == 200
    img = client.get(f"/api/jobs/{jid}/viewer/images/art-1.jpg")
    assert img.status_code == 200 and "max-age" in img.headers["cache-control"]
    big = client.get(f"/api/jobs/{jid}/viewer/items.json", headers={"accept-encoding": "gzip"})
    assert big.headers.get("content-encoding") == "gzip" and big.headers["cache-control"] == "no-cache" and "etag" in big.headers
    assert client.get(f"/api/jobs/{jid}/viewer/items.json", headers={"if-none-match": big.headers["etag"]}).status_code == 304


def test_sse_resume_and_no_reconnect_loop(env):
    client, mgr, tmp = env
    jid = post(client, [("loves", None, b'[{"name":"x"}]')]).json()["job_id"]
    wait_status(client, jid)
    all_evs, _ = read_sse(client, f"/api/jobs/{jid}/events")
    cut = all_evs[3]["seq"]
    rest, ids = read_sse(client, f"/api/jobs/{jid}/events", headers={"Last-Event-ID": str(cut)})
    assert ids[0] == cut + 1 and rest[-1]["type"] == "result"
    again = client.get(f"/api/jobs/{jid}/events", headers={"Last-Event-ID": str(all_evs[-1]["seq"])})
    assert again.status_code == 204                                                    # EventSource stops reconnecting on 204
    q = client.get(f"/api/jobs/{jid}/events?last_event_id={cut}")
    assert q.status_code == 200 and q.text.count("\ndata: ") == len(all_evs) - cut


def test_sse_streams_live_events_while_job_runs(tmp_path):
    """TestClient buffers streaming responses, so this one talks to a real uvicorn server over a socket."""
    import socket

    import httpx
    import uvicorn
    gate = threading.Event()

    def slow_taste(ctx):
        ctx.progress(0.3, "working")
        gate.wait(10)
        return {}

    mgr = JobManager(tmp_path / "jobs", stages={**FAKES, "taste": slow_taste})
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(appmod.create_app(mgr, serve_viewer=False), host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{port}"
        t0 = time.time()
        while time.time() - t0 < 5:
            try:
                httpx.get(base + "/api/health", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        body, ctype = multipart([("loves", None, b'["x"]')])
        jid = httpx.post(base + "/api/jobs", content=body, headers={"content-type": ctype}).json()["job_id"]
        seen, got_live = [], None
        with httpx.stream("GET", f"{base}/api/jobs/{jid}/events", timeout=15) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    e = json.loads(line[6:])
                    seen.append(e)
                    if e["type"] == "stage_progress" and e.get("label") == "working":
                        got_live = time.time()
                        assert not gate.is_set()               # delivered while the stage is still blocked
                        gate.set()
                    if e["type"] == "result":
                        break
        assert got_live is not None and seen[-1]["type"] == "result"
        assert r.headers["cache-control"].startswith("no-cache")
    finally:
        gate.set()
        server.should_exit = True
        th.join(5)
        mgr.stop()


def test_html_export_rejected_with_friendly_message(env):
    client, mgr, tmp = env
    z = instagram_zip(tmp / "h.zip", html=True)
    r = post(client, [("instagram", "h.zip", z.read_bytes())])
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "bad_export" and "JSON" in err["message"]
    assert list((tmp / "jobs").iterdir()) == []                                        # nothing left behind


def test_bad_inputs(env):
    client, mgr, tmp = env
    assert post(client, [("instagram", "x.zip", b"not a zip")]).status_code == 400
    assert post(client, [("note", None, b"hi")]).json()["error"]["code"] == "no_input"
    assert post(client, [("loves", None, b"[not json")]).json()["error"]["code"] == "bad_field"
    assert post(client, [("loves", None, b'["a"]'), ("room_id", None, b"nope")]).json()["error"]["code"] == "bad_room"
    assert post(client, [("loves", None, b'["a"]'), ("room", "r.json", b'{"floor": 1}')]).json()["error"]["code"] == "bad_room"
    assert client.post("/api/jobs", json={"a": 1}).status_code == 415
    assert list((tmp / "jobs").iterdir()) == []


def test_upload_size_limit_enforced_while_streaming(env, monkeypatch):
    client, mgr, tmp = env
    monkeypatch.setattr(appmod, "MAX_UPLOAD", 2 * 1024 * 1024)
    z = instagram_zip(tmp / "big.zip", pad_mb=4)
    body, ctype = multipart([("instagram", "big.zip", z.read_bytes())])
    r = client.post("/api/jobs", content=iter([body[i:i + 65536] for i in range(0, len(body), 65536)]), headers={"content-type": ctype})   # chunked: no Content-Length
    assert r.status_code == 413 and "limit" in r.json()["error"]["message"]
    r2 = client.post("/api/jobs", content=body, headers={"content-type": ctype})                                                              # with Content-Length
    assert r2.status_code == 413
    assert list((tmp / "jobs").iterdir()) == []


def test_room_upload_and_scan_stub(env):
    client, mgr, tmp = env
    room = json.loads((appmod.pipeline.SAMPLES / "room.json").read_text())
    r = post(client, [("loves", None, b'["a"]'), ("room", "room.json", json.dumps(room).encode()), ("scan", "scan.glb", b"glTF" + b"0" * 100)])
    jid = r.json()["job_id"]
    wait_status(client, jid)
    assert (tmp / "jobs" / jid / "input" / "room.json").exists() and (tmp / "jobs" / jid / "input" / "scan.bin").exists()
    assert json.loads((tmp / "jobs" / jid / "job.json").read_text())["cfg"]["scan_stored"] is True


def test_path_traversal_blocked(env):
    client, mgr, tmp = env
    jid = post(client, [("loves", None, b'["a"]')]).json()["job_id"]
    wait_status(client, jid)
    for p in ("../job.json", "..%2fjob.json", "%2e%2e/events.jsonl", "images/../../input/x", "/etc/passwd", "..\\job.json", "images/%2e%2e/%2e%2e/job.json"):
        r = client.get(f"/api/jobs/{jid}/viewer/{p}")
        assert r.status_code == 404, p
        assert "ptah" not in r.text and "events" not in r.text.lower().replace("no such", "")
    assert client.get("/api/jobs/..%2f..%2fetc/viewer/x").status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 404
    assert client.get(f"/api/jobs/{'0' * 16}").status_code == 404


def test_delete_removes_files_and_cancels_running(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(ctx):
        if started.is_set():
            return {}
        started.set()
        for _ in range(100):
            ctx.progress(0.1)
            time.sleep(0.05)
        return {}

    mgr = JobManager(tmp_path / "jobs", stages={**FAKES, "taste": slow})
    client = TestClient(appmod.create_app(mgr, serve_viewer=False))
    jid = post(client, [("loves", None, b'["a"]')]).json()["job_id"]
    assert started.wait(5)
    r = client.delete(f"/api/jobs/{jid}")
    assert r.status_code == 202 and r.json()["status"] == "cancelling"
    t0 = time.time()
    while (tmp_path / "jobs" / jid).exists() and time.time() - t0 < 5:
        time.sleep(0.05)
    assert not (tmp_path / "jobs" / jid).exists() and client.get(f"/api/jobs/{jid}").status_code == 404
    # finished job: immediate delete
    jid2 = post(client, [("loves", None, b'["a"]')]).json()["job_id"]
    wait_status(client, jid2)
    r2 = client.delete(f"/api/jobs/{jid2}")
    assert r2.status_code in (200, 202)
    assert client.delete(f"/api/jobs/{jid2}").status_code in (404, 202)
    mgr.stop()


def test_failed_job_can_be_resumed(tmp_path):
    from ptah.pipeline import StageError
    state = {"fail": True, "calls": []}

    def flaky_brief(ctx):
        state["calls"].append("brief")
        if state["fail"]:
            raise StageError("Qloo is not answering right now.", "Try again in a minute.", "qloo_unavailable")
        return FAKES["brief"](ctx)

    mgr = JobManager(tmp_path / "jobs", stages={**FAKES, "brief": flaky_brief})
    client = TestClient(appmod.create_app(mgr, serve_viewer=False))
    jid = post(client, [("loves", None, b'["a"]')]).json()["job_id"]
    st = wait_status(client, jid)
    assert st["status"] == "error" and st["error"]["message"].startswith("Qloo") and st["error"]["retryable"]
    state["fail"] = False
    assert client.post(f"/api/jobs/{jid}/resume").status_code == 202
    st = wait_status(client, jid, want=("done",))
    assert st["status"] == "done" and st["last_seq"] > 5
    evs, ids = read_sse(client, f"/api/jobs/{jid}/events", stop=("result",))
    assert ids == list(range(1, len(ids) + 1)) and any((e.get("detail") or {}).get("cached") for e in evs)
    assert [e["type"] for e in evs].count("error") == 1 and evs[-1]["type"] == "result"
    assert client.post(f"/api/jobs/{jid}/resume").status_code == 409
    mgr.stop()


def test_jobs_are_reloaded_after_restart(env):
    client, mgr, tmp = env
    jid = post(client, [("loves", None, b'["a"]')]).json()["job_id"]
    wait_status(client, jid)
    mgr2 = JobManager(tmp / "jobs", stages=dict(FAKES), autostart=False)
    c2 = TestClient(appmod.create_app(mgr2, serve_viewer=False))
    assert c2.get(f"/api/jobs/{jid}").json()["status"] == "done"
    evs, _ = read_sse(c2, f"/api/jobs/{jid}/events")
    assert evs[-1]["type"] == "result"


def test_demo_replay_completes(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    (d / "bundle" / "images").mkdir(parents=True)
    for n in ("room.json", "items.json", "layout.json", "lighting.json"):
        (d / "bundle" / n).write_bytes((appmod.pipeline.SAMPLES / n).read_bytes())
    (d / "bundle" / "images" / "a.jpg").write_bytes(b"x")
    tl = [{"t": 0, "type": "stage_start", "stage": "taste", "label": "Understanding your taste"},
          {"t": 0.2, "type": "activity_start", "stage": "taste", "service": "qloo", "label": "Qloo: x", "id": "a1"},
          {"t": 0.6, "type": "activity_end", "stage": "taste", "service": "qloo", "label": "Qloo: x", "id": "a1", "duration_ms": 400},
          {"t": 0.8, "type": "stage_progress", "stage": "taste", "stage_progress": 0.5, "label": "round 2"},
          {"t": 1.0, "type": "stage_end", "stage": "taste", "duration_ms": 1000},
          {"t": 1.1, "type": "result", "label": "Your room is ready"}]
    (d / "events.json").write_text(json.dumps(tl))
    monkeypatch.setattr(demo_mod, "DEMO_DIR", d)
    mgr = JobManager(tmp_path / "jobs", stages=dict(FAKES))
    client = TestClient(appmod.create_app(mgr, serve_viewer=False))
    assert client.get("/api/health").json()["demo"] is True
    r = client.post("/api/demo?seconds=1")
    assert r.status_code == 201
    jid = r.json()["job_id"]
    evs, ids = read_sse(client, f"/api/jobs/{jid}/events")
    assert evs[-1]["type"] == "result" and evs[-1]["detail"]["demo"] is True and evs[-1]["progress"] == 1.0
    assert [e["type"] for e in evs if e["type"].startswith("activity")] == ["activity_start", "activity_end"]
    st = wait_status(client, jid)
    assert st["status"] == "done" and st["kind"] == "demo"
    assert client.get(f"/api/jobs/{jid}/viewer/items.json").status_code == 200
    mgr.stop()


def test_demo_unavailable_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_mod, "DEMO_DIR", tmp_path / "nope")
    mgr = JobManager(tmp_path / "jobs", stages=dict(FAKES))
    client = TestClient(appmod.create_app(mgr, serve_viewer=False))
    assert client.post("/api/demo").status_code == 404
    mgr.stop()


def test_rooms_and_health(env):
    client, mgr, tmp = env
    rooms = client.get("/api/rooms").json()
    assert rooms[0]["id"] == "sample-living-room" and "path" not in rooms[0]
    h = client.get("/api/health").json()
    assert h["ok"] is True and h["max_upload_mb"] == 300


def test_qloo_typeahead_cached_and_suppressed(env, monkeypatch):
    client, mgr, tmp = env
    f = FakeQloo()
    monkeypatch.setenv("QLOO_API_KEY", KEY)
    monkeypatch.setenv("PTAH_QLOO_CACHE_DIR", str(tmp / "qcache"))
    monkeypatch.setattr(qloo, "_http_get", f)
    qloo.reset(50)
    r = client.get("/api/qloo/search", params={"q": "Radiohead", "type": "artist"})
    assert r.status_code == 200 and r.json()[0]["name"] == "Radiohead" and set(r.json()[0]) >= {"qloo_id", "name", "type", "tags"}
    n = len(f.requests)
    assert client.get("/api/qloo/search", params={"q": "Radiohead", "type": "artist"}).json() == r.json()
    assert len(f.requests) == n                                                         # served from the in-memory cache
    assert client.get("/api/qloo/search", params={"q": "a"}).json() == []
    assert client.get("/api/qloo/search", params={"q": "abc", "type": "bogus"}).status_code == 400
    assert KEY not in r.text


def test_qloo_typeahead_without_key(env, monkeypatch):
    client, mgr, tmp = env
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    monkeypatch.setattr(qloo, "api_key", lambda: None)
    r = client.get("/api/qloo/search", params={"q": "Radiohead"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "qloo_not_configured"

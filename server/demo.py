"""Demo mode: replay a recorded run (events + finished bundle) in ~45 s. No keys, no network, no LLM.

Fixture (private, under data/ because it holds the recorder's own taste data): ``data/demo/events.json`` (see tools/record_demo.py)
and ``data/demo/bundle/`` (the viewer files). The replay goes through the same JobSink as a real job, so the UI cannot tell the difference.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from ptah import events, pipeline
from ptah.env import ROOT

DEMO_DIR = Path(__import__("os").environ.get("PTAH_DEMO_DIR") or ROOT / "data" / "demo")


def available(demo_dir: Path | None = None) -> bool:
    d = demo_dir or DEMO_DIR
    return (d / "events.json").exists() and (d / "bundle" / "items.json").exists()


def start(mgr, target_seconds: float = 45.0, demo_dir: Path | None = None):
    d = demo_dir or DEMO_DIR
    tl = json.loads((d / "events.json").read_text())
    jid, path = mgr.new_dir()
    cfg = pipeline.JobConfig(has_zip=True, loves=[], room={"sample": "sample-living-room"})
    from .jobs import JobRecord
    rec = JobRecord(id=jid, dir=path, cfg=cfg, kind="demo", status="running")
    rec.sink = mgr.make_sink(rec)
    with mgr.lock:
        mgr.jobs[jid] = rec
    rec.save()
    threading.Thread(target=_play, args=(rec, tl, d, target_seconds), name=f"ptah-demo-{jid}", daemon=True).start()
    return rec


def _play(rec, tl: list[dict], d: Path, target: float) -> None:
    sink = rec.sink
    span = max((e.get("t", 0) for e in tl), default=1) or 1
    k = target / span
    t0 = time.monotonic()
    try:
        for e in tl:
            wait = e.get("t", 0) * k - (time.monotonic() - t0)
            if wait > 0:
                if rec.cancel.wait(wait):
                    raise InterruptedError
            if rec.cancel.is_set():
                raise InterruptedError
            kind = e["type"]
            if kind == "result":
                shutil.copytree(d / "bundle", rec.dir / "viewer", dirs_exist_ok=True)
                items = json.loads((rec.dir / "viewer" / "items.json").read_text())
                det = {**(e.get("detail") or {}), "viewer": "viewer/", "demo": True, "items": len(items)}
                sink.emit("result", label=e.get("label") or "Your room is ready", detail=det)
                rec.status, rec.result = "done", {"viewer": "viewer/", "items": len(items), "demo": True}
                break
            sink.emit(kind, stage=e.get("stage"), service=e.get("service"), label=e.get("label", ""), detail=e.get("detail"),
                      stage_progress=e.get("stage_progress"), id=e.get("id"),
                      duration_ms=int(e["duration_ms"] * k) if e.get("duration_ms") else e.get("duration_ms"))
    except InterruptedError:
        rec.status = "cancelled"
    except Exception as ex:  # noqa: BLE001
        rec.status, rec.error = "error", {"code": "demo_failed", "message": "The demo could not be played.", "technical": events.scrub(str(ex))}
        sink.emit("error", label=rec.error["message"], detail={"code": "demo_failed"})
    finally:
        sink.close()
        if rec.deleting:
            mgr._remove(rec)
        else:
            rec.save()

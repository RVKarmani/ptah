"""Job registry + the single worker thread (one job at a time: Qloo state, DSPy settings and the posts dir are process-wide)."""
from __future__ import annotations

import json
import queue
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ptah import events, pipeline

JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
TERMINAL = ("done", "error", "cancelled", "interrupted")


@dataclass
class JobRecord:
    id: str
    dir: Path
    cfg: pipeline.JobConfig
    kind: str = "real"                    # real | demo
    status: str = "queued"                # queued | running | done | error | cancelled | interrupted
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    partial: bool = False
    error: dict | None = None
    result: dict | None = None
    sink: events.JobSink | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    deleting: bool = False
    resume: bool = False

    def public(self) -> dict:
        d = {"job_id": self.id, "kind": self.kind, "status": self.status, "created": round(self.created, 1), "updated": round(self.updated, 1),
             "partial": self.partial, "error": self.error, "result": self.result, "inputs": {
                 "instagram": self.cfg.has_zip, "loves": len(self.cfg.loves), "room": self.cfg.room, "owned_items": len(self.cfg.owned.get("items") or []),
                 "preferences": self.cfg.prefs}}
        if self.sink:
            last = self.sink.events[-1] if self.sink.events else None
            d["progress"] = last["progress"] if last else 0.0
            d["stage"] = self.sink.stage
            d["plan"] = self.sink.plan_info()
            d["last_seq"] = len(self.sink.events)
        return d

    def save(self) -> None:
        self.updated = time.time()
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps({"id": self.id, "kind": self.kind, "status": self.status, "created": self.created, "updated": self.updated,
                                   "partial": self.partial, "error": self.error, "result": self.result, "cfg": self.cfg.to_json()}))
        tmp.replace(self.dir / "job.json")


class JobManager:
    def __init__(self, jobs_dir: Path | None = None, runner: Callable | None = None, stages: dict | None = None,
                 ttl_hours: float = 72.0, autostart: bool = True):
        self.dir = Path(jobs_dir or pipeline.JOBS_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner or pipeline.run_job
        self.stages = stages                      # test hook: fake stage functions
        self.ttl = ttl_hours * 3600
        self.jobs: dict[str, JobRecord] = {}
        self.q: "queue.Queue[str | None]" = queue.Queue()
        self.lock = threading.RLock()
        self.current: str | None = None
        self._thread: threading.Thread | None = None
        self._load_existing()
        if autostart:
            self.start()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="ptah-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _load_existing(self) -> None:
        now = time.time()
        for p in sorted(self.dir.iterdir()) if self.dir.exists() else []:
            jf = p / "job.json"
            if not (p.is_dir() and JOB_ID_RE.match(p.name) and jf.exists()):
                continue
            try:
                d = json.loads(jf.read_text())
                rec = JobRecord(id=d["id"], dir=p, cfg=pipeline.JobConfig.from_json(d.get("cfg") or {}), kind=d.get("kind", "real"),
                                status=d.get("status", "interrupted"), created=d.get("created", now), updated=d.get("updated", now),
                                partial=d.get("partial", False), error=d.get("error"), result=d.get("result"))
            except (OSError, ValueError, KeyError):
                continue
            if now - rec.updated > self.ttl:
                shutil.rmtree(p, ignore_errors=True)
                continue
            if rec.status in ("queued", "running"):
                rec.status = "interrupted"
                rec.error = {"code": "interrupted", "message": "The server restarted while this job was running.", "retryable": True}
            rec.sink = events.JobSink.load(rec.id, p / "events.jsonl")
            self.jobs[rec.id] = rec

    # ------------------------------------------------------------------ create / submit
    def new_dir(self) -> tuple[str, Path]:
        while True:
            jid = uuid.uuid4().hex[:16]
            p = self.dir / jid
            try:
                p.mkdir(parents=True)
                return jid, p
            except FileExistsError:
                continue

    def make_sink(self, rec: JobRecord) -> events.JobSink:
        sink = events.JobSink(rec.id, rec.dir / "events.jsonl", weights=pipeline.weights_for(rec.cfg))
        sink.set_plan(pipeline.plan_for(rec.cfg))
        return sink

    def submit(self, jid: str, cfg: pipeline.JobConfig, path: Path) -> JobRecord:
        rec = JobRecord(id=jid, dir=path, cfg=cfg)
        rec.sink = self.make_sink(rec)
        with self.lock:
            self.jobs[jid] = rec
            ahead = self.q.qsize() + (1 if self.current else 0)
        rec.save()
        rec.sink.emit("log", label="Waiting in line" if ahead else "Starting", detail={"ahead": ahead, "plan": rec.sink.plan_info()})
        self.q.put(jid)
        return rec

    def resubmit(self, rec: JobRecord) -> None:
        """Re-run an errored / interrupted job; finished stages are skipped."""
        old = rec.sink.events if rec.sink else []
        rec.status, rec.error, rec.resume, rec.partial = "queued", None, True, False
        rec.cancel.clear()
        rec.sink = self.make_sink(rec)
        rec.sink.preload(old)
        rec.sink.emit("log", label="Trying again", detail={"resume": True})
        rec.save()
        self.q.put(rec.id)

    def get(self, jid: str) -> JobRecord | None:
        return self.jobs.get(jid) if JOB_ID_RE.match(jid or "") else None

    def queue_position(self, jid: str) -> int:
        with self.q.mutex:
            items = list(self.q.queue)
        return items.index(jid) + 1 if jid in items else 0

    # ------------------------------------------------------------------ delete
    def delete(self, jid: str) -> str:
        """-> 'deleted' | 'cancelling' (running job: files go when the worker stops) | 'missing'."""
        rec = self.get(jid)
        if rec is None:
            return "missing"
        rec.deleting = True
        rec.cancel.set()
        if self.current == jid:
            return "cancelling"
        self._remove(rec)
        return "deleted"

    def _remove(self, rec: JobRecord) -> None:
        with self.lock:
            self.jobs.pop(rec.id, None)
        if rec.sink:
            rec.sink.close()
        shutil.rmtree(rec.dir, ignore_errors=True)

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            jid = self.q.get()
            if jid is None:
                return
            rec = self.jobs.get(jid)
            if rec is None or rec.deleting or rec.status not in ("queued",):
                continue
            self.current = jid
            try:
                self._run(rec)
            except BaseException as e:  # noqa: BLE001 - the worker must survive anything
                rec.status, rec.error = "error", {"code": "internal", "message": "Something went wrong while building your room.", "technical": events.scrub(f"{type(e).__name__}: {e}")}
                if rec.sink and not rec.sink.closed:
                    rec.sink.emit("error", label=rec.error["message"], detail={"code": "internal"})
                    rec.sink.close()
            finally:
                self.current = None
                if rec.deleting:
                    self._remove(rec)
                else:
                    rec.save()

    def _run(self, rec: JobRecord) -> None:
        rec.status = "running"
        rec.save()
        res = self.runner(rec.dir, rec.cfg, rec.sink, stages=self.stages, resume=rec.resume, cancelled=lambda: rec.cancel.is_set())
        evs = rec.sink.events
        last = evs[-1] if evs else {}
        if res.get("ok"):
            rec.status, rec.partial = "done", bool(res.get("partial"))
            rec.result = {"viewer": f"viewer/", **{k: v for k, v in (last.get("detail") or {}).items() if k in ("items", "products", "owned", "cost", "warnings", "skipped")}}
        elif res.get("code") == "cancelled":
            rec.status = "cancelled"
        else:
            rec.status = "error"
            d = last.get("detail") or {}
            rec.error = {"code": d.get("code", res.get("code")), "message": last.get("label") or res.get("message"), "hint": d.get("hint"),
                         "stage": d.get("stage"), "retryable": d.get("retryable", True)}

    def active_count(self) -> int:
        return sum(1 for r in self.jobs.values() if r.status in ("queued", "running"))

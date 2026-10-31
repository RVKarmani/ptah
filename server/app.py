"""Ptah backend (FastAPI). Run: ``python -m server`` (see README "Backend").

POST /api/jobs                    multipart upload -> {job_id, ...}; the job runs in the background (one worker)
GET  /api/jobs/{id}               status + progress + plan
GET  /api/jobs/{id}/events        Server-Sent Events (Last-Event-ID resume, heartbeat)
GET  /api/jobs/{id}/viewer/{p}    the finished bundle (room/items/layout/lighting/taste json, images/)
POST /api/jobs/{id}/resume        retry a failed / interrupted job (finished stages are skipped)
DELETE /api/jobs/{id}             cancel + delete all files
GET  /api/qloo/search?q=          typeahead for "things you love" (cached)
GET  /api/rooms                   sample rooms
POST /api/demo                    replay a recorded run
GET  /api/health
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import zipfile
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ptah import events, pipeline
from ptah.env import ROOT

from . import demo as demo_mod
from .jobs import JobManager, JobRecord
from .uploads import ROOM_MAX, UploadError, receive_form

MAX_UPLOAD = int(os.environ.get("PTAH_MAX_UPLOAD_MB", 300)) * 1024 * 1024
HEARTBEAT_S = 15.0
VIEWER_DIST = ROOT / "viewer" / "dist"
QLOO_TYPES = {"artist", "movie", "tv_show", "book", "brand", "place", "destination", "designer", "podcast", "video_game", "actor", "author", "director", "person"}


def err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def _wire(ev: dict) -> dict:
    """Event as sent to the browser: null fields omitted."""
    return {k: v for k, v in ev.items() if v is not None}


def sse(ev: dict) -> bytes:
    return f"id: {ev['seq']}\ndata: {json.dumps(_wire(ev), ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


def create_app(manager: JobManager | None = None, *, serve_viewer: bool = True) -> FastAPI:
    app = FastAPI(title="Ptah backend", docs_url="/api/docs", openapi_url="/api/openapi.json")
    mgr = manager or JobManager()
    app.state.jobs = mgr
    app.add_middleware(GZipMiddleware, minimum_size=1000)           # JSON bundle files; text/event-stream is excluded by Starlette
    origins = [o for o in os.environ.get("PTAH_CORS_ORIGINS", "").split(",") if o]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["Last-Event-ID"])
    qcache: dict[tuple, list] = {}

    def job_or_404(jid: str) -> JobRecord | None:
        return mgr.get(jid)

    # ------------------------------------------------------------------ jobs
    @app.post("/api/jobs", status_code=201)
    async def create_job(request: Request):
        jid, path = mgr.new_dir()
        inp = path / "input"
        try:
            fields, files = await receive_form(request, inp, {"instagram": ("instagram.zip", MAX_UPLOAD), "room": ("room.json", ROOM_MAX),
                                                               "scan": ("scan.bin", MAX_UPLOAD)}, MAX_UPLOAD)
            cfg = await run_in_threadpool(_build_config, fields, files, inp)
        except UploadError as e:
            _discard(path)
            return err(e.status, "upload_failed", e.message)
        except pipeline.StageError as e:
            _discard(path)
            return err(400, e.code, e.message, hint=e.hint)
        except BadRequest as e:
            _discard(path)
            return err(400, e.code, str(e))
        rec = mgr.submit(jid, cfg, path)
        return {"job_id": jid, "status": rec.status, "status_url": f"/api/jobs/{jid}", "events_url": f"/api/jobs/{jid}/events",
                "viewer_base": f"/api/jobs/{jid}/viewer/", "viewer_url": f"/?data=/api/jobs/{jid}/viewer/"}

    @app.get("/api/jobs/{jid}")
    async def job_status(jid: str):
        rec = job_or_404(jid)
        if not rec:
            return err(404, "not_found", "No such job.")
        d = rec.public()
        d["queue_position"] = mgr.queue_position(jid)
        d["events_url"], d["viewer_base"] = f"/api/jobs/{jid}/events", f"/api/jobs/{jid}/viewer/"
        d["viewer_url"] = f"/?data=/api/jobs/{jid}/viewer/" if rec.status == "done" else None
        return d

    @app.get("/api/jobs/{jid}/events")
    async def job_events(jid: str, request: Request, last_event_id: int | None = None):
        rec = job_or_404(jid)
        if not rec or rec.sink is None:
            return err(404, "not_found", "No such job.")
        sink = rec.sink
        hdr = request.headers.get("last-event-id", "")
        last = last_event_id if last_event_id is not None else (int(hdr) if hdr.isdigit() else 0)
        if sink.closed and last >= len(sink.events) > 0:
            return Response(status_code=204)                         # finished and fully delivered: tells EventSource to stop reconnecting

        async def gen():
            nonlocal last
            loop = asyncio.get_running_loop()
            wake = asyncio.Event()
            sink.add_waiter(loop, wake)
            yield b"retry: 2000\n\n"
            try:
                while True:
                    wake.clear()
                    batch = sink.since(last)
                    if batch:
                        last = batch[-1]["seq"]
                        yield b"".join(sse(e) for e in batch)
                        if batch[-1]["type"] in ("result", "error"):
                            return
                        continue
                    if sink.closed:
                        return
                    try:
                        await asyncio.wait_for(wake.wait(), HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        yield b": hb\n\n"
                    if await request.is_disconnected():
                        return
            finally:
                sink.remove_waiter(loop, wake)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs/{jid}/viewer/{rel:path}")
    async def job_file(jid: str, rel: str, request: Request):
        rec = job_or_404(jid)
        if not rec:
            return err(404, "not_found", "No such job.")
        root = (rec.dir / "viewer").resolve()
        try:
            p = (root / rel).resolve()
            p.relative_to(root)
        except (ValueError, OSError):
            return err(404, "not_found", "No such file.")
        if not p.is_file():
            return err(404, "not_found", "No such file.")
        finished = rec.status == "done"
        cache = "public, max-age=86400" if (finished and "images" in p.relative_to(root).parts) else "no-cache"   # no-cache = revalidate (ETag/304)
        stt = p.stat()
        etag = f'"{stt.st_mtime_ns:x}-{stt.st_size:x}"'
        hdrs = {"Cache-Control": cache, "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=hdrs)
        return FileResponse(p, headers=hdrs)

    @app.post("/api/jobs/{jid}/resume", status_code=202)
    async def job_resume(jid: str):
        rec = job_or_404(jid)
        if not rec:
            return err(404, "not_found", "No such job.")
        if rec.status not in ("error", "interrupted", "cancelled") or rec.kind != "real":
            return err(409, "not_resumable", "Only a failed or interrupted job can be retried.")
        mgr.resubmit(rec)
        return {"job_id": jid, "status": rec.status}

    @app.delete("/api/jobs/{jid}")
    async def job_delete(jid: str):
        r = mgr.delete(jid)
        if r == "missing":
            return err(404, "not_found", "No such job.")
        return JSONResponse({"job_id": jid, "status": r}, status_code=200 if r == "deleted" else 202)

    # ------------------------------------------------------------------ misc
    @app.get("/api/rooms")
    async def rooms():
        return [{k: v for k, v in r.items() if k != "path"} for r in pipeline.sample_rooms().values()]

    @app.get("/api/qloo/search")
    async def qloo_search(q: str = "", type: str = "", take: int = 6):
        q = " ".join(q.split())[:80]
        if len(q) < 2:
            return []
        t = type.strip().lower().replace(" ", "_")
        if t and t not in QLOO_TYPES:
            return err(400, "bad_type", "Unknown type.")
        key = (q.lower(), t, min(max(take, 1), 10))
        if key not in qcache:
            try:
                qcache[key] = await run_in_threadpool(_typeahead, mgr, q, key[1], key[2])
            except _Unavailable as e:
                return err(503, e.code, e.message)
            if len(qcache) > 2000:
                qcache.pop(next(iter(qcache)))
        return JSONResponse(qcache[key], headers={"Cache-Control": "private, max-age=300"})

    @app.post("/api/demo", status_code=201)
    async def demo(seconds: float = 45.0):
        if not demo_mod.available():
            return err(404, "no_demo", "No recorded demo is installed on this server (run tools/record_demo.py).")
        rec = demo_mod.start(mgr, target_seconds=min(max(seconds, 1.0), 300.0))
        jid = rec.id
        return {"job_id": jid, "status": rec.status, "status_url": f"/api/jobs/{jid}", "events_url": f"/api/jobs/{jid}/events",
                "viewer_base": f"/api/jobs/{jid}/viewer/", "viewer_url": f"/?data=/api/jobs/{jid}/viewer/"}

    @app.get("/api/health")
    async def health():
        from ptah import qloo
        return {"ok": True, "qloo": not qloo.is_stub(), "llm": bool(os.environ.get("PTAH_LM")), "demo": demo_mod.available(),
                "active_jobs": mgr.active_count(), "max_upload_mb": MAX_UPLOAD // (1024 * 1024)}

    # Local-only: your private room bundle (viewer/public/me, git-ignored) at /me/. Never enabled when PTAH_PUBLIC=1.
    private_me = ROOT / "viewer" / "public" / "me"
    if serve_viewer and private_me.is_dir() and os.environ.get("PTAH_PUBLIC") != "1":
        app.mount("/me", StaticFiles(directory=private_me), name="private-me")
    if serve_viewer and (VIEWER_DIST / "index.html").exists():
        app.mount("/", StaticFiles(directory=VIEWER_DIST, html=True), name="viewer")
    return app


# ---------------------------------------------------------------------------------------------------------------- helpers
class BadRequest(Exception):
    def __init__(self, message: str, code: str = "bad_request"):
        super().__init__(message)
        self.code = code


class _Unavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _discard(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _json_field(fields: dict, name: str, default):
    raw = (fields.get(name) or "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        raise BadRequest(f"The field '{name}' is not valid JSON.", "bad_field") from None


def _build_config(fields: dict, files: dict, inp: Path) -> pipeline.JobConfig:
    """Validate the form (runs in a thread: opens the zip's directory, parses room.json)."""
    import ingest_instagram as ig
    cfg = pipeline.JobConfig()
    if "instagram" in files:
        try:
            ig.locate(files["instagram"])
        except ig.IngestError as e:
            raise BadRequest(str(e), "bad_export") from None
        except (zipfile.BadZipFile, OSError):
            raise BadRequest("That file is not a readable zip archive. Upload the .zip you downloaded from Instagram.", "bad_export") from None
        cfg.has_zip = True
    loves = _json_field(fields, "loves", [])
    if isinstance(loves, str):
        loves = [{"name": x} for x in re.split(r"[\n,]", loves) if x.strip()]
    if not isinstance(loves, list):
        raise BadRequest("'loves' must be a list.", "bad_field")
    cfg.loves = [{"name": str(l.get("name") if isinstance(l, dict) else l).strip()[:80],
                  **({"type": str(l["type"])[:20]} if isinstance(l, dict) and l.get("type") else {}),
                  **({"qloo_id": str(l["qloo_id"])[:40]} if isinstance(l, dict) and l.get("qloo_id") else {})}
                 for l in loves[:30] if (l.get("name") if isinstance(l, dict) else l)]
    if not cfg.has_zip and not cfg.loves:
        raise BadRequest("Add an Instagram export or at least one thing you love.", "no_input")
    if "room" in files:
        try:
            room = pipeline.validate_room(json.loads(files["room"].read_text(encoding="utf-8")))
        except ValueError:
            raise BadRequest("The room file is not valid JSON.", "bad_room") from None
        files["room"].write_text(json.dumps(room))
        cfg.room = {"upload": True}
    else:
        rid = (fields.get("room_id") or "sample-living-room").strip()
        if rid not in pipeline.sample_rooms():
            raise BadRequest(f"Unknown sample room '{rid[:40]}'.", "bad_room")
        cfg.room = {"sample": rid}
    cfg.scan_stored = "scan" in files
    owned = _json_field(fields, "owned", {})
    if isinstance(owned, list):
        owned = {"items": owned}
    cfg.owned = {"sample": bool(owned.get("sample", False)), "items": [i for i in (owned.get("items") or []) if isinstance(i, dict)][:20]}
    prefs = _json_field(fields, "preferences", {})
    try:
        budget = float(prefs["budget"]) if prefs.get("budget") not in (None, "") else None
    except (TypeError, ValueError):
        raise BadRequest("Budget must be a number.", "bad_field") from None
    cfg.prefs = {"budget": budget, "can_paint": bool(prefs.get("can_paint", True)), "location": str(prefs.get("location") or pipeline.DEFAULT_LOCATION)[:60]}
    return cfg


def _typeahead(mgr: JobManager, q: str, t: str, take: int) -> list[dict]:
    from ptah import qloo
    if qloo.is_stub():
        raise _Unavailable("qloo_not_configured", "Qloo is not connected on this server.")
    with events.suppressed():                                  # a typeahead lookup is not part of any job's activity
        try:
            try:
                hits = qloo.search(q, t, take)
            except qloo.CallCapReached:
                if mgr.active_count():
                    raise
                qloo.reset(200)
                hits = qloo.search(q, t, take)
        except qloo.QlooError as e:
            raise _Unavailable("qloo_unavailable", "Qloo is not answering right now.") from e
    return [{"qloo_id": h["qloo_id"], "name": h["name"], "type": h["type"], "tags": (h.get("tags") or [])[:3],
             "description": (h.get("description") or "")[:140] or None} for h in hits]


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        from ptah.env import load_dotenv
        load_dotenv()
        os.environ.setdefault("PTAH_DESIGN_MAX_LLM_CALLS", "10")  # free OpenRouter tier is ~50 requests/day; raise if you buy credits
        app = create_app()
    return app

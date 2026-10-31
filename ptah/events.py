"""Pipeline events: a tiny thread-safe event bus with a job-scoped sink.

Producers (Qloo client, SerpApi client, DSPy callback, pipeline stages) call the module-level helpers
(``emit``, ``activity``, ``progress``, ...). They are cheap no-ops when no job sink is bound, so the CLIs
(run_taste.py, run_brief.py, ...) are unaffected.

A :class:`JobSink` owns one job's event list: it assigns ``seq``, computes overall progress from stage
weights, throttles ``stage_progress`` (<= 10/s, with a trailing flush so the last value is never lost),
appends every event to ``events.jsonl`` and wakes SSE subscribers. Schema: docs/contracts.md ("Pipeline events").

The sink is found through a ContextVar first, then through a process-wide "active sink" fallback because
DSPy's sandbox bridge and thread pools call our hooks from threads that do not inherit the context.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

EVENT_TYPES = ("stage_start", "stage_progress", "stage_end", "activity_start", "activity_end", "log", "result", "error")
SERVICES = ("qloo", "llm", "serpapi", "compute")

# Stage order and default weights (sum = 1.0). Stages absent from a job's plan are dropped and the rest renormalised.
STAGES = ("ingest", "taste", "brief", "design", "products", "layout", "lighting")
STAGE_WEIGHTS = {"ingest": 0.05, "taste": 0.45, "brief": 0.15, "design": 0.15, "products": 0.12, "layout": 0.05, "lighting": 0.03}
STAGE_LABELS = {
    "ingest": "Reading your posts", "taste": "Understanding your taste", "brief": "Asking Qloo what people like you love",
    "design": "Designing your room", "products": "Finding real pieces", "layout": "Arranging the furniture",
    "lighting": "Planning the lighting",
}

PROGRESS_MIN_INTERVAL_S = 0.1          # <= 10 stage_progress events per second
_SECRET_WORDS = ("key", "token", "secret", "password", "authorization", "cookie")
_MAX_STR = 200
_MAX_ITEMS = 24


def clean_detail(detail: dict | None) -> dict:
    """Small, JSON-safe, secret-free detail dict (counts and names only)."""
    return _clean_dict(detail or {}, 0)


def _clean_dict(d: dict, depth: int) -> dict:
    out: dict[str, Any] = {}
    for k, v in list(d.items())[:_MAX_ITEMS]:
        if any(w in str(k).lower() for w in _SECRET_WORDS):
            continue
        out[str(k)] = _clean_value(v, depth + 1)
    return out


def _clean_value(v: Any, depth: int) -> Any:
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    if isinstance(v, str):
        return v if len(v) <= _MAX_STR else v[:_MAX_STR] + "..."
    if depth < 3 and isinstance(v, dict):
        return _clean_dict(v, depth)
    if depth < 3 and isinstance(v, (list, tuple)):
        return [_clean_value(x, depth + 1) for x in list(v)[:_MAX_ITEMS]]
    return str(v)[:_MAX_STR]


class JobSink:
    """Event collector for one job (see module docstring)."""

    def __init__(self, job_id: str, path: Path | None = None, plan: list[str] | None = None,
                 weights: dict[str, float] | None = None, min_interval: float = PROGRESS_MIN_INTERVAL_S):
        self.job_id = job_id
        self.events: list[dict] = []
        self.closed = False
        self.min_interval = min_interval
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._fh = open(path, "a", encoding="utf-8") if path else None
        self._waiters: list[tuple[Any, Any]] = []    # (loop, asyncio.Event)
        self._weights = dict(weights or STAGE_WEIGHTS)
        self._plan: list[str] = []
        self._share: dict[str, float] = {}
        self._done: set[str] = set()
        self.stage: str | None = None
        self._stage_progress = 0.0
        self._overall = 0.0
        self._last_progress_ts = 0.0
        self._pending: dict | None = None
        self._timer: threading.Timer | None = None
        self.llm_label = "AI model call"
        self.observers: list = []        # callables(event) run (inside the lock, keep them cheap) for every appended event
        if plan:
            self.set_plan(plan)

    # ------------------------------------------------------------------ plan / progress maths
    def set_plan(self, plan: list[str]) -> None:
        with self._lock:
            self._plan = list(plan)
            tot = sum(self._weights.get(s, 0.05) for s in self._plan) or 1.0
            self._share = {s: self._weights.get(s, 0.05) / tot for s in self._plan}

    def plan_info(self) -> list[dict]:
        return [{"stage": s, "label": STAGE_LABELS.get(s, s), "weight": round(self._share.get(s, 0), 4)} for s in self._plan]

    def _compute_overall(self) -> float:
        done = sum(self._share.get(s, 0) for s in self._done)
        cur = 0.0
        if self.stage and self.stage not in self._done:
            cur = self._share.get(self.stage, 0) * self._stage_progress
        return min(1.0, done + cur)

    # ------------------------------------------------------------------ emitting
    def emit(self, type: str, *, stage: str | None = None, service: str | None = None, label: str = "",
             detail: dict | None = None, stage_progress: float | None = None, duration_ms: int | None = None,
             id: str | None = None) -> dict | None:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        with self._lock:
            if self.closed:
                return None
            stage = stage or self.stage
            if type == "stage_start":
                self._flush_pending_locked()
                self.stage, self._stage_progress = stage, 0.0
            elif type == "stage_end":
                self._flush_pending_locked()
                if stage:
                    self._done.add(stage)
                self._stage_progress = 1.0
            elif type == "stage_progress" and stage_progress is not None and stage == self.stage:
                self._stage_progress = max(self._stage_progress, min(1.0, float(stage_progress)))
            elif type == "result":
                self._flush_pending_locked()
            now = time.time()
            ev = {"job_id": self.job_id, "seq": 0, "ts": round(now, 3), "type": type, "stage": stage,
                  "progress": 0.0, "stage_progress": 0.0, "service": service, "label": label,
                  "detail": clean_detail(detail), "duration_ms": duration_ms}
            if id:
                ev["id"] = id
            if type == "stage_progress":
                if now - self._last_progress_ts < self.min_interval:
                    self._pending = ev          # keep only the latest; flushed by the timer or the next event
                    self._schedule_flush_locked()
                    return None
                self._last_progress_ts = now
                self._pending = None            # this event carries the latest value; the trailing flush is no longer needed
            return self._append_locked(ev)

    def _append_locked(self, ev: dict) -> dict:
        self._overall = max(self._overall, self._compute_overall())
        if ev["type"] == "result":
            self._overall = 1.0
        ev["seq"] = len(self.events) + 1
        ev["progress"] = round(self._overall, 4)
        ev["stage_progress"] = 1.0 if ev["type"] in ("stage_end", "result") else round(self._stage_progress, 4)
        self.events.append(ev)
        for ob in list(self.observers):
            try:
                ob(ev)
            except Exception:  # noqa: BLE001 - observers must never break the pipeline
                pass
        if self._fh:
            self._fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._fh.flush()
        self._cond.notify_all()
        self._wake_locked()
        return ev

    def _wake_locked(self) -> None:
        for loop, aev in list(self._waiters):
            try:
                loop.call_soon_threadsafe(aev.set)
            except RuntimeError:     # loop closed
                self._waiters.remove((loop, aev))

    def _schedule_flush_locked(self) -> None:
        if self._timer is None:
            self._timer = threading.Timer(max(0.001, self.min_interval - (time.time() - self._last_progress_ts)), self._timer_flush)
            self._timer.daemon = True
            self._timer.start()

    def _timer_flush(self) -> None:
        with self._lock:
            self._timer = None
            if self._pending is None or self.closed:
                return
            wait = self.min_interval - (time.time() - self._last_progress_ts)
            if wait > 0.002:                    # an event went out meanwhile: keep the spacing, flush later
                self._timer = threading.Timer(wait, self._timer_flush)
                self._timer.daemon = True
                self._timer.start()
                return
            self._flush_pending_locked()

    def _flush_pending_locked(self) -> None:
        ev, self._pending = self._pending, None
        if ev is None or self.closed or ev["stage"] != self.stage:
            return
        self._last_progress_ts = time.time()
        ev["ts"] = round(self._last_progress_ts, 3)
        self._append_locked(ev)

    def close(self) -> None:
        with self._lock:
            self._flush_pending_locked()
            self.closed = True
            if self._timer:
                self._timer.cancel()
            if self._fh:
                self._fh.close()
                self._fh = None
            self._cond.notify_all()
            self._wake_locked()

    # ------------------------------------------------------------------ consumers
    def since(self, last_seq: int) -> list[dict]:
        """Events with seq > last_seq (cheap slice; seq == index + 1)."""
        with self._lock:
            return self.events[last_seq:]

    def wait(self, last_seq: int, timeout: float) -> list[dict]:
        """Blocking wait for new events (threads, tests)."""
        with self._cond:
            if len(self.events) <= last_seq and not self.closed:
                self._cond.wait(timeout)
            return self.events[last_seq:]

    def add_waiter(self, loop, aev) -> None:
        with self._lock:
            self._waiters.append((loop, aev))

    def remove_waiter(self, loop, aev) -> None:
        with self._lock:
            if (loop, aev) in self._waiters:
                self._waiters.remove((loop, aev))

    def preload(self, events: list[dict]) -> None:
        """Continue a previous run: adopt its events (seq keeps counting), finished stages and overall progress."""
        with self._lock:
            self.events = list(events)
            for ev in events:
                if ev.get("type") == "stage_end" and ev.get("stage"):
                    self._done.add(ev["stage"])
            self._overall = max([0.0] + [float(e.get("progress") or 0) for e in events])

    @classmethod
    def load(cls, job_id: str, path: Path) -> "JobSink":
        """A closed, read-only sink rebuilt from an events.jsonl file."""
        sink = cls(job_id, None)
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    sink.events.append(json.loads(line))
        except (OSError, ValueError):
            pass
        sink.closed = True
        return sink


# ---------------------------------------------------------------------------- binding / module-level producers
_ctx_sink: contextvars.ContextVar[JobSink | None] = contextvars.ContextVar("ptah_event_sink", default=None)
_active_sink: JobSink | None = None
_active_lock = threading.Lock()


_SUPPRESS = object()


def current() -> JobSink | None:
    c = _ctx_sink.get()
    if c is _SUPPRESS:
        return None
    return c or _active_sink


@contextlib.contextmanager
def suppressed() -> Iterator[None]:
    """Emit nothing from this context (e.g. a typeahead Qloo lookup running while a job is active)."""
    tok = _ctx_sink.set(_SUPPRESS)      # type: ignore[arg-type]
    try:
        yield
    finally:
        _ctx_sink.reset(tok)


@contextlib.contextmanager
def bind(sink: JobSink) -> Iterator[JobSink]:
    """Make ``sink`` the destination of all events from this context and (as a fallback) from every thread."""
    global _active_sink
    tok = _ctx_sink.set(sink)
    with _active_lock:
        prev, _active_sink = _active_sink, sink
    try:
        yield sink
    finally:
        with _active_lock:
            _active_sink = prev
        _ctx_sink.reset(tok)


def emit(type: str, **kw) -> dict | None:
    s = current()
    return s.emit(type, **kw) if s else None


def log(label: str, detail: dict | None = None, stage: str | None = None) -> None:
    emit("log", label=label, detail=detail, stage=stage)


def progress(fraction: float, label: str = "", detail: dict | None = None, stage: str | None = None) -> None:
    emit("stage_progress", stage_progress=fraction, label=label, detail=detail, stage=stage)


@contextlib.contextmanager
def activity(service: str, label: str, detail: dict | None = None, stage: str | None = None) -> Iterator[dict]:
    """activity_start / activity_end around a block (one external request). No-op without a sink.

    The yielded dict can be filled with extra detail for the end event; ``ok`` is set automatically."""
    sink = current()
    extra: dict[str, Any] = {}
    if sink is None:
        yield extra
        return
    aid = uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    sink.emit("activity_start", service=service, label=label, detail=detail, stage=stage, id=aid)
    ok = True
    try:
        yield extra
    except BaseException as e:
        ok = False
        extra["error"] = type(e).__name__
        raise
    finally:
        sink.emit("activity_end", service=service, label=label, detail={**(detail or {}), **extra, "ok": ok}, stage=stage,
                  id=aid, duration_ms=int((time.monotonic() - t0) * 1000))


def traced_llm(label: str):
    """Decorator: an ``llm`` activity around a function that makes one LLM request (no-op without a job sink)."""
    import functools

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with activity("llm", label):
                return fn(*a, **kw)
        return wrapper
    return deco


@contextlib.contextmanager
def creep(expected_s: float, lo: float = 0.0, hi: float = 0.9, label: str = "", stage: str | None = None) -> Iterator[None]:
    """While a long opaque call runs, nudge stage progress from ``lo`` towards ``hi`` (asymptotically, ~63% of the way after
    ``expected_s``), so the bar keeps moving. Stops on exit; the caller reports the real progress afterwards."""
    sink = current()
    if sink is None:
        yield
        return
    stop = threading.Event()
    t0 = time.monotonic()

    def run() -> None:
        import math
        while not stop.wait(0.5):
            f = lo + (hi - lo) * (1 - math.exp(-(time.monotonic() - t0) / max(expected_s, 1.0)))
            sink.emit("stage_progress", stage=stage, stage_progress=f, label=label)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    try:
        yield
    finally:
        stop.set()
        th.join(timeout=1)


def scrub(text: str, limit: int = 300) -> str:
    """Remove secret values (any env var whose name has KEY / TOKEN / SECRET) and long tails from an error message."""
    import os
    text = str(text)
    for k, v in os.environ.items():
        if v and len(v) >= 6 and any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET")):
            text = text.replace(v, "***")
    return text if len(text) <= limit else text[:limit] + "..."


def qloo_label(path: str, params: dict) -> str:
    """Human label for a Qloo request (names only, never the key)."""
    if path == "/search":
        return f'Qloo: looking up "{str(params.get("query", ""))[:40]}"'
    if path == "/v2/tags":
        return f'Qloo: tag lookup "{str(params.get("filter.query", ""))[:40]}"'
    ft = str(params.get("filter.type", ""))
    if ft == "urn:tag":
        tt = str(params.get("filter.tag.types", ""))
        kind = "decor" if "decor" in tt else "ambience" if "ambience" in tt else "style"
        return f"Qloo: {kind} tags for your taste"
    short = ft.rsplit(":", 1)[-1].replace("_", " ")
    return f"Qloo: {short or 'recommendations'} you might love"


# ---------------------------------------------------------------------------- DSPy callback (LLM calls + RLM iterations)
def make_dspy_callback():
    """A dspy BaseCallback emitting llm activity per LM call and taste stage_progress per RLM iteration."""
    from dspy.utils.callback import BaseCallback

    class EventsCallback(BaseCallback):
        def __init__(self):
            self._calls: dict[str, tuple[str, float, str]] = {}
            self._lock = threading.Lock()
            self.n_calls = 0

        def on_lm_start(self, call_id, instance, inputs):
            sink = current()
            if sink is None:
                return
            model = str(getattr(instance, "model", "") or "")
            aid = uuid.uuid4().hex[:8]
            with self._lock:
                self.n_calls += 1
                n = self.n_calls
                self._calls[call_id] = (aid, time.monotonic(), sink.llm_label)
            sink.emit("activity_start", service="llm", label=sink.llm_label, id=aid,
                      detail={"model": model.split("/")[-1][:60], "call": n})

        def on_lm_end(self, call_id, outputs, exception=None):
            sink = current()
            with self._lock:
                rec = self._calls.pop(call_id, None)
            if sink is None or rec is None:
                return
            aid, t0, label = rec
            sink.emit("activity_end", service="llm", label=label, id=aid, duration_ms=int((time.monotonic() - t0) * 1000),
                      detail={"ok": exception is None, **({"error": type(exception).__name__} if exception else {})})

        def on_module_start(self, call_id, instance, inputs):
            sink = current()
            if sink is None or sink.stage != "taste":
                return
            it = find_iteration(inputs)
            if it:
                n, m = it
                sink.emit("stage_progress", stage="taste", stage_progress=0.04 + 0.92 * min(1.0, (n - 1) / max(m, 1)),
                          label=f"Thinking about your posts (round {n} of {m})", detail={"iteration": n, "max_iters": m})

    return EventsCallback()


def find_iteration(obj: Any, depth: int = 0) -> tuple[int, int] | None:
    """Find an ``iteration="n/max"`` value in module-call inputs (RLM passes it to its action predictor)."""
    if isinstance(obj, dict):
        v = obj.get("iteration")
        if isinstance(v, str) and "/" in v:
            a, _, b = v.partition("/")
            if a.strip().isdigit() and b.strip().isdigit():
                return int(a), int(b)
        if depth < 2:
            for x in obj.values():
                if isinstance(x, dict):
                    r = find_iteration(x, depth + 1)
                    if r:
                        return r
    return None


_dspy_cb = None


def install_dspy_callback() -> None:
    """Register the callback once (call from the thread that owns dspy.settings, i.e. the pipeline worker)."""
    global _dspy_cb
    if _dspy_cb is not None:
        return
    import dspy
    _dspy_cb = make_dspy_callback()
    dspy.configure(callbacks=list(dspy.settings.get("callbacks", []) or []) + [_dspy_cb])

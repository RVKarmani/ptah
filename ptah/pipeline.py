"""Background pipeline: one job = inputs on disk -> stages -> a viewer bundle, with live events.

    ingest -> taste -> brief -> design -> products -> layout -> lighting

Each stage is a function ``stage(ctx) -> dict | None`` registered in ``STAGES`` (tests replace entries with fakes). The runner

* emits ``stage_start`` / ``stage_progress`` / ``stage_end`` (see ptah/events.py, docs/contracts.md "Pipeline events"),
* writes a ``work/state.json`` marker per finished stage, so ``run_job(..., resume=True)`` skips finished stages,
* turns failures into friendly ``error`` events (``StageError`` carries the message; anything else is scrubbed and generic),
* degrades softly: a failing ``products`` / ``layout`` / ``lighting`` stage keeps the designed bundle and the job still ends with a
  ``result`` (``partial: true``); a failing ``ingest`` / ``taste`` / ``brief`` / ``design`` stage ends the job with an ``error``.

Job directory (everything private; ``data/`` is git-ignored)::

    input/   instagram.zip (deleted after ingest), room.json, scan.<ext> (stored, not used yet)
    work/    ingest/{posts.jsonl,music.json,summary.json}  taste_profile.json  brief.json  design/  products/  state.json
    viewer/  room.json items.json layout.json lighting.json taste.json images/   <- what the viewer loads (?data=.../viewer/)
    events.jsonl  job.json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import events
from .env import ROOT

if str(ROOT) not in sys.path:          # ingest_instagram.py / run_scout.py live in the project root
    sys.path.insert(0, str(ROOT))

JOBS_DIR = Path(os.environ.get("PTAH_JOBS_DIR") or ROOT / "data" / "jobs")
TASTE_CACHE = ROOT / "data" / "taste_cache"
SAMPLES = ROOT / "samples"
DEFAULT_LOCATION = os.environ.get("PTAH_DEFAULT_LOCATION", "New York City")
SOFT_STAGES = ("products", "layout", "lighting")           # failure keeps the designed bundle
BUNDLE_FILES = ("room.json", "items.json", "layout.json", "lighting.json", "taste.json")


class StageError(Exception):
    """A failure with a message safe to show the person (``hint`` says what they can do)."""

    def __init__(self, message: str, hint: str = "", code: str = "stage_failed", technical: str = ""):
        super().__init__(message)
        self.message, self.hint, self.code, self.technical = message, hint, code, technical


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------- inputs
def sample_rooms() -> dict[str, dict]:
    """{id: {id, name, path, width_cm, depth_cm}} - samples/room.json plus any samples/rooms/*.json."""
    out: dict[str, dict] = {}
    cands = [("sample-living-room", SAMPLES / "room.json")] + [(p.stem, p) for p in sorted((SAMPLES / "rooms").glob("*.json"))]
    for rid, p in cands:
        if p.exists():
            try:
                r = json.loads(p.read_text())
                xs, zs = [q[0] for q in r["floor"]], [q[1] for q in r["floor"]]
                out[rid] = {"id": rid, "name": r.get("name") or rid, "path": p, "width_cm": max(xs) - min(xs), "depth_cm": max(zs) - min(zs)}
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                continue
    return out


def validate_room(room: Any) -> dict:
    """Check an uploaded room.json (docs/contracts.md "room.json"); returns it or raises StageError with a friendly message."""
    try:
        floor = room["floor"]
        walls = room["walls"]
        assert isinstance(floor, list) and len(floor) >= 3 and all(len(p) == 2 for p in floor)
        assert isinstance(walls, list) and walls and all({"id", "start", "end"} <= set(w) for w in walls)
        xs, zs = [float(p[0]) for p in floor], [float(p[1]) for p in floor]
    except (KeyError, TypeError, ValueError, AssertionError):
        raise StageError("That room file does not look like a Ptah room (it needs a floor outline and walls).",
                         "Pick one of the sample rooms, or export room.json again.", "bad_room") from None
    w, d = max(xs) - min(xs), max(zs) - min(zs)
    if not (150 <= w <= 3000 and 150 <= d <= 3000):
        raise StageError(f"The room is {w / 100:.1f} x {d / 100:.1f} m, which looks wrong. Sizes are in centimetres.",
                         "Check the units in room.json (centimetres).", "bad_room")
    room.setdefault("height", 260)
    room.setdefault("openings", [])
    room.setdefault("existing", [])
    return room


def normalize_owned(items: list[dict]) -> list[dict]:
    """User-entered owned pieces -> items.json rows (owned: true). Only name/category/size are used."""
    out = []
    for n, it in enumerate(items or []):
        try:
            name = str(it.get("name") or it.get("category") or "Your piece")[:60]
            cat = re.sub(r"[^a-z0-9_]", "_", str(it.get("category") or "furniture").lower())[:30]
            w, d, h = (max(5.0, min(500.0, float(it.get(k) or v))) for k, v in (("w", 60), ("d", 40), ("h", 80)))
        except (TypeError, ValueError, AttributeError):
            continue
        out.append({"id": f"owned-{n + 1}", "category": cat, "name": name, "w": w, "d": d, "h": h, "dims_estimated": "w" not in it,
                    "image": None, "price": None, "url": None, "owned": True, "why": "Yours.",
                    "model": {"template": str(it.get("template") or "box"), "params": {}},
                    "colors": {"primary": str(it.get("color") or "#8a7f73"), "secondary": None, "accent": None},
                    "material": {"primary": "wood", "secondary": None}})
    return out


@dataclass
class JobConfig:
    """What the person asked for (persisted in job.json)."""
    has_zip: bool = False
    loves: list[dict] = field(default_factory=list)           # [{name, type?, qloo_id?}]
    room: dict = field(default_factory=lambda: {"sample": "sample-living-room"})     # {"sample": id} | {"upload": True}
    owned: dict = field(default_factory=lambda: {"sample": False, "items": []})
    prefs: dict = field(default_factory=lambda: {"budget": None, "can_paint": True, "location": DEFAULT_LOCATION})
    scan_stored: bool = False

    def to_json(self) -> dict:
        return {"has_zip": self.has_zip, "loves": self.loves, "room": self.room, "owned": self.owned, "prefs": self.prefs,
                "scan_stored": self.scan_stored}

    @classmethod
    def from_json(cls, d: dict) -> "JobConfig":
        c = cls()
        for k in ("has_zip", "loves", "room", "owned", "prefs", "scan_stored"):
            if k in d:
                setattr(c, k, d[k])
        return c


def plan_for(cfg: JobConfig) -> list[str]:
    return (["ingest"] if cfg.has_zip else []) + ["taste", "brief", "design", "products", "layout", "lighting"]


def weights_for(cfg: JobConfig) -> dict[str, float]:
    w = dict(events.STAGE_WEIGHTS)
    if not cfg.has_zip:
        w["taste"] = 0.06           # only resolving the typed-in favourites: seconds, not minutes
    return w


# ---------------------------------------------------------------------------------------------------------------- context
class Ctx:
    """Everything a stage needs."""

    def __init__(self, job_dir: Path, cfg: JobConfig, sink: events.JobSink, cancelled: Callable[[], bool] = lambda: False):
        self.dir, self.cfg, self.sink = Path(job_dir), cfg, sink
        self.cancelled = cancelled
        self.input, self.work, self.viewer = self.dir / "input", self.dir / "work", self.dir / "viewer"
        for p in (self.input, self.work, self.viewer):
            p.mkdir(parents=True, exist_ok=True)
        self.warnings: list[str] = []

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled()

    def log(self, msg: str, detail: dict | None = None) -> None:
        self.check()
        self.sink.emit("log", label=str(msg)[:200], detail=detail)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        self.sink.emit("log", label=msg[:200], detail={"warning": True})

    def progress(self, f: float, label: str = "", detail: dict | None = None) -> None:
        self.check()
        self.sink.emit("stage_progress", stage_progress=f, label=label, detail=detail)

    @property
    def ingest_dir(self) -> Path:
        return self.work / "ingest"

    @property
    def profile_path(self) -> Path:
        return self.work / "taste_profile.json"

    @property
    def brief_path(self) -> Path:
        return self.work / "brief.json"

    @property
    def design_dir(self) -> Path:
        return self.work / "design"

    @property
    def products_dir(self) -> Path:
        return self.work / "products"

    def final_dir(self) -> Path:
        """The newest complete bundle: products > design."""
        return self.products_dir if (self.products_dir / "items.json").exists() else self.design_dir


# ---------------------------------------------------------------------------------------------------------------- stages
def stage_ingest(ctx: Ctx) -> dict:
    import ingest_instagram as ig
    zp = ctx.input / "instagram.zip"
    if not zp.exists():
        raise StageError("The Instagram export is missing.", "Upload the .zip again.", "missing_input")
    try:
        with events.activity("compute", "Reading your saved and liked posts"):
            summary = ig.ingest(zp, ctx.ingest_dir, on_file=lambda kind, n, total: ctx.progress(
                0.1 + 0.8 * n / max(total, 1), f"Read {kind.replace('_', ' ')} ({n} of {total} files)"))
    except ig.IngestError as e:
        raise StageError(str(e), "", "bad_export") from None
    except (ValueError, OSError, KeyError) as e:
        raise StageError("We could not read that Instagram export.", "Make sure it is the JSON export.", "bad_export", events.scrub(str(e))) from None
    if summary["posts_total"] == 0 and summary["music_tracks"] == 0:
        raise StageError("The export has no saved or liked posts and no saved music, so there is nothing to learn your taste from.",
                         "Save or like a few posts on Instagram, or add some favourites by hand.", "empty_export")
    zp.unlink(missing_ok=True)          # personal data: keep only the derived files
    return {"posts": summary["posts_total"], "music_tracks": summary["music_tracks"], "owners": summary["unique_owners"]}


def _resolve_loves(ctx: Ctx, loves: list[dict]) -> tuple[list[dict], list[str]]:
    """Typed-in favourites -> verified Qloo entities (cached lookups; near-exact name matches only)."""
    from . import qloo
    ents: list[dict] = []
    missed: list[str] = []
    for n, love in enumerate(loves):
        name = str(love.get("name") or "").strip()
        if not name:
            continue
        ctx.progress(n / max(len(loves), 1), f'Looking up "{name[:40]}"')
        try:
            hits = [h for h in qloo.smart_search(name, str(love.get("type") or "")) if h["score"] >= qloo.GOOD_MATCH]
        except qloo.QlooError as e:
            raise StageError("Qloo is not answering right now.", "Try again in a minute.", "qloo_unavailable", events.scrub(str(e))) from None
        want = str(love.get("qloo_id") or "").upper()
        hit = next((h for h in hits if h["qloo_id"].upper() == want), None) or (hits[0] if hits else None)
        if not hit:
            missed.append(name)
            continue
        if all(e["qloo_id"].upper() != hit["qloo_id"].upper() for e in ents):
            ents.append({"qloo_id": hit["qloo_id"], "name": hit["name"], "type": hit["type"],
                         "evidence": "You told us you love this.", "confidence": 0.95})
    return ents, missed


def _run_rlm(ctx: Ctx) -> dict:
    """The slow part: the DSPy RLM reads the posts (progress from its iterations, LLM + Qloo activity from hooks)."""
    from . import data, profile_check, qloo, taste
    events.install_dspy_callback()
    ctx.sink.llm_label = "Claude is reading your posts"
    cap = int(os.environ.get("PTAH_QLOO_CAP_TASTE", qloo.DEFAULT_CALL_CAP))
    try:
        with data.use_data_dir(ctx.ingest_dir):
            result = taste.run_extraction(cap)
    except SystemExit as e:                      # configure_lms exits when PTAH_LM is unset
        raise StageError("The AI model is not set up on this server.", "Ask whoever runs the server to set PTAH_LM.", "llm_not_configured", events.scrub(str(e))) from None
    finally:
        ctx.sink.llm_label = "AI model call"
    raw = json.loads(result["profile"].model_dump_json())
    clean, dropped = profile_check.check_profile(raw, result["registry"]["seen"])
    out = ctx.work / "taste"
    out.mkdir(exist_ok=True)
    (out / "profile.raw.json").write_text(json.dumps(raw, indent=1))
    (out / "dropped.json").write_text(json.dumps(dropped, indent=1, default=str))
    return clean


def _taste_key(ctx: Ctx) -> str:
    h = hashlib.sha256()
    for n in ("posts.jsonl", "music.json"):
        p = ctx.ingest_dir / n
        h.update(p.read_bytes() if p.exists() else b"-")
    h.update(os.environ.get("PTAH_LM", "").encode())
    return h.hexdigest()[:20]


def stage_taste(ctx: Ctx) -> dict:
    from . import qloo
    from .brief import seed_from_music
    if qloo.is_stub():
        raise StageError("Qloo is not connected on this server, so your taste cannot be grounded.", "Ask whoever runs the server to set QLOO_API_KEY.", "qloo_not_configured")
    qloo.reset(int(os.environ.get("PTAH_QLOO_CAP_TASTE", qloo.DEFAULT_CALL_CAP)))
    profile: dict = {"entities": [], "aesthetic_tags": [], "themes": [], "style_summary": ""}
    detail: dict[str, Any] = {}
    if ctx.cfg.has_zip:
        cache = TASTE_CACHE / f"{_taste_key(ctx)}.json"
        if cache.exists():
            profile = json.loads(cache.read_text())
            ctx.log("Using a saved reading of these posts")
            ctx.progress(0.9)
            detail["cached"] = True
        else:
            try:
                profile = _run_rlm(ctx)
                TASTE_CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(profile))
            except Cancelled:
                raise
            except StageError as e:
                if not (ctx.cfg.loves or (ctx.ingest_dir / "music.json").exists()):
                    raise
                ctx.warn(f"The AI reading of your posts was skipped: {e.message}")
            except Exception:  # noqa: BLE001
                ctx.warn("The AI reading of your posts did not finish; continuing with what we have.")
                (ctx.work / "taste_error.txt").write_text(events.scrub(traceback.format_exc(), 4000))
            if not profile["entities"] and not ctx.cfg.loves:        # last resort without the LLM: artists from saved music
                ents, _skipped = seed_from_music(ctx.ingest_dir / "music.json")
                if ents:
                    ctx.warn("Used your saved music to find your taste (no AI reading).")
                    profile["entities"] = [{"qloo_id": e["qloo_id"], "name": e["name"], "type": e.get("type") or "artist",
                                            "evidence": "Saved music", "confidence": 0.5} for e in ents]
    if ctx.cfg.loves:
        ents, missed = _resolve_loves(ctx, ctx.cfg.loves)
        have = {e["qloo_id"].upper() for e in profile["entities"]}
        profile["entities"] += [e for e in ents if e["qloo_id"].upper() not in have]
        if missed:
            ctx.warn("Qloo did not know: " + ", ".join(missed[:5]))
        detail["loves_resolved"] = len(ents)
    if not profile["entities"]:
        raise StageError("We could not find anything to learn your taste from.",
                         "Add a few things you love (artists, films, places, brands) or upload an Instagram export.", "no_taste")
    if not profile.get("aesthetic_tags"):
        try:
            ids = [e["qloo_id"] for e in profile["entities"]][:15]
            profile["aesthetic_tags"] = [t["name"] for t in qloo.taste_tags(ids, "decor", take=6)]
        except qloo.QlooError:
            pass
    if not profile.get("style_summary"):
        names = ", ".join(e["name"] for e in profile["entities"][:6])
        tags = profile.get("aesthetic_tags") or []
        profile["style_summary"] = f"A taste shaped by {names}." + (f" Interiors that suit it: {', '.join(tags[:4])}." if tags else "")
    ctx.profile_path.write_text(json.dumps(profile, indent=1, ensure_ascii=False))
    return {"entities": len(profile["entities"]), "themes": len(profile.get("themes") or []), **detail}


def stage_brief(ctx: Ctx) -> dict:
    from . import qloo
    from .brief import build_brief, entities_from_profile
    qloo.reset(int(os.environ.get("PTAH_QLOO_CAP_BRIEF", 400)))
    ents = entities_from_profile(ctx.profile_path)
    if not ents:
        raise StageError("There are no verified taste signals to build a brief from.", "", "no_taste")
    try:
        brief = build_brief(ents, ctx.cfg.prefs.get("location") or DEFAULT_LOCATION)
    except qloo.CallCapReached as e:
        raise StageError("This job asked Qloo for more than its allowance.", "Try again with fewer favourites.", "qloo_cap", events.scrub(str(e))) from None
    except qloo.QlooError as e:
        raise StageError("Qloo is not answering right now.", "Try again in a minute.", "qloo_unavailable", events.scrub(str(e))) from None
    ctx.brief_path.write_text(json.dumps(brief, indent=1, ensure_ascii=False))
    return {"decor_tags": len(brief["taste"]["decor"]), "places": len(brief["inspiration_places"]), "shops": len(brief["local_shops"]),
            "brands": len(brief["brands"]), "qloo_calls": brief["qloo"]["network_calls"]}


def _room_path(ctx: Ctx) -> Path:
    if ctx.cfg.room.get("upload"):
        p = ctx.input / "room.json"
        if p.exists():
            return p
        raise StageError("The room file is missing.", "Upload it again or pick a sample room.", "missing_input")
    r = sample_rooms().get(ctx.cfg.room.get("sample") or "")
    if not r:
        raise StageError("That sample room does not exist.", "", "bad_room")
    return r["path"]


def stage_design(ctx: Ctx) -> dict:
    from . import design, design_build
    room = validate_room(json.loads(_room_path(ctx).read_text()))
    sample_items = json.loads((SAMPLES / "items.json").read_text())
    base_items = [i for i in sample_items if i.get("owned")] if ctx.cfg.owned.get("sample") else []
    extra = normalize_owned(ctx.cfg.owned.get("items") or [])
    items_path, room_path = ctx.work / "owned_items.json", ctx.work / "room_in.json"
    items_path.write_text(json.dumps(base_items + extra))
    room_path.write_text(json.dumps(room))
    inp = design.load_inputs(ctx.profile_path, ctx.brief_path, room_path, items_path)
    vocab = design.build_vocab(inp)
    ctx.progress(0.05, "Briefing the designer")
    try:
        with events.creep(float(os.environ.get("PTAH_DESIGN_EXPECTED_S", 45)), 0.08, 0.7, "Designing your room"):
            spec, meta = design.generate_spec(inp, ROOT / "data" / "design_cache", log=ctx.log)
    except design.DesignError as e:
        raise StageError("The designer's first idea did not pass our checks.", "Try again; if it keeps happening, change your favourites a little.",
                         "design_rejected", events.scrub("; ".join(e.errors[:3]))) from None
    except RuntimeError as e:
        msg = str(e)
        if "budget" in msg:
            raise StageError("The AI design budget for this server is used up.", "Ask whoever runs the server to raise PTAH_DESIGN_MAX_LLM_CALLS.", "llm_budget", events.scrub(msg)) from None
        raise StageError("The AI designer is not set up on this server.", "Ask whoever runs the server to set PTAH_SUB_LM and OPENROUTER_API_KEY.", "llm_not_configured", events.scrub(msg)) from None
    ctx.progress(0.72, "Turning the design into furniture")
    base_cons = json.loads((SAMPLES / "constraints.json").read_text())
    n_round = [0]

    def blog(m: str) -> None:
        n_round[0] += 1
        ctx.progress(min(0.95, 0.72 + 0.07 * n_round[0]), m)

    try:
        built = design_build.build_and_solve(spec, vocab, inp.room, base_items, base_cons, log=blog, extra_items=extra)
    except Exception as e:  # noqa: BLE001 - solver / room-shape problems
        raise StageError("We could not fit the design into that room.", "Try a sample room, or check the walls in your room file.",
                         "layout_failed", events.scrub(f"{type(e).__name__}: {e}")) from None
    if not ctx.cfg.prefs.get("can_paint", True):
        orig = room.get("finishes") or {}
        built["room"]["finishes"] = {**built["room"].get("finishes", {}), "wall_color": orig.get("wall_color") or "#f2efe8", "wall_paint": None}
        built["room"]["can_paint"] = False
    design_build.write_bundle(ctx.design_dir, spec, vocab, built, inp, meta)
    if ctx.cfg.prefs.get("budget"):
        tp = ctx.design_dir / "taste.json"
        tp.write_text(json.dumps({**json.loads(tp.read_text()), "budget": ctx.cfg.prefs["budget"]}))
    lay = built["layout"]
    return {"concept": spec.concept_name, "pieces": len(lay["placements"]), "unplaced": len(lay.get("unplaced", [])),
            "llm_calls": meta.get("llm_calls", 0), "cached": bool(meta.get("cached"))}


_PICK = re.compile(r"^([\w-]+): .+ \| .+ \| ")


def stage_products(ctx: Ctx) -> dict:
    import argparse

    import run_scout
    n_serp = [0]
    picks = [0]

    def observe(ev: dict) -> None:
        if ev["type"] == "activity_end" and ev.get("service") == "serpapi":
            n_serp[0] += 1

    def slog(m: str) -> None:
        ctx.check()
        low = m.lower()
        if "cohesive curation" in low:
            ctx.progress(0.06, "Planning product searches")
        elif "cached art listings" in low:
            ctx.progress(0.5, "Comparing listings against your palette")
        elif _PICK.match(m):
            picks[0] += 1
            ctx.progress(min(0.85, 0.5 + 0.12 * picks[0]), "Picked " + m.split(":", 1)[0])
        elif "furniture set check" in low:
            ctx.progress(0.9, "Checking the pieces belong together")
        elif "live searches" in low:
            ctx.progress(0.97)
        ctx.sink.emit("log", label=m[:160])

    args = argparse.Namespace(bundle=str(ctx.design_dir), out=str(ctx.products_dir), max_searches=int(os.environ.get("PTAH_SCOUT_MAX_SEARCHES", 12)),
                              no_details=False, no_relayout=False, publish_dir="none", details_all=False, slots=None, only="art", sample=False, cohesive=True)
    ctx.sink.observers.append(observe)
    try:
        shutil.rmtree(ctx.products_dir, ignore_errors=True)
        with events.creep(float(os.environ.get("PTAH_PRODUCTS_EXPECTED_S", 40)), 0.06, 0.88, "Matching products to your palette"):    # image analysis of the listings is opaque
            rc = run_scout.cohesive_main(args, slog)
    finally:
        if observe in ctx.sink.observers:
            ctx.sink.observers.remove(observe)
    if rc != 0 or not (ctx.products_dir / "items.json").exists():
        shutil.rmtree(ctx.products_dir, ignore_errors=True)
        raise StageError("We could not find real products that fit your design, so the room shows designed pieces.", "", "no_products")
    items = json.loads((ctx.products_dir / "items.json").read_text())
    return {"products": sum(1 for i in items if i.get("scouted")), "searches": n_serp[0]}


def stage_layout(ctx: Ctx) -> dict:
    """Final fit with the real products' sizes: keep the solved layout when it is still valid, re-solve otherwise."""
    from .layout import Budget, hard_violations, solve
    d = ctx.final_dir()
    room, items = json.loads((d / "room.json").read_text()), json.loads((d / "items.json").read_text())
    lay = json.loads((d / "layout.json").read_text())
    known = {p["item_id"] for p in lay["placements"]} | {u["item_id"] for u in lay.get("unplaced", [])}
    bad = hard_violations(room, items, lay)
    resolved = False
    if bad or any(i["id"] not in known for i in items):
        ctx.progress(0.2, "Re-arranging for the real product sizes")
        cons = json.loads((d / "constraints.json").read_text())
        with events.activity("compute", "Arranging the furniture"):
            res = solve(room, items, cons, Budget(time_s=4.0, max_expansions=260, step=10.0), room_name="room.json")
        lay, resolved = res.layout, True
        bad = hard_violations(room, items, lay)
        (d / "layout.json").write_text(json.dumps(lay, indent=2) + "\n")
    return {"placed": len(lay["placements"]), "unplaced": len(lay.get("unplaced", [])), "hard_violations": len(bad), "re_solved": resolved}


def stage_lighting(ctx: Ctx) -> dict:
    from .lighting import compute_lighting
    d = ctx.final_dir()
    room, items, lay = (json.loads((d / n).read_text()) for n in ("room.json", "items.json", "layout.json"))
    scene = "evening"
    if (d / "design_spec.json").exists():
        scene = (json.loads((d / "design_spec.json").read_text()).get("lighting_mood") or {}).get("default_scene", "evening")
    with events.activity("compute", "Planning the lighting"):
        lighting = compute_lighting(room, items, lay, "day" if scene == "day" else "night", None, scene)
    (d / "lighting.json").write_text(json.dumps(lighting, indent=1) + "\n")
    return {"scene": scene, "issues": len(lighting.get("issues_all", []))}


STAGES: dict[str, Callable[[Ctx], dict | None]] = {
    "ingest": stage_ingest, "taste": stage_taste, "brief": stage_brief, "design": stage_design,
    "products": stage_products, "layout": stage_layout, "lighting": stage_lighting,
}


# ---------------------------------------------------------------------------------------------------------------- bundle
def assemble_viewer(ctx: Ctx) -> dict:
    """Copy the newest complete bundle to viewer/ (the folder the viewer loads with ?data=)."""
    d = ctx.final_dir()
    ctx.viewer.mkdir(parents=True, exist_ok=True)
    for n in BUNDLE_FILES:
        if (d / n).exists():
            shutil.copyfile(d / n, ctx.viewer / n)
    if (d / "images").is_dir():
        (ctx.viewer / "images").mkdir(exist_ok=True)
        for f in (d / "images").iterdir():
            if f.is_file() and not f.name.endswith(".src.jpg"):
                shutil.copyfile(f, ctx.viewer / "images" / f.name)
    items = json.loads((ctx.viewer / "items.json").read_text())
    summary: dict[str, Any] = {"items": len(items), "products": sum(1 for i in items if i.get("scouted")), "owned": sum(1 for i in items if i.get("owned"))}
    try:
        from .owned import total_cost
        cost = total_cost(items)
        if cost:
            summary["cost"] = cost
    except Exception:  # noqa: BLE001 - cost is a nicety
        pass
    return summary


# ---------------------------------------------------------------------------------------------------------------- runner
def run_job(job_dir: Path, cfg: JobConfig, sink: events.JobSink, *, stages: dict | None = None, resume: bool = False,
            cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Run every stage of the plan. Always ends the sink with ``result`` or ``error``; returns {"ok", "partial", ...}."""
    stages = {**STAGES, **(stages or {})}
    ctx = Ctx(job_dir, cfg, sink, cancelled)
    plan = plan_for(cfg)
    sink.set_plan(plan)
    try:
        st = json.loads((ctx.work / "state.json").read_text()) if resume else {"done": {}}
    except (OSError, ValueError):
        st = {"done": {}}
    failed_soft: list[str] = []
    with events.bind(sink):
        try:
            for name in plan:
                ctx.check()
                label = events.STAGE_LABELS.get(name, name)
                sink.emit("stage_start", stage=name, label=label, detail={"plan": sink.plan_info()} if name == plan[0] else None)
                if name in st["done"]:
                    sink.emit("stage_end", stage=name, label=label, detail={**st["done"][name], "cached": True}, duration_ms=0)
                    continue
                t0 = time.monotonic()
                ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
                try:
                    out = stages[name](ctx) or {}
                except Cancelled:
                    raise
                except StageError as e:
                    if name not in SOFT_STAGES:
                        raise
                    failed_soft.append(name)
                    ctx.warn(e.message)
                    sink.emit("stage_end", stage=name, label=label, detail={"skipped": True, "reason": e.message, "code": e.code}, duration_ms=ms())
                    continue
                except Exception as e:  # noqa: BLE001
                    if name not in SOFT_STAGES:
                        raise
                    failed_soft.append(name)
                    ctx.warn(f"{label} did not finish; kept the earlier result.")
                    (ctx.work / f"{name}_error.txt").write_text(events.scrub(traceback.format_exc(), 4000))
                    sink.emit("stage_end", stage=name, label=label, detail={"skipped": True, "reason": type(e).__name__}, duration_ms=ms())
                    continue
                st["done"][name] = out
                (ctx.work / "state.json").write_text(json.dumps(st))
                sink.emit("stage_end", stage=name, label=label, detail=out, duration_ms=ms())
            summary = assemble_viewer(ctx)
            partial = bool(failed_soft)
            sink.emit("result", label="Your room is ready" if not partial else "Your room is ready (some steps were skipped)",
                      detail={"viewer": "viewer/", "partial": partial, "skipped": failed_soft, "warnings": ctx.warnings[:10], **summary})
            return {"ok": True, "partial": partial, "summary": summary, "warnings": ctx.warnings}
        except Cancelled:
            sink.emit("error", label="Cancelled", detail={"code": "cancelled"})
            return {"ok": False, "code": "cancelled"}
        except StageError as e:
            sink.emit("error", label=e.message, detail={"code": e.code, "hint": e.hint, "technical": e.technical, "stage": sink.stage,
                                                        "retryable": e.code not in ("bad_export", "bad_room", "empty_export", "no_taste")})
            return {"ok": False, "code": e.code, "message": e.message}
        except BaseException as e:  # noqa: BLE001 - the job must always end with an event
            (ctx.work / "error.txt").write_text(events.scrub(traceback.format_exc(), 6000))
            sink.emit("error", label="Something went wrong while building your room.",
                      detail={"code": "internal", "hint": "Try again. If it keeps happening, tell us.", "technical": events.scrub(f"{type(e).__name__}: {e}"),
                              "stage": sink.stage, "retryable": True})
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            return {"ok": False, "code": "internal"}
        finally:
            sink.close()

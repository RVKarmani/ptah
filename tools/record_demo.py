#!/usr/bin/env python
"""Record the demo fixture used by POST /api/demo: data/demo/events.json + data/demo/bundle/.

    python tools/record_demo.py                       # newest of data/runs/design_*_linked / _cohesive
    python tools/record_demo.py --bundle data/runs/design_X --profile data/taste_profile.json --brief data/brief.json
    python tools/record_demo.py --events data/jobs/<id>/events.jsonl --bundle data/jobs/<id>/viewer
        # use a REAL job's recorded events as the timeline (best; times are normalised)

The bundle is a finished run's viewer files. Without --events the timeline is *reconstructed* from that run's artifacts, using the same
event vocabulary the live pipeline emits (stage names, services, labels, counts come from the real taste profile / brief / design spec /
scout report; durations are typical, not measured, because the earlier CLI runs did not log per-request timings).
The result is private (it contains the recorder's own taste data) and lives under data/, which is git-ignored.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ptah import events  # noqa: E402

FILES = ("room.json", "items.json", "layout.json", "lighting.json", "taste.json")


class Clock:
    def __init__(self):
        self.t, self.rows, self.n = 0.0, [], 0

    def add(self, dt: float, type: str, **kw) -> dict:
        self.t += dt
        row = {"t": round(self.t, 2), "type": type, **{k: v for k, v in kw.items() if v is not None}}
        self.rows.append(row)
        return row

    def activity(self, service: str, label: str, dur: float, stage: str, gap: float = 0.05, detail: dict | None = None) -> None:
        self.n += 1
        aid = f"d{self.n:04d}"
        self.add(gap, "activity_start", stage=stage, service=service, label=label, id=aid, detail=detail)
        self.add(dur, "activity_end", stage=stage, service=service, label=label, id=aid, duration_ms=int(dur * 1000), detail={"ok": True})


def timeline(profile: dict, brief: dict, summary: dict, spec: dict, items: list[dict], queries: list[str], seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    c = Clock()
    names = [e["name"] for e in profile["entities"]] or ["your favourites"]
    E = events.STAGE_LABELS
    # ---- ingest
    c.add(0, "stage_start", stage="ingest", label=E["ingest"])
    c.add(0.2, "activity_start", stage="ingest", service="compute", label="Reading your saved and liked posts", id="ing")
    for n, kind in enumerate(("saved posts", "liked posts", "saved music"), 1):
        c.add(1.2, "stage_progress", stage="ingest", stage_progress=0.1 + 0.8 * n / 3, label=f"Read {kind} ({n} of 3 files)")
    c.add(0.5, "activity_end", stage="ingest", service="compute", label="Reading your saved and liked posts", id="ing", duration_ms=4300, detail={"ok": True})
    c.add(0.1, "stage_end", stage="ingest", label=E["ingest"], duration_ms=4400,
          detail={"posts": summary.get("posts_total"), "music_tracks": summary.get("music_tracks"), "owners": summary.get("unique_owners")})
    # ---- taste: RLM iterations, each = one LLM call + a few Qloo lookups
    c.add(0.1, "stage_start", stage="taste", label=E["taste"])
    iters = 12
    pool = list(names)
    for it in range(1, iters + 1):
        c.add(0.2, "stage_progress", stage="taste", stage_progress=0.04 + 0.92 * (it - 1) / 20, label=f"Thinking about your posts (round {it} of 20)",
              detail={"iteration": it, "max_iters": 20})
        c.activity("llm", "Claude is reading your posts", rnd.uniform(5, 11), "taste", detail={"call": it})
        if it >= 3:
            for _ in range(rnd.randint(1, 4)):
                nm = pool[rnd.randrange(len(pool))]
                c.activity("qloo", events.qloo_label("/search", {"query": nm}), rnd.uniform(0.3, 1.1), "taste", gap=0.3)
        if it in (7, 10):
            nm = pool[rnd.randrange(len(pool))]
            c.activity("qloo", events.qloo_label("/v2/insights", {"filter.type": "urn:tag", "filter.tag.types": "urn:tag:decor:qloo"}), 1.4, "taste", gap=0.3)
    c.add(0.3, "stage_end", stage="taste", label=E["taste"], duration_ms=int(c.t * 1000), detail={"entities": len(profile["entities"]), "themes": len(profile.get("themes") or [])})
    # ---- brief
    c.add(0.1, "stage_start", stage="brief", label=E["brief"])
    decor = [t["name"] for t in brief["taste"]["decor"]] or ["Classic"]
    steps = [(0.03, "Reading the decor styles behind your taste", 6), (0.35, "Ranking home brands for your taste", 4), (0.5, "Finding places with interiors you would love", 16),
             (0.72, "Finding shops near " + brief.get("location", "you"), 10), (0.9, "Finding films, music and books you might love", 4)]
    for frac, label, n_req in steps:
        c.add(0.2, "stage_progress", stage="brief", stage_progress=frac, label=label)
        for k in range(n_req):
            if "decor" in label or "styles" in label:
                lab = events.qloo_label("/v2/insights", {"filter.type": "urn:tag", "filter.tag.types": "urn:tag:decor:qloo" if k % 2 else "urn:tag:ambience:qloo"})
            elif "brands" in label:
                lab = events.qloo_label("/v2/insights", {"filter.type": "urn:entity:brand"})
            elif "films" in label:
                lab = events.qloo_label("/v2/insights", {"filter.type": ["urn:entity:movie", "urn:entity:artist", "urn:entity:book"][k % 3]})
            else:
                lab = events.qloo_label("/v2/insights", {"filter.type": "urn:entity:place"}).replace("place", ["hotel", "bar", "restaurant", "cafe", "furniture store", "gallery"][k % 6])
            c.activity("qloo", lab, rnd.uniform(0.5, 1.6), "brief", gap=0.15)
    c.add(0.3, "stage_end", stage="brief", label=E["brief"], duration_ms=int((c.t - 0) * 1000),
          detail={"decor_tags": len(decor), "places": len(brief["inspiration_places"]), "shops": len(brief["local_shops"]), "brands": len(brief["brands"])})
    # ---- design
    c.add(0.1, "stage_start", stage="design", label=E["design"])
    c.add(0.3, "stage_progress", stage="design", stage_progress=0.05, label="Briefing the designer")
    c.add(0.2, "activity_start", stage="design", service="llm", label="Designing your room with AI", id="dsg")
    for k in range(1, 12):
        c.add(3.5, "stage_progress", stage="design", stage_progress=0.08 + 0.62 * (1 - 0.8 ** k), label="Designing your room")
    c.add(1.5, "activity_end", stage="design", service="llm", label="Designing your room with AI", id="dsg", duration_ms=40000, detail={"ok": True})
    c.add(0.3, "stage_progress", stage="design", stage_progress=0.72, label="Turning the design into furniture")
    c.add(1.5, "stage_progress", stage="design", stage_progress=0.85, label="design: round 1: 1 designed piece(s) unplaced, 0 hard violations")
    c.add(1.2, "stage_end", stage="design", label=E["design"], duration_ms=52000, detail={"concept": spec.get("concept_name"), "pieces": len(items), "unplaced": 0, "llm_calls": 1})
    # ---- products
    c.add(0.1, "stage_start", stage="products", label=E["products"])
    c.add(0.3, "stage_progress", stage="products", stage_progress=0.06, label="Planning product searches")
    for k, q in enumerate(queries[:8], 1):
        c.activity("serpapi", f"Shopping search: {q[:60]}", rnd.uniform(1.0, 2.4), "products", gap=0.2)
        c.add(0.1, "stage_progress", stage="products", stage_progress=min(0.45, 0.08 + 0.05 * k), label="Searching shops")
    c.add(1.0, "stage_progress", stage="products", stage_progress=0.5, label="Comparing listings against your palette")
    scouted = [i for i in items if i.get("scouted")]
    for k, it in enumerate(scouted[:6], 1):
        c.add(2.2, "stage_progress", stage="products", stage_progress=min(0.85, 0.5 + 0.06 * k), label="Picked " + it["id"])
    c.add(1.0, "stage_progress", stage="products", stage_progress=0.9, label="Checking the pieces belong together")
    c.add(0.5, "stage_end", stage="products", label=E["products"], duration_ms=int((c.t) * 1000) // 8, detail={"products": len(scouted), "searches": min(len(queries), 8)})
    # ---- layout, lighting
    c.add(0.1, "stage_start", stage="layout", label=E["layout"])
    c.activity("compute", "Arranging the furniture", 3.0, "layout", gap=0.2)
    c.add(0.1, "stage_end", stage="layout", label=E["layout"], duration_ms=3300, detail={"placed": len(items), "hard_violations": 0, "re_solved": False})
    c.add(0.1, "stage_start", stage="lighting", label=E["lighting"])
    c.activity("compute", "Planning the lighting", 1.6, "lighting", gap=0.2)
    c.add(0.1, "stage_end", stage="lighting", label=E["lighting"], duration_ms=1800, detail={"scene": "evening", "issues": 0})
    c.add(0.3, "result", label="Your room is ready", detail={"partial": False, "products": len(scouted), "items": len(items)})
    return c.rows


def from_events(path: Path) -> list[dict]:
    evs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    t0 = evs[0]["ts"]
    rows = []
    for e in evs:
        row = {"t": round(e["ts"] - t0, 2), "type": e["type"]}
        for k in ("stage", "service", "label", "detail", "id", "duration_ms"):
            if e.get(k) is not None:
                row[k] = e[k]
        if e["type"] == "stage_progress":
            row["stage_progress"] = e["stage_progress"]
        rows.append(row)
    return rows


def pick_bundle() -> Path:
    for suffix in ("linked", "cohesive"):
        p = ROOT / "data" / "runs" / f"design_20261030_1300_{suffix}"
        if (p / "items.json").exists():
            return p
    raise SystemExit("no design bundle under data/runs/")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path)
    ap.add_argument("--profile", type=Path, default=ROOT / "data" / "taste_profile.json")
    ap.add_argument("--brief", type=Path, default=ROOT / "data" / "brief.json")
    ap.add_argument("--summary", type=Path, default=ROOT / "data" / "summary.json")
    ap.add_argument("--events", type=Path, help="a real job's events.jsonl to use as the timeline")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "demo")
    a = ap.parse_args()
    b = a.bundle or pick_bundle()
    b = b if b.is_absolute() else ROOT / b
    items = json.loads((b / "items.json").read_text())
    if a.events:
        tl = from_events(a.events)
    else:
        spec = json.loads((b / "design_spec.json").read_text()) if (b / "design_spec.json").exists() else {}
        rep = (b / "cohesion_report.md").read_text() if (b / "cohesion_report.md").exists() else ""
        queries = list(dict.fromkeys(q for line in re.findall(r"Queries considered: (.*)", rep) for q in re.findall(r"`([^`]+)`", line)))
        tl = timeline(json.loads(a.profile.read_text()), json.loads(a.brief.read_text()), json.loads(a.summary.read_text()), spec, items, queries)
    shutil.rmtree(a.out, ignore_errors=True)
    (a.out / "bundle" / "images").mkdir(parents=True)
    for n in FILES:
        if (b / n).exists():
            shutil.copyfile(b / n, a.out / "bundle" / n)
    for f in (b / "images").glob("*"):
        if f.is_file() and not f.name.endswith(".src.jpg"):
            shutil.copyfile(f, a.out / "bundle" / "images" / f.name)
    (a.out / "events.json").write_text(json.dumps(tl, ensure_ascii=False, separators=(",", ":")))
    print(f"bundle {b.name}: {len(items)} items; timeline {len(tl)} events spanning {tl[-1]['t']:.0f}s -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

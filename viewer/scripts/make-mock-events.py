#!/usr/bin/env python3
"""Build the development-only event fixtures used by `?mockEvents=` from a recorded demo run (../data/demo/events.json).

The recording comes from a real person's run, so labels that could name their taste are replaced with generic text and
details are dropped: the output is safe to commit. Run:  python3 scripts/make-mock-events.py  (writes dev-fixtures/*.json)

Static demo (GitHub Pages): `python3 scripts/make-mock-events.py --demo` rebuilds public/demo/events.json from the committed, already
sanitised dev-fixtures/mock-events.json (no private data needed), retimed to DEMO_SECONDS and with the post count replaced by a made-up figure.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1] / "dev-fixtures"
SRC = ROOT / "data" / "demo" / "events.json"
WEIGHTS = [("ingest", "Reading your posts", 5), ("taste", "Understanding your taste", 45), ("brief", "Asking Qloo what people like you love", 15),
           ("design", "Designing your room", 15), ("products", "Finding real pieces", 12), ("layout", "Arranging the furniture", 5), ("lighting", "Planning the lighting", 3)]
SHOP = ["sage green armchair", "oak coffee table", "linen floor lamp", "wool area rug", "walnut media console", "framed botanical print", "ceramic table lamp", "brass wall sconce"]
TOTAL = sum(w for _, _, w in WEIGHTS)
SCALE = 60.0  # seconds for the whole recording
DEMO_SECONDS = 35.0  # the bundled sample-person replay on the static demo
DEMO_POSTS = 240  # made-up: the sample person is not a real account


def clean(e, shop_i):
    t, svc, lab = e["type"], e.get("service"), e.get("label", "")
    if t.startswith("activity") and svc == "qloo" and lab.startswith("Qloo: looking up"):
        lab = "Qloo: looking up something you love"
    if t.startswith("activity") and svc == "serpapi":
        lab = "Shopping search: " + SHOP[shop_i % len(SHOP)]
    lab = re.sub(r"Finding shops near .*", "Finding shops near you", lab)
    if lab.startswith("design: round"):
        lab = "Checking the layout fits"
    out = {k: e[k] for k in ("t", "type", "stage", "service", "id", "stage_progress", "duration_ms") if k in e}
    out["label"] = lab
    if t == "activity_end":
        out["detail"] = {"ok": (e.get("detail") or {}).get("ok", True)}
    if t == "stage_end":
        out["detail"] = {k: v for k, v in (e.get("detail") or {}).items() if k in ("posts", "entities", "products", "placed") and isinstance(v, int)}
    if t == "result":
        out["detail"] = {"partial": False, "items": 28, "products": 18}
    return out


def main():
    src = json.loads(SRC.read_text())
    k = SCALE / max(e["t"] for e in src)
    shop_i = {}
    evs, done, cur = [], {}, {}
    for e in src:
        key = e.get("id")
        if e["type"] == "activity_start" and e.get("service") == "serpapi":
            shop_i[key] = len(shop_i)
        c = clean(e, shop_i.get(key, 0))
        c["t"] = round(e["t"] * k, 2)
        if c.get("duration_ms"):
            c["duration_ms"] = int(c["duration_ms"] * k)
        evs.append(c)
        st = e.get("stage")
        if e["type"] == "stage_progress":
            cur[st] = e["stage_progress"]
        if e["type"] == "stage_end":
            done[st] = 1.0
        prog = sum(w * (done.get(s, cur.get(s, 0))) for s, _, w in WEIGHTS) / TOTAL
        c["progress"] = round(min(1.0, prog), 4) if e["type"] != "result" else 1.0
    evs[0]["detail"] = {"plan": [{"stage": s, "label": l, "weight": w} for s, l, w in WEIGHTS]}
    (OUT / "mock-events.json").write_text(json.dumps(evs, separators=(",", ":")))

    # failure + resume: the AI designer hits a quota halfway through the design stage; Resume replays finished stages as cached
    d0 = next(i for i, e in enumerate(evs) if e["type"] == "stage_start" and e["stage"] == "design")
    cut = d0 + 5
    last = evs[cut - 1]
    part = [dict(e) for e in evs[:cut] if e["type"] != "activity_start" or any(x.get("id") == e.get("id") and x["type"] == "activity_end" for x in evs[:cut])]
    err = {"t": last["t"] + 0.5, "type": "error", "stage": "design", "progress": last["progress"], "label": "The AI designer has reached its limit for now.",
           "detail": {"code": "llm_budget", "hint": "Try again in a little while. Everything finished so far is kept.", "retryable": True, "stage": "design", "technical": "RateLimitError: 429 daily quota exceeded"}}
    # drop activities that were still open at the cut (the error closes them)
    open_ids = {e["id"] for e in part if e["type"] == "activity_start"} - {e["id"] for e in part if e["type"] == "activity_end"}
    part = [e for e in part if not (e["type"] == "activity_start" and e["id"] in open_ids)]
    resume = [{"t": 0.0, "type": "log", "label": "Trying again", "detail": {"resume": True}, "progress": err["progress"]}]
    for i, (s, l, w) in enumerate(WEIGHTS[:3]):
        resume += [{"t": 0.3 + i * 0.4, "type": "stage_start", "stage": s, "label": l, "progress": err["progress"]},
                   {"t": 0.5 + i * 0.4, "type": "stage_end", "stage": s, "label": l, "duration_ms": 0, "detail": {"cached": True}, "progress": err["progress"]}]
    base = evs[d0]["t"]
    tail = [dict(e) for e in evs[d0:]]
    for e in tail:
        e["t"] = round(2.0 + (e["t"] - base) * 0.6, 2)
        e["progress"] = max(e.get("progress", 0), err["progress"])
    (OUT / "mock-events-error.json").write_text(json.dumps({"events": part + [err], "resume": resume + tail}, separators=(",", ":")))
    print("wrote", len(evs), "events;", len(part) + 1, "before the error;", len(resume + tail), "after resume")


def demo():
    evs = json.loads((OUT / "mock-events.json").read_text())
    k = DEMO_SECONDS / max(e["t"] for e in evs)
    for e in evs:
        e["t"] = round(e["t"] * k, 2)
        if e.get("duration_ms"):
            e["duration_ms"] = int(e["duration_ms"] * k)
        if e["type"] == "stage_end" and "posts" in (e.get("detail") or {}):
            e["detail"]["posts"] = DEMO_POSTS
    dest = OUT.parent / "public" / "demo"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "events.json").write_text(json.dumps(evs, separators=(",", ":")))
    print("wrote", dest / "events.json", len(evs), "events,", DEMO_SECONDS, "s")


if __name__ == "__main__":
    sys.exit(demo() if "--demo" in sys.argv[1:] else main())

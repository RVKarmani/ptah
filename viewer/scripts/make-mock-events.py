#!/usr/bin/env python3
"""Build the static demo's progress replay (public/demo/events.json) from a recorded run (../data/demo/events.json).

The recording comes from a real person's run, so labels that could name their taste are replaced with generic text and details
are dropped: the output is safe to commit. Run from viewer/:  python3 scripts/make-mock-events.py
In development the same file replays with  ?mockEvents=/demo/events.json&mockData=/demo/room/
"""
import json
import re
from pathlib import Path

VIEWER = Path(__file__).resolve().parents[1]
SRC = VIEWER.parent / "data" / "demo" / "events.json"
DEST = VIEWER / "public" / "demo" / "events.json"
ROOM = VIEWER / "public" / "demo" / "room" / "items.json"
WEIGHTS = [("ingest", "Reading your posts", 5), ("taste", "Understanding your taste", 45), ("brief", "Asking Qloo what people like you love", 15),
           ("design", "Designing your room", 15), ("products", "Finding real pieces", 12), ("layout", "Arranging the furniture", 5), ("lighting", "Planning the lighting", 3)]
SHOP = ["sage green armchair", "oak coffee table", "linen floor lamp", "wool area rug", "walnut media console", "framed botanical print", "ceramic table lamp", "brass wall sconce"]
TOTAL = sum(w for _, _, w in WEIGHTS)
SECONDS = 35.0  # length of the replay
POSTS = 240     # shown instead of the real post count


def clean(e, shop_i, result):
    t, svc, lab = e["type"], e.get("service"), e.get("label", "")
    if t.startswith("activity") and svc == "qloo" and lab.startswith("Qloo: looking up"):
        lab = "Qloo: looking up something you love"
    if t.startswith("activity") and svc == "serpapi":
        lab = "Shopping search: " + SHOP[shop_i % len(SHOP)]
    lab = re.sub(r"Finding shops near .*", "Finding shops near you", lab).replace("Claude is reading", "AI is reading")
    if lab.startswith("design: round"):
        lab = "Checking the layout fits"
    out = {k: e[k] for k in ("t", "type", "stage", "service", "id", "stage_progress", "duration_ms") if k in e}
    out["label"] = lab
    if t == "activity_end":
        out["detail"] = {"ok": (e.get("detail") or {}).get("ok", True)}
    if t == "stage_end":
        d = {k: v for k, v in (e.get("detail") or {}).items() if k in ("posts", "entities", "products", "placed") and isinstance(v, int)}
        if "posts" in d:
            d["posts"] = POSTS
        out["detail"] = d
    if t == "result":
        out["detail"] = result
    return out


def main():
    src = json.loads(SRC.read_text())
    items = json.loads(ROOM.read_text())
    result = {"partial": False, "items": len(items), "products": sum(1 for i in items if i.get("product") or i.get("scouted"))}
    k = SECONDS / max(e["t"] for e in src)
    shop_i, evs, done, cur = {}, [], {}, {}
    for e in src:
        key = e.get("id")
        if e["type"] == "activity_start" and e.get("service") == "serpapi":
            shop_i[key] = len(shop_i)
        c = clean(e, shop_i.get(key, 0), result)
        c["t"] = round(e["t"] * k, 2)
        if c.get("duration_ms"):
            c["duration_ms"] = int(c["duration_ms"] * k)
        st = e.get("stage")
        if e["type"] == "stage_progress":
            cur[st] = e["stage_progress"]
        if e["type"] == "stage_end":
            done[st] = 1.0
        prog = sum(w * done.get(s, cur.get(s, 0)) for s, _, w in WEIGHTS) / TOTAL
        c["progress"] = 1.0 if e["type"] == "result" else round(min(1.0, prog), 4)
        evs.append(c)
    evs[0]["detail"] = {"plan": [{"stage": s, "label": l, "weight": w} for s, l, w in WEIGHTS]}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(evs, separators=(",", ":")))
    print("wrote", DEST, len(evs), "events,", SECONDS, "s, result", result)


if __name__ == "__main__":
    main()

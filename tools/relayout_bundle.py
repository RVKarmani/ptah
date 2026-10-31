"""Re-translate constraints and re-solve layout + lighting for an existing design bundle (no LLM, no shopping calls).

    python tools/relayout_bundle.py data/runs/<bundle> [--out data/runs/<new bundle>] [--add extra_items.json]

--add appends items (e.g. a reading nook picked from the bundle's own alternatives) before solving.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ptah.design_build import build_constraints, tune_lighting  # noqa: E402
from ptah.layout import Budget, hard_violations, solve  # noqa: E402
from ptah.lighting import compute_lighting  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("--out")
    ap.add_argument("--add", help="JSON list of items to add before solving")
    ap.add_argument("--base-constraints", default=str(ROOT / "samples/constraints.json"))
    a = ap.parse_args()
    src = Path(a.bundle)
    out = Path(a.out) if a.out else src
    if out != src:
        if out.exists():
            sys.exit(f"{out} exists")
        shutil.copytree(src, out)
    room = json.loads((out / "room.json").read_text())
    items = json.loads((out / "items.json").read_text())
    if a.add:
        extra = json.loads(Path(a.add).read_text())
        have = {i["id"] for i in items}
        items += [e for e in extra if e["id"] not in have]
    old = json.loads((out / "constraints.json").read_text())
    cons = build_constraints(items, room, json.loads(Path(a.base_constraints).read_text()))
    for iid, c in old.items():           # keep hand-made constraints for pieces the translator has no rule for
        cons.setdefault(iid, c)
    res = solve(room, items, cons, Budget(time_s=4.0, max_expansions=260, step=10.0), room_name="room.json")
    lay = res.layout
    notes: list = []
    tune_lighting(items, room, lay, notes)
    bad = hard_violations(room, items, lay)
    scene = "evening"
    spec = out / "design_spec.json"
    if spec.exists():
        scene = (json.loads(spec.read_text()).get("lighting_mood") or {}).get("default_scene", "evening")
    lighting = compute_lighting(room, items, lay, "day" if scene == "day" else "night", None, scene)
    (out / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    (out / "constraints.json").write_text(json.dumps(cons, indent=1) + "\n")
    (out / "layout.json").write_text(json.dumps(lay, indent=2) + "\n")
    (out / "lighting.json").write_text(json.dumps(lighting, indent=1) + "\n")
    print(f"{out}: {len(lay['placements'])} placed, unplaced {[u['item_id'] for u in lay.get('unplaced', [])]}, score {lay['score']}, "
          f"balance offset {res.stats.get('balance_offset')}, hard violations {len(bad)}, notes {notes}")
    scenes = lighting.get("scenes") or {}
    for name, sc in scenes.items():
        print(f"  {name}: issues {[i['code'] for i in sc.get('issues', [])]}")


if __name__ == "__main__":
    main()

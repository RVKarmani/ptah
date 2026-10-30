"""CLI:  python -m ptah.layout room.json items.json constraints.json -o layout.json"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .constraints import hard_violations
from .solver import Budget, solve


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ptah.layout", description="Place furniture in a room from LLM constraints.")
    ap.add_argument("room")
    ap.add_argument("items")
    ap.add_argument("constraints")
    ap.add_argument("-o", "--output", default="layout.json")
    ap.add_argument("--time-budget", type=float, default=2.0, help="search safety-net, seconds")
    ap.add_argument("--max-expansions", type=int, default=260)
    ap.add_argument("--step", type=float, default=10.0, help="candidate grid step in cm")
    args = ap.parse_args(argv)

    with open(args.room) as f:
        room = json.load(f)
    with open(args.items) as f:
        items = json.load(f)
    with open(args.constraints) as f:
        constraints = json.load(f)
    budget = Budget(time_s=args.time_budget, max_expansions=args.max_expansions, step=args.step)
    res = solve(room, items, constraints, budget, room_name=os.path.basename(args.room))
    bad = hard_violations(room, items, res.layout)
    with open(args.output, "w") as f:
        json.dump(res.layout, f, indent=2)
        f.write("\n")
    lay = res.layout
    print(f"placed {len(lay['placements'])}/{len(lay['placements']) + len(lay['unplaced'])} items, score {lay['score']}, "
          f"{res.stats['runtime_s']}s ({res.stats['expansions']} expansions) -> {args.output}", file=sys.stderr)
    for u in lay["unplaced"]:
        print(f"  unplaced {u['item_id']}: {u['reason']} (max_w={u['max_w']}, max_d={u['max_d']})", file=sys.stderr)
    for w in lay.get("warnings", []):
        print(f"  warning: {w}", file=sys.stderr)
    if bad:
        print("HARD RULE VIOLATIONS: " + "; ".join(bad), file=sys.stderr)
        return 2
    return 0

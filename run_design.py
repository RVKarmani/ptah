"""Design stage: taste profile + Qloo design brief -> a room design -> items/finishes/lighting -> solved layout -> viewer bundle.

    python run_design.py                      # one LLM call (cached by input hash), writes data/runs/design_<ts>/ and viewer/public/me/
    python run_design.py --spec data/runs/design_X/design_spec.json   # re-translate an existing spec (no LLM)
    python run_design.py --no-publish         # do not copy the bundle to viewer/public/me/

Everything derived from the person's data lives under data/ or viewer/public/me/ (both git-ignored).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ptah.design import ROOT, DesignError, build_vocab, generate_spec, ledger_count, load_inputs, validate_spec
from ptah.design_build import build_and_solve, publish_to_viewer, write_bundle


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=str(ROOT / "data/taste_profile.json"))
    ap.add_argument("--brief", default=str(ROOT / "data/brief.json"))
    ap.add_argument("--room", default=str(ROOT / "samples/room.json"))
    ap.add_argument("--items", default=str(ROOT / "samples/items.json"), help="sample items: the owned items (TV, guitar, amp) are taken from here")
    ap.add_argument("--constraints", default=str(ROOT / "samples/constraints.json"))
    ap.add_argument("--spec", help="existing design_spec.json: skip the LLM and only re-translate")
    ap.add_argument("--out", help="output dir (default data/runs/design_<YYYYMMDD_HHMM>)")
    ap.add_argument("--force", action="store_true", help="ignore the cached LLM answer")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args(argv)

    inp = load_inputs(Path(args.profile), Path(args.brief), Path(args.room), Path(args.items))
    vocab = build_vocab(inp)
    meta = {"llm_calls": 0}
    if args.spec:
        try:
            spec = validate_spec(json.loads(Path(args.spec).read_text()), vocab)
        except DesignError as e:
            print("spec rejected:\n  - " + "\n  - ".join(e.errors), file=sys.stderr)
            return 2
    else:
        try:
            spec, meta = generate_spec(inp, ROOT / "data" / "design_cache", force=args.force, log=lambda m: print(m, file=sys.stderr))
        except DesignError as e:
            print("design rejected after retry:\n  - " + "\n  - ".join(e.errors), file=sys.stderr)
            return 2
    out = Path(args.out) if args.out else ROOT / "data" / "runs" / f"design_{time.strftime('%Y%m%d_%H%M')}"
    base_items = json.loads(Path(args.items).read_text())
    base_cons = json.loads(Path(args.constraints).read_text())
    built = build_and_solve(spec, vocab, inp.room, base_items, base_cons, log=lambda m: print(m, file=sys.stderr))
    lighting = write_bundle(out, spec, vocab, built, inp, meta)
    if not args.no_publish:
        publish_to_viewer(out, ROOT / "viewer" / "public" / "me")
    lay = built["layout"]
    print(f"design '{spec.concept_name}': {len(lay['placements'])} placed, {len(lay.get('unplaced', []))} unplaced, score {lay.get('score')}, "
          f"hard violations {len(built['hard_violations'])}; LLM calls this run {meta.get('llm_calls', 0)} (ledger {ledger_count()}) -> {out}", file=sys.stderr)
    for n in built["notes"]:
        print("  note:", n, file=sys.stderr)
    for i in lighting["issues_all"]:
        print(f"  lighting {i['code']} ({', '.join(i['scenes'])}): {i['message']}", file=sys.stderr)
    return 2 if built["hard_violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

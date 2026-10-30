#!/usr/bin/env python
"""Build the Qloo-grounded design brief and write data/brief.json.

  python run_brief.py                       # entities from data/taste_profile.json, else a seed from data/music.json
  python run_brief.py --profile P.json      # explicit TasteProfile JSON
  python run_brief.py --entity "Name:type"  # add entities (repeatable); alone, they replace the default source
  python run_brief.py --location "Brooklyn"
Needs QLOO_API_KEY (environment or .env). Responses are cached privately under cache/qloo/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ptah import qloo
from ptah.brief import build_brief, entities_from_profile, resolve, seed_from_music
from ptah.data import DATA_DIR


def summarize(brief: dict) -> None:
    def why(item: dict) -> str:
        return ", ".join(w["name"] for w in item.get("why", [])[:2]) or "-"

    print(f"\nlocation: {brief['location']}   inputs: " + ", ".join(i["name"] for i in brief["inputs"]))
    for kind in ("decor", "ambience"):
        print(f"{kind}: " + ", ".join(f"{t['name']} {t['affinity']}" for t in brief["taste"][kind][:6]))
    print("\ninspiration places:")
    for p in brief["inspiration_places"][:8]:
        print(f"  {p['name']} [{p['kind']}, {p['neighborhood']}] {p['affinity']} decor={p['decor_tags'][:3]} why: {why(p)}")
    print("local shops:")
    for p in brief["local_shops"][:8]:
        print(f"  {p['name']} [{p['shop_kind']}, {p['neighborhood']}] {p['affinity']} why: {why(p)}")
    print("brands:")
    for b in brief["brands"][:8]:
        print(f"  {b['name']} {b['affinity']} why: {why(b)}")
    print("discovery:")
    for d in brief["discovery"]:
        print(f"  {d['type']}: {d['name']} {d['affinity']} why: {why(d)}")
    print(f"\nnetwork calls: {brief['qloo']['network_calls']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, help="TasteProfile JSON (default: data/taste_profile.json if present)")
    ap.add_argument("--music", type=Path, default=DATA_DIR / "music.json", help="seed source when no profile")
    ap.add_argument("--entity", action="append", default=[], metavar="NAME:TYPE",
                    help="extra entity to resolve via /search, e.g. 'Name:artist' (repeatable)")
    ap.add_argument("--location", default="New York City")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "brief.json")
    ap.add_argument("--qloo-cap", type=int, default=400, help="max network calls this run (cache hits are free)")
    ap.add_argument("--no-tag-why", action="store_true", help="skip per-entity taste analyses (saves calls)")
    args = ap.parse_args()

    if qloo.is_stub():
        sys.exit("QLOO_API_KEY is not set (environment or .env); the brief needs the real Qloo API.")
    qloo.reset(args.qloo_cap)

    skipped: list[dict] = []
    profile = args.profile or (DATA_DIR / "taste_profile.json")
    if profile.exists():
        entities = entities_from_profile(profile)
        print(f"entities: {len(entities)} from {profile}")
    elif args.entity and not args.profile:
        entities = []
    else:
        entities, skipped = seed_from_music(args.music)
        print(f"entities: {len(entities)} seeded from {args.music} ({len(skipped)} skipped)")
    for spec in args.entity:
        name, _, typ = spec.rpartition(":")
        if not name:
            name, typ = spec, ""
        hit = resolve(name, typ)
        if not hit:
            skipped.append({"name": name, "reason": "no near-exact Qloo match"})
        elif all(hit["qloo_id"] != e["qloo_id"] for e in entities):
            entities.append({"qloo_id": hit["qloo_id"], "name": hit["name"], "type": typ or hit["type"]})
    if not entities:
        sys.exit("no usable entities (profile empty / nothing resolved)")

    try:
        brief = build_brief(entities, args.location, tag_why=not args.no_tag_why, skipped_inputs=skipped)
    except qloo.QlooError as e:
        sys.exit(f"Qloo error: {e}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}")
    summarize(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Extract a TasteProfile from data/ and write data/taste_profile.json.

  python run_taste.py            # real run (needs PTAH_LM + provider key + Deno)
  python run_taste.py --dry-run  # no LLM: exercises tools + schemas, prints what would be sent
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ptah import qloo
from ptah.data import DATA_DIR, load_music, load_posts, load_summary, posts_with_tag, search_posts
from ptah.schemas import QlooEntity, TasteProfile, Theme


def dry_run() -> int:
    from ptah import taste

    posts, music, summary = load_posts(), load_music(), load_summary()
    print(f"posts: {len(posts)} rows, {sum(len(p.get('caption') or '') for p in posts)} caption chars")
    print(f"music: {len(music)} tracks; summary keys: {sorted(summary)}")
    print("would send as sandbox variables: posts_overview, posts, music (not stuffed in prompt)")
    print("tools:", [t.__name__ for t in taste.TOOLS])

    qloo.reset()
    # Probe terms come from this user's own data, never from hard-coded examples.
    top_tag = summary["top_hashtags"][0][0] if summary.get("top_hashtags") else ""
    probe_name = (summary.get("music_artists") or [top_tag or "test"])[0]
    for label, rows in [(f"search_posts({top_tag!r})", search_posts(top_tag, 3)),
                        (f"posts_with_tag({top_tag!r})", posts_with_tag(top_tag, 3))]:
        print(f"\n{label} -> {len(rows)} rows (limit 3)")
        for r in rows:
            print("  ", r["id"], r["owner"], "|", r["caption"][:70].replace("\n", " "))
    found = qloo.qloo_search(probe_name, "artist")
    rel = qloo.qloo_related([found[0]["qloo_id"]], "brand")
    print("\nqloo_search ->", found[0])
    print("qloo_related ->", [r["name"] for r in rel], f"(calls used: {qloo.calls_used()})")

    profile = TasteProfile(
        entities=[QlooEntity(qloo_id=found[0]["qloo_id"], name=found[0]["name"], type="artist",
                             evidence="dry-run example", confidence=0.5)],
        aesthetic_tags=["example"],
        themes=[Theme(name=top_tag or "example", post_count=len(rows), example_post_ids=[r["id"] for r in rows])],
        style_summary="Dry-run placeholder.",
    )
    TasteProfile.model_validate_json(profile.model_dump_json())
    print("\nTasteProfile schema round-trip OK")

    try:
        prog = taste.build_program()
        print("RLM program built OK:", type(prog).__name__)
    except Exception as e:  # noqa: BLE001
        print("RLM program build failed:", repr(e))
        return 1
    print("\n--- instructions that would be sent (first 400 chars) ---")
    print(taste.INSTRUCTIONS[:400])
    print("\ndry-run complete; no LLM calls made.")
    return 0


def main() -> int:
    from ptah.env import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="skip the LLM; exercise tools and schemas")
    ap.add_argument("--qloo-cap", type=int, default=qloo.DEFAULT_CALL_CAP, help="max Qloo tool calls per run")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "taste_profile.json")
    ap.add_argument("--allow-stub-qloo", action="store_true",
                    help="allow a real LLM run with the STUB Qloo tools (output is not a real profile)")
    args = ap.parse_args()
    if args.dry_run:
        return dry_run()
    import os
    if not os.environ.get("QLOO_API_KEY") and not args.allow_stub_qloo:
        sys.exit("QLOO_API_KEY is not set, so Qloo tools are stubs. Set the key, or pass --allow-stub-qloo "
                 "to test the LLM loop knowing the profile will contain placeholder entities.")

    from ptah.taste import extract_taste

    profile = extract_taste(args.qloo_cap)
    args.out.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"entities: {len(profile.entities)}  themes: {len(profile.themes)}  Qloo calls: {qloo.calls_used()}")
    for t in sorted(profile.themes, key=lambda t: -t.post_count)[:5]:
        print(f"  theme {t.name} ({t.post_count})")
    print("tags:", ", ".join(profile.aesthetic_tags))
    print("style:", profile.style_summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

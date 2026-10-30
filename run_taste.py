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

    return real_run(args)


def summary_md(info: dict) -> str:
    """Human-readable run summary (themes, kept/dropped entities with reasons, models, iterations, calls)."""
    prof = info.get("profile") or {}
    lines = [f"# Taste run {info['stamp']}", "", f"- status: **{info['status']}**" + (f" ({info['error']})" if info.get("error") else ""),
             f"- models: main `{info['models']['main']}`, sub `{info['models']['sub']}`",
             f"- RLM iterations (trajectory steps): {info.get('iterations', 'n/a')}",
             f"- LLM calls: {info.get('llm_calls', 'n/a')}",
             f"- Qloo: {info['qloo_stats']['calls']} live calls, {info['qloo_stats']['cache_hits']} cache hits, "
             f"{info['qloo_stats']['n429']} x 429, {info['qloo_stats']['retries']} retries; "
             f"{info['qloo_tool_calls']} tool calls, {info['qloo_seen']} distinct entities returned",
             "", "## Themes", ""]
    for t in sorted(prof.get("themes") or [], key=lambda t: -t["post_count"]):
        lines.append(f"- {t['name']} ({t['post_count']} posts)")
    lines += ["", f"## Entities kept ({len(info['kept'])})", ""]
    lines += [f"- {e['name']} [{e['type']}] {e['qloo_id']} conf {e['confidence']}" for e in info["kept"]]
    lines += ["", f"## Entities dropped ({len(info['dropped'])})", ""]
    lines += [f"- {d['entity'].get('name')} [{d['entity'].get('type')}]: {'; '.join(d['reasons'])}" for d in info["dropped"]] or ["- none"]
    lines += ["", "## Style", "", f"tags: {', '.join(prof.get('aesthetic_tags') or [])}", "", prof.get("style_summary", ""), ""]
    return "\n".join(lines)


def real_run(args) -> int:
    """Real extraction with persistent logs; the run directory is written even if the run fails."""
    import datetime as dt
    import os
    import traceback

    from ptah import profile_check, taste
    from ptah.runlog import RunDir

    info: dict = {"status": "failed", "error": None, "kept": [], "dropped": [], "profile": None,
                  "models": {"main": os.environ.get("PTAH_LM", "?"),
                             "sub": os.environ.get("PTAH_SUB_LM") or os.environ.get("PTAH_LM", "?")}}
    result: dict = {}
    with RunDir() as run:
        info["stamp"] = run.path.name
        print(f"run directory: {run.path}")
        try:
            result = taste.run_extraction(args.qloo_cap)
            raw = json.loads(result["profile"].model_dump_json())
            run.write_json("profile.raw.json", raw)
            seen = result["registry"]["seen"]
            clean, dropped = profile_check.check_profile(raw, seen)
            run.write_json("profile.json", clean)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")
            info.update(status="ok", profile=clean, kept=clean["entities"], dropped=dropped)
            print(f"wrote {args.out}")
            print(f"entities: {len(clean['entities'])} kept, {len(dropped)} dropped  themes: {len(clean['themes'])}  "
                  f"Qloo calls: {qloo.calls_used()}")
            for d in dropped:
                print("  DROPPED", d["entity"].get("name"), "-", "; ".join(d["reasons"]))
            for t in sorted(clean["themes"], key=lambda t: -t["post_count"])[:5]:
                print(f"  theme {t['name']} ({t['post_count']})")
            print("tags:", ", ".join(clean["aesthetic_tags"]))
            print("style:", clean["style_summary"])
        except BaseException as e:  # noqa: BLE001 - logs must survive any failure, including Ctrl-C
            info["error"] = f"{type(e).__name__}: {e}"[:500]
            traceback.print_exc()
            status = 1
        else:
            status = 0
        finally:
            lastrun = qloo.last_run()
            run.write_json("qloo_calls.json", lastrun["calls"])
            pred = result.get("prediction")
            traj = getattr(pred, "trajectory", None) if pred is not None else None
            if traj is not None:
                run.write_json("trajectory.json", traj)
                info["iterations"] = len(traj)
            lms = result.get("lms") or {}
            hist = [len(getattr(lm, "history", []) or []) for lm in {id(v): v for v in lms.values()}.values()]
            info["llm_calls"] = sum(hist) if hist else "n/a"
            info["qloo_stats"] = qloo.stats()
            info["qloo_tool_calls"] = len(lastrun["calls"])
            info["qloo_seen"] = len(lastrun["seen"])
            info.setdefault("stamp", dt.datetime.now().strftime("%Y-%m-%d_%H%M"))
            run.write_text("summary.md", summary_md(info))
            print(f"run logs: {run.path}")
    return status


if __name__ == "__main__":
    sys.exit(main())

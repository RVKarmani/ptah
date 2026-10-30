"""Product scout CLI: replace designed pieces with REAL products (SerpApi Google Shopping).

    python run_scout.py                              # newest data/runs/design_* bundle, art/poster slots only
    python run_scout.py --bundle data/runs/design_X --only all --max-searches 12
    python run_scout.py --sample                     # dry run on the samples/ art + poster, scratch bundle data/runs/scout_test_<ts>/

Updates the bundle's items.json in place (image, price, url, source, name, colours, product{}), keeps why_chain, writes
scout_report.md and images/<slot_id>.jpg, re-solves the layout when a print's size changed, and re-copies the bundle to
viewer/public/me/.  Searches are cached under cache/serpapi/ and budgeted (--max-searches counts LIVE calls only).
Downloaded images and search results are private working data: they stay under data/, cache/ and viewer/public/me/ (git-ignored).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ptah.env import ROOT
from ptah.products.enrich import classify_material
from ptah.products.scout import ScoutResult, Slot, direct_link, scout_slot, slot_from_item, upscale_for_texture, clean_title
from ptah.products.serpapi import BudgetExceeded, SerpApiClient, SerpApiError

BUNDLE_FILES = ("room.json", "items.json", "layout.json", "lighting.json", "taste.json", "constraints.json")
# scouting priority for --only all (art first, then the pieces a room is judged by)
PRIORITY = ["art", "poster", "sofa", "armchair", "coffee_table", "rug", "floor_lamp", "bookshelf", "tv_stand", "side_table", "table_lamp",
            "pendant", "sconce", "mirror", "cushion", "throw", "vase"]
SKIP_FOR_ALL = {"led_strip", "picture_light", "plant", "small_plant", "book_stack", "vinyl_crate", "candle", "tray", "guitar", "amp", "guitar_hanger", "tv", "frame"}


def newest_bundle() -> Optional[Path]:
    runs = sorted((ROOT / "data" / "runs").glob("design_*"), key=lambda p: p.stat().st_mtime)
    return runs[-1] if runs else None


def make_sample_bundle() -> Path:
    out = ROOT / "data" / "runs" / f"scout_test_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    s = ROOT / "samples"
    for n in ("room.json", "items.json", "layout.json", "lighting.json", "constraints.json"):
        shutil.copyfile(s / n, out / n)
    (out / "taste.json").write_text(json.dumps({"style_summary": "sample", "aesthetic_tags": []}))
    # give the sample art slots a subject / keywords so the dry run exercises the real code path
    items = json.loads((out / "items.json").read_text())
    for it in items:
        if it["id"] == "art-1":
            it["slot"] = {"role": "art_main", "template": "art", "category": "art", "size_range_cm": {"w": [80, 100], "d": [4, 4], "h": [53, 67]},
                          "style_keywords": ["japanese", "landscape", "calm"], "inspired_by": "Spirited Away", "colors": it["colors"], "params": {"frame": "framed"}}
        if it["id"] == "poster-1":
            it["slot"] = {"role": "art_secondary", "template": "poster", "category": "poster", "size_range_cm": {"w": [44, 56], "d": [3, 3], "h": [62, 78]},
                          "style_keywords": ["minimal", "movie", "vintage"], "inspired_by": "Jojo Rabbit", "colors": it["colors"], "params": {"frame": "thin"}}
    (out / "items.json").write_text(json.dumps(items, indent=1) + "\n")
    return out


def select_slots(items: List[Dict[str, Any]], only: str) -> List[Dict[str, Any]]:
    arts, rest = [], []
    for it in items:
        if it.get("owned"):
            continue
        if not it.get("designed") and "slot" not in it and only == "all" and it.get("price"):
            continue
        s = slot_from_item(it)
        if s.is_art:
            arts.append(it)
        elif only == "all" and s.category in PRIORITY and s.category not in SKIP_FOR_ALL:
            rest.append((PRIORITY.index(s.category), it))
    return arts + [it for _, it in sorted(rest, key=lambda t: t[0])]


def apply_result(item: Dict[str, Any], res: ScoutResult, bundle: Path, me_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    c = res.chosen
    if c is None:
        return None
    slot = res.slot
    (bundle / "images").mkdir(exist_ok=True)
    rel = f"images/{slot.id}.jpg"
    tex = upscale_for_texture(c.image) if slot.is_art else c.image
    tex.save(bundle / rel, quality=92)
    if c.source_image is not None:
        c.source_image.save(bundle / "images" / f"{slot.id}.src.jpg", quality=90)
    if me_dir is not None:
        (me_dir / "images").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle / rel, me_dir / rel)
    link, listed = direct_link(res.detail, c.source)
    r = c.result
    price = f"${c.price:,.2f}".replace(".00", "") if c.price is not None else r.get("price")
    item["name"] = clean_title(c.title)
    item["image"] = rel
    item["price"] = price
    item["url"] = link or r.get("product_link")
    item["source"] = c.source
    if c.colors:
        # the viewer paints the frame from colors.secondary (artT/posterT): keep the DESIGNED frame colour there, the print's own
        # colours stay available as product.image_colors
        designed = dict(item.get("colors") or {})
        cols = dict(c.colors)
        if slot.is_art and designed.get("secondary"):
            cols["secondary"] = designed["secondary"]
        item["colors"] = cols
    if res.dims:
        item["w"], item["h"] = res.dims
        item["dims_estimated"] = False
    mat = classify_material(c.title)
    if mat.get("primary") and not slot.is_art:   # the real product's material, when its title names one
        item["material"] = {"primary": mat["primary"], "secondary": mat.get("secondary") or (item.get("material") or {}).get("secondary")}
    item["designed"] = False
    item["scouted"] = True
    reasons = "; ".join(c.notes[:3])
    item["why"] = f"Real product from {c.source} for the '{slot.role}' slot" + (f": {reasons}." if reasons else ".")
    item["product"] = {
        "title": c.title, "source": c.source, "price": price, "extracted_price": c.price, "link": link, "google_link": r.get("product_link"),
        "product_id": r.get("product_id"), "thumbnail": r.get("thumbnail"), "query": c.query, "score": round(c.score, 2),
        "image_file": rel, "crop_confidence": round(c.crop_conf, 2), "aspect": round(c.aspect, 3) if c.aspect else None,
        "listed_dimensions": listed, "image_colors": c.colors, "scouted_at": int(time.time()),
    }
    return item["product"]


def report_md(results: List[ScoutResult], client: SerpApiClient, started_used: int) -> str:
    L = ["# Scout report", "", f"Live SerpApi calls this run: {client.live_calls} (cache hits {client.cache_hits}); month total {client.used_this_month()}/{client.monthly_cap}.", ""]
    for res in results:
        s = res.slot
        L += [f"## {s.id} ({s.role}, {s.category})", ""]
        L.append(f"- Slot: size {s.w_range[0]:.0f}-{s.w_range[1]:.0f} x {s.h_range[0]:.0f}-{s.h_range[1]:.0f} cm; keywords: {', '.join(s.style_keywords) or '-'}; inspired by: {s.inspired_by or '-'}")
        for q in res.queries:
            L.append(f"- Query: `{q['q']}` (price {q.get('min_price')}-{q.get('max_price')}; {q['why']})")
        c = res.chosen
        if c is None:
            L += [f"- RESULT: nothing chosen. {res.note}", ""]
            continue
        L.append(f"- CHOSEN: **{c.title}** | {c.source} | {c.price if c.price is None else '$%.2f' % c.price} | score {c.score:.2f}")
        L.append(f"  - why: {'; '.join(c.notes) or 'best overall score'}; store quality, price sanity and palette fit weighed in")
        if res.dims:
            L.append(f"  - dims: {res.dims[0]} x {res.dims[1]} cm (print aspect {c.aspect:.2f}, crop confidence {c.crop_conf:.2f})")
        if res.note:
            L.append(f"  - note: {res.note}")
        L.append("- Runners-up:")
        for r in [x for x in res.ranked if x is not c][:4]:
            L.append(f"  - {r.title[:80]} | {r.source} | {r.price} | score {r.score or r.pre:.2f}" + (" | digital" if r.digital else ""))
        L.append(f"- Rejected by the filters: {len(res.rejected)} (e.g. " + "; ".join(f"{t[:40]} [{w}]" for t, w in res.rejected[:3]) + ")")
        L.append("")
    return "\n".join(L) + "\n"


def relayout(bundle: Path, items: List[Dict[str, Any]], log) -> None:
    from ptah.layout import Budget, hard_violations, solve
    from ptah.lighting import compute_lighting
    room = json.loads((bundle / "room.json").read_text())
    cons = json.loads((bundle / "constraints.json").read_text())
    res = solve(room, items, cons, Budget(time_s=4.0, max_expansions=260, step=10.0), room_name="room.json")
    lay = res.layout
    bad = hard_violations(room, items, lay)
    (bundle / "layout.json").write_text(json.dumps(lay, indent=2) + "\n")
    scene = "evening"
    spec = bundle / "design_spec.json"
    if spec.exists():
        scene = (json.loads(spec.read_text()).get("lighting_mood") or {}).get("default_scene", "evening")
    lighting = compute_lighting(room, items, lay, "day" if scene == "day" else "night", None, scene)
    (bundle / "lighting.json").write_text(json.dumps(lighting, indent=1) + "\n")
    log(f"re-solved layout: {len(lay['placements'])} placed, {len(lay.get('unplaced', []))} unplaced, score {lay.get('score')}, hard violations {len(bad)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", help="design bundle dir (default: newest data/runs/design_*)")
    ap.add_argument("--sample", action="store_true", help="dry run on a scratch copy of samples/ (data/runs/scout_test_<ts>/), never touches samples/")
    ap.add_argument("--only", choices=["art", "all"], default="art")
    ap.add_argument("--max-searches", type=int, default=10, help="max LIVE SerpApi calls this run (cache hits are free)")
    ap.add_argument("--no-details", action="store_true", help="skip the product-detail call (direct store link) for chosen products")
    ap.add_argument("--details-all", action="store_true", help="also fetch product details (1 search each) for furniture / decor picks; art always does")
    ap.add_argument("--no-relayout", action="store_true")
    ap.add_argument("--publish-dir", default=str(ROOT / "viewer" / "public" / "me"), help="where the bundle is copied for the viewer; 'none' disables")
    ap.add_argument("--slots", help="comma-separated item ids to scout (default: all selected)")
    args = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731

    bundle = make_sample_bundle() if args.sample else (Path(args.bundle) if args.bundle else newest_bundle())
    if bundle is None or not (bundle / "items.json").exists():
        log("no design bundle found (data/runs/design_*); run run_design.py first, or use --sample")
        return 2
    bundle = bundle if bundle.is_absolute() else (ROOT / bundle)
    me_dir = None if args.publish_dir == "none" else Path(args.publish_dir)
    items = json.loads((bundle / "items.json").read_text())
    chosen_items = select_slots(items, args.only)
    if args.slots:
        want = set(args.slots.split(","))
        chosen_items = [i for i in chosen_items if i["id"] in want]
    log(f"bundle {bundle.relative_to(ROOT) if bundle.is_relative_to(ROOT) else bundle}: scouting {[i['id'] for i in chosen_items]} (max {args.max_searches} live searches)")

    client = SerpApiClient(max_searches=args.max_searches, verbose=True)
    started_used = client.used_this_month()
    by_id = {i["id"]: i for i in items}
    results: List[ScoutResult] = []
    changed_dims = False
    for it in chosen_items:
        slot = slot_from_item(it)
        before = (it.get("w"), it.get("h"))
        try:
            res = scout_slot(slot, client, details=(not args.no_details) and (slot.is_art or args.details_all))
        except BudgetExceeded as e:
            log(f"stopping: {e}")
            break
        except SerpApiError as e:
            log(f"{slot.id}: SerpApi error: {e}")
            continue
        results.append(res)
        prod = apply_result(by_id[slot.id], res, bundle, me_dir)
        if prod is None:
            log(f"{slot.id}: no product ({res.note})")
            continue
        log(f"{slot.id}: {prod['title'][:70]} | {prod['source']} | {prod['price']} | {by_id[slot.id]['w']}x{by_id[slot.id]['h']} cm")
        if before != (it.get("w"), it.get("h")):
            changed_dims = True

    (bundle / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    (bundle / "scout_report.md").write_text(report_md(results, client, started_used))
    if changed_dims and not args.no_relayout:
        relayout(bundle, items, log)
    if me_dir is not None:
        me_dir.mkdir(parents=True, exist_ok=True)
        for n in BUNDLE_FILES:
            if (bundle / n).exists():
                shutil.copyfile(bundle / n, me_dir / n)
        log(f"copied bundle to {me_dir}")
    log(f"live searches this run: {client.live_calls}; month {client.used_this_month()}/{client.monthly_cap}; report -> {bundle / 'scout_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

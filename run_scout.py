"""Product scout CLI: replace designed pieces with REAL products (SerpApi Google Shopping).

    python run_scout.py                              # newest data/runs/design_* bundle, art/poster slots only
    python run_scout.py --bundle data/runs/design_X --only all --max-searches 12
    python run_scout.py --sample                     # dry run on the samples/ art + poster, scratch bundle data/runs/scout_test_<ts>/
    python run_scout.py --cohesive --bundle data/runs/design_X_artonly --out data/runs/design_X_cohesive
        # cohesion-first: art chosen as a SET against the room palette; furniture / decor swapped for a real product ONLY when it passes the
        # strict gate (palette + designed colour + material + style + size + studio photo); otherwise the designed piece stays and the best
        # candidate is recorded as "not cohesive enough".  Writes cohesion_report.md, never touches the source bundle.

Updates the bundle's items.json in place (image, price, url, source, name, colours, product{}), keeps why_chain, writes
scout_report.md and images/<slot_id>.jpg, re-solves the layout when a print's size changed, and re-copies the bundle to
viewer/public/me/.  Searches are cached under cache/serpapi/ and budgeted (--max-searches counts LIVE calls only).
Downloaded images and search results are private working data: they stay under data/, cache/ and viewer/public/me/ (git-ignored).
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ptah.env import ROOT
from ptah.products import cohesion, curate, decor, links
from ptah.products.enrich import classify_material
from ptah.products.scout import ScoutResult, Slot, direct_link, scout_slot, slot_from_item, store_quality, upscale_for_texture, clean_title
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
    curate.materialize(c)           # analyses come from the disk cache without the picture; load it for the piece actually used
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
    _, listed = direct_link(res.detail, c.source)
    pick = links.pick_store(res.detail, c.source, store_quality)          # merchant link, only ever from the SerpApi response
    r = c.result
    google = r.get("product_link")
    link = pick["url"] if pick else None
    store = pick["store"] if pick else c.source
    price = (pick or {}).get("price") or (f"${c.price:,.2f}".replace(".00", "") if c.price is not None else r.get("price"))
    item["name"] = clean_title(c.title)
    item["image"] = rel
    item["price"] = price
    item["url"] = link                                                      # the merchant page (None when SerpApi gave no store link)
    item["source"] = store
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
        "title": c.title, "source": c.source, "store": store, "price": price, "extracted_price": (pick or {}).get("extracted_price") or c.price,
        "url": link, "google_url": google, "link": link, "google_link": google, "store_matched": (pick or {}).get("matched"),
        "other_stores": (pick or {}).get("others") or [],
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


# ----------------------------------------------------------------------------------------------------------------------------------
# cohesive mode: palette / material / style / scale / set coherence decide, furniture swaps pass a strict gate
# ----------------------------------------------------------------------------------------------------------------------------------

def _cand_row(c, extra: str = "") -> str:
    b = c.cohesion or {}
    p = b.get("parts", {})
    cols = " ".join(x["hex"] for x in (b.get("palette", {}).get("clusters") or [])[:4])
    price = "?" if c.price is None else f"${c.price:,.0f}"
    return (f"| {clean_title(c.title, 52)} | {c.source[:22]} | {price} | **{b.get('total', c.score):.2f}** | "
            f"{p.get('palette', 0):.2f} | {p.get('style', p.get('material', 0)):.2f} | {p.get('scale', 0):.2f} | {p.get('quality', 0):.2f} | "
            f"{p.get('taste', p.get('material', 0)):.2f} | {b.get('medium', '')} | {cols}{extra} |")


def furn_block(f: Dict[str, Any]) -> List[str]:
    """Report lines of one scouted furniture / decor slot (best candidate, why it was kept or swapped, runners-up)."""
    L: List[str] = []
    c = f["best"]
    b = c.cohesion
    L += [f"### {f['id']} ({f['category']}): " + ("SWAPPED" if f["accepted"] else "KEPT DESIGNED PIECE (best candidate not cohesive enough)"), ""]
    pal = b["palette"]
    L += [f"Best candidate: **{clean_title(c.title, 70)}** | {c.source} | " + (f"${c.price:,.0f}" if c.price else "?") + f" | total {b['total']:.2f}",
          f"- palette {b['parts']['palette']:.2f} (room fit {pal['score']:.2f}, dominant colour {pal.get('slot_dE', '?')} dE from designed {f['designed_colors'].get('primary')}, "
          f"{pal.get('slot_cover', 0):.0%} in designed colours); material {b['parts']['material']:.2f} ({b['material']['primary']}/{b['material']['secondary']}"
          f"{'; ' + '; '.join(b['material']['flags']) if b['material']['flags'] else ''}); style {b['parts']['style']:.2f} ({', '.join(b['style']['positive'][:4]) or 'no mood words'}); "
          f"scale {b['parts']['scale']:.2f}; studio photo: {b['studio_photo']}"]
    if b.get("light_spec"):
        ls = b["light_spec"]
        L += [f"- light spec read from the listing: stated {', '.join(ls['stated']) or 'nothing'}; kept the designed value for {', '.join(ls['kept_designed']) or 'nothing'} "
              f"(parsed {ls.get('color_temp_k')} K, {ls.get('lumens')} lm, dimmable {ls.get('dimmable')}, guessed: {', '.join(ls.get('estimated') or []) or 'none'})"]
    if not f["accepted"]:
        L += ["- rejected because: " + "; ".join(b["gate"]["reasons"])]
    L += ["", "Runners-up:", ""]
    for r in f["runners"]:
        rb = r.cohesion
        L.append(f"- {clean_title(r.title, 58)} | {r.source} | " + (f"${r.price:,.0f}" if r.price else "?") + f" | total {rb['total']:.2f} | " +
                 ("passes the gate" if rb["gate"]["accepted"] else "fails: " + "; ".join(rb["gate"]["reasons"][:2])))
    L += [""]
    return L


def cohesion_report(profile, art: Dict[str, Any], furn: List[Dict[str, Any]], set_check: Dict[str, Any], notes: List[str], client) -> str:
    L = ["# Cohesion report", "",
         f"Room aesthetic: **{profile.name}** (walls {profile.wall}, floor {profile.floor_hex}, accents {', '.join(profile.accents)}); materials {', '.join(profile.materials)}; "
         f"avoid {', '.join(profile.avoid)}; designed woods {', '.join(sorted(set(profile.woods)))}; metals {', '.join(sorted(set(profile.metals)))}.", "",
         f"SerpApi: {client.live_calls} live call(s) in this run, month total {client.used_this_month()}/{client.monthly_cap}.", "",
         "Scoring: CIEDE2000 palette fit to the room palette (background / frame removed; clashing saturated colours, cool greys, loud or busy pictures punished; "
         "an echo of the wall / accent colours rewarded), style words vs the aesthetic + avoid list + off-mood vocabulary, material (furniture), scale / aspect, store + cut-out quality, "
         "taste link (Qloo subject). Art pieces are chosen as a SET (palette overlap, medium + era compatibility, matching chroma, how many room colours the wall echoes).",
         f"Furniture swap gate (all must hold): {json.dumps(cohesion.GATE)}.", ""]
    L += ["## Art", ""]
    pair = art.get("pair")
    if pair:
        st = pair["set"]
        L += [f"Chosen set score **{pair['total']:.3f}** = 0.55 x mean individual ({pair['individual']:.2f}) + 0.45 x set coherence ({st['score']:.2f}): palette overlap {st['palette_overlap']:.2f}, "
              f"mediums {st['medium'][0]} + {st['medium'][1]} (compat {st['medium_compat']:.2f}), eras {st['era'][0]} / {st['era'][1]}, chroma match {st['chroma_match']:.2f}, "
              f"room colours echoed by the wall: {', '.join(st.get('coverage', {}).get('echoed', [])) or '-'} of {', '.join(st.get('coverage', {}).get('of', []))}.", ""]
    hdr = "| Candidate | Store | Price | Total | Palette | Style | Scale | Quality | Taste | Medium | Colours |\n|---|---|---|---|---|---|---|---|---|---|---|"
    for sid, sec in art.get("slots", {}).items():
        L += [f"### {sid} ({sec['role']})", "", f"Queries considered: {', '.join('`%s`' % q for q in sec['queries'])}", "", "CHOSEN:", "", hdr, _cand_row(sec["chosen"], ""), "", "Runners-up (best alternatives for this slot, same ranking):", "", hdr]
        L += [_cand_row(c) for c in sec["runners"]]
        L += ["", "Rejected / capped (examples):", ""]
        L += [f"- {clean_title(t, 60)} [{w}]" for t, w in sec["rejected"][:8]]
        L += [""]
    if art.get("alt_pairs"):
        L += ["Alternative sets:", ""]
        for p in art["alt_pairs"]:
            L.append(f"- {p['total']:.3f}: {clean_title(p['main'].title, 48)} + {clean_title(p['side'].title, 48)} (set {p['set']['score']:.2f}, individual {p['individual']:.2f})")
        L += [""]
    L += ["## Furniture and decor swaps", ""]
    acc = [f for f in furn if f["accepted"]]
    L += [f"Accepted {len(acc)} of {len(furn)} scouted slots; the rest keep the designed piece.", ""]
    for f in furn:
        L += furn_block(f)
    L += ["## Furniture set check (after swaps)", "",
          f"Wood families {set_check['wood_families']}, metals {set_check['metals']}; new wood family: {set_check['new_wood_family'] or 'none'}; new metal: {set_check['new_metal'] or 'none'}; ok = {set_check['ok']}.", ""]
    if notes:
        L += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(L) + "\n"


def cohesive_main(args, log) -> int:
    import copy
    src = Path(args.bundle) if args.bundle else None
    if src is None:
        log("--cohesive needs --bundle (the bundle to copy from)")
        return 2
    src = src if src.is_absolute() else ROOT / src
    dst = Path(args.out) if args.out else src.with_name(src.name.replace("_artonly", "") + "_cohesive")
    dst = dst if dst.is_absolute() else ROOT / dst
    shutil.copytree(src, dst, dirs_exist_ok=True)
    spec = json.loads((dst / "design_spec.json").read_text())
    items: List[Dict[str, Any]] = json.loads((dst / "items.json").read_text())
    room = json.loads((dst / "room.json").read_text())
    by_id = {i["id"]: i for i in items}
    profile = cohesion.build_profile(spec, items, room)
    client = SerpApiClient(max_searches=args.max_searches, verbose=True)
    notes: List[str] = []
    slots = {i["id"]: slot_from_item(i) for i in items if not i.get("owned")}
    art_slots = sorted([s for s in slots.values() if s.is_art], key=lambda s: (s.role != "art_main", s.id))
    log(f"cohesive curation: {src.name} -> {dst.name}; art slots {[s.id for s in art_slots]}")

    # ---- art: cached results + aesthetic-derived queries (cache first, live only within the budget)
    aq: List[str] = []
    for s in art_slots:
        for q in curate.aesthetic_queries(profile, s):
            aq.append(q["q"])
            try:
                client.search(q["q"], max_price=q["max_price"])
            except BudgetExceeded as e:
                log(f"aesthetic query skipped (budget): {q['q']} ({e})")
                break
            except SerpApiError as e:
                log(f"aesthetic query failed: {q['q']}: {e}")
    allres = curate.cached_results()
    by_q: Dict[str, List[Dict[str, Any]]] = {}
    for q, r in allres:
        by_q.setdefault(q, []).append(r)
    from ptah.products.scout import classify_art
    art_q = {q for q, rs in by_q.items() if rs and sum(1 for r in rs if classify_art(r)[0]) / len(rs) >= 0.5}
    art_results = [(q, r) for q, r in allres if q in art_q]
    log(f"{len(art_results)} cached art listings from {len(art_q)} queries")
    prefiltered: List[Tuple[str, str]] = []
    pool = curate.build_art_pool(art_results, art_slots[0], profile, skipped=prefiltered)
    log(f"art pool: {len(pool)} analysed, {len(prefiltered)} skipped on text alone (off-mood / avoid-list wording, price)")
    art: Dict[str, Any] = {"slots": {}, "pair": None, "alt_pairs": []}
    ranked: Dict[str, List[Any]] = {}
    rejected_by_slot: Dict[str, List[Tuple[str, str]]] = {}
    for s in art_slots:
        ranked[s.id] = curate.rank_for_slot(pool, s, profile)
        rejected_by_slot[s.id] = [(c.title, "; ".join(c.cohesion["caps"])) for c in ranked[s.id] if c.cohesion["caps"]] + prefiltered[:8]
    picks: Dict[str, Any] = {}
    orig_dims = {i["id"]: (i.get("w"), i.get("h")) for i in items}
    if len(art_slots) >= 2:
        pairs = curate.pick_art_set(ranked[art_slots[0].id], ranked[art_slots[1].id], art_slots[0], art_slots[1], profile, top=18)
        if not pairs:
            log("no art pair found")
            return 3
        art["pair"] = pairs[0]
        picks = {art_slots[0].id: pairs[0]["main"], art_slots[1].id: pairs[0]["side"]}
        art["alt_pairs"] = [p for p in pairs[1:] if p["main"].title != pairs[0]["main"].title or p["side"].title != pairs[0]["side"].title][:4]
    elif art_slots:
        picks = {art_slots[0].id: ranked[art_slots[0].id][0]}
    # unify frames: one wood family for the picture frames (the walnut of the designed furniture)
    walnut = next((((i.get("slot") or {}).get("colors") or i.get("colors") or {}).get("primary") for i in items
                   if ((i.get("slot") or {}).get("materials") or {}).get("primary") == "walnut"), "#6a4833")
    for s in art_slots:
        c = picks[s.id]
        item = by_id[s.id]
        res = ScoutResult(s, [], c, ranked[s.id][:8], rejected_by_slot[s.id])
        res.dims = curate.outer_dims(s, c.aspect)
        tok = c.result.get("immersive_product_page_token")
        if tok and not args.no_details and client.remaining() > 0:
            try:
                res.detail = client.product_detail(tok)
            except SerpApiError as e:
                log(f"{s.id}: detail fetch failed: {e}")
        apply_result(item, res, dst, None)
        # frames: one wood for every picture (the room's walnut).  art: frame is the secondary slot; poster: the thin frame is the primary slot
        item["colors"] = dict(item.get("colors") or {}, secondary=walnut)
        mat = dict(item.get("material") or {})
        if s.template == "poster":
            item["colors"]["primary"] = walnut
            mat["primary"] = "walnut"
        else:
            mat["secondary"] = "walnut"
        item["material"] = mat
        item["cohesion"] = {"total": c.cohesion["total"], "parts": c.cohesion["parts"], "medium": c.cohesion["medium"], "era": c.cohesion["era"],
                            "set": (art["pair"]["set"] if art["pair"] else None), "frame_colour": walnut}
        item["why"] = (f"Chosen for cohesion {c.cohesion['total']:.2f} (palette {c.cohesion['parts']['palette']:.2f}, style {c.cohesion['parts']['style']:.2f}); "
                       f"{c.cohesion['medium']} print, picked jointly with the other wall art as one set. Real product from {c.source}.")
        item["product"]["cohesion_score"] = c.cohesion["total"]
        distinct = [x for x in ranked[s.id] if x is not c][:5]
        art["slots"][s.id] = {"role": s.role, "chosen": c, "runners": distinct, "rejected": rejected_by_slot[s.id],
                              "queries": sorted({x.query for x in [c] + distinct})}
        log(f"{s.id}: {clean_title(c.title, 60)} | {c.source} | {item['price']} | {item['w']}x{item['h']} cm | cohesion {c.cohesion['total']:.2f}")

    # ---- furniture / decor: strict swap gate
    all_res = allres
    furn: List[Dict[str, Any]] = []
    accepted_swaps: List[Tuple[Dict[str, Any], Any, Dict[str, Any]]] = []
    for it in items:
        if it.get("owned") or it["id"] in picks:
            continue
        sl = slots.get(it["id"])
        if sl is None or sl.is_art or sl.category not in curate_slot_categories():
            continue
        cs = curate.furniture_candidates(sl, profile, all_res)
        if not cs:
            continue
        best = cs[0]
        ok = best.cohesion["gate"]["accepted"]
        furn.append({"id": it["id"], "category": sl.category, "best": best, "runners": cs[1:4], "accepted": ok, "designed_colors": dict(sl.colors)})
        if ok:
            accepted_swaps.append((it, best, copy.deepcopy(it)))
        else:
            it["product_candidate"] = {"status": "not cohesive enough", "title": best.title, "source": best.source, "price": best.result.get("price"),
                                       "link": best.result.get("product_link"), "cohesion": best.cohesion["total"], "parts": best.cohesion["parts"],
                                       "reasons": best.cohesion["gate"]["reasons"]}
    # apply accepted swaps, then verify the set (one wood family / metals); drop the weakest swaps until it holds
    def apply_swaps(sw):
        for it, best, original in sw:
            designed_mat, designed_cols = dict(original.get("material") or {}), dict(original.get("colors") or {})
            res = ScoutResult(slots[it["id"]], [], best, [best], [])
            apply_result(it, res, dst, None)
            it["material"] = designed_mat                       # the material direction stays the designed one
            it["colors"] = designed_cols                        # never recolour a designed piece: the 3D model keeps its designed colours (the gate
                                                                # guarantees the product is that colour); the photo's colours stay in product.image_colors
            it["cohesion"] = {"total": best.cohesion["total"], "parts": best.cohesion["parts"]}
            it["why"] = (f"Real product that passes the cohesion gate (total {best.cohesion['total']:.2f}; palette {best.cohesion['parts']['palette']:.2f}, "
                         f"material {best.cohesion['parts']['material']:.2f}, style {best.cohesion['parts']['style']:.2f}): same colour, material and wood / metal family as the designed piece.")
            it["product"]["cohesion_score"] = best.cohesion["total"]
    apply_swaps(accepted_swaps)
    set_check = cohesion.furniture_set_check(items, profile)
    log(f"furniture set check: {set_check}")
    dims_changed = any((by_id[i].get("w"), by_id[i].get("h")) != orig_dims[i] for i in picks)
    (dst / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    (dst / "cohesion_report.md").write_text(cohesion_report(profile, art, furn, set_check, notes + [f"Picture frames unified to the room's walnut {walnut} (the designed frames were indigo, ochre and thin black metal): one wood for every frame; the print colours stay in product.image_colors."], client))
    (dst / "scout_report.md").write_text("# Scout report\n\nSuperseded by cohesion_report.md in this bundle (cohesion-ranked picks, swap gate, runners-up).\n")
    (dst / "cohesion_profile.json").write_text(json.dumps(profile.to_json(), indent=1) + "\n")
    if dims_changed and not args.no_relayout:
        relayout(dst, items, log)
    me_dir = None if args.publish_dir == "none" else Path(args.publish_dir)
    if me_dir is not None:
        me_dir.mkdir(parents=True, exist_ok=True)
        (me_dir / "images").mkdir(exist_ok=True)
        for f in (dst / "images").glob("*"):
            if not f.name.endswith(".src.jpg"):
                shutil.copyfile(f, me_dir / "images" / f.name)
        for n in BUNDLE_FILES:
            if (dst / n).exists():
                shutil.copyfile(dst / n, me_dir / n)
        log(f"published to {me_dir}")
    log(f"live searches this run: {client.live_calls}; month {client.used_this_month()}/{client.monthly_cap}; report -> {dst / 'cohesion_report.md'}")
    return 0


# ----------------------------------------------------------------------------------------------------------------------------------
# linked mode: merchant links for the products, lights + decor through the same gate, shop-similar links for what stays designed
# ----------------------------------------------------------------------------------------------------------------------------------

RETRY_QUERIES = {   # one sharper, aesthetic-led query for the two big pieces the first gate kept designed
    "sofa": "denim blue linen 3 seater sofa tapered wood legs",
    "bookshelf": "solid walnut open bookcase 5 tier",
}


def relight(bundle: Path, items: List[Dict[str, Any]], log) -> None:
    """Recompute lighting.json from the (unchanged) layout after a luminaire's spec changed."""
    from ptah.lighting import compute_lighting
    room = json.loads((bundle / "room.json").read_text())
    lay = json.loads((bundle / "layout.json").read_text())
    scene = "evening"
    spec = bundle / "design_spec.json"
    if spec.exists():
        scene = (json.loads(spec.read_text()).get("lighting_mood") or {}).get("default_scene", "evening")
    (bundle / "lighting.json").write_text(json.dumps(compute_lighting(room, items, lay, "day" if scene == "day" else "night", None, scene), indent=1) + "\n")
    log("lighting re-computed for the changed luminaires (layout unchanged)")


def _fetch_detail(client: SerpApiClient, token: Optional[str], label: str, log) -> Optional[Dict[str, Any]]:
    if not token:
        log(f"{label}: no product page token in the cached results; cannot read the merchant links")
        return None
    try:
        return client.product_detail(token)
    except BudgetExceeded as e:
        log(f"{label}: detail skipped (budget): {e}")
    except SerpApiError as e:
        log(f"{label}: detail fetch failed: {e}")
    return None


def resolve_product_link(item: Dict[str, Any], allres, client: SerpApiClient, log) -> Optional[Dict[str, Any]]:
    """Merchant link for an already-swapped product (its product page token comes from the cached shopping results)."""
    prod = item.get("product") or {}
    token = links.page_token_for(prod.get("product_id"), allres)
    detail = _fetch_detail(client, token, item["id"], log)
    pick = links.pick_store(detail, prod.get("source"), store_quality)
    google = prod.get("google_url") or prod.get("google_link") or (item.get("url") if str(item.get("url") or "").startswith("https://www.google.") else None)
    prod["google_url"], prod["google_link"] = google, google
    if pick is None:
        prod["url"] = prod["link"] = None
        prod.setdefault("store", prod.get("source"))
        item["url"] = None
        return None
    prod.update(url=pick["url"], link=pick["url"], store=pick["store"], store_matched=pick["matched"], other_stores=pick["others"])
    if pick.get("price"):
        prod["price"] = item["price"] = pick["price"]
        if pick.get("extracted_price") is not None:
            prod["extracted_price"] = pick["extracted_price"]
    item["url"] = pick["url"]
    item["source"] = pick["store"]
    return pick


def swap_in(it: Dict[str, Any], slot: Slot, best, detail: Optional[Dict[str, Any]], dst: Path, light: Optional[Dict[str, Any]] = None,
            light_spec: Optional[Dict[str, Any]] = None) -> None:
    """Replace a designed piece with the product (name / photo / price / merchant link); geometry, colours and materials stay the designed ones."""
    original = copy.deepcopy(it)
    res = ScoutResult(slot, [], best, [best], [])
    res.detail = detail
    apply_result(it, res, dst, None)
    it["material"], it["colors"] = dict(original.get("material") or {}), dict(original.get("colors") or {})
    it.pop("product_candidate", None)
    it["cohesion"] = {"total": best.cohesion["total"], "parts": best.cohesion["parts"]}
    it["why"] = (f"Real product that passes the cohesion gate (total {best.cohesion['total']:.2f}; palette {best.cohesion['parts']['palette']:.2f}, "
                 f"material {best.cohesion['parts']['material']:.2f}, style {best.cohesion['parts']['style']:.2f}): same colour, material and wood / metal family as the designed piece.")
    it["product"]["cohesion_score"] = best.cohesion["total"]
    if light is not None:
        it["light"] = light
        it["cohesion"]["light_spec"] = {k: light_spec.get(k) for k in ("stated", "kept_designed", "estimated", "color_temp_k", "lumens", "dimmable")} if light_spec else None
        it["why"] += " Light: " + (", ".join(light_spec["stated"]) if light_spec and light_spec.get("stated") else "nothing stated") + " read from the listing; the rest keeps the designed light."


def _note_candidate(it: Dict[str, Any], best) -> None:
    it["product_candidate"] = {"status": "not cohesive enough", "title": best.title, "source": best.source, "price": best.result.get("price"),
                               "link": best.result.get("product_link"), "cohesion": best.cohesion["total"], "parts": best.cohesion["parts"],
                               "reasons": best.cohesion["gate"]["reasons"]}


def linked_report(link_rows: List[Dict[str, Any]], furn: List[Dict[str, Any]], counts: Dict[str, int], client: SerpApiClient, start_used: int) -> str:
    L = ["## Linked bundle: merchant links, lights, decor, sofa and shelf", "",
         f"SerpApi: {client.live_calls} live call(s) in this run (month {start_used} -> {client.used_this_month()}/{client.monthly_cap}); everything else came from the cache.",
         f"Final: {counts['merchant']} pieces with a merchant link, {counts['similar']} designed pieces with a shop-similar search link, {counts['owned']} owned.", "",
         "### Merchant links (from SerpApi product-detail data; nothing is built or guessed)", "",
         "| Item | Store | Chosen by | Price | Link host |", "|---|---|---|---|---|"]
    for r in link_rows:
        L.append(f"| {r['id']} | {r['store'] or '-'} | {r['matched'] or 'not resolved'} | {r['price'] or '-'} | {r['host'] or '-'} |")
    L += ["", "Rule: the store of the offer the product was chosen from; else the cheapest reputable seller; a Google Shopping page is never used as the merchant link.", ""]
    L += ["### Lights and decor (same gate as furniture) and the sofa / shelf retry", ""]
    for f in furn:
        L += furn_block(f)
    return "\n".join(L) + "\n"


def linked_main(args, log) -> int:
    src = Path(args.bundle) if args.bundle else None
    if src is None:
        log("--linked needs --bundle (the cohesive bundle to copy from)")
        return 2
    src = src if src.is_absolute() else ROOT / src
    dst = Path(args.out) if args.out else src.with_name(src.name.replace("_cohesive", "") + "_linked")
    dst = dst if dst.is_absolute() else ROOT / dst
    shutil.copytree(src, dst, dirs_exist_ok=True)
    spec = json.loads((dst / "design_spec.json").read_text())
    items: List[Dict[str, Any]] = json.loads((dst / "items.json").read_text())
    room = json.loads((dst / "room.json").read_text())
    by_id = {i["id"]: i for i in items}
    profile = cohesion.build_profile(spec, items, room)
    client = SerpApiClient(max_searches=args.max_searches, monthly_cap=args.month_cap, verbose=True)
    start_used = client.used_this_month()
    log(f"linked: {src.name} -> {dst.name}; month {start_used}/{client.monthly_cap}, up to {args.max_searches} live calls")
    allres = curate.cached_results()

    # ---- A. merchant links of the products already swapped in
    link_rows: List[Dict[str, Any]] = []
    for it in items:
        if it.get("owned") or not it.get("product") or not it["product"].get("product_id"):
            continue
        pick = resolve_product_link(it, allres, client, log)
        link_rows.append({"id": it["id"], "store": (pick or {}).get("store"), "matched": (pick or {}).get("matched"), "price": it["product"].get("price"),
                          "host": (urlparse(pick["url"]).hostname if pick else None)})
        log(f"{it['id']}: " + (f"{pick['store']} ({pick['matched']}) {urlparse(pick['url']).hostname}" if pick else "no merchant link in the SerpApi response"))

    # ---- B. what to scout: lights + decor (designed), plus sofa / shelf once more with a sharper query
    todo: List[Tuple[Dict[str, Any], Slot, List[str], Any]] = []     # (item, slot, queries, DecorSpec | None)
    for key in decor.SCOUT_ORDER:
        for it in items:
            if it.get("owned") or not it.get("designed") or decor.spec_key(it) != key:
                continue
            sl = decor.decor_slot(it)
            todo.append((it, sl, decor.queries_for(it, sl), decor.SPECS[key]))
    from ptah.products.scout import build_queries
    for it in items:
        if it.get("owned") or not it.get("designed") or it.get("category") not in RETRY_QUERIES:
            continue
        sl = slot_from_item(it)
        qs = [q["q"] for q in build_queries(sl, 2)] + [RETRY_QUERIES[it["category"]]]
        todo.append((it, sl, qs, None))
    if args.only_ids:
        want = set(args.only_ids.split(","))
        todo = [t for t in todo if t[0]["id"] in want]
    seen_q: set = set()
    for it, sl, qs, sp in todo:                      # live searches (cache first); retry queries are the last of the list
        for q in (qs if sp is not None else qs[-1:]):
            if q.lower() in seen_q:
                continue
            seen_q.add(q.lower())
            lo, hi = (sp.price if sp is not None else _generic_band(sl))
            try:
                client.search(q, min_price=lo, max_price=hi)
            except BudgetExceeded as e:
                log(f"search skipped (budget): {q} ({e})")
            except SerpApiError as e:
                log(f"search failed: {q}: {e}")
    allres = curate.cached_results()

    furn: List[Dict[str, Any]] = []
    changed_light = False
    for it, sl, qs, sp in todo:
        kw = dict(queries=qs, accept=sp.accept, intent=sp.intent, also_ok=sp.also_ok, tolerate=sp.tolerate) if sp is not None else dict(queries=qs)
        cs = curate.furniture_candidates(sl, profile, allres, **kw)
        if not cs:
            log(f"{it['id']}: no usable candidates")
            continue
        for c in cs:
            c.light = None
            if sp is not None and sp.light:
                merged, reasons, lspec = decor.light_check(c.title, c.result.get("snippet") or "", sp.category, (it.get("w"), it.get("d"), it.get("h")), it.get("light"))
                c.light = (merged, lspec)
                c.cohesion["light_spec"] = ({k: lspec.get(k) for k in ("stated", "kept_designed", "estimated", "color_temp_k", "lumens", "dimmable")} if lspec else None)
                if reasons:
                    c.cohesion["gate"]["accepted"] = False
                    c.cohesion["gate"]["reasons"] = list(c.cohesion["gate"]["reasons"]) + reasons
        cs.sort(key=lambda c: (-int(c.cohesion["gate"]["accepted"]), -curate.fit_rank(c)))
        passing = [c for c in cs if c.cohesion["gate"]["accepted"]]
        done = None
        for cand in passing[:2]:                                            # a swap must come with a merchant link: try the two best
            tok = cand.result.get("immersive_product_page_token")
            detail = _fetch_detail(client, tok, it["id"], log)    # cache first; a live call only within the budget
            if links.pick_store(detail, cand.source, store_quality) is None:
                log(f"{it['id']}: '{clean_title(cand.title, 40)}' passes the gate but SerpApi gave no merchant link")
                cand.cohesion["gate"] = {"accepted": False, "reasons": ["passes the gate but no merchant link in the SerpApi response"]}
                continue
            lt = cand.light if cand.light else (None, None)
            swap_in(it, sl, cand, detail, dst, light=lt[0] if sp is not None and sp.light else None, light_spec=lt[1])
            done = cand
            changed_light = changed_light or (sp is not None and sp.light)
            break
        best = done or cs[0]
        furn.append({"id": it["id"], "category": sl.category, "best": best, "runners": [c for c in cs if c is not best][:3], "accepted": done is not None,
                     "designed_colors": dict(sl.colors)})
        if done is None:
            _note_candidate(it, best)
        log(f"{it['id']}: " + (f"SWAPPED -> {clean_title(best.title, 50)} | {it['source']} | {it['price']}" if done else "kept designed: " + "; ".join(best.cohesion['gate']['reasons'][:2])))

    # ---- C. shop-similar for every designed piece that is still only a concept
    for it in items:
        if it.get("owned"):
            it.pop("shop_similar", None)
            continue
        if it.get("designed"):
            it["shop_similar"] = decor.shop_similar_for(it)
        else:
            it.pop("shop_similar", None)
    counts = {"merchant": sum(1 for i in items if not i.get("owned") and (i.get("product") or {}).get("url")),
              "similar": sum(1 for i in items if i.get("designed") and not i.get("owned") and i.get("shop_similar")),
              "owned": sum(1 for i in items if i.get("owned"))}
    counts["products"] = sum(1 for i in items if i.get("scouted") and not i.get("owned"))
    log(f"result: {counts}")

    # ---- D. write the bundle, reports, lighting, publish
    (dst / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    rep = linked_report(link_rows, furn, counts, client, start_used)
    base = (src / "cohesion_report.md").read_text() if (src / "cohesion_report.md").exists() else "# Cohesion report\n\n"
    (dst / "cohesion_report.md").write_text(base.rstrip() + "\n\n" + rep)
    (dst / "scout_report.md").write_text("# Scout report\n\nThe art, furniture, light and decor decisions are in cohesion_report.md (cohesion-ranked picks, the swap gate, runners-up).\n\n" + rep)
    if changed_light:
        relight(dst, items, log)
    me_dir = None if args.publish_dir == "none" else Path(args.publish_dir)
    if me_dir is not None:
        me_dir.mkdir(parents=True, exist_ok=True)
        (me_dir / "images").mkdir(exist_ok=True)
        for f in (dst / "images").glob("*"):
            if not f.name.endswith(".src.jpg"):
                shutil.copyfile(f, me_dir / "images" / f.name)
        for n in BUNDLE_FILES:
            if (dst / n).exists():
                shutil.copyfile(dst / n, me_dir / n)
        log(f"published to {me_dir}")
    log(f"live searches this run: {client.live_calls}; month {client.used_this_month()}/{client.monthly_cap}; report -> {dst / 'cohesion_report.md'}")
    return 0


# ----------------------------------------------------------------------------------------------------------------------------------
# alternatives mode: targeted (brand-aware) searches for what stays designed, real options for every piece
# ----------------------------------------------------------------------------------------------------------------------------------

TARGETED_IDS = ("sofa-1", "throw-1")           # designed pieces searched harder (home brands + sharper aesthetic query)
COLOUR_VARIANTS = {"sofa": ("navy blue {mat} sofa {leg} legs", "blue {mat} mid-century sofa {leg} legs")}
TARGET_BRANDS = {"sofa": 5, "throw": 3}        # how many Qloo-ranked home brands are searched per piece
CAT_QUERY_RE = {
    "sofa": r"\bsofa\b|\bcouch\b", "armchair": r"chair", "coffee_table": r"coffee table", "side_table": r"side table|end table",
    "rug": r"\brug\b", "floor_lamp": r"floor lamp|floor light|arc lamp", "table_lamp": r"table lamp|desk lamp|accent lamp",
    "bookshelf": r"bookcase|bookshelf|book shelf|etagere", "tv_stand": r"tv stand|media console|console|credenza|sideboard",
    "pendant": r"pendant|lantern", "sconce": r"sconce|wall lamp|wall light", "picture_light": r"picture light|art light|display (spot)?light",
    "led_strip": r"led|strip|bias|backlight", "plant": r"fig|ficus|plant", "guitar_hanger": r"guitar", "cushion": r"pillow|cushion",
    "throw": r"throw|blanket", "vase": r"vase|bottle", "book_stack": r"\bbook", "vinyl_crate": r"crate|record|vinyl", "mirror": r"mirror",
}
CAT_TITLE_RE = {"floor_lamp": r"floor|arc\b|standing|torchi", "table_lamp": r"table|desk|bedside|accent lamp|task", "throw": r"^(?!.*\b(pillow|cushion|cover)\b)",
                "tv_stand": r"stand|console|credenza|sideboard|media"}


def targeted_plan(it: Dict[str, Any], sl: Slot, brief: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Searches for a piece the gate kept designed: one sharper aesthetic query (colour + material + noun + leg wood), then ``<home brand> <that query>``
    for the brands Qloo ranked highest for the user that sell this kind of piece."""
    from ptah.products import alternatives as alt
    colour = decor._colour_word(sl) or links.colour_name(sl.colors.get("primary")).split(" ")[0]
    mat = _mat = (sl.materials or {}).get("primary") or ""
    if sl.category == "sofa":
        leg = (sl.materials or {}).get("secondary") or ""
        sharp = f"{colour} {mat} sofa {leg} legs"
    else:
        striped = "striped " if any("striped" in k for k in sl.style_keywords) else ""
        sharp = f"{colour} {striped}{mat} {'throw blanket' if sl.category == 'throw' else sl.category.replace('_', ' ')}"
    sharp = re.sub(r"\s+", " ", sharp).strip()
    n = TARGET_BRANDS.get(sl.category, 3)
    plan = [{"q": sharp, "brand": None, "affinity": None, "why": "sharper aesthetic query: colour + material + noun" + (" + leg wood" if sl.category == "sofa" else "")}]
    for b in alt.brand_queries(sharp, alt.home_brands(brief, sl.category, n)):
        plan.append(dict(b, why=f"home brand {b['brand']} (Qloo affinity {b['affinity']:.2f})"))
    for v in COLOUR_VARIANTS.get(sl.category, ()):               # the words stores use for the same colour (indigo -> navy, denim)
        q = re.sub(r"\s+", " ", v.format(mat=mat, leg=(sl.materials or {}).get("secondary") or "")).strip()
        plan.append({"q": q, "brand": None, "affinity": None, "why": "same piece in the colour words stores use (indigo is sold as navy / blue)"})
    return plan


def cached_detail(client: SerpApiClient, cand) -> Optional[Dict[str, Any]]:
    tok = cand.result.get("immersive_product_page_token")
    if not tok:
        return None
    return client.cached({"engine": "google_immersive_product", "page_token": tok, "more_stores": "true"})


def item_pool(it: Dict[str, Any], sl: Slot, profile, allres):
    """(candidates best first, DecorSpec | None, queries used) for a furniture / decor item, from every cached search whose query names its kind of piece."""
    key = decor.spec_key(it)
    sp = decor.SPECS.get(key)
    cat = sp.category if sp else sl.category
    qrx = re.compile(CAT_QUERY_RE.get(cat, re.escape(cat.replace("_", " "))), re.I)
    trx = re.compile(CAT_TITLE_RE[cat], re.I) if cat in CAT_TITLE_RE else None
    queries = sorted({q for q, _ in allres if qrx.search(q)})
    res = [(q, r) for q, r in allres if qrx.search(q) and (trx is None or trx.search(r.get("title") or ""))]
    kw: Dict[str, Any] = dict(queries=queries)
    if sp is not None:
        kw.update(accept=sp.accept, intent=sp.intent, also_ok=sp.also_ok, tolerate=sp.tolerate)
    cs = curate.furniture_candidates(sl, profile, res, top_images=32, **kw)
    for c in cs:
        c.light = None
        c.extra_misses = []
        if sp is not None and sp.light:
            merged, reasons, lspec = decor.light_check(c.title, c.result.get("snippet") or "", sp.category, (it.get("w"), it.get("d"), it.get("h")), it.get("light"))
            c.light = (merged, lspec)
            c.cohesion["light_spec"] = ({k: lspec.get(k) for k in ("stated", "kept_designed", "estimated", "color_temp_k", "lumens", "dimmable")} if lspec else None)
            if reasons:
                c.cohesion["gate"] = {"accepted": False, "reasons": list(c.cohesion["gate"]["reasons"]) + reasons}
                c.extra_misses = ["cooler light than the warm glow designed" if "not warm" in r else "colour-changing LEDs; the design is one warm colour" for r in reasons]
    cs.sort(key=lambda c: (-int(c.cohesion["gate"]["accepted"]), -curate.fit_rank(c)))
    return cs, sp, queries


def make_options(it: Dict[str, Any], sl: Slot, cs, dst: Path, client: SerpApiClient, *, live_details: int = 0, log=print) -> List[Dict[str, Any]]:
    """The ``alternatives`` entries of one furniture / decor item (photo saved under images/alt/, merchant link from cached or budgeted product detail)."""
    from ptah.products import alternatives as alt
    prod = it.get("product") or {}
    head = alt.order(cs[:24], sl.colors)[:12]
    for c in head:
        curate.materialize(c)
        if c.ahash is None and c.image is not None:
            c.ahash = curate._ahash(c.image)
    picked = alt.select(head, exclude_ids=[prod.get("product_id")], exclude_titles=[prod.get("title") or ""])
    good = [c for c in picked if alt.meaningful(c, sl.colors)]
    picked = good + [c for c in picked if c not in good][:max(0, 2 - len(good))]      # far-off look-alikes only to fill up to two
    out = []
    for n, c in enumerate(picked):
        detail = cached_detail(client, c)
        if detail is None and live_details > n:
            detail = _fetch_detail(client, c.result.get("immersive_product_page_token"), f"{it['id']} option", log)
        pick = alt.resolve_link(detail, c.source)
        misses = alt.misses_for(c.cohesion, slot_colors=sl.colors, designed_material=(sl.materials or {}).get("primary"), category=sl.category,
                                extra=getattr(c, "extra_misses", []), title=c.title)
        passes = bool(c.cohesion["gate"]["accepted"])
        rel = alt.save_photo(c, dst, f"images/alt/{it['id']}-{alt.alt_key(c)}.jpg")
        out.append(alt.build_entry(c, misses=misses, passes=passes, image=rel, url=(pick or {}).get("url"), store=(pick or {}).get("store"),
                                   price=(pick or {}).get("price"), other_stores=(pick or {}).get("others")))
    return out


def art_options(items: List[Dict[str, Any]], slots: Dict[str, Slot], profile, dst: Path, client: SerpApiClient, log) -> Dict[str, List[Dict[str, Any]]]:
    """Alternatives for the wall art: runners-up of the same ranking that fit the piece's current frame shape (so a swap needs no new layout)."""
    from ptah.products import alternatives as alt
    from ptah.products.scout import classify_art
    art_slots = sorted([s for s in slots.values() if s.is_art], key=lambda s: (s.role != "art_main", s.id))
    if not art_slots:
        return {}
    allres = curate.cached_results()
    by_q: Dict[str, List[Dict[str, Any]]] = {}
    for q, r in allres:
        by_q.setdefault(q, []).append(r)
    art_q = {q for q, rs in by_q.items() if rs and sum(1 for r in rs if classify_art(r)[0]) / len(rs) >= 0.5}
    pool = curate.build_art_pool([(q, r) for q, r in allres if q in art_q], art_slots[0], profile, skipped=[])
    out: Dict[str, List[Dict[str, Any]]] = {}
    by_id = {i["id"]: i for i in items}
    for s in art_slots:
        it = by_id[s.id]
        prod = it.get("product") or {}
        ranked = curate.rank_for_slot(pool, s, profile)
        fit = []
        for c in ranked:
            if c.price is not None and c.price > curate.MAX_ART_PRICE:
                continue
            w, h = curate.outer_dims(s, c.aspect)
            if it.get("w") and it.get("h") and (abs(w / it["w"] - 1) > 0.07 or abs(h / it["h"] - 1) > 0.07):
                continue                                       # a different frame shape would need a new layout: not offered as a swap
            c.alt_dims = (w, h)
            fit.append(c)
        for c in fit[:10]:
            curate.materialize(c)
        picked = alt.select(fit[:10], exclude_ids=[prod.get("product_id")], exclude_titles=[prod.get("title") or ""])
        entries = []
        for c in picked:
            detail = cached_detail(client, c)
            pick = alt.resolve_link(detail, c.source)
            rel = alt.save_photo(c, dst, f"images/alt/{s.id}-{alt.alt_key(c)}.jpg")
            entries.append(alt.build_entry(c, misses=alt.art_misses(c.cohesion), passes=c.cohesion["total"] >= 0.7 and not c.cohesion["caps"], image=rel,
                                           url=(pick or {}).get("url"), store=(pick or {}).get("store"), price=(pick or {}).get("price"),
                                           other_stores=(pick or {}).get("others"), dims=c.alt_dims))
        out[s.id] = entries
    return out


def alternatives_report(searches: List[Dict[str, Any]], outcomes: List[Dict[str, Any]], counts: Dict[str, int], client: SerpApiClient, start_used: int) -> str:
    L = ["## Targeted search and real options (alternatives)", "",
         f"SerpApi: {client.live_calls} live call(s) in this run (month {start_used} -> {client.used_this_month()}/{client.monthly_cap}); the rest came from the cache.",
         "Small textiles (throws, cushions) use a wider colour tolerance than the strict gate: accent colours 16 dE instead of 12, other colours 22 dE instead of 18; "
         "sofas, rugs, chairs and every other check (material, style, size, palette fit, studio photo) are unchanged.",
         "Google Shopping has no brand parameter (its Brand / Store filters are continuation tokens of an earlier response), so each brand search puts the brand name "
         "in the query; the brands are the home brands Qloo ranked for this user (brief.json), best affinity first.", "",
         "### Searches", "", "| Piece | Query | Why | Calls |", "|---|---|---|---|"]
    for s in searches:
        L.append(f"| {s['id']} | `{s['q']}` | {s['why']} | {s['live']} |")
    L += ["", "### Outcome", ""]
    for o in outcomes:
        L.append(f"- **{o['id']}**: {o['summary']}")
        for a in o.get("options", []):
            L.append(f"  - {a['title'][:70]} | {a['store']} | {a['price'] or '?'} | {'passes' if a['passes'] else 'near miss'} | " + ("; ".join(a["misses"]) or "no misses"))
    L += ["", f"Options stored: {counts['items']} items carry `alternatives` ({counts['options']} options in total, {counts['with_link']} with a merchant link, {counts['photos']} with a photo).", ""]
    return "\n".join(L) + "\n"


def alternatives_main(args, log) -> int:
    from ptah.products import alternatives as alt
    src = Path(args.bundle) if args.bundle else None
    if src is None:
        log("--alternatives needs --bundle (the linked bundle to copy from)")
        return 2
    src = src if src.is_absolute() else ROOT / src
    dst = Path(args.out) if args.out else src.with_name(src.name.replace("_linked", "") + "_linked2")
    dst = dst if dst.is_absolute() else ROOT / dst
    shutil.copytree(src, dst, dirs_exist_ok=True)
    spec = json.loads((dst / "design_spec.json").read_text())
    items: List[Dict[str, Any]] = json.loads((dst / "items.json").read_text())
    room = json.loads((dst / "room.json").read_text())
    brief_path = Path(args.brief) if args.brief else ROOT / "data" / "brief.json"
    brief = json.loads(brief_path.read_text()) if brief_path.exists() else None
    by_id = {i["id"]: i for i in items}
    profile = cohesion.build_profile(spec, items, room)
    client = SerpApiClient(max_searches=args.max_searches, monthly_cap=args.month_cap, verbose=True)
    start_used = client.used_this_month()
    targeted = [t for t in (args.only_ids.split(",") if args.only_ids else TARGETED_IDS) if t in by_id]
    log(f"alternatives: {src.name} -> {dst.name}; month {start_used}/{client.monthly_cap}, up to {args.max_searches} live calls; targeted {targeted}")
    slots = {i["id"]: slot_from_item(i) for i in items if not i.get("owned")}

    # ---- A. targeted searches (cache first; each live call is budgeted)
    searches: List[Dict[str, Any]] = []
    for tid in targeted:
        it, sl = by_id[tid], slots[tid]
        lo, hi = (decor.SPECS[decor.spec_key(it)].price if decor.spec_key(it) in decor.SPECS else _generic_band(sl))
        for q in targeted_plan(it, sl, brief):
            before = client.live_calls
            try:
                client.search(q["q"], min_price=lo, max_price=hi)
            except BudgetExceeded as e:
                log(f"search skipped (budget): {q['q']} ({e})")
                searches.append(dict(q, id=tid, live="skipped (budget)"))
                continue
            except SerpApiError as e:
                log(f"search failed: {q['q']}: {e}")
                searches.append(dict(q, id=tid, live="failed"))
                continue
            searches.append(dict(q, id=tid, live=client.live_calls - before))
    allres = curate.cached_results()

    # ---- B. real options for every furniture / decor piece; swap the targeted pieces when a candidate passes the (category) gate
    outcomes: List[Dict[str, Any]] = []
    furn: List[Dict[str, Any]] = []
    for it in items:
        if it.get("owned") or it["id"] not in slots or slots[it["id"]].is_art:
            continue
        sl = slots[it["id"]]
        if sl.category in ("tv", "guitar", "amp", "frame") or (decor.spec_key(it) not in decor.SPECS and sl.category not in curate_slot_categories()):
            continue
        cs, sp, qs = item_pool(it, sl, profile, allres)
        if not cs:
            log(f"{it['id']}: no candidates")
            continue
        swapped = None
        if it["id"] in targeted and it.get("designed"):
            for cand in [c for c in cs if c.cohesion["gate"]["accepted"]][:2]:       # a swap must come with a merchant link
                tok = cand.result.get("immersive_product_page_token")
                detail = cached_detail(client, cand) or _fetch_detail(client, tok, it["id"], log)
                if links.pick_store(detail, cand.source, store_quality) is None:
                    log(f"{it['id']}: '{clean_title(cand.title, 40)}' passes the gate but SerpApi gave no merchant link")
                    cand.cohesion["gate"] = {"accepted": False, "reasons": ["passes the gate but no merchant link in the SerpApi response"]}
                    continue
                lt = cand.light if cand.light else (None, None)
                swap_in(it, sl, cand, detail, dst, light=lt[0] if sp is not None and sp.light else None, light_spec=lt[1])
                swapped = cand
                break
            if swapped is None:
                _note_candidate(it, cs[0])
                it["product_candidate"]["misses"] = alt.misses_for(cs[0].cohesion, slot_colors=sl.colors, designed_material=(sl.materials or {}).get("primary"), category=sl.category,
                                                                    extra=getattr(cs[0], "extra_misses", []))
        elif it.get("designed") and it.get("product_candidate"):
            best = cs[0]
            it["product_candidate"]["misses"] = alt.misses_for(best.cohesion, slot_colors=sl.colors, designed_material=(sl.materials or {}).get("primary"), category=sl.category,
                                                                extra=getattr(best, "extra_misses", []))
        live = args.detail_live if it["id"] in targeted else 0
        opts = make_options(it, sl, cs, dst, client, live_details=live, log=log)
        if opts:
            it["alternatives"] = opts
        if it["id"] in targeted:
            summary = (f"SWAPPED to a real product: {clean_title(swapped.title, 60)} | {it['source']} | {it['price']}" if swapped else
                       "no candidate passes the gate: kept the designed piece; closest real options below")
            outcomes.append({"id": it["id"], "summary": summary, "options": opts})
            furn.append({"id": it["id"], "category": sl.category, "best": swapped or cs[0], "runners": [c for c in cs if c is not (swapped or cs[0])][:3],
                         "accepted": swapped is not None, "designed_colors": dict(sl.colors)})
        log(f"{it['id']}: {len(opts)} option(s)" + (" ; SWAPPED" if swapped else ""))

    # ---- C. wall art options
    art = art_options(items, slots, profile, dst, client, log)
    for sid, entries in art.items():
        if entries:
            by_id[sid]["alternatives"] = entries
        log(f"{sid}: {len(entries)} art option(s)")

    # ---- D. refresh shop-similar links, write bundle + reports + publish
    for it in items:
        if it.get("owned") or not it.get("designed"):
            it.pop("shop_similar", None)
        else:
            it["shop_similar"] = decor.shop_similar_for(it)
    bad = [(i["id"], p) for i in items for a in i.get("alternatives", []) for p in alt.valid_entry(a)]
    if bad:
        log(f"WARNING: invalid alternatives entries: {bad[:5]}")
    ents = [a for i in items for a in i.get("alternatives", [])]
    counts = {"items": sum(1 for i in items if i.get("alternatives")), "options": len(ents), "with_link": sum(1 for a in ents if a.get("url")), "photos": sum(1 for a in ents if a.get("image"))}
    (dst / "items.json").write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n")
    rep = alternatives_report(searches, outcomes, counts, client, start_used)
    swapped_ids = [o["id"] for o in outcomes if o["summary"].startswith("SWAPPED")]
    if furn:
        rep += "\n### Targeted pieces: gate details\n\n" + "\n".join(L for f in furn for L in furn_block(f))
    for name in ("cohesion_report.md", "scout_report.md"):
        base = (src / name).read_text() if (src / name).exists() else f"# {name}\n\n"
        (dst / name).write_text(base.rstrip() + "\n\n" + rep)
    me_dir = None if args.publish_dir == "none" else Path(args.publish_dir)
    if me_dir is not None:
        me_dir.mkdir(parents=True, exist_ok=True)
        (me_dir / "images").mkdir(exist_ok=True)
        for f in (dst / "images").glob("*"):
            if f.is_file() and not f.name.endswith(".src.jpg"):
                shutil.copyfile(f, me_dir / "images" / f.name)
        if (dst / "images" / "alt").is_dir():
            shutil.copytree(dst / "images" / "alt", me_dir / "images" / "alt", dirs_exist_ok=True)
        for n in BUNDLE_FILES:
            if (dst / n).exists():
                shutil.copyfile(dst / n, me_dir / n)
        log(f"published to {me_dir}")
    log(f"result: swapped {swapped_ids or 'nothing'}; {counts}; live searches this run: {client.live_calls}; month {client.used_this_month()}/{client.monthly_cap}")
    return 0


def _generic_band(slot: Slot):
    from ptah.products.scout import generic_price_band
    return generic_price_band(slot)



def curate_slot_categories() -> set:
    return {"sofa", "armchair", "coffee_table", "side_table", "rug", "floor_lamp", "table_lamp", "bookshelf", "tv_stand", "pendant", "sconce", "mirror", "cushion", "throw", "vase"}


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
    ap.add_argument("--cohesive", action="store_true", help="cohesion-first curation of --bundle into a NEW bundle (--out): art chosen as a set, furniture swaps behind a strict gate")
    ap.add_argument("--out", help="with --cohesive / --linked: destination bundle dir (default: <bundle without _artonly>_cohesive / <bundle without _cohesive>_linked)")
    ap.add_argument("--linked", action="store_true", help="copy a cohesive --bundle to a NEW bundle (--out): merchant links for the products, lights + decor through the same gate, "
                                                          "sofa / shelf retried once, shop-similar links for the remaining designed pieces, lighting re-computed, published")
    ap.add_argument("--month-cap", type=int, default=250, help="hard cap on this month's total SerpApi calls (counter in cache/serpapi/_budget.json)")
    ap.add_argument("--only-ids", help="with --linked: only scout these item ids; with --alternatives: the designed pieces to search harder (default sofa-1,throw-1)")
    ap.add_argument("--alternatives", action="store_true", help="copy a linked --bundle to a NEW bundle (--out, default <bundle>_linked2): brand-aware targeted searches for sofa-1 / throw-1, "
                                                                "swap them if a candidate passes the gate, and store real `alternatives` (near-misses with plain-language misses) on every item")
    ap.add_argument("--brief", help="brief.json with the Qloo brand ranking (default data/brief.json)")
    ap.add_argument("--detail-live", type=int, default=2, help="with --alternatives: live product-detail calls per targeted piece to resolve store links of its options")
    args = ap.parse_args(argv)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    if args.alternatives:
        return alternatives_main(args, log)
    if args.linked:
        return linked_main(args, log)
    if args.cohesive:
        return cohesive_main(args, log)

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

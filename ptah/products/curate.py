"""Cohesive curation: pick real products that FIT the designed aesthetic and read as ONE room.

    art        every candidate (cached shopping results first, new aesthetic-derived queries only when nothing scores well) is cut out,
               colour-clustered and scored with ptah.products.cohesion; the pair / set of art pieces is chosen jointly (palette overlap,
               compatible mediums and eras, matching chroma), not one by one
    furniture  the best candidate per slot is scored against the room palette, the designed colour, the spec materials and the room's
               single wood family / metals; the product REPLACES the designed piece only when cohesion.swap_gate passes, otherwise the
               designed piece stays and the best candidate is recorded as "not cohesive enough" with the reasons

No LLM; SerpApi only through the budgeted client; thumbnails are cached on disk under cache/thumbs/ (private working data).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..env import ROOT
from . import artimg, cohesion
from .scout import (Candidate, Slot, art_price_band, classify_art, classify_generic, fetch_image, fit_art_dims, parse_price, pre_score, slot_from_item,
                    store_quality, subject_tokens)
from .serpapi import BudgetExceeded, CACHE_DIR, SerpApiClient, SerpApiError

THUMB_DIR = ROOT / "cache" / "thumbs"
ANALYSIS_DIR = ROOT / "cache" / "analysis"
ANALYSIS_VERSION = 1      # bump when image analysis (cut-out, colour clustering, background share) changes: old entries are then ignored

_ANALYSIS: Dict[str, Dict[str, Any]] = {}      # kind -> {thumbnail url: analysis}, loaded once per process
_ANALYSIS_DIRTY: set = set()


def _analysis_path(kind: str, d: Optional[Path] = None) -> Path:
    return (Path(d) if d else ANALYSIS_DIR) / f"{kind}_v{ANALYSIS_VERSION}.json"


def analysis_get(kind: str, url: Optional[str], d: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Persistent per-thumbnail analysis (colour clusters, cut-out box, ...): one JSON file per kind and analysis version under cache/analysis/."""
    if not url:
        return None
    key = f"{kind}@{d}"
    if key not in _ANALYSIS:
        try:
            _ANALYSIS[key] = json.loads(_analysis_path(kind, d).read_text())
        except (OSError, ValueError):
            _ANALYSIS[key] = {}
    return _ANALYSIS[key].get(url)


def analysis_put(kind: str, url: Optional[str], value: Dict[str, Any], d: Optional[Path] = None) -> None:
    if not url:
        return
    analysis_get(kind, url, d)
    _ANALYSIS[f"{kind}@{d}"][url] = value
    _ANALYSIS_DIRTY.add((kind, str(d) if d else ""))


def flush_analysis() -> None:
    """Write the analyses added in this process (atomic replace)."""
    import os
    for kind, dd in list(_ANALYSIS_DIRTY):
        d = Path(dd) if dd else None
        path = _analysis_path(kind, d)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_ANALYSIS[f"{kind}@{d}"], separators=(",", ":")))
        os.replace(tmp, path)
    _ANALYSIS_DIRTY.clear()


def _clusters_out(cl: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"rgb": [round(float(v), 2) for v in c["rgb"]], "hex": c["hex"], "lab": [round(float(v), 3) for v in c["lab"]], "weight": round(float(c["weight"]), 5)} for c in cl]


def _clusters_in(cl: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"rgb": list(c["rgb"]), "hex": c["hex"], "lab": tuple(c["lab"]), "weight": c["weight"]} for c in cl]


def materialize(c: Candidate, thumb_dir=None, fetch=fetch_image) -> Candidate:
    """Load the picture of a candidate whose analysis came from the cache (images are only needed for the pieces that are actually used)."""
    if c.image is not None or not c.thumb_url:
        return c
    im = load_thumb(c.thumb_url, thumb_dir, fetch)
    if im is None:
        return c
    c.source_image = im
    c.image = im.crop(tuple(c.crop_box)) if c.crop_box else im
    return c


# ----------------------------------------------------------------------------------------------------------------- thumbnails + cache

def load_thumb(url: Optional[str], thumb_dir: Optional[Path] = None, fetch=fetch_image):
    """Thumbnail from the on-disk cache, else downloaded once (free) and cached."""
    if not url:
        return None
    from PIL import Image
    d = Path(thumb_dir) if thumb_dir else THUMB_DIR
    p = d / (hashlib.sha1(url.encode()).hexdigest()[:24] + ".jpg")
    if p.exists():
        try:
            im = Image.open(p)
            im.load()
            return im.convert("RGB")
        except Exception:
            pass
    im = fetch(url)
    if im is not None:
        d.mkdir(parents=True, exist_ok=True)
        im.save(p, quality=92)
    return im


def cached_results(cache_dir: Optional[Path] = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Every cached google_shopping result as (query, result), offline."""
    d = Path(cache_dir) if cache_dir else CACHE_DIR
    out: List[Tuple[str, Dict[str, Any]]] = []
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            j = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        prm = j.get("params") or {}
        if prm.get("engine") != "google_shopping":
            continue
        for r in SerpApiClient.results(j.get("response") or {}):
            out.append((prm.get("q") or "", r))
    return out


# ----------------------------------------------------------------------------------------------------------------- candidate evaluation

def _art_candidate(res: Dict[str, Any], q: str, slot: Slot, profile: cohesion.RoomProfile, thumb_dir=None, fetch=fetch_image) -> Optional[Candidate]:
    ok, digital, _why = classify_art(res)
    if not ok or digital:
        return None
    c = pre_score(slot, res, 8, 400)
    c.query = q
    url = res.get("thumbnail") or res.get("serpapi_thumbnail")
    hit = analysis_get("art", url, thumb_dir and Path(thumb_dir) / "_analysis")
    if hit is not None:                       # analysed before: no image load, no clustering
        if hit.get("failed"):
            return None
        c.evaluated, c.thumb_url, c.crop_box = True, url, tuple(hit["box"])
        c.crop_conf, c.aspect, c.min_side, c.ahash = hit["conf"], hit["aspect"], hit["min_side"], hit["ahash"]
        c.clusters, c.colors = _clusters_in(hit["clusters"]), hit["colors"]
        return c
    im = load_thumb(url, thumb_dir, fetch)
    c.evaluated = True
    if im is None:
        return None
    c.source_image = im
    box, conf = artimg.find_artwork_rect(im)
    art = im.crop(box)
    c.image, c.crop_conf, c.aspect = art, conf, art.width / float(art.height)
    c.clusters = cohesion.image_palette(art, remove_background=False)
    from .enrich import extract_colors
    c.colors = extract_colors(art)
    c.thumb_url, c.crop_box, c.min_side, c.ahash = url, tuple(box), min(art.size), _ahash(art)
    analysis_put("art", url, {"box": [int(v) for v in box], "conf": round(float(conf), 4), "aspect": round(float(c.aspect), 5), "min_side": int(c.min_side),
                              "ahash": int(c.ahash), "clusters": _clusters_out(c.clusters), "colors": c.colors}, thumb_dir and Path(thumb_dir) / "_analysis")
    return c


def score_art_for(c: Candidate, slot: Slot, profile: cohesion.RoomProfile) -> Dict[str, Any]:
    return cohesion.score_art(c.title, c.clusters or [], profile, slot_w=slot.w_range, slot_h=slot.h_range, aspect=c.aspect,
                              store_q=store_quality(c.source), crop_conf=c.crop_conf, min_side=(c.min_side if c.min_side is not None else min(c.image.size)), inspired_by=slot.inspired_by,
                              keywords=slot.style_keywords)


MAX_ART_PRICE = 400.0


def cheap_reject(c_title: str, price: Optional[float], profile: cohesion.RoomProfile) -> Optional[str]:
    """Text-only reasons a listing cannot win, so its image is never analysed: off-mood vocabulary, avoid-list words, absurd price."""
    t = (c_title or "").lower()
    bad = [w for w in cohesion.OFF_MOOD if cohesion._has(t, w)]
    if bad:
        return "off-mood wording: " + ", ".join(bad[:3])
    for a in profile.avoid:
        for w in cohesion._AVOID_ALIASES.get(a, [a]):
            if cohesion._has(t, w):
                return f"avoid-list wording: {w}"
    if price is not None and price > MAX_ART_PRICE:
        return f"price ${price:,.0f} above the art budget"
    return None


def build_art_pool(results: Iterable[Tuple[str, Dict[str, Any]]], slot: Slot, profile: cohesion.RoomProfile, thumb_dir=None, fetch=fetch_image,
                   skipped: Optional[List[Tuple[str, str]]] = None) -> List[Candidate]:
    """Evaluate (crop, colours) every acceptable physical print once; duplicates (same product id / title) dropped.  Listings that text alone rules
    out are skipped before any image work (collected in ``skipped``); analyses are cached on disk (cache/analysis), so a repeat run is near-instant."""
    pool: Dict[str, Candidate] = {}
    seen: set = set()
    for q, r in results:
        key = str(r.get("product_id") or r.get("title"))
        if key in pool or key in seen:
            continue
        seen.add(key)
        why = cheap_reject(r.get("title") or "", parse_price(r), profile)
        if why:
            if skipped is not None:
                skipped.append((r.get("title") or "", why))
            continue
        c = _art_candidate(r, q, slot, profile, thumb_dir, fetch)
        if c is not None and c.crop_conf >= 0.3:
            pool[key] = c
    flush_analysis()
    return list(pool.values())


def rank_for_slot(pool: Sequence[Candidate], slot: Slot, profile: cohesion.RoomProfile) -> List[Candidate]:
    """Score every pool member for this slot (aspect band, size) and return them best first; breakdown in ``cohesion``."""
    out = []
    for c in pool:
        bd = score_art_for(c, slot, profile)
        scored = Candidate(result=c.result, query=c.query, price=c.price, pre=c.pre, digital=c.digital, notes=list(c.notes), image=c.image,
                           source_image=c.source_image, crop_conf=c.crop_conf, colors=c.colors, aspect=c.aspect, evaluated=True,
                           cohesion=bd, clusters=c.clusters, ahash=c.ahash, min_side=c.min_side, crop_box=c.crop_box, thumb_url=c.thumb_url)
        scored.aspect_pen = 0.0
        scored.score = bd["total"]
        out.append(scored)
    return dedupe_by_image(sorted(out, key=lambda c: (-c.score, c.price or 0)))


def _ahash(im) -> int:
    g = im.convert("L").resize((8, 8))
    px = list(g.tobytes())
    m = sum(px) / 64.0
    return sum(1 << i for i, v in enumerate(px) if v >= m)


def dedupe_by_image(cands: Sequence[Candidate], max_bits: int = 5) -> List[Candidate]:
    """The same picture is sold by many stores / in many formats: keep the best-ranked listing of each look-alike group."""
    kept: List[Tuple[int, Candidate]] = []
    for c in cands:      # ``cands`` is best first
        h = c.ahash if c.ahash is not None else _ahash(c.image)
        if any(bin(h ^ k).count("1") <= max_bits for k, _ in kept):
            continue
        kept.append((h, c))
    return [c for _, c in kept]


def outer_dims(slot: Slot, aspect: float) -> Tuple[float, float]:
    return fit_art_dims(slot, aspect or 0.75)


def pick_art_set(main: Sequence[Candidate], side: Sequence[Candidate], main_slot: Slot, side_slot: Slot, profile: cohesion.RoomProfile, top: int = 14,
                 w_individual: float = 0.55, min_crop: float = 0.85, max_price: float = 160.0) -> List[Dict[str, Any]]:
    """Best (main, secondary) pairs by 0.55 * mean individual cohesion + 0.45 * set coherence.  A product may serve one slot only."""
    pairs = []
    usable = lambda c: c.crop_conf >= min_crop and (c.price is None or c.price <= max_price)  # noqa: E731
    main = [c for c in main if usable(c)]
    side = [c for c in side if usable(c)]
    for a in list(main)[:top]:
        for b in list(side)[:top]:
            if a.result.get("product_id") and a.result.get("product_id") == b.result.get("product_id") or a.title == b.title:
                continue
            wa, ha = outer_dims(main_slot, a.aspect)
            wb, hb = outer_dims(side_slot, b.aspect)
            st = cohesion.art_set_score({"title": a.title, "clusters": a.clusters, "w": wa, "h": ha}, {"title": b.title, "clusters": b.clusters, "w": wb, "h": hb}, profile)
            ind = 0.5 * (a.score + b.score)
            pairs.append({"main": a, "side": b, "set": st, "individual": round(ind, 3), "total": round(w_individual * ind + (1 - w_individual) * st["score"], 3)})
    return sorted(pairs, key=lambda p: -p["total"])


# ----------------------------------------------------------------------------------------------------------------- aesthetic queries

def _colour_word(hexv: str) -> str:
    lab = cohesion.hex_to_lab(hexv)
    if lab is None:
        return ""
    C, H = cohesion.chroma_hue(lab)
    if C < 10:
        return "cream" if lab[0] > 80 else "charcoal"
    names = [(25, "red"), (50, "terracotta"), (80, "ochre"), (110, "olive"), (170, "sage green"), (200, "teal"), (280, "indigo blue"), (335, "plum"), (361, "red")]
    for lim, n in names:
        if H < lim:
            return n
    return ""


def aesthetic_queries(profile: cohesion.RoomProfile, slot: Slot, max_queries: int = 4) -> List[Dict[str, Any]]:
    """Queries derived from the aesthetic itself (palette words + mood + medium + subject from the why chain), not from a film title."""
    wall = _colour_word(profile.wall or "")
    accs = sorted({a for a in (_colour_word(x) for x in profile.accents) if a and a not in ("cream",)}, key=lambda a: (a != "indigo blue", a))
    sw = set(profile.style_words)
    mediums: List[str] = []
    if sw & {"japanese", "asian", "zen", "woodblock"}:
        mediums += ["Japanese woodblock print", "Japanese ink wash landscape"]
    if sw & {"botanical", "forest", "tropical", "moss", "nature"}:
        mediums += ["botanical forest print"]
    if sw & {"vintage", "retro", "70s", "nostalgic"}:
        mediums += ["vintage 70s print"]
    noun = "poster" if slot.template == "poster" else "art print"
    portrait = slot.w_range[1] < slot.h_range[0] + 10
    lo, hi = art_price_band(slot)
    qs: List[Dict[str, Any]] = []
    if portrait:
        subj = [w for w in profile.subject_words if w in ("fleetwood mac", "dire straits")]
        base = ["vintage 70s concert poster muted tones", f"retro 70s abstract {noun} ochre rust cream", f"{wall} botanical {noun} muted", f"Japanese woodblock {noun} vertical muted"]
    else:
        base = [f"{wall} Japanese woodblock print", f"{wall} forest landscape {noun} muted", f"{(accs[0] if accs else 'indigo')} Japanese ink wash landscape {noun}",
                f"Studio Ghibli style forest landscape {noun} {wall}"]
    for b in base:
        qs.append({"q": re.sub(r"\s+", " ", b).strip(), "min_price": lo, "max_price": hi, "why": "aesthetic-derived: palette words + mood + medium"})
    return qs[:max_queries]


# ----------------------------------------------------------------------------------------------------------------- furniture

def furniture_candidates(slot: Slot, profile: cohesion.RoomProfile, results: Iterable[Tuple[str, Dict[str, Any]]], top_images: int = 24,
                         thumb_dir=None, fetch=fetch_image, queries: Optional[Sequence[str]] = None, accept=None,
                         intent: Sequence[str] = (), also_ok: Sequence[str] = (), tolerate: Sequence[str] = ()) -> List[Candidate]:
    """Score the cached results of a furniture / decor slot for cohesion.  -> candidates best first.

    ``queries`` the exact queries whose results count (default: the slot's own build_queries); ``accept`` a title filter replacing
    classify_generic (lights and decor have their own); ``intent`` / ``also_ok`` / ``tolerate`` are passed to the material score."""
    from .scout import build_queries
    wanted = {q.lower() for q in queries} if queries is not None else {q["q"].lower() for q in build_queries(slot, 2)}
    pool: Dict[str, Candidate] = {}
    for q, r in results:
        if q.lower() not in wanted:
            continue
        if accept is not None:
            ok = bool(accept(r.get("title") or "", r))
        else:
            ok, _dig, _why = classify_generic(slot, r)
        if not ok:
            continue
        key = str(r.get("product_id") or r.get("title"))
        if key in pool:
            continue
        c = pre_score(slot, r, None, None)
        c.query = q
        pool[key] = c
    cands = sorted(pool.values(), key=lambda c: -c.pre)[:top_images]
    out = []
    adir = Path(thumb_dir) / "_analysis" if thumb_dir else None
    for c in cands:
        url = c.result.get("thumbnail") or c.result.get("serpapi_thumbnail")
        hit = analysis_get("furn", url, adir)
        if hit is not None:
            if hit.get("failed"):
                continue
            c.thumb_url, c.evaluated, c.crop_conf = url, True, 1.0
            c.clusters, c.colors, bg = _clusters_in(hit["clusters"]), hit["colors"], hit["bg"]
        else:
            im = load_thumb(url, thumb_dir, fetch)
            if im is None:
                continue
            c.image = c.source_image = im
            c.evaluated, c.crop_conf = True, 1.0
            c.clusters = cohesion.image_palette(im)
            from .enrich import extract_colors
            c.colors = extract_colors(im)
            bg = cohesion.background_fraction(im)
            c.thumb_url = url
            analysis_put("furn", url, {"clusters": _clusters_out(c.clusters), "colors": c.colors, "bg": round(float(bg), 5)}, adir)
        c.cohesion = cohesion.score_furniture(c.title, c.clusters, profile, slot_colors=slot.colors, slot_materials=slot.materials, slot_w=slot.w_range,
                                              slot_h=slot.h_range, store_q=store_quality(c.source), keywords=slot.style_keywords,
                                              bg_fraction=bg, description=(c.result.get("snippet") or "") if accept is not None else "",
                                              intent=intent, also_ok=also_ok, tolerate=tolerate)
        ok, why = cohesion.swap_gate(c.cohesion, category=slot.category)
        c.cohesion["gate"] = {"accepted": ok, "reasons": why}
        c.score = c.cohesion["total"]
        out.append(c)
    flush_analysis()
    return sorted(out, key=lambda c: (-int(c.cohesion["gate"]["accepted"]), -fit_rank(c)))


def fit_rank(c: Candidate) -> float:
    """Order among furniture candidates: overall cohesion, then how closely the product IS the designed piece (colour match / cover)."""
    pal = c.cohesion["palette"]
    return c.score + 0.12 * pal.get("slot_match", 0.0) + 0.12 * pal.get("slot_cover", 0.0)

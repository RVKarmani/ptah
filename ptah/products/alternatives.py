"""Real options for every piece: the closest real products, each with what it misses in plain words.

A designed piece the strict cohesion gate could not match, and a product the user may not love, both get ``alternatives`` in items.json:
the top three or four real candidates (passing the gate or near-misses) with a local photo, the store, the price, the merchant link
(only when SerpApi gave one; otherwise the Google Shopping page SerpApi returned, never a built URL), their colours, their score, a list
of ``misses`` in everyday language ("cotton, not linen", "slightly bluer than designed") and the cohesion breakdown behind them.
The viewer lists them in the item card and swaps the piece in the room when the user picks one.

This module is offline and deterministic (no network, no LLM); the caller supplies scored candidates.  Also here: the brand-aware
search queries for the pieces that stay designed (the home brands Qloo ranked for the user, from brief.json).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import cohesion as co
from .links import is_merchant_url, pick_store
from .scout import Candidate, clean_title, store_quality

MAX_ALTS = 4
MAX_MISSES = 3
MAX_PER_STORE = 2

# ----------------------------------------------------------------------------------------------------------------- brands (Qloo)

# Home brands that sell a kind of piece at a sensible price.  Only brands in this table are searched; the table says what a brand sells,
# Qloo (brief.json "brands", ranked by affinity) says which ones the user likes, and the order of the searches follows Qloo.
BRAND_SELLS: Dict[str, Tuple[str, ...]] = {
    "sofa": ("Anthropologie", "Joybird", "Burrow", "Article Furniture", "West Elm"),
    "throw": ("Urban Outfitters", "Anthropologie", "Pottery Barn", "West Elm", "Target", "Crate & Barrel", "HomeGoods"),
    "cushion": ("Urban Outfitters", "Anthropologie", "Pottery Barn", "West Elm", "Target"),
    "armchair": ("Anthropologie", "Joybird", "Burrow", "Article Furniture", "West Elm", "Crate & Barrel"),
    "bookshelf": ("Crate & Barrel", "Article Furniture", "West Elm", "IKEA", "Pottery Barn", "CB2"),
}
_BRAND_SHORT = {"Article Furniture": "Article", "Crate & Barrel": "Crate and Barrel"}


def ranked_brands(brief: Optional[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """[(brand name, Qloo affinity)] best first, from brief.json's ``brands`` list."""
    out = []
    for b in (brief or {}).get("brands") or []:
        n = str(b.get("name") or "").strip()
        if n:
            out.append((n, float(b.get("affinity") or 0.0)))
    return sorted(out, key=lambda t: -t[1])


def _norm_brand(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", n.lower().replace("&", "and").replace(" a/s", ""))


def home_brands(brief: Optional[Dict[str, Any]], category: str, limit: int = 4) -> List[Tuple[str, float]]:
    """The user's Qloo-ranked home brands that sell this category, best affinity first (a brand not in Qloo's list is never searched)."""
    sells = {_norm_brand(n) for n in BRAND_SELLS.get(category, ())}
    out = [(n, a) for n, a in ranked_brands(brief) if _norm_brand(n) in sells]
    return out[:limit]


def brand_queries(descriptor: str, brands: Sequence[Tuple[str, float]]) -> List[Dict[str, Any]]:
    """[{q, brand, affinity}]: ``<brand> <descriptor>`` per brand (Google Shopping has no brand parameter: its Brand / Store filters only
    exist as continuation tokens of a previous response, so the brand name in the query is what steers the search)."""
    return [{"q": f"{_BRAND_SHORT.get(n, n)} {descriptor}".strip(), "brand": n, "affinity": a} for n, a in brands]


# ----------------------------------------------------------------------------------------------------------------- plain-language misses

_DEGREE = ((14.0, "slightly"), (24.0, "noticeably"), (999.0, "much"))


def _degree(dE: float) -> str:
    return next(w for lim, w in _DEGREE if dE <= lim)


def colour_direction(product_hex: Optional[str], designed_hex: Optional[str], dE: Optional[float] = None) -> Optional[str]:
    """How the product's dominant colour differs from the designed one, in a shopper's words ("slightly bluer and lighter than designed").
    ``dE`` is the gate's own colour distance when known (it also compares against the designed secondary colour); the degree word follows it.
    None when the two are close (under 8 dE) or either colour is unknown."""
    a, b = co.hex_to_lab(product_hex), co.hex_to_lab(designed_hex)
    if a is None or b is None:
        return None
    d = co.ciede2000(a, b)
    dE = d if dE is None else min(d, dE)
    if dE < 8.0:
        return None
    dL, da, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    dC = co.chroma_hue(a)[0] - co.chroma_hue(b)[0]
    cands: List[Tuple[float, str]] = []
    for val, thr, pos, neg in ((dL, 8.0, "lighter", "darker"), (db, 7.0, "yellower", "bluer"), (da, 7.0, "redder", "greener"), (dC, 10.0, "more saturated", "greyer")):
        if abs(val) >= thr:
            cands.append((abs(val) / thr, pos if val > 0 else neg))
    cands.sort(reverse=True)
    words = [w for _, w in cands[:2]]
    if not words:
        return f"{_degree(dE)} different shade than designed"
    return f"{_degree(dE)} {' and '.join(words)} than designed"


_OTHER_COLOURS = ("gray", "grey", "white", "cream", "ivory", "beige", "black", "pink", "rose", "blush", "green", "purple", "lavender", "red", "orange", "yellow", "blue", "navy")
_FAMILY = (  # (hue from, hue to, words that agree with a designed colour of that hue family)
    (200, 280, {"blue", "navy", "indigo", "denim", "cobalt", "teal"}), (30, 100, {"yellow", "ochre", "mustard", "gold", "camel", "tan", "amber", "caramel", "brown", "rust", "orange", "copper", "honey"}),
    (100, 200, {"green", "olive", "sage", "teal", "moss", "forest"}), (345, 361, {"red", "rust", "terracotta", "burgundy", "brick", "orange", "coral"}),
    (0, 30, {"red", "rust", "terracotta", "burgundy", "brick", "orange", "coral", "brown", "copper"}), (280, 345, {"purple", "plum", "mauve", "lavender", "burgundy", "pink"}))


def title_colour_conflict(title: str, designed_hex: Optional[str]) -> Optional[str]:
    """A colour word in the listing title that contradicts a chromatic designed colour ("Frost Gray" for an indigo sofa), else None."""
    lab = co.hex_to_lab(designed_hex)
    if lab is None:
        return None
    C, H = co.chroma_hue(lab)
    if C < 20:
        return None
    ok = next((w for lo, hi, w in _FAMILY if lo <= H < hi), set())
    t = (title or "").lower()
    named = [w for w in _OTHER_COLOURS if re.search(r"(?<![a-z])" + w + r"(?![a-z])", t)]
    if not named or any(re.search(r"(?<![a-z])" + w + r"(?![a-z])", t) for w in ok):
        return None
    return named[0]


def _dominant_hex(bd: Dict[str, Any]) -> Optional[str]:
    rows = (bd.get("palette") or {}).get("clusters") or []
    return rows[0]["hex"] if rows else None


def misses_for(bd: Dict[str, Any], *, slot_colors: Optional[Dict[str, Optional[str]]] = None, designed_material: Optional[str] = None,
               category: Optional[str] = None, extra: Sequence[str] = (), limit: int = MAX_MISSES, title: str = "") -> List[str]:
    """What a furniture / decor candidate misses against the designed piece, in plain words (no scores, no jargon), most important first.
    Empty when it matches on everything the gate checks."""
    gate = co.gate_for(category)
    out: List[str] = []
    p, pal, mat = bd["parts"], bd["palette"], bd["material"]
    want, got = designed_material, mat.get("primary")
    # material first: it is what a shopper checks on the listing
    if mat.get("hard_reject"):
        out.append(f"made of {got or 'a material'} that the design avoids")
    elif got and want and got != want and not (co.wood_family(got) and co.wood_family(got) == co.wood_family(want)):
        if p["material"] < gate["material"] or (got in co._SOFT and want in co._SOFT):
            out.append(f"{got.replace('_', ' ')}, not {want.replace('_', ' ')}")
    elif p["material"] < gate["material"]:
        flags = " ".join(mat.get("flags") or [])
        sec = mat.get("secondary")
        if got and want and got == want and sec and f"off-palette material: {sec}" in flags:
            out.append(f"{got.replace('_', ' ')} mixed with {sec.replace('_', ' ')}, not pure {want.replace('_', ' ')}")
        elif "no material named" in flags:
            out.append("the listing doesn't say what it is made of")
        elif "wood family" in flags:
            out.append("its wood is a different family from the rest of the room")
        elif "metal" in flags:
            out.append("its metal isn't used anywhere else in the room")
        else:
            out.append("the material is off from the design")
    # colour
    note = pal.get("note") or ""
    if "cool grey" in note:
        out.append("a cool grey, which this room avoids")
    elif "clash" in note:
        out.append("a colour that fights with the room")
    accent = pal.get("slot_chroma", 0) >= 35.0
    limit_dE = gate["accent_dE"] if accent else gate["slot_dE"]
    direction = colour_direction(_dominant_hex(bd), (slot_colors or {}).get("primary"), pal.get("slot_dE"))
    if direction and pal.get("slot_dE", 0) > limit_dE * 0.6:
        out.append(direction)
    elif pal.get("slot_dE", 0) > limit_dE:
        out.append("a different shade from the designed colour")
    if pal.get("slot_cover", 1.0) < gate["slot_cover"]:
        out.append("a good part of it is in colours the design doesn't use")
    if pal["score"] < gate["palette"] and not any("grey" in m or "fights" in m or "than designed" in m or "shade" in m for m in out):
        out.append("its colours sit outside the room's palette")
    if bd["style"].get("avoid_hits"):
        out.append("described as " + ", ".join(bd["style"]["avoid_hits"][:2]) + ", which the design avoids")
    elif p["style"] < gate["style"]:
        out.append("its description doesn't echo the style of the room")
    if p["scale"] < gate["scale"]:
        out.append("its listed size doesn't suit the space")
    word = title_colour_conflict(title, (slot_colors or {}).get("primary"))
    if word:
        out.append(f"the listing calls it {word}")
    if bd.get("studio_photo") is False:
        out.append("shown in a room set, so its true colour is a guess")
    out += [e for e in extra if e]
    if not out and bd["total"] < gate["total"]:
        out.append("a weaker overall match")
    return list(dict.fromkeys(out))[:limit]


_LIGHT_WORDS = (("is not warm", "cooler light than the warm glow designed"), ("colour-changing", "colour-changing LEDs; the design is one warm colour"))


def light_misses(reasons: Iterable[str]) -> List[str]:
    out: List[str] = []
    for r in reasons:
        for key, phrase in _LIGHT_WORDS:
            if key in r and phrase not in out:
                out.append(phrase)
    return out


def art_misses(bd: Dict[str, Any], limit: int = MAX_MISSES) -> List[str]:
    p = bd["parts"]
    out: List[str] = []
    note = (bd.get("palette") or {}).get("note") or ""
    if "cool grey" in note:
        out.append("mostly cool grey, which this room avoids")
    elif p["palette"] < 0.6:
        out.append("its colours drift from the room's palette")
    if (bd.get("style") or {}).get("avoid_hits"):
        out.append("described as " + ", ".join(bd["style"]["avoid_hits"][:2]) + ", which the design avoids")
    elif p["style"] < 0.55:
        out.append("less in the mood of the room")
    if p["scale"] < 0.5:
        out.append("a different shape from the wall space")
    if p["quality"] < 0.45:
        out.append("a plainer store or a rougher image")
    for c in bd.get("caps") or []:
        if "not cleanly cut out" in c:
            out.append("shown in a mock-up room, so the print itself is hard to judge")
    if not out and bd["total"] < 0.7:
        out.append("a weaker overall match")
    return list(dict.fromkeys(out))[:limit]


# ----------------------------------------------------------------------------------------------------------------- entries

def alt_key(c: Candidate) -> str:
    """Stable id of an option within its item: product id (else title) hashed."""
    raw = str(c.result.get("product_id") or c.title or "")
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()[:60]


def _compact_cohesion(c: Candidate) -> Dict[str, Any]:
    bd = c.cohesion or {}
    pal, mat = bd.get("palette") or {}, bd.get("material") or {}
    out: Dict[str, Any] = {"total": bd.get("total"), "parts": dict(bd.get("parts") or {}),
                           "palette": {"score": pal.get("score"), "slot_dE": pal.get("slot_dE"), "slot_cover": pal.get("slot_cover"), "dominant": _dominant_hex(bd)},
                           "material": {"primary": mat.get("primary"), "secondary": mat.get("secondary"), "flags": list(mat.get("flags") or [])}}
    if bd.get("gate"):
        out["gate"] = {"accepted": bool(bd["gate"].get("accepted")), "reasons": list(bd["gate"].get("reasons") or [])}
    return out


def build_entry(c: Candidate, *, misses: Sequence[str], passes: bool, image: Optional[str], url: Optional[str] = None, store: Optional[str] = None,
                price: Optional[str] = None, other_stores: Optional[List[Dict[str, Any]]] = None, dims: Optional[Tuple[float, float]] = None) -> Dict[str, Any]:
    """One ``alternatives`` entry (JSON-serialisable).  ``url`` is a merchant link taken from SerpApi or None; ``google_url`` is SerpApi's own page."""
    r = c.result
    google = r.get("product_link")
    e: Dict[str, Any] = {
        "key": alt_key(c), "title": clean_title(c.title, 110), "store": store or c.source, "price": price or r.get("price"),
        "url": url if is_merchant_url(url) else None, "google_url": google if isinstance(google, str) and google.startswith("https://") else None,
        "image": image, "colors": dict(c.colors or {}), "score": round(float((c.cohesion or {}).get("total", c.score) or 0.0), 3),
        "passes": bool(passes), "misses": list(misses), "cohesion": _compact_cohesion(c),
        "product_id": str(r["product_id"]) if r.get("product_id") else None, "query": c.query,
    }
    if other_stores:
        e["other_stores"] = other_stores[:3]
    if dims:
        e["w"], e["h"] = float(dims[0]), float(dims[1])
    return e


def meaningful(c: Candidate, slot_colors: Optional[Dict[str, Optional[str]]] = None) -> bool:
    """A near-miss worth showing as "the closest real option": no clash / cool-grey / avoided material or wording, and a palette that is at least half right."""
    bd = c.cohesion or {}
    pal, mat, sty = bd.get("palette") or {}, bd.get("material") or {}, bd.get("style") or {}
    if bd.get("gate", {}).get("accepted"):
        return True
    if pal.get("note") or sty.get("avoid_hits") or mat.get("hard_reject") or (pal.get("score") or 0) < 0.55:
        return False
    if (pal.get("slot_dE") or 0) > 26.0 or (pal.get("slot_cover") if pal.get("slot_cover") is not None else 1.0) < 0.55:
        return False                                 # too far from the designed colour to call it "closest"
    return title_colour_conflict(c.title, (slot_colors or {}).get("primary")) is None


def order(cands: Sequence[Candidate], slot_colors: Optional[Dict[str, Optional[str]]] = None) -> List[Candidate]:
    """Passing options first, then near-misses with the fewest reasons against them (ties: overall fit); junk near-misses go last."""
    def key(c: Candidate):
        g = (c.cohesion or {}).get("gate") or {}
        return (not g.get("accepted"), not meaningful(c, slot_colors), len(g.get("reasons") or []), -float((c.cohesion or {}).get("total") or 0.0))
    return sorted(cands, key=key)


def select(cands: Sequence[Candidate], exclude_ids: Iterable[Optional[str]] = (), exclude_titles: Iterable[str] = (), limit: int = MAX_ALTS,
           per_store: int = MAX_PER_STORE) -> List[Candidate]:
    """The options to show, in the order given (callers sort best first): the current product and duplicates (same picture sold twice, same
    title) removed, at most ``per_store`` per store so the list isn't one shop."""
    ex_ids = {str(i) for i in exclude_ids if i}
    ex_titles = {_norm_title(t) for t in exclude_titles if t}
    out: List[Candidate] = []
    seen_titles: set = set()
    seen_hash: List[int] = []
    n_store: Dict[str, int] = {}
    for c in cands:
        pid = str(c.result.get("product_id") or "")
        nt = _norm_title(c.title)
        if (pid and pid in ex_ids) or nt in ex_titles or nt in seen_titles:
            continue
        h = c.ahash
        if h is not None and any(bin(h ^ k).count("1") <= 4 for k in seen_hash):
            continue
        st = (c.source or "").lower()
        if n_store.get(st, 0) >= per_store:
            continue
        out.append(c)
        seen_titles.add(nt)
        n_store[st] = n_store.get(st, 0) + 1
        if h is not None:
            seen_hash.append(h)
        if len(out) >= limit:
            break
    return out


def save_photo(c: Candidate, dest: Path, rel: str, max_side: int = 360) -> Optional[str]:
    """Write the candidate's product photo (already downloaded for scoring) into the bundle: same-origin relative path, or None."""
    im = c.source_image or c.image
    if im is None:
        return None
    try:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        im.save(dest / rel, quality=88)
        return rel
    except Exception:
        return None


def resolve_link(detail: Optional[Dict[str, Any]], source: Optional[str]) -> Optional[Dict[str, Any]]:
    """Merchant store for an option from a SerpApi product-detail response, or None (nothing is built)."""
    return pick_store(detail, source, store_quality)


def valid_entry(e: Dict[str, Any]) -> List[str]:
    """Structure problems of an ``alternatives`` entry (empty = valid); used by the tests and the bundle writer's self-check."""
    bad = []
    for k in ("key", "title", "store", "score", "passes", "misses", "colors", "cohesion"):
        if k not in e:
            bad.append(f"missing {k}")
    if not isinstance(e.get("misses"), list) or not all(isinstance(m, str) and m for m in e.get("misses", [])):
        bad.append("misses must be a list of non-empty strings")
    if e.get("url") and not is_merchant_url(e["url"]):
        bad.append("url is not a merchant link")
    img = e.get("image")
    if img and (str(img).startswith(("http:", "https:", "//", "/")) or ".." in str(img)):
        bad.append("image must be a relative same-origin path")
    if e.get("passes") and any("weaker" in m for m in e.get("misses", [])):
        bad.append("a passing option cannot be a weaker match")
    return bad

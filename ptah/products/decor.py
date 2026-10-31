"""Lights and decor: what to search for, which listings count, and what a luminaire's listing must say before it may replace the designed light.

The swap decision itself is the SAME cohesion gate as for furniture (palette / designed colour / material / style / size / studio photo, see
ptah.products.cohesion.swap_gate); this module only supplies per-category search phrases, title filters, material hints and the extra
luminaire checks:

* a luminaire's spec is read with ``enrich.parse_light_spec`` (lumens, Kelvin, dimmable, RGB);
* a listing that says it is RGB / colour-changing, or whose Kelvin is stated and is not warm (outside 1800-3000 K), is rejected;
* fields the listing does NOT state (``spec["estimated"]``) keep the designed light behaviour; stated fields replace it.

No network, no LLM.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .enrich import LIGHT_CATEGORIES, parse_light_spec
from .links import _COLOUR_VOCAB, colour_name, shop_similar_query, shop_similar_url
from .scout import Slot, slot_from_item

WARM_K = (1800, 3000)


@dataclass
class DecorSpec:
    key: str
    queries: List[str]
    category: str
    price: Tuple[Optional[int], Optional[int]] = (None, None)
    must: str = ""                       # regex the title must match
    ban: str = ""                        # regex that rejects a title
    intent: Tuple[str, ...] = ()
    also_ok: Tuple[str, ...] = ()
    tolerate: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()       # style keywords for slots that carry none
    noun: str = ""                       # product noun for shop-similar queries
    light: bool = False
    shop_query: str = ""                 # fixed "shop similar" phrase when the slot keywords + material + colour read badly

    def accept(self, title: str, _res: Optional[Dict[str, Any]] = None) -> bool:
        t = (title or "").lower()
        if self.must and not re.search(self.must, t):
            return False
        if self.ban and re.search(self.ban, t):
            return False
        return not re.search(r"\b(dollhouse|miniature|toy|doll|1:12|sticker|poster)\b", t)


_LIGHT_BAN = r"\b(lamp ?shade only|shade only|replacement|socket|cord set|pendant cord|bulbs? only|ceiling fan|chandelier|rgb|christmas|fairy|string lights?)\b"


def spec_key(item: Dict[str, Any]) -> str:
    """Which DecorSpec an items.json entry uses (role first, then category; a picture light aimed at a target is the guitar spotlight)."""
    sl = item.get("slot") or {}
    role = sl.get("role") or ""
    cat = item.get("category") or sl.get("category") or ""
    if cat == "picture_light" and (item.get("light") or {}).get("target"):
        return "spotlight"
    if role in SPECS:
        return role
    if cat == "plant":
        return "floor_plant"
    return cat


def _specs() -> Dict[str, DecorSpec]:
    S: Dict[str, DecorSpec] = {}
    S["pendant"] = DecorSpec("pendant", ["japanese paper lantern pendant light"], "pendant", (25, 250), must=r"pendant|lantern|hanging", ban=_LIGHT_BAN,
                             intent=("paper", "washi", "rice paper", "lantern"), keywords=("paper lantern pendant", "globe pendant", "washi"),
                             noun="pendant light", light=True, shop_query="japanese paper lantern globe pendant light")
    S["sconce"] = DecorSpec("sconce", ["brass wall sconce cone shade plug in"], "sconce", (20, 220), must=r"sconce|wall (light|lamp)", ban=_LIGHT_BAN,
                            keywords=("brass wall sconce", "cone shade", "plug-in sconce"), noun="wall sconce", light=True)
    S["picture_light"] = DecorSpec("picture_light", ["brass picture light plug in"], "picture_light", (20, 220),
                                   must=r"picture light|art light|painting light|display light|gallery light", ban=_LIGHT_BAN,
                                   keywords=("picture light", "brass"), noun="picture light", light=True)
    S["spotlight"] = DecorSpec("spotlight", ["black picture light plug in display spotlight"], "picture_light", (20, 220),
                               must=r"picture light|art light|display light|spot ?light|accent light|gallery light", ban=_LIGHT_BAN,
                               keywords=("accent spotlight", "black", "picture light"), noun="picture light", light=True, shop_query="black picture light accent spotlight")
    S["shelf_strip"] = DecorSpec("shelf_strip", ["warm white led strip light 2700k dimmable shelf"], "led_strip", (8, 80), must=r"led|strip|light bar|tape",
                                 ban=_LIGHT_BAN, intent=("led strip", "strip light", "strip lights", "light strip", "light bar", "tape light"), tolerate=("plastic",),
                                 keywords=("led strip", "warm amber", "shelf lighting"), noun="led strip light", light=True, shop_query="warm white led strip light for shelves")
    S["tv_bias_light"] = DecorSpec("tv_bias_light", ["tv backlight led bias lighting warm white 2700k"], "led_strip", (8, 80), must=r"led|strip|bias|backlight|tape",
                                   ban=_LIGHT_BAN, intent=("bias", "backlight", "led strip", "strip light"), tolerate=("plastic",),
                                   keywords=("led bias light", "amber", "tv backlight"), noun="tv bias light", light=True, shop_query="warm white tv bias backlight led")
    S["floor_plant"] = DecorSpec("floor_plant", ["fiddle leaf fig live plant in pot"], "plant", (15, 250), must=r"fiddle|ficus|plant|planter|fig",
                                 ban=r"\b(artificial|faux|fake|silk|seeds?|fertili[sz]er|soil|plant stand|stand only|pot only|saucer)\b",
                                 intent=("fiddle", "ficus", "planter", "plant", "fig"), keywords=("fiddle leaf fig", "large indoor plant", "terracotta pot"),
                                 noun="plant")
    S["guitar_hanger"] = DecorSpec("guitar_hanger", ["guitar wall hanger black walnut"], "guitar_hanger", (8, 80), must=r"guitar",
                                   ban=r"\b(strap|pick|capo|string|stand only|case|tuner)\b", intent=("hanger", "wall mount", "hook", "holder"),
                                   also_ok=("walnut", "dark_wood", "black_metal", "oak"), keywords=("guitar wall hanger", "black walnut"), noun="guitar wall hanger", shop_query="guitar wall hanger black walnut")
    S["cushion"] = DecorSpec("cushion", ["{colour} {mat} throw pillow"], "cushion", (8, 80), must=r"pillow|cushion",
                             ban=r"\b(insert only|inserts? only|body pillow|neck pillow|floor pillow|dog|pet)\b", intent=("pillow", "cushion"),
                             noun="throw pillow")
    S["throw"] = DecorSpec("throw", ["{colour} {mat} throw blanket"], "throw", (15, 160), must=r"throw|blanket", ban=r"\b(electric|heated|weighted|baby|swaddle|dog|pet|tapestry|beach)\b",
                           intent=("throw", "blanket"), noun="throw blanket")
    S["vase"] = DecorSpec("vase", ["{colour} ceramic bottle vase"], "vase", (8, 150), must=r"vase|bottle", ban=r"\b(artificial|faux|flowers? included|plants?|set of [3-9])\b",
                          intent=("vase",), noun="ceramic vase")
    S["book_stack"] = DecorSpec("book_stack", ["studio ghibli art book hardcover"], "book_stack", (15, 150), must=r"book|art of|artbook|hardcover|hardback",
                                ban=r"\b(journal|notebook|planner|calendar|coloring|colouring|puzzle|bookend|bookmark|stand|holder)\b", intent=("book", "books", "hardcover", "art of"),
                                keywords=("coffee table books", "art books"), noun="coffee table books", shop_query="art coffee table books japanese studio ghibli")
    S["vinyl_crate"] = DecorSpec("vinyl_crate", ["oak wood vinyl record storage crate"], "vinyl_crate", (15, 200), must=r"crate|record (storage|box|holder|case)|vinyl (storage|box|holder|case)",
                                 ban=r"\b(plastic|milk crate|player|turntable|cleaner|sleeves?|brush)\b", intent=("crate", "record storage", "vinyl storage", "record box"),
                                 also_ok=("oak", "light_wood", "walnut", "dark_wood"), keywords=("wooden record crate", "vinyl storage"), noun="record storage crate")
    return S


SPECS: Dict[str, DecorSpec] = _specs()
SCOUT_ORDER = ["pendant", "sconce", "picture_light", "spotlight", "shelf_strip", "tv_bias_light", "floor_plant", "guitar_hanger", "cushion", "throw", "vase",
               "book_stack", "vinyl_crate"]


def decor_slot(item: Dict[str, Any]) -> Slot:
    """Slot of a light / decor item.  Items with no ``slot`` block (picture light, spotlight, guitar hanger) get one from the item itself."""
    sp = SPECS.get(spec_key(item))
    sl = slot_from_item(item)
    if sp and not (item.get("slot") or {}).get("style_keywords"):
        sl.style_keywords = list(sp.keywords)
        sl.category = sp.category
    if not (item.get("slot") or {}).get("size_range_cm"):
        w, h = float(item.get("w") or 20), float(item.get("h") or 20)
        sl.w_range, sl.h_range = (w * 0.7, w * 1.4), (h * 0.7, h * 1.4)
    return sl


def _colour_word(slot: Slot) -> str:
    for kw in slot.style_keywords:
        for w in re.findall(r"[a-z]+", kw.lower()):
            if w in _COLOUR_VOCAB:
                return w
    return colour_name(slot.colors.get("primary"))


def _mat_word(slot: Slot) -> str:
    m = (slot.materials or {}).get("primary")
    return {"light_wood": "", "painted": "", "plastic": ""}.get(m, m or "")


def queries_for(item: Dict[str, Any], slot: Optional[Slot] = None) -> List[str]:
    sp = SPECS.get(spec_key(item))
    if sp is None:
        return []
    slot = slot or decor_slot(item)
    return [re.sub(r"\s+", " ", q.format(colour=_colour_word(slot), mat=_mat_word(slot))).strip() for q in sp.queries]


def shop_similar_for(item: Dict[str, Any]) -> Optional[str]:
    """Google Shopping search URL for a designed piece (style keywords + material + colour words); None for owned items."""
    if item.get("owned"):
        return None
    sp = SPECS.get(spec_key(item))
    if sp and sp.shop_query:
        return shop_similar_url(sp.shop_query)
    slot = decor_slot(item) if sp else slot_from_item(item)
    kws = [re.sub(r"\b\d+\s*[x×]\s*\d+\b", " ", k) for k in slot.style_keywords]
    return shop_similar_url(shop_similar_query(kws, slot.materials, slot.colors, sp.noun if sp else ""))


# ----------------------------------------------------------------------------------------------------------------- luminaire specs

def light_check(title: str, description: str, category: str, dims: Optional[Sequence[float]], designed: Optional[Dict[str, Any]]
                ) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[Dict[str, Any]]]:
    """(merged light dict, reasons to reject, raw parsed spec) for a luminaire listing.

    Stated lumens / Kelvin / dimmable replace the designed values; whatever the listing does not state keeps the designed light behaviour
    (layer, kind, rgb, emit_offset, cone and target always stay the designed ones).  RGB / colour-changing listings and a stated non-warm
    Kelvin are rejected."""
    if category not in LIGHT_CATEGORIES:
        return designed, [], None
    spec = parse_light_spec(title, description, category, dims=dims)
    if spec is None:
        return designed, [], None
    reasons: List[str] = []
    est = set(spec.get("estimated") or [])
    k = spec.get("color_temp_k")
    if "color_temp_k" not in est and k is not None and not (WARM_K[0] <= k <= WARM_K[1]):
        reasons.append(f"colour temperature {k} K is not warm ({WARM_K[0]}-{WARM_K[1]} K wanted)")
    if spec.get("color_changing"):
        reasons.append("RGB / colour-changing light; the design is a single warm colour")
    if designed is None:
        merged = {key: spec[key] for key in spec if key not in ("estimated", "color_changing")}
        merged["estimated"] = sorted(est)
        return merged, reasons, dict(spec, stated=[], kept_designed=[])
    merged = copy.deepcopy(designed)
    stated = []
    for key in ("color_temp_k", "lumens"):
        if key not in est and spec.get(key) is not None:
            merged[key] = spec[key]
            stated.append(key)
    if spec.get("dimmable") and not merged.get("dimmable"):
        merged["dimmable"] = True
        stated.append("dimmable")
    spec = dict(spec, stated=stated, kept_designed=[f for f in ("color_temp_k", "lumens", "dimmable") if f not in stated])
    return merged, reasons, spec

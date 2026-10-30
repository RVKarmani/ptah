"""Product scout: turn a design slot (role, size range, colours, style keywords, subject) into a REAL product.

    slot -> 1-2 Google Shopping queries (ptah.products.serpapi, cached + budgeted)
         -> filter obvious misses (digital files, merchandise, wrong price band)
         -> download the top thumbnails, cut the artwork out of mockup photos (ptah.products.artimg)
         -> score: keyword match, price sanity, store quality, image usability, aspect fit, palette fit
         -> best candidate: title / price / store / link / image + colours, art dims from the print's aspect ratio.

Art / posters get a real texture (the print cropped out of the shopping thumbnail); furniture and decor get the
product photo, name, price, link and colours (their 3D geometry stays the designed one).  No LLM, no network except
SerpApi and the thumbnail host.  Everything downloaded is private working data (never commit it).
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import artimg
from .enrich import extract_colors, normalize_category
from .serpapi import BudgetExceeded, SerpApiClient, SerpApiError

ART_CATEGORIES = {"art", "poster"}
ART_TEMPLATES = {"art", "poster"}

# ----------------------------------------------------------------------------------------------------------------- slots

@dataclass
class Slot:
    id: str
    role: str
    category: str
    template: str
    name: str
    w_range: Tuple[float, float]
    h_range: Tuple[float, float]
    colors: Dict[str, Optional[str]] = field(default_factory=dict)
    materials: Dict[str, Optional[str]] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    style_keywords: List[str] = field(default_factory=list)
    inspired_by: Optional[str] = None
    why_chain: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_art(self) -> bool:
        return self.category in ART_CATEGORIES or self.template in ART_TEMPLATES

    @property
    def mid_area(self) -> float:
        return 0.5 * sum(self.w_range) * 0.5 * sum(self.h_range)


def slot_from_item(item: Dict[str, Any]) -> Slot:
    """Slot from an items.json entry: its ``slot`` block (design stage) or, for plain items (samples), the item itself."""
    sl = item.get("slot") or {}
    rng = sl.get("size_range_cm") or {}
    w, h = float(item.get("w") or 50), float(item.get("h") or 50)
    w_range = tuple(rng.get("w") or (w * 0.8, w * 1.2))
    h_range = tuple(rng.get("h") or (h * 0.8, h * 1.2))
    model = item.get("model") or {}
    cat = normalize_category(sl.get("category") or item.get("category"))
    kws = list(sl.get("style_keywords") or [])
    if not kws:
        kws = [t for t in re.split(r"[^a-z]+", (item.get("name") or "").lower()) if len(t) > 3 and t not in _STOP][:4]
    return Slot(
        id=item["id"], role=sl.get("role") or cat, category=cat, template=(sl.get("template") or model.get("template") or cat),
        name=re.sub(r"\s*\(designed for you\)\s*$", "", item.get("name") or cat, flags=re.I),
        w_range=(float(w_range[0]), float(w_range[1])), h_range=(float(h_range[0]), float(h_range[1])),
        colors=dict(sl.get("colors") or item.get("colors") or {}), materials=dict(sl.get("materials") or item.get("material") or {}),
        params=dict(sl.get("params") or model.get("params") or {}), style_keywords=kws, inspired_by=sl.get("inspired_by"),
        why_chain=dict(item.get("why_chain") or {}))


_STOP = set("a an and the of for to in on with by from at or as is are this that these those your you our my set pack piece pcs pc "
            "new best wall art print prints poster posters framed frame canvas home decor room living gift gifts".split())

# ----------------------------------------------------------------------------------------------------------------- text helpers

def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())]


def subject_tokens(subject: Optional[str]) -> List[str]:
    return [t for t in tokens(subject or "") if t not in _STOP and len(t) > 1]


def clean_title(title: str, max_len: int = 64) -> str:
    t = re.sub(r"[​-‏‪-‮⁠﻿]", "", title or "")
    t = re.sub(r"\s+", " ", t).strip(" -|,;")
    if len(t) <= max_len:
        return t
    cut = t[:max_len]
    for sep in (" | ", " - ", " – ", ", ", " "):
        k = cut.rfind(sep)
        if k >= 24:
            return cut[:k].strip(" -|,;")
    return cut.strip()


_UNIT_IN = 2.54
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\"|”|in(?:ch(?:es)?)?|cm)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(\"|”|in(?:ch(?:es)?)?\b|cm\b)?", re.I)


def parse_listed_size(title: str) -> Optional[Tuple[float, float]]:
    """'16x24 in' / '40x60 cm' / '11x17' in a title -> (a, b) cm as written (first x second)."""
    m = _SIZE_RE.search(title or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    unit = (m.group(3) or "").lower()
    if not unit:  # look just before: "cm" after first number?
        pre = (title or "")[max(0, m.start() - 0):m.end()]
        unit = "cm" if re.search(r"cm", pre, re.I) else ""
    inches = unit.startswith(("in", '"', "”")) or (not unit and max(a, b) <= 40)
    f = _UNIT_IN if inches else 1.0
    a, b = a * f, b * f
    if not (8 <= a <= 400 and 8 <= b <= 400):
        return None
    return round(a, 1), round(b, 1)


def parse_price(res: Dict[str, Any]) -> Optional[float]:
    v = res.get("extracted_price")
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"(\d[\d,]*\.?\d*)", str(res.get("price") or ""))
    return float(m.group(1).replace(",", "")) if m else None

# ----------------------------------------------------------------------------------------------------------------- queries

_ART_NOUN = {"art": "art print", "poster": "poster"}

def build_queries(slot: Slot, max_queries: int = 2) -> List[Dict[str, Any]]:
    """1-2 queries ``{"q", "min_price", "max_price", "why"}`` for a slot, best first."""
    qs: List[Dict[str, Any]] = []
    if slot.is_art:
        noun = _ART_NOUN.get(slot.template, "art print")
        lo, hi = art_price_band(slot)
        subj = (slot.inspired_by or "").strip()
        style = art_style_words(slot)
        if subj:
            qs.append({"q": f"{subj} {noun}", "min_price": lo, "max_price": hi, "why": f"subject '{subj}' from the slot's inspiration"})
        if style:
            qs.append({"q": f"{style} {noun}", "min_price": lo, "max_price": hi, "why": "style keywords of the slot"})
    else:
        noun = CATEGORY_NOUN.get(slot.category) or slot.category.replace("_", " ")
        style = " ".join(slot.style_keywords[:3])
        mat = _material_word(slot)
        lo, hi = generic_price_band(slot)
        qs.append({"q": " ".join(x for x in (style, mat, noun) if x), "min_price": lo, "max_price": hi, "why": "style keywords + material + role"})
        if slot.inspired_by:
            qs.append({"q": f"{slot.inspired_by} inspired {noun}", "min_price": lo, "max_price": hi, "why": "inspiration"})
    seen, out = set(), []
    for q in qs:
        k = q["q"].lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[:max_queries]


_ART_DROP = _STOP | {"frame", "framed", "oak", "thin", "black", "wood", "wooden", "light", "gallery", "inspired", "small", "large", "big",
                     "style", "print", "prints", "poster", "art", "artwork", "painting", "canvas", "x", "cm"}


def art_style_words(slot: Slot, limit: int = 4) -> str:
    """Subject-matter / colour / mood words of the slot's style keywords (frame and 'print' words dropped: they are in the noun)."""
    out: List[str] = []
    for kw in slot.style_keywords:
        for t in tokens(kw):
            if t not in _ART_DROP and not t.isdigit() and t not in out:
                out.append(t)
    return " ".join(out[:limit])


def art_price_band(slot: Slot) -> Tuple[int, int]:
    return 8, int(round((50 + 0.03 * slot.mid_area) / 25.0) * 25)


CATEGORY_NOUN = {
    "sofa": "sofa", "armchair": "accent chair", "chair": "dining chair", "coffee_table": "coffee table", "side_table": "side table",
    "dining_table": "dining table", "desk": "desk", "bed": "bed frame", "nightstand": "nightstand", "bookshelf": "bookshelf",
    "cabinet": "sideboard", "tv_stand": "tv stand", "rug": "area rug", "floor_lamp": "floor lamp", "table_lamp": "table lamp",
    "pendant": "pendant light", "sconce": "wall sconce", "plant": "artificial plant", "mirror": "wall mirror", "vase": "ceramic vase",
    "cushion": "throw pillow", "throw": "throw blanket", "candle": "candle", "tray": "decorative tray", "book_stack": "coffee table books",
    "small_plant": "small potted plant", "vinyl_crate": "record storage crate", "frame": "picture frame",
}
_BAND = {  # rough US$ bands by category (min, max) - a sanity filter only
    "sofa": (250, 2500), "armchair": (80, 1200), "chair": (30, 500), "coffee_table": (60, 900), "side_table": (25, 400),
    "dining_table": (150, 1800), "desk": (80, 900), "bed": (150, 1800), "nightstand": (40, 500), "bookshelf": (60, 800),
    "cabinet": (120, 1500), "tv_stand": (80, 900), "rug": (40, 900), "floor_lamp": (30, 450), "table_lamp": (20, 300),
    "pendant": (25, 500), "sconce": (20, 250), "plant": (15, 250), "mirror": (25, 500), "vase": (8, 150), "cushion": (8, 80),
    "throw": (15, 160), "candle": (5, 80), "tray": (8, 120), "book_stack": (15, 150), "small_plant": (5, 80), "vinyl_crate": (15, 200),
}


def generic_price_band(slot: Slot) -> Tuple[Optional[int], Optional[int]]:
    b = _BAND.get(slot.category)
    return (b[0], b[1]) if b else (None, None)


def _material_word(slot: Slot) -> str:
    m = (slot.materials or {}).get("primary")
    names = {"light_wood": "light wood", "dark_wood": "dark wood", "black_metal": "black metal", "boucle": "boucle"}
    if not m or m in ("painted", "plastic"):
        return ""
    return names.get(m, m)

# ----------------------------------------------------------------------------------------------------------------- filtering

_BAN_COMMON = re.compile(
    r"\b(sticker|stickers|puzzle|jigsaw|t-?shirt|tee|hoodie|sweatshirt|mug|cup|tumbler|phone case|iphone|keychain|key chain|postcard|greeting card|"
    r"figure|figurine|funko|lego|plush|toy|bedding|duvet|comforter|sheet set|towel|shower curtain|tapestry|beach towel|diamond painting|"
    r"paint by number|paint-by-number|cross stitch|embroidery|mouse ?pad|desk mat|calendar|dvd|blu-?ray|vinyl record|lp\b|cd\b|soundtrack|"
    r"book\b|novel|manga|enamel pin|pin set|bookmark|coaster|wallet|bag|tote|backpack|socks|jacket|costume|sheet|decal|wall sticker|"
    r"flag|banner|garland|ornament|magnet|pillow ?case|cushion cover|blanket|rug|carpet|curtain|stencil|template|mat board|frame only|"
    r"photo frame|picture frame|replacement|adapter|cable|charger)\b", re.I)
_DIGITAL = re.compile(r"\b(digital|printable|instant download|download(able)?|pdf|svg|jpe?g file|png file|file only|editable|template|clipart|digital file)\b", re.I)
_ART_WORDS = re.compile(r"\b(print|prints|poster|posters|canvas|art|artwork|painting|illustration|giclee|giclée|lithograph|serigraph|screenprint|wall art|reproduction|photograph|watercolou?r)\b", re.I)
_ART_OK_OVER_BAN = re.compile(r"\b(framed (art )?print|art print|poster print|canvas print|wall art print)\b", re.I)


def classify_art(res: Dict[str, Any]) -> Tuple[bool, bool, str]:
    """-> (acceptable, digital, reason).  ``digital`` results are only used when nothing physical is left."""
    title = res.get("title") or ""
    digital = bool(_DIGITAL.search(title))
    if not _ART_WORDS.search(title):
        return False, digital, "no print/poster/canvas wording in the title"
    ban = _BAN_COMMON.search(title)
    if ban and not _ART_OK_OVER_BAN.search(title):
        return False, digital, f"merchandise ('{ban.group(0)}')"
    return True, digital, "digital download" if digital else "ok"


def classify_generic(slot: Slot, res: Dict[str, Any]) -> Tuple[bool, bool, str]:
    title = (res.get("title") or "").lower()
    noun_toks = [t for t in tokens(CATEGORY_NOUN.get(slot.category) or slot.category.replace("_", " ")) if t not in _STOP]
    if noun_toks and not any(t in title or t.rstrip("s") in title for t in noun_toks[-1:]):
        return False, False, f"title lacks '{noun_toks[-1]}'"
    if re.search(r"\b(cover|slipcover|replacement|leg(s)? only|parts?|cushion for|pad for|protector|assembly|manual|mount|hardware)\b", title) and slot.category in ("sofa", "armchair", "chair", "coffee_table", "side_table", "rug", "floor_lamp", "table_lamp"):
        return False, False, "accessory / part"
    if re.search(r"\b(dollhouse|miniature|toy|doll|1:12|model kit)\b", title):
        return False, False, "toy / miniature"
    return True, False, "ok"

# ----------------------------------------------------------------------------------------------------------------- store quality

_STORE_TIERS: List[Tuple[float, Tuple[str, ...]]] = [
    (1.0, ("mondo", "fine art america", "fineartamerica", "icanvas", "society6", "displate", "art.com", "allposters", "desenio", "juniqe", "posterlounge",
           "minted", "framebridge", "saatchi", "spoke art", "bottleneck", "gallery nucleus", "king & mcgee", "west elm", "cb2", "crate", "pottery barn",
           "ikea", "wayfair", "article", "target", "world market", "anthropologie", "urban outfitters", "rejuvenation", "allmodern", "burke decor",
           "lulu and georgia", "serena", "ethan allen", "rove concepts", "interior define", "joybird", "castlery", "hay", "muji", "amazon", "barnes", "criterion",
           "studio ghibli", "ghibli", "disney", "warner", "universal", "sony", "walmart.com", "best buy", "home depot", "lowe", "overstock", "houzz", "dwell",
           "design within reach", "etsy.com")),
    (0.75, ("posterstore", "abposters", "abposters.com", "posters.com", "barewalls", "wallspace", "walls 360", "lostwallart", "popcultcha", "pixels", "imagekind",
            "great big canvas", "greatbigcanvas", "artfulwalls", "wall art", "framed", "canvas freaks", "gift horse")),
    (0.55, ("etsy",)),
    (0.35, ("walmart", "ebay", "amazon seller", "aliexpress", "temu", "wish", "shein", "dhgate", "alibaba")),
]


def store_quality(source: str) -> float:
    s = (source or "").lower()
    if s.startswith(("ebay", "walmart -")):
        return 0.3 if s.startswith("ebay") else 0.35
    if s.startswith("etsy -"):
        return 0.5
    for score, keys in _STORE_TIERS:
        if any(k in s for k in keys):
            return score
    return 0.5

# ----------------------------------------------------------------------------------------------------------------- scoring

def _hex_rgb(h: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not h or not re.fullmatch(r"#[0-9a-fA-F]{6}", h):
        return None
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def palette_fit(img_colors: Optional[Dict[str, Optional[str]]], slot_colors: Dict[str, Optional[str]]) -> float:
    """0..1: how close the product's dominant colours are to the slot's designed colours."""
    ic = [_hex_rgb(img_colors.get(k)) for k in ("primary", "secondary")] if img_colors else []
    sc = [_hex_rgb(slot_colors.get(k)) for k in ("primary", "secondary", "accent")]
    ic, sc = [c for c in ic if c], [c for c in sc if c]
    if not ic or not sc:
        return 0.0
    best = min(math.dist(a, b) / 441.7 for a in ic for b in sc)
    return max(0.0, 1.0 - 3.0 * best)


def aspect_penalty(aspect: float, slot: Slot) -> float:
    lo = slot.w_range[0] / slot.h_range[1]
    hi = slot.w_range[1] / slot.h_range[0]
    lo, hi = lo / 1.03, hi * 1.03
    if lo <= aspect <= hi:
        return 0.0
    d = math.log(lo / aspect) if aspect < lo else math.log(aspect / hi)
    return 20.0 * d


def keyword_score(slot: Slot, title: str) -> Tuple[float, List[str]]:
    tt = set(tokens(title))
    notes: List[str] = []
    s = 0.0
    st = subject_tokens(slot.inspired_by)
    if st:
        hit = [t for t in st if t in tt]
        frac = len(hit) / len(st)
        s += 3.0 * frac
        if hit:
            notes.append(f"title matches '{slot.inspired_by}'")
    kw_hits = []
    for kw in slot.style_keywords:
        ks = [t for t in tokens(kw) if t not in _STOP]
        if ks and all(t in tt for t in ks):
            kw_hits.append(kw)
    s += min(1.5, 0.5 * len(kw_hits))
    if kw_hits:
        notes.append("style: " + ", ".join(kw_hits))
    if re.search(r"\b(official|licensed|mondo|limited edition|giclee|giclée|archival|lithograph|screenprint|signed)\b", title, re.I):
        s += 0.5
        notes.append("official / archival wording")
    if re.search(r"\b(inspired|fan art|unofficial|style)\b", title, re.I):
        s -= 0.3
    return s, notes


def price_score(price: Optional[float], lo: Optional[float], hi: Optional[float]) -> float:
    if price is None:
        return -1.0
    if hi is not None and price > hi:
        return -min(3.0, (price - hi) / max(hi, 1) * 3.0)
    if lo is not None and price < lo:
        return -0.5
    return 1.0


@dataclass
class Candidate:
    result: Dict[str, Any]
    query: str
    price: Optional[float]
    pre: float = 0.0
    digital: bool = False
    notes: List[str] = field(default_factory=list)
    # image
    image: Any = None            # PIL image of the artwork (art) or the product photo
    source_image: Any = None
    crop_conf: float = 0.0
    colors: Optional[Dict[str, Optional[str]]] = None
    aspect: Optional[float] = None
    aspect_pen: float = 0.0
    evaluated: bool = False
    score: float = 0.0

    @property
    def title(self) -> str:
        return self.result.get("title") or ""

    @property
    def source(self) -> str:
        return self.result.get("source") or ""


def pre_score(slot: Slot, res: Dict[str, Any], lo: Optional[float], hi: Optional[float]) -> Candidate:
    price = parse_price(res)
    kw, notes = keyword_score(slot, res.get("title") or "")
    s = kw + 1.2 * store_quality(res.get("source") or "") + price_score(price, lo, hi)
    return Candidate(result=res, query="", price=price, pre=s, notes=notes)

# ----------------------------------------------------------------------------------------------------------------- image fetch

MAX_IMAGE_BYTES = 8_000_000


def fetch_image(url: str, session: Any = None, timeout: int = 30):
    """Download an image (http/https only, size-capped) -> PIL RGB image or None."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    try:
        from PIL import Image
        import requests
        r = (session or requests).get(url, timeout=timeout, stream=False)
        if r.status_code != 200 or len(r.content) > MAX_IMAGE_BYTES or len(r.content) < 500:
            return None
        im = Image.open(io.BytesIO(r.content))
        im.load()
        return im.convert("RGB")
    except Exception:
        return None


def upscale_for_texture(im, min_long: int = 640):
    from PIL import Image, ImageFilter
    w, h = im.size
    long = max(w, h)
    if long >= min_long:
        return im
    f = min_long / float(long)
    out = im.resize((int(round(w * f)), int(round(h * f))), Image.LANCZOS)
    return out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=40, threshold=3))

# ----------------------------------------------------------------------------------------------------------------- art dims

def poster_margin(w: float, h: float) -> float:
    return min(4.0, max(2.0, min(w, h) * 0.07))


def print_area(template: str, params: Dict[str, Any], w: float, h: float) -> Tuple[float, float]:
    """Size (cm) of the picture plane the viewer draws inside an item of outer size w x h.  MIRRORS viewer/src/models.ts
    (artT: frame ft = clamp(min(w,h)*0.05, 1.6, 4.5), plane = w-2ft x h-2ft; posterT: thin frame 1.0 (0 when frame is none) + margin)."""
    if template == "poster":
        ft = 0.0 if params.get("frame") == "none" else 1.0
        iw, ih = w - 2 * ft, h - 2 * ft
        m = poster_margin(w, h)
        return iw - 2 * m, ih - 2 * m
    ft = min(4.5, max(1.6, min(w, h) * 0.05))
    return w - 2 * ft, h - 2 * ft


def fit_art_dims(slot: Slot, aspect: float, target_area: Optional[float] = None) -> Tuple[float, float]:
    """Outer (w, h) in cm such that the picture plane has the print's aspect ratio (never stretched) and the item stays
    inside the slot's MAXIMUM size (the layout is built for it); below the minimums only if the aspect forces it."""
    wmin, wmax = slot.w_range
    hmin, hmax = slot.h_range
    ta = target_area or slot.mid_area
    best = None
    steps = 120
    lo_h = 0.55 * hmin
    for i in range(steps + 1):
        h = lo_h + (hmax - lo_h) * i / steps
        w = h * aspect
        for _ in range(6):  # the viewer's frame / margin insets depend on w and h: fixed point
            pw, ph = print_area(slot.template, slot.params, w, h)
            if pw <= 0 or ph <= 0:
                break
            w = (h - (h - ph)) * aspect + (w - pw)
        pw, ph = print_area(slot.template, slot.params, w, h)
        if pw <= 0 or ph <= 0 or w > wmax + 0.05 or h > hmax + 0.05:
            continue
        short = max(0.0, wmin - w) + max(0.0, hmin - h)
        cost = short * 5 + abs(w * h - ta) / ta
        if best is None or cost < best[0]:
            best = (cost, w, h)
    if best is None:
        return round(wmax, 1), round(hmax, 1)
    return round(best[1], 1), round(best[2], 1)


# ----------------------------------------------------------------------------------------------------------------- scouting

@dataclass
class ScoutResult:
    slot: Slot
    queries: List[Dict[str, Any]]
    chosen: Optional[Candidate]
    ranked: List[Candidate]
    rejected: List[Tuple[str, str]]
    dims: Optional[Tuple[float, float]] = None
    detail: Optional[Dict[str, Any]] = None
    note: str = ""


def _gather(slot: Slot, client: SerpApiClient, q: Dict[str, Any], classify, pool: Dict[str, Candidate], rejected: List[Tuple[str, str]]) -> List[Candidate]:
    """Run one query, classify its results and add the new ones to ``pool``.  -> the new candidates."""
    # only max_price goes to Google: a min+max pair is appended to the query text ("between $8 and $249") and empties the
    # results; the lower bound is applied locally in price_score
    resp = client.search(q["q"], max_price=q.get("max_price"))
    new: List[Candidate] = []
    for res in client.results(resp):
        ok, digital, why = classify(res)
        if not ok:
            rejected.append((res.get("title") or "", why))
            continue
        key = str(res.get("product_id") or res.get("title"))
        if key in pool:
            continue
        c = pre_score(slot, res, q.get("min_price"), q.get("max_price"))
        c.query, c.digital = q["q"], digital
        if digital:
            c.pre -= 6.0
            c.notes.append("digital download")
        pool[key] = c
        new.append(c)
    return new


def _evaluate(slot: Slot, c: Candidate, session: Any = None) -> None:
    """Download the thumbnail, cut the print out (art), colours, final score."""
    url = c.result.get("thumbnail") or c.result.get("serpapi_thumbnail")
    im = fetch_image(url, session)
    c.evaluated = True
    if im is None:
        c.notes.append("image download failed")
        c.score = c.pre - 4.0
        return
    c.source_image = im
    if slot.is_art:
        box, conf = artimg.find_artwork_rect(im)
        art = im.crop(box)
        c.image, c.crop_conf = art, conf
        c.aspect = art.width / float(art.height)
        if conf >= 0.5:
            c.notes.append(f"print cut out of the photo (confidence {conf:.2f})")
    else:
        c.image, c.crop_conf = im, 1.0
    c.colors = extract_colors(c.image)
    s = c.pre
    if slot.is_art:
        s += 1.5 if c.crop_conf >= 0.7 else (0.8 if c.crop_conf >= 0.5 else -1.5)
        ms = min(c.image.size)
        s += 0.5 if ms >= 300 else (0.0 if ms >= 180 else -0.7)
        if not (0.35 <= c.aspect <= 2.8):
            s -= 3.0
        c.aspect_pen = aspect_penalty(c.aspect, slot)
        if c.aspect_pen:
            c.notes.append(f"aspect {c.aspect:.2f} outside the slot's {slot.w_range[0] / slot.h_range[1]:.2f}-{slot.w_range[1] / slot.h_range[0]:.2f}")
        s -= c.aspect_pen
    s += 0.8 * palette_fit(c.colors, slot.colors)
    c.score = s


def _satisfied(slot: Slot, c: Optional[Candidate]) -> bool:
    if c is None or c.image is None or c.digital:
        return False
    if slot.is_art:
        return c.crop_conf >= 0.5 and c.aspect_pen == 0 and (not subject_tokens(slot.inspired_by) or c.pre >= 3.5)
    return True


def scout_slot(slot: Slot, client: SerpApiClient, *, top_images: int = 24, details: bool = True, session: Any = None,
               max_queries: int = 2) -> ScoutResult:
    """Find the best real product for a slot.  Queries run one at a time; the next one only when the best candidate so far is
    unusable (digital file, no clear print, aspect that does not fit the slot).  The best ``top_images`` text matches are
    downloaded, cut out and scored together."""
    queries = build_queries(slot, max_queries=max_queries)
    classify = classify_art if slot.is_art else (lambda r: classify_generic(slot, r))
    if not queries:
        return ScoutResult(slot, [], None, [], [], note="no query could be built")
    pool: Dict[str, Candidate] = {}
    rejected: List[Tuple[str, str]] = []
    used: List[Dict[str, Any]] = []
    best: Optional[Candidate] = None
    for q in queries:
        try:
            new = _gather(slot, client, q, classify, pool, rejected)
        except BudgetExceeded:
            if used:
                break
            raise
        used.append(q)
        for c in sorted(new, key=lambda c: -c.pre)[:top_images]:   # thumbnails are free: look at the best of each query
            _evaluate(slot, c, session)
        evald = [c for c in pool.values() if c.evaluated and c.image is not None]
        best = max(evald, key=lambda c: c.score) if evald else None
        if _satisfied(slot, best):
            break
    ranked = sorted((c for c in pool.values() if c.evaluated), key=lambda c: -c.score) + sorted((c for c in pool.values() if not c.evaluated), key=lambda c: -c.pre)
    if not pool:
        return ScoutResult(slot, used, None, [], rejected, note="no acceptable result")
    chosen = best
    res = ScoutResult(slot, used, chosen, ranked, rejected)
    if chosen is None:
        res.note = "no candidate image could be used"
        return res
    if slot.is_art:
        res.dims = fit_art_dims(slot, chosen.aspect or 0.75)
    if details and chosen.result.get("immersive_product_page_token") and client.remaining() > 0:
        try:
            res.detail = client.product_detail(chosen.result["immersive_product_page_token"])
        except SerpApiError as e:
            res.note += f" detail fetch failed: {e}"
    return res


def direct_link(detail: Optional[Dict[str, Any]], source: str) -> Tuple[Optional[str], Optional[str]]:
    """(merchant link, listed dimensions text) from an immersive-product response."""
    if not detail:
        return None, None
    pr = detail.get("product_results") or {}
    stores = pr.get("stores") or []
    link = None
    for s in stores:
        if source and source.lower().split(" - ")[0] in (s.get("name") or "").lower():
            link = s.get("link")
            break
    link = link or (stores[0].get("link") if stores else None)
    dim = None
    for f in (pr.get("about_the_product") or {}).get("features") or []:
        if "dimension" in str(f.get("title", "")).lower() or "size" in str(f.get("title", "")).lower():
            dim = str(f.get("value"))
    return link, dim

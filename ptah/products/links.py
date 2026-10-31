"""Where to buy: merchant links from SerpApi product-detail data, and "shop similar" search URLs for designed pieces.

* ``pick_store`` chooses the merchant (store page) for a product from a ``google_immersive_product`` response: the store matching the
  chosen offer's source first, else the lowest-price reputable seller.  Only links that are IN the response are ever returned
  (no URL is built or guessed); anything that is not an http(s) link to a non-Google host is ignored.
* ``shop_similar_url`` builds a Google Shopping SEARCH URL (https://www.google.com/search?tbm=shop&q=...) from a slot's style keywords,
  material and colour words.  It is a plain search link, no API call, and is only offered for designed (non-owned) pieces.
* ``page_token_for`` finds the immersive-product page token of a product id in cached shopping results (so the detail call is the only
  live request needed).
"""
from __future__ import annotations

import colorsys
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urlparse

SHOP_SEARCH = "https://www.google.com/search?tbm=shop&q="


# ----------------------------------------------------------------------------------------------------------------- merchant links

def is_merchant_url(url: Optional[str]) -> bool:
    """http(s) link to something that is not a Google page (a Google Shopping page is not the merchant)."""
    if not url or not isinstance(url, str):
        return False
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host or "." not in host:
        return False
    return not re.search(r"(^|\.)google\.[a-z.]+$|(^|\.)gstatic\.com$|(^|\.)googleusercontent\.com$", host)


def _norm_store(name: Optional[str]) -> str:
    s = (name or "").lower()
    s = re.sub(r"\s+-\s+(seller|sold by.*)$", "", s)
    s = re.sub(r"\.(com|net|co|org|co\.uk)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def same_store(a: Optional[str], b: Optional[str]) -> bool:
    x, y = _norm_store(a), _norm_store(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long_ = sorted((x, y), key=len)
    return len(short) >= 4 and short in long_


def _price_of(store: Dict[str, Any]) -> Optional[float]:
    for k in ("extracted_price", "extracted_total"):
        v = store.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def merchant_offers(detail: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stores of an immersive-product response that carry a direct merchant link: [{store, url, price, extracted_price}]."""
    out: List[Dict[str, Any]] = []
    if not isinstance(detail, dict):
        return out
    stores = (detail.get("product_results") or {}).get("stores") or []
    for s in stores:
        if not isinstance(s, dict):
            continue
        url = s.get("link")
        if not is_merchant_url(url):
            continue
        out.append({"store": s.get("name") or urlparse(url).hostname, "url": url, "price": s.get("price"), "extracted_price": _price_of(s)})
    return out


def pick_store(detail: Optional[Dict[str, Any]], source: Optional[str], store_quality=None) -> Optional[Dict[str, Any]]:
    """The merchant to send the user to -> {store, url, price, extracted_price, matched, others} or None.

    ``matched`` is ``"source"`` (the store of the offer the product was chosen from), ``"lowest_price"`` (cheapest reputable seller) or
    ``"first"`` (nothing else to go on).  ``others`` lists up to 3 further stores (name, url, price), all from the response.
    """
    offers = merchant_offers(detail)
    if not offers:
        return None
    quality = store_quality or (lambda name: 0.5)
    chosen, how = None, ""
    for o in offers:
        if source and same_store(source, o["store"]):
            chosen, how = o, "source"
            break
    if chosen is None:
        reputable = [o for o in offers if quality(o["store"]) >= 0.5]
        pool = reputable or offers
        priced = [o for o in pool if o["extracted_price"] is not None]
        if priced:
            chosen, how = min(priced, key=lambda o: o["extracted_price"]), "lowest_price"
        else:
            chosen, how = pool[0], "first"
    others = [{"store": o["store"], "url": o["url"], "price": o["price"]} for o in offers if o is not chosen][:3]
    return {**chosen, "matched": how, "others": others}


def page_token_for(product_id: Optional[str], results: Iterable[Tuple[str, Dict[str, Any]]]) -> Optional[str]:
    """immersive_product_page_token of a product id, from (query, shopping result) pairs (cached search results)."""
    if not product_id:
        return None
    for _q, r in results:
        if str(r.get("product_id")) == str(product_id) and r.get("immersive_product_page_token"):
            return r["immersive_product_page_token"]
    return None


def response_urls(obj: Any) -> set:
    """Every string in a response that looks like an http(s) URL (for 'no invented URLs' checks)."""
    found: set = set()
    if isinstance(obj, dict):
        for v in obj.values():
            found |= response_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= response_urls(v)
    elif isinstance(obj, str) and obj.startswith(("http://", "https://")):
        found.add(obj)
    return found


# ----------------------------------------------------------------------------------------------------------------- colour words

def colour_name(hexv: Optional[str]) -> str:
    """A shopper's word for a hex colour ('indigo blue', 'sand', 'terracotta', 'sage green' ...); '' when unknown."""
    if not hexv or not re.fullmatch(r"#[0-9a-fA-F]{6}", hexv):
        return ""
    r, g, b = (int(hexv[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    hd = h * 360.0
    if s < 0.12 or l < 0.1 or l > 0.95:
        return "black" if l < 0.14 else "white" if l > 0.93 else "charcoal" if l < 0.35 else "grey" if l < 0.75 else "cream"
    if l > 0.78 and s < 0.6:
        return "cream" if hd < 70 else "sage green" if hd < 170 else "light blue"
    if hd < 14 or hd >= 345:
        return "red" if l >= 0.3 else "burgundy"
    if hd < 38:
        return "terracotta" if l >= 0.35 else "brown"
    if hd < 52:
        return ("ochre" if s > 0.5 else "sand") if l >= 0.5 else "walnut brown"
    if hd < 70:
        return "mustard" if s > 0.5 else "sand"
    if hd < 100:
        return "olive"
    if hd < 165:
        return "sage green" if l > 0.45 or s < 0.4 else "forest green"
    if hd < 200:
        return "teal"
    if hd < 260:
        return "indigo blue" if l < 0.5 else "blue"
    if hd < 345:
        return "plum"
    return ""


_COLOUR_VOCAB = set("""red burgundy terracotta brown ochre mustard sand olive sage green forest teal indigo blue navy plum black white charcoal grey gray cream
beige ivory celadon amber gold brass walnut oak""".split())
_MATERIAL_LABEL = {"light_wood": "oak", "dark_wood": "walnut", "black_metal": "black metal", "painted": "", "plastic": "", "boucle": "boucle"}
_NOISE = set("designed for you the a an and of with set pcs piece".split())


def _words(text: str) -> List[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]*", (text or "").lower())


def shop_similar_query(style_keywords: Sequence[str], materials: Optional[Dict[str, Optional[str]]] = None,
                       colors: Optional[Dict[str, Optional[str]]] = None, noun: str = "", max_words: int = 9) -> str:
    """Style keywords + material + colour words -> a short product-search phrase (deduplicated, in that order)."""
    words: List[str] = []

    def add(ws: Iterable[str]) -> None:
        for w in ws:
            if w and w not in words and w not in _NOISE and len(words) < max_words:
                words.append(w)

    for kw in style_keywords:
        add(_words(kw))
    if noun and not any(w in words for w in _words(noun)[-1:]):     # the product noun ('sofa', 'sconce') must be in the query
        add(_words(noun))
    mat = (materials or {}).get("primary")
    add(_words(_MATERIAL_LABEL.get(mat, mat or "")))
    if not any(w in _COLOUR_VOCAB for w in words):
        add(_words(colour_name((colors or {}).get("primary"))))
    return " ".join(words)


def shop_similar_url(query: str) -> Optional[str]:
    q = re.sub(r"\s+", " ", query or "").strip()
    return SHOP_SEARCH + quote_plus(q) if q else None

"""Design brief: turn a person's taste entities into interior-design direction, via Qloo.

``build_brief(entities, location)`` makes these Qloo calls (all cached on disk, see ptah/qloo.py):

1. Taste analysis (``filter.type=urn:tag``) -> top decor and ambience tags for the whole taste, plus one
   single-entity analysis per input so every tag can say *which inputs it comes from*.
2. Inspiration places: places with strong interior identity (hotels, restaurants, bars, cafes) in the
   location. One /v2/insights place query per category x top decor tag
   (``filter.tags`` = category AND decor tag, ``operator.filter.tags=intersection``), landmarks removed
   with ``filter.exclude.tags``.
3. Local shops (furniture, home goods, antiques, rugs, lighting, galleries, record stores): one query per
   category in the location, big-box / department-store / warehouse-club categories removed with
   ``filter.exclude.tags``.
4. Home brands: a candidate catalog (ptah/catalog/home_brands.json) is resolved to ids with /search and
   ranked for this taste with ``filter.results.entities`` (affinity of those specific brands).
5. Discovery: films / artists / books the taste predicts but that are not already inputs
   (``filter.exclude.entities``), for poster and print ideas.

Every item carries ``why``: the input entities (name + contribution) that Qloo's
``feature.explainability`` credits most. Nothing about the person is hard-coded here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from . import events, qloo
from .data import DATA_DIR

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "home_brands.json"
MAX_SIGNALS = 15          # taste entities sent as signal.interests.entities
MIN_NAME_MATCH = 0.85     # /search sanity check: accept a candidate only if its name is this close

# --- documented filters (Entity Type Parameter Guide: place -> filter.tags, operator.filter.tags,
# --- filter.exclude.tags, filter.location.query) --------------------------------------------------
# Landmarks / tourist attractions dominate unfiltered place results; exclude them by tag.
LANDMARK_TAGS = [
    "urn:tag:category:place:tourist_attraction", "urn:tag:genre:place:tourist_attraction",
    "urn:tag:category:place:landmark", "urn:tag:genre:place:landmark",
    "urn:tag:category:place:historical_landmark", "urn:tag:genre:place:historical_landmark",
]
# Big-box retail noise for furniture/home queries.
BIG_BOX_TAGS = [
    "urn:tag:category:place:department_store", "urn:tag:genre:place:department_store",
    "urn:tag:category:place:warehouse_club", "urn:tag:genre:place:warehouse_club",
    "urn:tag:category:place:home_improvement_store",
    "urn:tag:category:place:shopping_mall", "urn:tag:genre:place:shopping_mall",
    "urn:tag:category:place:discount_store", "urn:tag:category:place:wholesaler",
    "urn:tag:category:place:furniture_wholesaler", "urn:tag:category:place:furniture_manufacturer",
]
# Counter-service / takeaway categories whose interiors are not worth borrowing from.
QUICK_SERVICE_TAGS = [
    "urn:tag:category:place:fast_food", "urn:tag:category:place:fast_food_restaurant",
    "urn:tag:category:place:sandwich_shop", "urn:tag:category:place:pizza",
    "urn:tag:category:place:pizza_restaurant", "urn:tag:category:place:juice_shop",
]
# Furniture / home-goods queries also match gift shops and appliance stores that merely stock a few items.
RETAIL_NOISE_TAGS = ["urn:tag:category:place:gift_shop", "urn:tag:category:place:appliance_store"]
# Places worth borrowing interior ideas from: label -> category tag (urn:tag:category:place:*).
INSPIRATION_CATEGORIES = {
    "hotel": "urn:tag:category:place:hotel",
    "restaurant": "urn:tag:category:place:restaurant",
    "bar": "urn:tag:category:place:bar",
    "cafe": "urn:tag:category:place:cafe",
}
SHOP_CATEGORIES = {
    "furniture store": "urn:tag:category:place:furniture_store",
    "home goods store": "urn:tag:category:place:home_goods_store",
    "antiques": "urn:tag:category:place:antique_store",
    "flea market": "urn:tag:category:place:flea_market",
    "art gallery": "urn:tag:category:place:art_gallery",
    "record store": "urn:tag:category:place:record_store",
}
DISCOVERY_TYPES = {"movie": 4, "artist": 3, "book": 3}
TOP_DECOR_FOR_PLACES = 3
PLACES_PER_QUERY = 8
PLACES_PER_CATEGORY = 4
SHOPS_PER_KIND = 3
MATCH_BONUS = 0.02        # score = affinity + bonus per top-decor tag the place carries


# --------------------------------------------------------------------------- input entities

def _norm_entity(e: Any) -> dict:
    if isinstance(e, str):
        return {"qloo_id": e, "name": qloo.name_of(e), "type": ""}
    if hasattr(e, "model_dump"):
        e = e.model_dump()
    return {"qloo_id": e["qloo_id"], "name": e.get("name") or qloo.name_of(e["qloo_id"]),
            "type": e.get("type", ""), "confidence": e.get("confidence")}


def resolve(name: str, type_hint: str, min_score: float = MIN_NAME_MATCH) -> dict | None:
    """/search with a sanity check: best near-exact name match (ties -> more popular), else None."""
    cands = [c for c in qloo.search(name, type_hint, take=5) if c["score"] >= min_score]
    if not cands:
        return None
    return max(cands, key=lambda c: (c["score"], c["popularity"] or 0))


def seed_from_music(path: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Seed entities from data/music.json: every distinct artist resolved via /search (type artist).

    Returns (entities, skipped); skipped names had no near-exact Qloo match.
    """
    music = json.loads((path or DATA_DIR / "music.json").read_text(encoding="utf-8"))
    names: list[str] = []
    for track in music:
        for a in track.get("artists") or []:
            if a not in names:
                names.append(a)
    ents: list[dict] = []
    skipped: list[dict] = []
    for n in names:
        hit = resolve(n, "artist")
        if not hit:
            skipped.append({"name": n, "reason": "no near-exact Qloo artist match"})
        elif any(e["qloo_id"] == hit["qloo_id"] for e in ents):
            skipped.append({"name": n, "reason": f"duplicate of {hit['name']}"})
        else:
            ents.append({"qloo_id": hit["qloo_id"], "name": hit["name"], "type": "artist", "source_name": n})
    return ents, skipped


def entities_from_profile(path: Path) -> list[dict]:
    """QlooEntity list from a TasteProfile JSON (run_taste.py output), most confident first."""
    prof = json.loads(path.read_text(encoding="utf-8"))
    ents = [_norm_entity(e) for e in prof.get("entities") or []]
    if any(e["qloo_id"].startswith("stub_") for e in ents):
        raise SystemExit(f"{path} contains STUB Qloo ids; re-run run_taste.py with a real QLOO_API_KEY")
    ents.sort(key=lambda e: -(e.get("confidence") or 0))
    return ents


# --------------------------------------------------------------------------- brief sections

def _short(text: str | None, n: int = 280) -> str | None:
    return text if not text or len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


def _ids(entities: list[dict]) -> list[str]:
    return [e["qloo_id"] for e in entities]


def taste_analysis(entities: list[dict], names: dict[str, str], take: int = 10, with_why: bool = True) -> dict:
    """Decor + ambience tags for the whole taste; ``why`` from single-entity analyses of each input."""
    out: dict[str, list[dict]] = {}
    for kind in ("decor", "ambience"):
        tags = qloo.taste_tags(_ids(entities), kind, take=take)
        if with_why:
            per_entity = {e["qloo_id"]: {t["tag_id"]: t["affinity"] or 0
                                         for t in qloo.taste_tags([e["qloo_id"]], kind, take=50)}
                          for e in entities}
            for t in tags:
                rows = sorted(((eid, m.get(t["tag_id"], 0)) for eid, m in per_entity.items()), key=lambda r: -r[1])
                t["why"] = [{"qloo_id": eid, "name": names.get(eid, eid), "score": round(s, 3)}
                            for eid, s in rows[:3] if s > 0]
        out[kind] = tags
    return out


def _decor_ids(tags: list[dict], n: int) -> list[str]:
    return [t["tag_id"] for t in tags[:n]]


def _place_item(e: dict, kind: str, top_decor: list[str]) -> dict:
    matched = [t for t in top_decor if t in e["tag_ids"]]
    return {
        "qloo_id": e["qloo_id"], "name": e["name"], "kind": kind, "category": e["category"],
        "address": e["address"], "neighborhood": e["neighborhood"], "website": e["website"],
        "images": e["images"][:3], "decor_tags": e["decor_tags"], "ambience_tags": e["ambience_tags"],
        "matched_decor": [t.rsplit(":", 1)[-1].replace("_", " ") for t in matched],
        "affinity": e["affinity"], "popularity": e["popularity"],
        "score": round((e["affinity"] or 0) + MATCH_BONUS * len(matched), 3),
        "why": e["why"],
    }


def inspiration_places(ids: list[str], decor: list[dict], location: str, names: dict[str, str]) -> list[dict]:
    """Interior-rich places: category AND top-decor-tag queries, landmarks excluded, merged and ranked."""
    top = _decor_ids(decor, TOP_DECOR_FOR_PLACES)
    pool: dict[str, dict] = {}
    for kind, cat in INSPIRATION_CATEGORIES.items():
        for decor_tag in top:
            es = qloo.insights_entities("place", ids, take=PLACES_PER_QUERY, names=names, extra={
                "filter.tags": f"{cat},{decor_tag}", "operator.filter.tags": "intersection",
                "filter.location.query": location,
                "filter.exclude.tags": ",".join(LANDMARK_TAGS + QUICK_SERVICE_TAGS)})
            for e in es:
                item = _place_item(e, kind, top)
                if not e["images"]:
                    continue  # the brief is for visual reference: skip places without a photo
                if e["qloo_id"] not in pool or item["score"] > pool[e["qloo_id"]]["score"]:
                    pool[e["qloo_id"]] = item
    by_kind: dict[str, list[dict]] = {}
    for item in sorted(pool.values(), key=lambda i: -i["score"]):
        by_kind.setdefault(item["kind"], []).append(item)
    picked = [i for items in by_kind.values() for i in items[:PLACES_PER_CATEGORY]]
    return sorted(picked, key=lambda i: -i["score"])


def local_shops(ids: list[str], decor: list[dict], location: str, names: dict[str, str]) -> list[dict]:
    """Design-relevant shops per category, big-box / department-store / warehouse categories excluded."""
    top = _decor_ids(decor, TOP_DECOR_FOR_PLACES)
    pool: dict[str, dict] = {}
    for kind, cat in SHOP_CATEGORIES.items():
        es = qloo.insights_entities("place", ids, take=SHOPS_PER_KIND * 3, names=names, extra={
            "filter.tags": cat, "filter.location.query": location,
            "filter.exclude.tags": ",".join(BIG_BOX_TAGS + RETAIL_NOISE_TAGS)})
        for e in es[:SHOPS_PER_KIND + 2]:
            item = _place_item(e, kind, top)
            item["shop_kind"] = item.pop("kind")
            if e["qloo_id"] not in pool or item["score"] > pool[e["qloo_id"]]["score"]:
                pool[e["qloo_id"]] = item
    by_kind: dict[str, list[dict]] = {}
    for item in sorted(pool.values(), key=lambda i: -i["score"]):
        by_kind.setdefault(item["shop_kind"], []).append(item)
    picked = [i for items in by_kind.values() for i in items[:SHOPS_PER_KIND]]
    return sorted(picked, key=lambda i: -i["score"])


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, str]:
    """Catalog JSON -> {brand name: price tier} (tier = the JSON group the name is listed under)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return {n: tier for tier, names in data.items() if not tier.startswith("_") for n in names}


def brand_ranking(ids: list[str], names: dict[str, str], catalog: dict[str, str] | list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Rank the candidate home-brand catalog for this taste (filter.results.entities). -> (ranked, unresolved)."""
    catalog = catalog if catalog is not None else load_catalog()
    tiers = catalog if isinstance(catalog, dict) else {n: "" for n in catalog}
    resolved: dict[str, str] = {}   # qloo_id -> catalog name
    unresolved: list[str] = []
    for n in tiers:
        hit = resolve(n, "brand")
        if hit:
            resolved.setdefault(hit["qloo_id"], n)
        else:
            unresolved.append(n)
    if not resolved:
        return [], unresolved
    es = qloo.insights_entities("brand", ids, take=len(resolved), names=names,
                                extra={"filter.results.entities": ",".join(resolved)})
    ranked = [{"qloo_id": e["qloo_id"], "name": e["name"], "catalog_name": resolved.get(e["qloo_id"]), "tier": tiers.get(resolved.get(e["qloo_id"], ""), ""),
               "affinity": e["affinity"], "tags": e["tags"], "description": _short(e["description"]),
               "images": e["images"][:1], "why": e["why"]} for e in es]
    missing = [n for i, n in resolved.items() if i not in {r["qloo_id"] for r in ranked}]
    return sorted(ranked, key=lambda r: -(r["affinity"] or 0)), unresolved + [f"{n} (no affinity returned)" for n in missing]


def discovery(ids: list[str], names: dict[str, str]) -> list[dict]:
    """Films / artists / books Qloo predicts for this taste that are not already inputs."""
    out: list[dict] = []
    for typ, n in DISCOVERY_TYPES.items():
        es = qloo.insights_entities(typ, ids, take=n, names=names, extra={"filter.exclude.entities": ",".join(ids)})
        for e in es:
            out.append({"qloo_id": e["qloo_id"], "name": e["name"], "type": typ, "affinity": e["affinity"],
                        "tags": e["tags"], "images": e["images"][:1], "description": _short(e["description"]), "why": e["why"]})
    return out


# --------------------------------------------------------------------------- entry point

def build_brief(entity_ids: Iterable[Any], location: str = "New York City", *, tag_why: bool = True,
                skipped_inputs: list[dict] | None = None, catalog: dict[str, str] | list[str] | None = None) -> dict:
    """Build the design brief (see module docstring and docs/contracts.md "brief.json").

    ``entity_ids`` are Qloo ids, or dicts / QlooEntity objects with qloo_id and name (names make the
    ``why`` chains readable; ids without a known name fall back to the id).
    """
    entities = [_norm_entity(e) for e in entity_ids][:MAX_SIGNALS]
    if not entities:
        raise ValueError("build_brief needs at least one Qloo entity")
    names = {e["qloo_id"]: e["name"] for e in entities}
    for i, n in names.items():
        qloo.remember(i, n)
    ids = _ids(entities)
    events.progress(0.03, "Reading the decor styles behind your taste")           # progress hooks: no-ops without a job sink
    taste = taste_analysis(entities, names, with_why=tag_why)
    decor = taste["decor"]
    events.progress(0.35, "Ranking home brands for your taste")
    brands, unresolved = brand_ranking(ids, names, catalog)
    events.progress(0.5, "Finding places with interiors you would love")
    places = inspiration_places(ids, decor, location, names)
    events.progress(0.72, f"Finding shops near {location}")
    shops = local_shops(ids, decor, location, names)
    events.progress(0.9, "Finding films, music and books you might love")
    return {
        "location": location,
        "inputs": [{k: e[k] for k in ("qloo_id", "name", "type")} for e in entities],
        "skipped_inputs": skipped_inputs or [],
        "taste": taste,
        "inspiration_places": places,
        "local_shops": shops,
        "brands": brands,
        "brands_unresolved": unresolved,
        "discovery": discovery(ids, names),
        "qloo": {"network_calls": qloo.calls_used(),
                 "filters": {"landmarks_excluded": LANDMARK_TAGS, "quick_service_excluded": QUICK_SERVICE_TAGS,
                             "big_box_excluded": BIG_BOX_TAGS, "retail_noise_excluded": RETAIL_NOISE_TAGS,
                             "inspiration_categories": INSPIRATION_CATEGORIES, "shop_categories": SHOP_CATEGORIES}},
    }

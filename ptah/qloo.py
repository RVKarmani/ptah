"""STUB Qloo tools. Replace the bodies with a real Qloo API client later.

Both tools share an in-memory cache and a per-run call cap. Cache hits do not count toward the cap.
If QLOO_API_KEY is set we raise NotImplementedError on purpose, so nobody mistakes stub output
for real data once a key is configured.
"""
from __future__ import annotations

import hashlib
import json
import os

DEFAULT_CALL_CAP = 60

_cache: dict[str, object] = {}
_state = {"calls": 0, "cap": DEFAULT_CALL_CAP}


def reset(cap: int = DEFAULT_CALL_CAP) -> None:
    """Clear the cache and call counter and set the per-run call cap."""
    _cache.clear()
    _state["calls"] = 0
    _state["cap"] = cap


def calls_used() -> int:
    return _state["calls"]


def _guarded(tool: str, args: dict, fn):
    if os.environ.get("QLOO_API_KEY"):
        raise NotImplementedError("real Qloo client not yet implemented")
    key = tool + ":" + json.dumps(args, sort_keys=True)
    if key in _cache:
        return _cache[key]
    if _state["calls"] >= _state["cap"]:
        return [{"error": f"Qloo call cap reached ({_state['cap']} calls per run); stop calling Qloo tools "
                          "and finalize with what you have.", "stub": True}]
    _state["calls"] += 1
    result = fn()
    _cache[key] = result
    return result


def _fake_id(name: str, etype: str) -> str:
    return "stub_" + hashlib.sha1(f"{etype}:{name.lower()}".encode()).hexdigest()[:10]


def _guess_type(name: str, type_hint: str) -> str:
    if type_hint:
        return type_hint.lower()
    return "entity"


def qloo_search(name: str, type_hint: str = "") -> list[dict]:
    """Look up a named entity in the Qloo taste graph and return matching canonical entities.

    Use this to verify that a candidate (artist, film, brand, designer, place, ...) exists in Qloo
    and to obtain its qloo_id BEFORE including it in the profile. Only entities returned here
    should be put in the final profile.

    Args:
        name: The entity name to look up, exactly as it appears in the data (a film, artist, brand, place...).
        type_hint: Optional entity type to narrow the search: artist, movie, tv_show, book,
            brand, place, designer, podcast, game. Empty string searches all types.

    Returns:
        A list of dicts with keys qloo_id, name, type, score (0-1 match quality). Empty list if
        there is no match. May return a single dict with an "error" key if the call budget is spent.
        (Real behaviour: Qloo entity search by text, best matches first. Current output is a stub.)
    """
    def run():
        etype = _guess_type(name, type_hint)
        return [
            {"qloo_id": _fake_id(name, etype), "name": name.strip().title(), "type": etype,
             "score": 0.9, "stub": True},
            {"qloo_id": _fake_id(name + " (alt)", etype), "name": name.strip().title() + " (related)",
             "type": etype, "score": 0.4, "stub": True},
        ]
    return _guarded("search", {"name": name, "type_hint": type_hint}, run)




def qloo_related(entity_ids: list[str], domain: str) -> list[dict]:
    """Expand from known Qloo entities to related entities in another domain via the taste graph.

    Use this after verifying a few strong entities with qloo_search, to discover brands, designers,
    places or aesthetics that people with the same taste tend to like, especially ones relevant
    to interiors and room furnishing (domain "brand" or "designer").

    Args:
        entity_ids: Qloo ids (from qloo_search) to use as taste signals. Keep to ~1-5 ids.
        domain: Target entity type to return: brand, designer, place, movie, artist, book, ...

    Returns:
        A list of dicts with keys qloo_id, name, type, affinity (0-1). Empty list if nothing
        found. May return a single dict with an "error" key if the call budget is spent.
        (Real behaviour: Qloo insights/recommendations seeded by the given entities. Current
        output is a stub.)
    """
    def run():
        dom = (domain or "brand").lower()
        # Neutral placeholders only: stub output must never look like a real recommendation.
        picks = [f"STUB {dom} {i + 1}" for i in range(3)]
        return [{"qloo_id": _fake_id(n, dom), "name": n, "type": dom,
                 "affinity": round(0.8 - 0.1 * i, 2), "stub": True} for i, n in enumerate(picks)]
    return _guarded("related", {"entity_ids": sorted(entity_ids), "domain": domain}, run)

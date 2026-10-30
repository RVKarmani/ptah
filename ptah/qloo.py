"""Qloo taste-graph client and the LLM-facing Qloo tools.

Real client for https://hackathon.api.qloo.com (GET only, ``X-Api-Key`` header):

* ``/search``      -> entity lookup by name            (``search`` / ``qloo_search``)
* ``/v2/insights`` -> recommendations + taste analysis (``insights`` / ``qloo_related`` / ``taste_tags``)
* ``/v2/tags``     -> tag lookup by keyword            (``tags``)

Behaviour shared by every call:

* Key comes from the ``QLOO_API_KEY`` environment variable, else a ``.env`` file in the project
  root. It is only ever sent as a request header and is never logged, cached or put in errors.
* Responses are cached on disk under ``cache/qloo/`` (git-ignored, private: Qloo's terms forbid
  publishing response data). The cache key is a hash of path + sorted query params; there is no TTL.
* A per-run cap limits *network* calls (``reset(cap)``); cache hits are free.
* 429 / 5xx / network errors are retried with exponential backoff.

If no key is configured the three LLM tools return clearly marked stubs (``"stub": True``) so a
dry run still works; nothing else in this module fakes data.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("ptah.qloo")

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.environ.get("QLOO_BASE_URL", "https://hackathon.api.qloo.com").rstrip("/")
DEFAULT_CALL_CAP = 60
TIMEOUT_S = 30
MAX_ATTEMPTS = 4
BACKOFF_S = 1.0

# Short names the LLM / CLI use -> Qloo URNs. "designer" has no Qloo type of its own: designers are
# people, so it maps to urn:entity:person.
ENTITY_TYPES = {
    "artist": "urn:entity:artist", "book": "urn:entity:book", "brand": "urn:entity:brand",
    "destination": "urn:entity:destination", "movie": "urn:entity:movie", "person": "urn:entity:person",
    "designer": "urn:entity:person", "place": "urn:entity:place", "podcast": "urn:entity:podcast",
    "tv_show": "urn:entity:tv_show", "video_game": "urn:entity:video_game",
    "videogame": "urn:entity:video_game",
}
# /search also accepts legacy types that /v2/insights does not (e.g. actor, author, director).
SEARCH_ONLY_TYPES = {"actor": "urn:entity:actor", "author": "urn:entity:author",
                     "director": "urn:entity:director", "album": "urn:entity:album"}
TAG_KINDS = {"decor": "urn:tag:decor:qloo", "ambience": "urn:tag:ambience:qloo"}

_state = {"calls": 0, "cap": DEFAULT_CALL_CAP}
_names: dict[str, str] = {}  # entity_id (upper) -> canonical name, filled by search/insights, used for why-chains


class QlooError(RuntimeError):
    """A Qloo request failed (message never contains the API key)."""


class CallCapReached(QlooError):
    """The per-run network call cap was hit."""


# --------------------------------------------------------------------------- config / state

def reset(cap: int = DEFAULT_CALL_CAP) -> None:
    """Reset the per-run network call counter and name registry and set the call cap."""
    _state["calls"] = 0
    _state["cap"] = cap
    _names.clear()


def calls_used() -> int:
    """Network calls made since the last reset() (cache hits are not counted)."""
    return _state["calls"]


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def api_key() -> str | None:
    """The Qloo API key from the environment or the project's .env file (None if unset)."""
    return os.environ.get("QLOO_API_KEY") or _load_dotenv(ROOT / ".env").get("QLOO_API_KEY") or None


def is_stub() -> bool:
    """True when no API key is configured, so the LLM tools return marked stubs."""
    return not api_key()


def cache_dir() -> Path:
    return Path(os.environ.get("PTAH_QLOO_CACHE_DIR") or ROOT / "cache" / "qloo")


# --------------------------------------------------------------------------- transport + cache

def _cache_path(path: str, params: dict) -> Path:
    canon = path + "?" + urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
    return cache_dir() / (hashlib.sha256(canon.encode()).hexdigest() + ".json")


def _http_get(url: str, key: str) -> tuple[int, str, dict]:
    """One GET. Returns (status, body, headers). Overridden in tests."""
    req = urllib.request.Request(url, headers={"X-Api-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})


def get(path: str, params: dict[str, Any]) -> dict:
    """GET ``path`` with query ``params`` -> parsed JSON, via disk cache, retries and the call cap."""
    params = {k: v for k, v in params.items() if v is not None and v != ""}
    cp = _cache_path(path, params)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError):
            pass  # corrupt cache entry: refetch
    key = api_key()
    if not key:
        raise QlooError("QLOO_API_KEY is not set")
    if _state["calls"] >= _state["cap"]:
        raise CallCapReached(f"Qloo call cap reached ({_state['cap']} network calls per run)")
    _state["calls"] += 1
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    last = ""
    for attempt in range(MAX_ATTEMPTS):
        log.debug("GET %s?%s (attempt %d)", path, urllib.parse.urlencode(params), attempt + 1)
        try:
            status, body, headers = _http_get(url, key)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status, body, headers = 0, "", {}
            last = f"network error: {type(e).__name__}"
        else:
            last = f"HTTP {status}"
        if status == 200:
            try:
                data = json.loads(body)
            except ValueError as e:
                raise QlooError(f"{path}: invalid JSON in response") from e
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"path": path, "params": params, "data": data}), encoding="utf-8")
            return data
        if status in (0, 429) or status >= 500:
            if attempt + 1 < MAX_ATTEMPTS:
                try:
                    wait = float(headers.get("Retry-After", ""))
                except ValueError:
                    wait = BACKOFF_S * (2 ** attempt)
                time.sleep(min(wait, 30))
            continue
        raise QlooError(f"{path}: HTTP {status}: {body[:200]!r}")
    raise QlooError(f"{path}: failed after {MAX_ATTEMPTS} attempts ({last})")


# --------------------------------------------------------------------------- parsing helpers

def short_type(urn_or_list: Any) -> str:
    """'urn:entity:place' -> 'place'; accepts a list (first element)."""
    if isinstance(urn_or_list, list):
        urn_or_list = urn_or_list[0] if urn_or_list else ""
    return str(urn_or_list or "").rsplit(":", 1)[-1]


def type_urn(t: str) -> str:
    """Resolve a short type name (artist, brand, place, ...) or URN to an ``urn:entity:*`` URN."""
    t = (t or "").strip()
    if t.startswith("urn:entity:"):
        return t
    low = t.lower().replace(" ", "_")
    if low in ENTITY_TYPES:
        return ENTITY_TYPES[low]
    if low in SEARCH_ONLY_TYPES:
        return SEARCH_ONLY_TYPES[low]
    raise QlooError(f"unknown entity type {t!r}; use one of {sorted(ENTITY_TYPES)}")


def name_similarity(a: str, b: str) -> float:
    """Case/punctuation-insensitive name match quality 0-1."""
    def norm(s: str) -> str:
        return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 3)


def remember(entity_id: str, name: str) -> None:
    if entity_id and name:
        _names[entity_id.upper()] = name


def name_of(entity_id: str) -> str:
    return _names.get(entity_id.upper(), entity_id)


def tag_names(tags: list[dict] | None, prefixes: tuple[str, ...] = (), limit: int = 5) -> list[str]:
    """Names of an entity's tags, optionally only ids starting with one of ``prefixes``."""
    out = []
    for t in tags or []:
        tid = t.get("id") or t.get("tag_id") or ""
        if (not prefixes or tid.startswith(prefixes)) and t.get("name") and t["name"] not in out:
            out.append(t["name"])
        if len(out) >= limit:
            break
    return out


def why_chain(explainability: dict | None, names: dict[str, str] | None = None, top: int = 3) -> list[dict]:
    """Per-result explainability -> the input entities that contributed most.

    ``explainability`` is ``result["query"]["explainability"]``: {"signal.interests.entities":
    [{"entity_id", "score"}, ...]}. Returns [{"qloo_id", "name", "score"}] best first.
    """
    rows = (explainability or {}).get("signal.interests.entities") or []
    lookup = {k.upper(): v for k, v in (names or {}).items()}
    out = []
    for r in sorted(rows, key=lambda r: -float(r.get("score") or 0)):
        eid = r.get("entity_id") or ""
        out.append({"qloo_id": eid, "name": lookup.get(eid.upper()) or name_of(eid),
                    "score": round(float(r.get("score") or 0), 3)})
    return out[:top]


def parse_entity(raw: dict, names: dict[str, str] | None = None) -> dict:
    """One /v2/insights entity -> rich normalized dict (used by the brief)."""
    props = raw.get("properties") or {}
    q = raw.get("query") or {}
    tags = raw.get("tags") or []
    eid = raw.get("entity_id") or ""
    remember(eid, raw.get("name") or "")
    images = [i["url"] for i in (props.get("images") or []) if isinstance(i, dict) and i.get("url")]
    if not images and isinstance(props.get("image"), dict) and props["image"].get("url"):
        images = [props["image"]["url"]]
    genre = props.get("primary_genre") or {}
    return {
        "qloo_id": eid,
        "name": raw.get("name"),
        "type": short_type(raw.get("subtype") or raw.get("types")),
        "affinity": round(float(q["affinity"]), 3) if q.get("affinity") is not None else None,
        "popularity": round(float(raw["popularity"]), 3) if raw.get("popularity") is not None else None,
        "address": props.get("address"),
        "neighborhood": props.get("neighborhood") or (props.get("geocode") or {}).get("name"),
        "website": props.get("website"),
        "description": props.get("description"),
        "images": images,
        "category": genre.get("name") if isinstance(genre, dict) else None,
        "decor_tags": tag_names(tags, ("urn:tag:decor:",), 6),
        "ambience_tags": tag_names(tags, ("urn:tag:ambience:",), 6),
        "tags": tag_names(tags, (), 6),
        "tag_ids": [t.get("id") or t.get("tag_id") for t in tags if (t.get("id") or t.get("tag_id"))],
        "why": why_chain(q.get("explainability"), names),
    }


def compact(entity: dict) -> dict:
    """Rich entity -> the small dict the LLM tools return."""
    out = {"qloo_id": entity["qloo_id"], "name": entity["name"], "type": entity["type"],
           "affinity": entity["affinity"], "tags": entity["tags"][:5],
           "why": [{"name": w["name"], "score": w["score"]} for w in entity["why"]]}
    if entity.get("address"):
        out["address"] = entity["address"]
    return out


# --------------------------------------------------------------------------- endpoint wrappers

def search(name: str, type_hint: str = "", take: int = 5) -> list[dict]:
    """/search -> normalized candidates [{qloo_id, name, type, score, popularity, tags, description}].

    ``score`` is a local 0-1 name-match quality (Qloo returns no match score); order is Qloo's.
    """
    params: dict[str, Any] = {"query": name.strip(), "take": take}
    if type_hint:
        params["types"] = type_urn(type_hint)
    data = get("/search", params)
    out = []
    for r in data.get("results") or []:
        eid = r.get("entity_id") or ""
        remember(eid, r.get("name") or "")
        props = r.get("properties") or {}
        out.append({
            "qloo_id": eid, "name": r.get("name"), "type": short_type(r.get("types")),
            "score": name_similarity(name, r.get("name") or ""),
            "popularity": round(float(r["popularity"]), 3) if r.get("popularity") is not None else None,
            "tags": tag_names(r.get("tags"), (), 5),
            "description": props.get("short_description") or props.get("description"),
        })
    return out


def insights(filter_type: str, entity_ids: list[str] | None = None, *, take: int = 20,
             explain: bool = True, extra: dict[str, Any] | None = None) -> dict:
    """Raw /v2/insights call. ``filter_type`` is a short name or URN ('place', 'urn:tag', ...)."""
    params: dict[str, Any] = {
        "filter.type": filter_type if filter_type.startswith("urn:") and not filter_type.startswith("urn:entity:")
        else type_urn(filter_type),
        "take": take,
    }
    if entity_ids:
        params["signal.interests.entities"] = ",".join(entity_ids)
    if explain:
        params["feature.explainability"] = "true"
    params.update(extra or {})
    return get("/v2/insights", params)


def insights_entities(filter_type: str, entity_ids: list[str] | None = None, *, take: int = 20,
                      extra: dict[str, Any] | None = None, names: dict[str, str] | None = None) -> list[dict]:
    """/v2/insights -> list of rich normalized entities (``parse_entity``), best affinity first."""
    data = insights(filter_type, entity_ids, take=take, extra=extra)
    return [parse_entity(r, names) for r in (data.get("results") or {}).get("entities") or []]


def taste_tags(entity_ids: list[str], kind: str = "decor", *, take: int = 10,
               parents: str = "urn:entity:place") -> list[dict]:
    """Taste analysis: tags of one namespace that best describe the given entities.

    filter.type=urn:tag + filter.tag.types=<namespace> + filter.parents.types=<parents> +
    signal.interests.entities. Returns [{tag_id, name, affinity, popularity}] best first.
    """
    ns = TAG_KINDS.get(kind, kind)
    data = insights("urn:tag", entity_ids, take=take, explain=False, extra={
        "filter.tag.types": ns, "filter.parents.types": parents})
    out = []
    for t in (data.get("results") or {}).get("tags") or []:
        aff = (t.get("query") or {}).get("affinity")
        out.append({"tag_id": t.get("tag_id"), "name": t.get("name"),
                    "affinity": round(float(aff), 3) if aff is not None else None,
                    "popularity": round(float(t["popularity"]), 3) if t.get("popularity") is not None else None})
    return out


def tags(query: str, *, take: int = 10, types: str = "") -> list[dict]:
    """/v2/tags keyword lookup -> [{tag_id, name, types}] (use to find valid tag ids for filter.tags)."""
    data = get("/v2/tags", {"filter.query": query, "take": take, "filter.tag.types": types})
    return [{"tag_id": t.get("id") or t.get("tag_id"), "name": t.get("name"), "popularity": t.get("popularity"),
             "types": [p.get("type") for p in t.get("parents") or []]}
            for t in (data.get("results") or {}).get("tags") or []]


# --------------------------------------------------------------------------- LLM tools

def _fake_id(name: str, etype: str) -> str:
    return "stub_" + hashlib.sha1(f"{etype}:{name.lower()}".encode()).hexdigest()[:10]


def _guard(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except CallCapReached as e:
        return [{"error": f"{e}; stop calling Qloo tools and finalize with what you have."}]
    except QlooError as e:
        return [{"error": str(e)}]


def qloo_search(name: str, type_hint: str = "") -> list[dict]:
    """Look up a named entity in the Qloo taste graph and return matching canonical entities.

    Use this to verify that a candidate (artist, film, brand, designer, place, ...) exists in Qloo
    and to obtain its qloo_id BEFORE including it in the profile. Only entities returned here
    should be put in the final profile. Check that the returned name really is the thing you meant
    (score is name-match quality; a low score means Qloo only found something loosely similar).

    Args:
        name: The entity name to look up, exactly as it appears in the data (a film, artist, brand, place...).
        type_hint: Optional entity type to narrow the search: artist, movie, tv_show, book,
            brand, place, designer (people), podcast, video_game, actor, author, director.
            Empty string searches all types.

    Returns:
        Up to 5 dicts with keys qloo_id, name, type, score (0-1 name-match quality), popularity
        (0-1), tags (top tag names), description. Best Qloo match first. Empty list if there is no
        match. May return a single dict with an "error" key (call budget spent, API error).
    """
    if is_stub():
        etype = (type_hint or "entity").lower()
        return [{"qloo_id": _fake_id(name, etype), "name": name.strip().title(), "type": etype,
                 "score": 0.9, "stub": True, "note": "STUB: no QLOO_API_KEY configured"}]
    return _guard(lambda: search(name, type_hint))


def qloo_related(entity_ids: list[str], domain: str) -> list[dict]:
    """Expand from known Qloo entities to related entities in another domain via the taste graph.

    Use this after verifying a few strong entities with qloo_search, to discover brands, designers,
    places or other media that people with the same taste tend to like, especially ones relevant
    to interiors and room furnishing (domain "brand" or "place").

    Args:
        entity_ids: Qloo ids (from qloo_search) to use as taste signals. Keep to ~1-10 ids.
        domain: Target entity type to return: brand, designer (people), place, movie, tv_show,
            artist, book, podcast, video_game, destination.

    Returns:
        Up to 15 dicts with keys qloo_id, name, type, affinity (0-1 relative score, not a
        probability), tags (top tag names), why (the input entities that contributed most, as
        name + score) and, for places, address. Empty list if nothing found. May return a single
        dict with an "error" key (call budget spent, API error).
    """
    if is_stub():
        dom = (domain or "brand").lower()
        return [{"qloo_id": _fake_id(f"STUB {dom} {i + 1}", dom), "name": f"STUB {dom} {i + 1}", "type": dom,
                 "affinity": round(0.8 - 0.1 * i, 2), "stub": True, "note": "STUB: no QLOO_API_KEY configured"}
                for i in range(3)]
    return _guard(lambda: [compact(e) for e in insights_entities(domain, list(entity_ids), take=15)])


def qloo_taste_tags(entity_ids: list[str], kind: str = "decor") -> list[dict]:
    """Describe what interiors a person's taste maps to: interior decor or ambience tags for given entities.

    This is Qloo taste analysis: it returns the tags (as used on places such as restaurants, hotels
    and shops) that best describe the taste of the given entities, e.g. decor styles like
    "Rustic" or "Contemporary", or ambiences like "Cozy". Use it on the person's strongest
    verified entities to ground the style_summary and aesthetic_tags in data.

    Args:
        entity_ids: Qloo ids (from qloo_search) forming the taste signal. Keep to ~1-10 ids.
        kind: "decor" (visual/interior style tags) or "ambience" (mood tags).

    Returns:
        Up to 10 dicts with keys tag_id, name, affinity (0-1 relative score), popularity (0-1),
        best first. May return a single dict with an "error" key (call budget spent, API error).
    """
    if is_stub():
        return [{"tag_id": f"stub:{kind}:{i}", "name": f"STUB {kind} {i + 1}", "affinity": round(0.8 - 0.1 * i, 2),
                 "stub": True, "note": "STUB: no QLOO_API_KEY configured"} for i in range(3)]
    return _guard(lambda: taste_tags(list(entity_ids), kind))

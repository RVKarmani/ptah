"""Synthetic Qloo transport for tests: minimal JSON in the right shape, no network, no real data."""
from __future__ import annotations

import json
import urllib.parse

KEY = "test-key-SECRET-9f3a"


def eid(name: str) -> str:
    return "ID-" + "".join(c for c in name.upper() if c.isalnum())


def _tag(tid: str, name: str, **kw) -> dict:
    return {"id": tid, "name": name, "type": "x", "weight": None, **kw}


class FakeQloo:
    """Callable replacing ptah.qloo._http_get. Records (path, params, key) for every request."""

    def __init__(self):
        self.requests: list[tuple[str, dict, str]] = []
        self.search_results: dict[str, list[str]] = {}   # query -> names returned (default: [query])
        self.statuses: list[int] = []                     # popped one per request before normal handling

    def __call__(self, url: str, key: str):
        parsed = urllib.parse.urlparse(url)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        self.requests.append((parsed.path, params, key))
        if self.statuses:
            st = self.statuses.pop(0)
            if st != 200:
                return st, '{"error":"nope"}', {}
        if parsed.path == "/search":
            q = params["query"]
            names = self.search_results.get(q, [q])
            res = [{"name": n, "entity_id": eid(n), "types": [params.get("types", "urn:entity:artist")],
                    "popularity": 0.5, "tags": [{"name": "T", "tag_id": "urn:tag:genre:t"}],
                    "properties": {"short_description": "d"}} for n in names]
            return 200, json.dumps({"results": res}), {}
        if parsed.path == "/v2/tags":
            return 200, json.dumps({"results": {"tags": [
                {"id": "urn:tag:category:place:x", "name": "X", "parents": [{"type": "urn:entity:place"}]}]}}), {}
        if parsed.path == "/v2/insights":
            return 200, json.dumps(self.insights(params)), {}
        return 404, "{}", {}

    # ------------------------------------------------------------------
    def insights(self, p: dict) -> dict:
        sig = [s for s in p.get("signal.interests.entities", "").split(",") if s]
        if p["filter.type"] == "urn:tag":
            ns = p["filter.tag.types"]
            # single-entity analyses favour that entity's own tag; group analyses return two tags
            tags = [("a", 0.9), ("b", 0.6)] if len(sig) != 1 else [("a", 0.5 + 0.1 * (hash(sig[0]) % 3)), ("b", 0.2)]
            return {"success": True, "results": {"tags": [
                {"tag_id": f"{ns}:{n}", "name": n.upper(), "types": ["urn:entity:place"], "popularity": 0.9,
                 "query": {"affinity": a}} for n, a in tags]}}
        ents = []
        wanted = [x for x in p.get("filter.results.entities", "").split(",") if x]
        for i in range(3):
            name = f"{p['filter.type'].rsplit(':', 1)[-1]}-{''.join(c for c in p.get('filter.tags', 'none') if c.isalnum())}-{i}"
            ents.append(self.entity(name, 0.9 - 0.1 * i, sig))
        if wanted:
            ents = [self.entity("brand-" + w, 0.8 - 0.01 * i, sig, entity_id=w) for i, w in enumerate(wanted)]
        return {"success": True, "results": {"entities": ents}}

    @staticmethod
    def entity(name: str, aff: float, sig: list[str], entity_id: str | None = None) -> dict:
        return {
            "name": name, "entity_id": entity_id or eid(name), "subtype": "urn:entity:place", "popularity": 0.7,
            "properties": {"address": "1 Test St", "images": [{"url": "https://img.example/x.jpg"}],
                           "neighborhood": "Testville", "primary_genre": {"name": "Hotel"}},
            "tags": [_tag("urn:tag:decor:qloo:a", "A"), _tag("urn:tag:ambience:qloo:cozy", "Cozy"),
                     _tag("urn:tag:genre:place:hotel", "Hotel")],
            "query": {"affinity": aff, "explainability": {"signal.interests.entities": [
                {"entity_id": s, "score": 0.3 - 0.05 * i} for i, s in enumerate(sig)]}},
        }

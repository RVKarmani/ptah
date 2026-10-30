"""SerpApi Google Shopping client: on-disk cache, search budget, key hygiene.

* Every request is cached under ``cache/serpapi/`` keyed by a hash of its parameters (the API key is
  never part of the key and never written anywhere; response bodies are scrubbed of it).
* A budget counter (``cache/serpapi/_budget.json``) counts only LIVE calls: per calendar month (hard cap
  ``MONTHLY_CAP``, the free plan is 250) and per client instance (``max_searches``).  A cache hit is free.
* Errors raise :class:`SerpApiError` (message scrubbed of the key); "no results" is a normal empty response.

Search results and downloaded images are private working data (cache/, data/): never commit them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..env import ROOT, load_dotenv

ENDPOINT = "https://serpapi.com/search.json"
CACHE_DIR = ROOT / "cache" / "serpapi"
MONTHLY_CAP = 250          # SerpApi free plan
SECRET_KEYS = {"api_key", "apikey", "key"}


class SerpApiError(RuntimeError):
    pass


class BudgetExceeded(SerpApiError):
    pass


def _scrub(obj: Any, secret: Optional[str]) -> Any:
    """Recursively drop key-ish fields and replace any occurrence of the secret."""
    if isinstance(obj, dict):
        return {k: _scrub(v, secret) for k, v in obj.items() if str(k).lower() not in SECRET_KEYS}
    if isinstance(obj, list):
        return [_scrub(v, secret) for v in obj]
    if isinstance(obj, str):
        s = obj
        if secret and secret in s:
            s = s.replace(secret, "***")
        return re.sub(r"([?&]api_key=)[^&\s\"']+", r"\1***", s)
    return obj


def request_hash(params: Dict[str, Any]) -> str:
    clean = {k: params[k] for k in sorted(params) if str(k).lower() not in SECRET_KEYS and params[k] is not None}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


Transport = Callable[[str, Dict[str, Any]], Tuple[int, Any]]


def _http_transport(url: str, params: Dict[str, Any]) -> Tuple[int, Any]:
    import requests  # lazy: tests inject a fake transport
    r = requests.get(url, params=params, timeout=60)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"error": f"non-JSON response (HTTP {r.status_code})"}


class SerpApiClient:
    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None, max_searches: Optional[int] = None,
                 monthly_cap: int = MONTHLY_CAP, transport: Optional[Transport] = None, verbose: bool = False):
        if api_key is None:
            load_dotenv()
            api_key = os.environ.get("SERPAPI_KEY")
        self._key = api_key
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.max_searches = max_searches
        self.monthly_cap = monthly_cap
        self.transport = transport or _http_transport
        self.verbose = verbose
        self.live_calls = 0     # live calls made by this instance
        self.cache_hits = 0

    # ------------------------------------------------------------------ budget
    @property
    def budget_path(self) -> Path:
        return self.cache_dir / "_budget.json"

    def _month(self) -> str:
        return time.strftime("%Y-%m", time.gmtime())

    def _budget(self) -> Dict[str, Any]:
        try:
            b = json.loads(self.budget_path.read_text())
        except (OSError, ValueError):
            b = {}
        if b.get("month") != self._month():
            b = {"month": self._month(), "used": 0, "log": []}
        return b

    def used_this_month(self) -> int:
        return int(self._budget().get("used", 0))

    def remaining(self) -> int:
        left = self.monthly_cap - self.used_this_month()
        if self.max_searches is not None:
            left = min(left, self.max_searches - self.live_calls)
        return max(0, left)

    def _charge(self, h: str, q: str) -> None:
        b = self._budget()
        b["used"] = int(b.get("used", 0)) + 1
        b.setdefault("log", []).append({"t": int(time.time()), "hash": h, "q": q})
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.budget_path.write_text(json.dumps(b, indent=1))

    # ------------------------------------------------------------------ cache
    def _cache_path(self, h: str) -> Path:
        return self.cache_dir / f"{h}.json"

    def cached(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        p = self._cache_path(request_hash(params))
        if p.exists():
            try:
                return json.loads(p.read_text())["response"]
            except (OSError, ValueError, KeyError):
                return None
        return None

    # ------------------------------------------------------------------ requests
    def _request(self, params: Dict[str, Any], label: str) -> Dict[str, Any]:
        h = request_hash(params)
        hit = self.cached(params)
        if hit is not None:
            self.cache_hits += 1
            return hit
        if self.remaining() <= 0:
            raise BudgetExceeded(f"SerpApi budget exhausted (instance cap {self.max_searches}, "
                                 f"month {self.used_this_month()}/{self.monthly_cap}); not calling for {label!r}")
        if not self._key:
            raise SerpApiError("SERPAPI_KEY is not set (put it in .env)")
        send = dict(params, api_key=self._key)
        try:
            status, body = self.transport(ENDPOINT, send)
        except Exception as e:  # network errors: scrub, never leak the key through a URL in the message
            raise SerpApiError(f"SerpApi request failed: {_scrub(str(e), self._key)}") from None
        self.live_calls += 1
        self._charge(h, label)
        body = _scrub(body, self._key)
        if status != 200 and not (isinstance(body, dict) and "error" in body):
            raise SerpApiError(f"SerpApi HTTP {status}")
        err = body.get("error") if isinstance(body, dict) else None
        if err and "hasn't returned any results" not in str(err).lower() and "no results" not in str(err).lower():
            raise SerpApiError(f"SerpApi error: {err}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(h).write_text(json.dumps({"params": _scrub(params, self._key), "t": int(time.time()), "response": body}))
        if self.verbose:
            print(f"  [serpapi] live call {self.live_calls} ({self.used_this_month()}/{self.monthly_cap} this month): {label}")
        return body

    def search(self, q: str, *, gl: str = "us", hl: str = "en", min_price: Optional[float] = None, max_price: Optional[float] = None,
               sort_by: Optional[int] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Google Shopping search -> response dict (``shopping_results`` list).  Cached; live calls are budgeted."""
        params: Dict[str, Any] = {"engine": "google_shopping", "q": q, "gl": gl, "hl": hl, "google_domain": "google.com"}
        if min_price is not None:
            params["min_price"] = int(min_price)
        if max_price is not None:
            params["max_price"] = int(max_price)
        if sort_by is not None:
            params["sort_by"] = sort_by
        if extra:
            params.update(extra)
        return self._request(params, q)

    def product_detail(self, page_token: str, more_stores: bool = True) -> Dict[str, Any]:
        """google_immersive_product detail for a result's ``immersive_product_page_token`` (counts toward the budget)."""
        params = {"engine": "google_immersive_product", "page_token": page_token, "more_stores": "true" if more_stores else "false"}
        return self._request(params, "immersive_product")

    @staticmethod
    def results(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = list(resp.get("shopping_results") or [])
        for blk in resp.get("categorized_shopping_results") or []:
            out.extend(blk.get("shopping_results") or [])
        return out

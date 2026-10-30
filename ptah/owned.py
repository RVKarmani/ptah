"""Owned items (things the user already has) and cost helpers.

``items.json`` entries with ``"owned": true`` have no price / buy link, are excluded from every total and are
shown as "Yours" in the viewer.  ``user_colors`` (same shape as ``colors``) overrides the colours extracted from
a photo, key by key.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple


def is_owned(item: dict) -> bool:
    return bool(item.get("owned"))


def effective_colors(item: dict) -> Dict[str, Optional[str]]:
    """``colors`` with ``user_colors`` taking precedence per key (a user colour of None does not erase a photo colour)."""
    out = dict(item.get("colors") or {})
    for k, v in (item.get("user_colors") or {}).items():
        if v:
            out[k] = v
    return out


def parse_price(p) -> Optional[Tuple[str, float]]:
    if p is None:
        return None
    if isinstance(p, (int, float)):
        return "", float(p)
    m = re.match(r"^\s*([^\d.\s]*)\s*([\d.]+)", str(p).replace(",", ""))
    if not m:
        return None
    try:
        return m.group(1), float(m.group(2))
    except ValueError:
        return None


def total_cost(items: Iterable[dict], placed_ids: Optional[Iterable[str]] = None) -> Optional[Dict[str, object]]:
    """Cost of the purchasable items (owned items excluded).  -> {"symbol", "total", "count", "owned"} or None when a
    purchasable item has no parseable price or the currencies differ."""
    ids = None if placed_ids is None else set(placed_ids)
    total, sym, n, owned = 0.0, None, 0, 0
    for it in items:
        if ids is not None and str(it["id"]) not in ids:
            continue
        if is_owned(it):
            owned += 1
            continue
        pp = parse_price(it.get("price"))
        if pp is None:
            return None
        if sym is None:
            sym = pp[0]
        elif sym != pp[0]:
            return None
        total += pp[1]
        n += 1
    return {"symbol": sym or "", "total": round(total, 2), "count": n, "owned": owned}

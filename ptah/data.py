"""Data loading and read-only tools over the ingested Instagram export.

The functions search_posts and posts_with_tag are given to the LLM as tools; the LLM only
sees their docstrings, so keep them accurate.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CAPTION_CUT = 300
MAX_LIMIT = 50


@lru_cache(maxsize=1)
def load_posts() -> list[dict]:
    """Load data/posts.jsonl once (cached)."""
    posts = []
    with open(DATA_DIR / "posts.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                posts.append(json.loads(line))
    return posts


@lru_cache(maxsize=1)
def load_music() -> list[dict]:
    """Load data/music.json once (cached)."""
    with open(DATA_DIR / "music.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_summary() -> dict:
    """Load data/summary.json once (cached)."""
    with open(DATA_DIR / "summary.json", encoding="utf-8") as f:
        return json.load(f)


def _clamp(limit: int, default: int) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, MAX_LIMIT))


def _trim(post: dict) -> dict:
    caption = post.get("caption") or ""
    if len(caption) > CAPTION_CUT:
        caption = caption[:CAPTION_CUT] + "..."
    return {
        "id": post.get("id"),
        "source": post.get("source"),
        "date": post.get("date"),
        "owner": post.get("owner"),
        "caption": caption,
        "hashtags": post.get("hashtags", []),
    }


def search_posts(query: str, limit: int = 20) -> list[dict]:
    """Keyword search over the user's saved/liked Instagram posts.

    Case-insensitive. Every whitespace-separated word in `query` must appear somewhere in the
    post's caption, hashtags, or owner username (AND semantics, substring match).

    Args:
        query: One or more keywords taken from the data (a word, name or phrase you saw in captions).
        limit: Max rows to return (default 20, hard cap 50).

    Returns:
        A list of dicts with keys id, source (saved|liked|both), date, owner, caption
        (truncated to 300 chars), hashtags. Empty list if nothing matches.
    """
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return []
    n = _clamp(limit, 20)
    out = []
    for p in load_posts():
        hay = " ".join([p.get("caption") or "", " ".join(p.get("hashtags") or []), p.get("owner") or ""]).lower()
        if all(t in hay for t in terms):
            out.append(_trim(p))
            if len(out) >= n:
                break
    return out


def posts_with_tag(tag: str, limit: int = 30) -> list[dict]:
    """Return posts that carry a specific hashtag.

    Case-insensitive exact match on the hashtag (a leading '#' is ignored).

    Args:
        tag: The hashtag, with or without '#'.
        limit: Max rows to return (default 30, hard cap 50).

    Returns:
        A list of dicts with keys id, source, date, owner, caption (truncated to 300 chars),
        hashtags. Empty list if no post has the tag.
    """
    t = (tag or "").strip().lstrip("#").lower()
    if not t:
        return []
    n = _clamp(limit, 30)
    out = []
    for p in load_posts():
        if t in [h.lstrip("#").lower() for h in (p.get("hashtags") or [])]:
            out.append(_trim(p))
            if len(out) >= n:
                break
    return out

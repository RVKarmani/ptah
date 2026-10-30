#!/usr/bin/env python3
"""Parse an Instagram data export (JSON format) into RLM-ready files.

Usage:
    python ingest_instagram.py <export folder or .zip> [--out data/]

Reads only taste-relevant files (saved posts, liked posts, saved music) and writes:
    posts.jsonl   one post per line: id, source, date, owner, caption, hashtags, url
    music.json    [{title, artists}]
    summary.json  counts, date range, top hashtags / accounts
"""
import argparse
import collections
import datetime as dt
import json
import re
import sys
import zipfile
from pathlib import Path

# Only these files are ever opened. Contacts, friend map, ad demographics etc. are ignored.
WANTED = {
    "saved_posts": re.compile(r"(^|/)saved_posts(_\d+)?\.json$"),
    "liked_posts": re.compile(r"(^|/)liked_posts(_\d+)?\.json$"),
    "saved_music": re.compile(r"(^|/)saved_music(_\d+)?\.json$"),
}

# Hashtags that say nothing about taste.
GENERIC_TAGS = {
    "fyp", "foryou", "foryoupage", "fy", "fypage", "viral", "viralvideo", "viralreels", "viralpost",
    "explore", "explorepage", "explorepage✨", "exploremore", "trending", "trend", "trendingreels",
    "reels", "reel", "reelsinstagram", "reelitfeelit", "instareels", "instagood", "instagram", "insta",
    "instadaily", "reelsvideo", "reelsviral", "funnyreels", "video", "videos", "follow", "followme",
    "like", "likes", "likeforlikes", "share", "love", "photooftheday", "picoftheday", "tiktok",
    "relatable", "foryourpage", "xyzbca", "contentcreator", "creator",
}


def fix_text(s):
    """Meta exports store UTF-8 bytes as Latin-1 code points; undo that."""
    if not isinstance(s, str):
        return ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def load_files(src: Path):
    """Yield (kind, parsed_json) for every wanted file in a folder or zip."""
    if src.is_file() and src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            for name in sorted(z.namelist()):
                for kind, rx in WANTED.items():
                    if rx.search(name):
                        yield kind, json.loads(z.read(name))
    elif src.is_dir():
        for path in sorted(src.rglob("*.json")):
            rel = path.relative_to(src).as_posix()
            for kind, rx in WANTED.items():
                if rx.search(rel):
                    yield kind, json.loads(path.read_text(encoding="utf-8"))
    else:
        sys.exit(f"Not a folder or .zip: {src}")


def shortcode(url):
    m = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([^/?#]+)", url or "")
    return m.group(1) if m else (url or None)


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat() if ts else None


def clean_tag(t):
    t = fix_text(t).strip().lstrip("#").lower()
    if not t or t in GENERIC_TAGS or re.fullmatch(r"f+y+p+\w*", t):
        return None
    return t


def parse_post_new(item):
    """Current export format: {timestamp, label_values:[...]}."""
    post = {"url": None, "owner": None, "captions": [], "hashtags": [], "ts": item.get("timestamp")}
    for lv in item.get("label_values", []):
        label, title = lv.get("label"), lv.get("title")
        if label == "URL" and not post["url"]:
            post["url"] = lv.get("href") or lv.get("value")
        elif label == "Caption":
            c = fix_text(lv.get("value", "")).strip()
            if c and c not in post["captions"]:
                post["captions"].append(c)
        elif title == "Hashtags":
            for entry in lv.get("dict", []):
                for d in entry.get("dict", []):
                    if d.get("label") == "Name":
                        post["hashtags"].append(d.get("value", ""))
        elif title == "Owner":
            for entry in lv.get("dict", []):
                for d in entry.get("dict", []):
                    if d.get("label") == "Username" and d.get("value"):
                        post["owner"] = fix_text(d["value"])
    return post


def parse_post_old(item):
    """Older export format: {title: owner, string_map_data / string_list_data}."""
    data = item.get("string_list_data") or list((item.get("string_map_data") or {}).values())
    first = data[0] if data else {}
    return {"url": first.get("href"), "owner": fix_text(item.get("title")) or None,
            "captions": [], "hashtags": [], "ts": first.get("timestamp")}


def iter_items(raw, old_keys):
    if isinstance(raw, list):
        for item in raw:
            yield parse_post_new(item)
    elif isinstance(raw, dict):
        for key in old_keys:
            for item in raw.get(key, []):
                yield parse_post_old(item)


def parse_music(raw):
    items = raw if isinstance(raw, list) else []
    out = []
    for item in items:
        fields = {lv.get("label"): lv.get("value") for lv in item.get("label_values", [])}
        title = fix_text(fields.get("Title", "")).strip()
        artists = [a.strip() for a in fix_text(fields.get("Artist", "")).split(",") if a.strip()]
        if title or artists:
            out.append({"title": title, "artists": artists})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", type=Path, help="Instagram export folder or .zip (JSON format)")
    ap.add_argument("--out", type=Path, default=Path("data"), help="output folder (default: data/)")
    args = ap.parse_args()

    posts = {}  # key -> post record
    music, found = [], collections.Counter()
    raw_counts = collections.Counter()

    for kind, raw in load_files(args.export):
        found[kind] += 1
        if kind == "saved_music":
            music.extend(parse_music(raw))
            continue
        source = "saved" if kind == "saved_posts" else "liked"
        old_keys = ["saved_saved_media"] if source == "saved" else ["likes_media_likes"]
        for p in iter_items(raw, old_keys):
            raw_counts[source] += 1
            key = shortcode(p["url"]) or f"{source}-{raw_counts[source]}"
            rec = posts.get(key)
            if rec is None:
                tags = []
                for t in p["hashtags"]:
                    c = clean_tag(t)
                    if c and c not in tags:
                        tags.append(c)
                posts[key] = {
                    "id": key,
                    "source": source,
                    "date": iso(p["ts"]),
                    "owner": p["owner"],
                    "caption": "\n".join(p["captions"]),
                    "hashtags": tags,
                    "url": p["url"],
                }
            elif rec["source"] != source:
                rec["source"] = "both"

    if not found:
        sys.exit("No saved_posts / liked_posts / saved_music JSON files found. "
                 "Make sure the export was downloaded in JSON format, not HTML.")

    rows = sorted(posts.values(), key=lambda r: r["date"] or "", reverse=True)
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "posts.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (args.out / "music.json").write_text(json.dumps(music, ensure_ascii=False, indent=2), encoding="utf-8")

    src_counts = collections.Counter(r["source"] for r in rows)
    tag_counts = collections.Counter(t for r in rows for t in r["hashtags"])
    owner_counts = collections.Counter(r["owner"] for r in rows if r["owner"])
    dates = [r["date"] for r in rows if r["date"]]
    summary = {
        "files_read": dict(found),
        "raw_items": dict(raw_counts),
        "posts_total": len(rows),
        "posts_by_source": dict(src_counts),
        "posts_with_caption": sum(1 for r in rows if r["caption"]),
        "caption_chars": sum(len(r["caption"]) for r in rows),
        "date_range": [min(dates), max(dates)] if dates else None,
        "unique_owners": len(owner_counts),
        "top_hashtags": tag_counts.most_common(50),
        "top_owners": owner_counts.most_common(25),
        "music_tracks": len(music),
        "music_artists": sorted({a for m in music for a in m["artists"]}),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"posts: {len(rows)} ({dict(src_counts)}) | music: {len(music)} | -> {args.out.resolve()}")


if __name__ == "__main__":
    main()

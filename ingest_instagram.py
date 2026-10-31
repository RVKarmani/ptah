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


class IngestError(Exception):
    """The export cannot be used (message is user-friendly)."""


HTML_WANTED = re.compile(r"(^|/)(saved_posts|liked_posts|saved_music)(_\d+)?\.html$")
MAX_MEMBER_BYTES = 300 * 1024 * 1024     # one JSON file; guards against zip bombs


def locate(src: Path) -> dict[str, list[str]]:
    """The wanted files present in a folder or zip, as {kind: [relative names]}. Reads names only, never file contents.

    Raises IngestError with a friendly message if the source is unusable (not a zip/folder, HTML format, nothing found)."""
    names: list[str] = []
    if src.is_file():
        if not zipfile.is_zipfile(src):
            raise IngestError("That file is not a zip archive. Upload the .zip you downloaded from Instagram.")
        with zipfile.ZipFile(src) as z:
            names = [i.filename for i in z.infolist() if not i.is_dir()]
    elif src.is_dir():
        names = [p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file()]
    else:
        raise IngestError(f"Not a folder or .zip: {src}")
    found: dict[str, list[str]] = {}
    for name in sorted(names):
        for kind, rx in WANTED.items():
            if rx.search(name):
                found.setdefault(kind, []).append(name)
    if not found:
        if any(HTML_WANTED.search(n) for n in names):
            raise IngestError("This export is in HTML format. Request your Instagram data again and choose the JSON format "
                              "(Accounts Center > Your information and permissions > Download your information > Format: JSON).")
        raise IngestError("No saved posts, liked posts or saved music found in this archive. Make sure the export was downloaded "
                          "in JSON format and includes 'Your Instagram activity'.")
    return found


def load_files(src: Path):
    """Yield (kind, parsed_json) for every wanted file in a folder or zip."""
    found = locate(src)
    if src.is_file():
        with zipfile.ZipFile(src) as z:
            for kind, rx in WANTED.items():
                for name in found.get(kind, []):
                    if z.getinfo(name).file_size > MAX_MEMBER_BYTES:
                        raise IngestError(f"{name} is unexpectedly large; refusing to read it.")
                    yield kind, json.loads(z.read(name))
    else:
        for kind in WANTED:
            for name in found.get(kind, []):
                yield kind, json.loads((src / name).read_text(encoding="utf-8"))


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


def ingest(export: Path, out: Path, on_file=None) -> dict:
    """Parse the export (folder or .zip) and write posts.jsonl, music.json, summary.json into ``out``. Returns the summary.

    ``on_file(kind, n_done, n_total)`` is called after each JSON file (progress for the web pipeline)."""
    posts = {}  # key -> post record
    music, found = [], collections.Counter()
    raw_counts = collections.Counter()
    total = sum(len(v) for v in locate(export).values())

    for kind, raw in load_files(export):
        found[kind] += 1
        if on_file:
            on_file(kind, sum(found.values()), total)
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

    rows = sorted(posts.values(), key=lambda r: r["date"] or "", reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "posts.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "music.json").write_text(json.dumps(music, ensure_ascii=False, indent=2), encoding="utf-8")

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
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", type=Path, help="Instagram export folder or .zip (JSON format)")
    ap.add_argument("--out", type=Path, default=Path("data"), help="output folder (default: data/)")
    args = ap.parse_args()
    try:
        summary = ingest(args.export, args.out)
    except IngestError as e:
        sys.exit(str(e))
    print(f"posts: {summary['posts_total']} ({summary['posts_by_source']}) | music: {summary['music_tracks']} | -> {args.out.resolve()}")


if __name__ == "__main__":
    main()

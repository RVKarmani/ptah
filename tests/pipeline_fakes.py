"""Synthetic inputs and fake stages for pipeline / server tests: no network, no LLM, no personal data."""
from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path

from ptah import events
from ptah.env import ROOT

SAMPLES = ROOT / "samples"


def _post(code: str, caption: str, tags: list[str]) -> dict:
    return {"timestamp": 1_700_000_000, "label_values": [
        {"label": "URL", "value": f"https://www.instagram.com/p/{code}/", "href": f"https://www.instagram.com/p/{code}/"},
        {"label": "Caption", "value": caption},
        {"title": "Hashtags", "dict": [{"dict": [{"label": "Name", "value": t}]} for t in tags]},
        {"title": "Owner", "dict": [{"dict": [{"label": "Username", "value": "someone"}]}]}]}


def instagram_zip(path: Path, *, n_posts: int = 5, html: bool = False, pad_mb: int = 0) -> Path:
    """A tiny export in the current JSON format (or an HTML-format one). ``pad_mb`` adds stored filler to test size limits."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED if pad_mb else zipfile.ZIP_DEFLATED) as z:
        if html:
            z.writestr("your_instagram_activity/saved/saved_posts.html", "<html></html>")
        else:
            posts = [_post(f"P{i}", f"cozy corner {i} #interior #plants", ["interior", "plants"]) for i in range(n_posts)]
            z.writestr("your_instagram_activity/saved/saved_posts.json", json.dumps(posts))
            z.writestr("your_instagram_activity/likes/liked_posts.json", json.dumps(posts[:2]))
            z.writestr("your_instagram_activity/saved/saved_music.json", json.dumps([{"label_values": [
                {"label": "Title", "value": "Song"}, {"label": "Artist", "value": "Some Band"}]}]))
        if pad_mb:
            chunk = b"\0" * (1024 * 1024)
            with z.open("padding.bin", "w", force_zip64=True) as f:
                for _ in range(pad_mb):
                    f.write(chunk)
    return path


def multipart(parts: list[tuple[str, str | None, bytes]], boundary: str = "XBOUNDARYX") -> tuple[bytes, str]:
    """[(field, filename|None, data)] -> (body, content-type)."""
    out = io.BytesIO()
    for name, fn, data in parts:
        out.write(f"--{boundary}\r\n".encode())
        disp = f'Content-Disposition: form-data; name="{name}"' + (f'; filename="{fn}"' if fn else "")
        out.write((disp + "\r\n").encode())
        if fn:
            out.write(b"Content-Type: application/octet-stream\r\n")
        out.write(b"\r\n" + data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------------------------------------------- fake stages
def fake_taste(ctx):
    for i in range(1, 6):
        ctx.progress(i / 5, f"round {i}")
        with events.activity("qloo", "Qloo: lookup"):
            pass
    ctx.profile_path.write_text(json.dumps({"entities": [{"qloo_id": "X"}], "aesthetic_tags": ["a"], "themes": [], "style_summary": "s"}))
    return {"entities": 1}


def fake_brief(ctx):
    ctx.progress(0.5, "places")
    ctx.brief_path.write_text("{}")
    return {"places": 3}


def fake_design(ctx):
    d = ctx.design_dir
    (d / "images").mkdir(parents=True, exist_ok=True)
    for n in ("room.json", "items.json", "layout.json", "lighting.json", "constraints.json"):
        shutil.copyfile(SAMPLES / n, d / n)
    (d / "taste.json").write_text(json.dumps({"style_summary": "fake", "aesthetic_tags": ["a"]}))
    (d / "images" / "art-1.jpg").write_bytes(b"\xff\xd8fakejpeg")
    with events.activity("llm", "Designing your room with AI"):
        time.sleep(0.01)
    ctx.progress(0.9)
    return {"concept": "Fake", "pieces": 3}


def fake_products(ctx):
    with events.activity("serpapi", "Shopping search: sofa"):
        pass
    return {"products": 0}


def fake_layout(ctx):
    return {"placed": 3}


def fake_lighting(ctx):
    return {"issues": 0}


FAKES = {"taste": fake_taste, "brief": fake_brief, "design": fake_design, "products": fake_products, "layout": fake_layout, "lighting": fake_lighting}

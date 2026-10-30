"""DSPy RLM program that turns an Instagram export into a TasteProfile."""
from __future__ import annotations

import os

import dspy

from . import qloo
from .data import load_music, load_posts, load_summary, posts_with_tag, search_posts
from .qloo import qloo_related, qloo_search, qloo_taste_tags
from .schemas import TasteProfile

TOOLS = [search_posts, posts_with_tag, qloo_search, qloo_related, qloo_taste_tags]

INSTRUCTIONS = """\
You are a taste analyst. From a person's saved/liked Instagram posts and saved music, build a TasteProfile
that will later be used to furnish and decorate their room.

Inputs (all available as Python variables in the sandbox; explore them with code, do not print everything):
- posts_overview: dict of counts, top hashtags, top owners, music artists.
- posts: list of ~1000 post dicts {id, source, date, owner, caption, hashtags, url}.
- music: list of {title, artists}. For film songs, the listed "artists" often include actors; treat those
  actors as signals of FILM taste (the film itself), not music taste.

Method:
1. Ignore memes, jokes, news, and one-off noise (e.g. #funny, #meme). Look for durable taste themes that
   recur across many posts and owners (hobbies, places, media, design, fashion, food, music).
2. Use code over `posts` (counting, grouping hashtags/owners, keyword search) and the search_posts /
   posts_with_tag tools to size each theme and collect example post ids.
3. Draft candidate entities (artists, films, shows, brands, places, destinations, designers). VERIFY every candidate with
   qloo_search before including it; use the qloo_id, canonical name and type it returns. Drop candidates that
   Qloo does not return. Keep Qloo calls economical (there is a per-run cap) and batch your thinking first.
4. Use qloo_related on your strongest verified entities to expand into brands, designers and aesthetics that
   are relevant to interiors and furnishing (domain "brand" / "designer" / "place").
   Use qloo_taste_tags (kind "decor" / "ambience") on your strongest verified entities to get interior decor and
   ambience tags with affinities, and ground aesthetic_tags in them.
5. Evidence for each entity must be short and cite post ids or an artist from music. Confidence is 0-1 and
   should reflect how durable the signal is (many posts = high, a single post = low).
6. style_summary: 2-3 sentences describing the person's likely interior/visual taste, grounded in the themes.
   aesthetic_tags: short plain-language visual/interior descriptors (2-4 words each), derived from the evidence.

Hard rules about Qloo (the final profile is machine-checked against what the tools really returned):
- NEVER write tool outputs yourself and never type a "result" of qloo_search / qloo_related / qloo_taste_tags in
  your own text or code comments. Only use qloo_id values, names and types copied from real tool output that the
  system showed you after you called the tool.
- Never invent, remember or reuse ids from anywhere else. An id that no tool returned in this run is rejected, and
  so is a name or type that does not match what Qloo returned for that id.
- For countries and cities (Norway, Tokyo, ...) call qloo_search with type_hint="destination" and use type
  "destination". Use type "place" only for a specific venue, shop or business.
- If qloo_search returns no good match (low score, or a different thing), drop the candidate; do not force it.
Finish by calling SUBMIT(profile=...) with a valid TasteProfile.
"""


class ExtractTaste(dspy.Signature):
    posts_overview: dict = dspy.InputField(desc="Summary stats of the export: counts, top hashtags/owners, music artists.")
    posts: list[dict] = dspy.InputField(desc="All saved/liked posts: id, source, date, owner, caption, hashtags, url.")
    music: list[dict] = dspy.InputField(desc="Saved music: title and artists (film-song 'artists' may be actors).")
    profile: TasteProfile = dspy.OutputField(desc="The person's taste profile, with Qloo-verified entities.")


ExtractTaste.__doc__ = INSTRUCTIONS


def configure_lms() -> tuple[dspy.LM, dspy.LM]:
    """Create LMs from env (or .env): PTAH_LM (required), PTAH_SUB_LM (optional, defaults to PTAH_LM)."""
    from .env import load_dotenv
    load_dotenv()
    main = os.environ.get("PTAH_LM")
    if not main:
        raise SystemExit(
            "PTAH_LM is not set. Set it to a litellm-style model string, e.g. "
            "export PTAH_LM='anthropic/<model-name>' (and the provider key, e.g. ANTHROPIC_API_KEY). "
            "Optionally set PTAH_SUB_LM to a cheaper model for sub-queries."
        )
    lm = dspy.LM(main)
    sub_lm = dspy.LM(os.environ.get("PTAH_SUB_LM") or main)
    dspy.configure(lm=lm)
    return lm, sub_lm


def build_program(sub_lm: dspy.LM | None = None) -> dspy.RLM:
    """Build the RLM program. Iteration limits can be overridden via PTAH_MAX_ITERS / PTAH_MAX_LLM_CALLS."""
    return dspy.RLM(
        ExtractTaste,
        max_iters=int(os.environ.get("PTAH_MAX_ITERS", 20)),
        max_llm_calls=int(os.environ.get("PTAH_MAX_LLM_CALLS", 50)),
        verbose=os.environ.get("PTAH_VERBOSE", "1") == "1",
        tools=TOOLS,
        sub_lm=sub_lm,
    )


def inputs() -> dict:
    return {"posts_overview": load_summary(), "posts": load_posts(), "music": load_music()}


def run_extraction(qloo_call_cap: int = qloo.DEFAULT_CALL_CAP) -> dict:
    """Run the full extraction (makes real LLM calls) inside a Qloo provenance run.

    Returns {"profile", "prediction", "lms", "registry"}; ``registry``/Qloo call log stay available through
    ``qloo.last_run()`` even if the RLM raises (the run is always ended).
    """
    lm, sub_lm = configure_lms()
    qloo.reset(qloo_call_cap)
    qloo.start_run()
    out: dict = {"lms": {"main": lm, "sub": sub_lm}}
    try:
        out["prediction"] = build_program(sub_lm)(**inputs())
        out["profile"] = out["prediction"].profile
    finally:
        out["registry"] = qloo.end_run()
    return out


def extract_taste(qloo_call_cap: int = qloo.DEFAULT_CALL_CAP) -> TasteProfile:
    """Run the full extraction and return the profile (see ``run_extraction`` for logs/registry)."""
    return run_extraction(qloo_call_cap)["profile"]

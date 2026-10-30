"""Design spec stage: taste profile + Qloo design brief -> a concrete, grounded room design (ONE LLM call).

The spec is a *design*: a palette, a material direction, a lighting mood and a list of piece "slots" (role, template
params, size ranges, colours, materials, style keywords). Real products (SerpApi) replace the designed pieces one for
one later, so every slot carries what a shopping search needs.

Grounding is enforced, not trusted: every entity / theme / tag a ``why_chain`` names must exist in the inputs
(``Vocab``), tag affinities must equal the brief's numbers, otherwise the spec is rejected (``DesignError``).

    from ptah.design import load_inputs, build_vocab, generate_spec
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]

FLOORS = ["light_oak", "mid_oak", "dark_wood", "herringbone", "concrete", "tile_light", "tile_dark", "carpet"]
MATERIALS = ["oak", "light_wood", "walnut", "dark_wood", "linen", "cotton", "velvet", "boucle", "wool", "leather", "rattan",
             "brass", "black_metal", "chrome", "ceramic", "glass", "marble", "stone", "plastic", "painted"]
LAYERS = ["ambient", "task", "accent"]
SCENES = ["day", "evening", "reading", "night"]
MAX_LLM_CALLS = 3

Floor = Literal["light_oak", "mid_oak", "dark_wood", "herringbone", "concrete", "tile_light", "tile_dark", "carpet"]
Material = Literal["oak", "light_wood", "walnut", "dark_wood", "linen", "cotton", "velvet", "boucle", "wool", "leather", "rattan",
                   "brass", "black_metal", "chrome", "ceramic", "glass", "marble", "stone", "plastic", "painted"]
Layer = Literal["ambient", "task", "accent"]
Scene = Literal["day", "evening", "reading", "night"]
TagKind = Literal["decor", "ambience", "place_tag", "discovery_tag"]

_HEX = re.compile(r"^#[0-9a-f]{6}$")


class DesignError(ValueError):
    """The spec is malformed or not grounded in the inputs. ``errors`` lists every problem (fed back to the LLM on retry)."""

    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors[:12]) + (f" (+{len(self.errors) - 12} more)" if len(self.errors) > 12 else ""))


# ---------------------------------------------------------------------------------------------------------------- roles
@dataclass(frozen=True)
class Role:
    name: str
    template: str            # viewer model template
    category: str            # items.json category
    prefix: str              # id prefix
    dims: Tuple[int, int, int]   # default w, d, h (cm)
    bounds: Tuple[int, int, int, int, int, int]  # wmin, wmax, dmin, dmax, hmin, hmax
    mount: str               # floor | wall | ceiling | surface | attached
    zone: str                # conversation | tv_wall | music_corner | window | wall | ceiling | surface
    max_count: int = 1
    required: bool = False
    major: bool = True       # needs a taste tag in its why chain (decor styling pieces only need an input)
    params: Dict[str, Any] = field(default_factory=dict)  # name -> list of allowed values | "bool" | (lo, hi)
    colors_doc: str = "primary=main colour"
    doc: str = ""


def _legs(*extra: str) -> List[str]:
    return ["tapered", "straight", "hairpin", "block", "none", *extra]


ROLES: Dict[str, Role] = {r.name: r for r in [
    Role("media_console", "tv_stand", "tv_stand", "console", (150, 40, 52), (110, 200, 32, 50, 38, 75), "floor", "tv_wall", required=True,
         params={"legs": _legs(), "doors": (2, 4)}, colors_doc="primary=body, secondary=legs/handles",
         doc="low unit under the wall-mounted TV (the focal wall)"),
    Role("tv_bias_light", "led_strip", "led_strip", "tvlight", (100, 2, 2), (60, 130, 2, 3, 2, 3), "attached", "tv_wall", major=False,
         colors_doc="primary=glow colour", doc="LED bias light behind the TV"),
    Role("main_sofa", "sofa", "sofa", "sofa", (180, 92, 82), (150, 230, 82, 100, 72, 92), "floor", "conversation", required=True,
         params={"legs": _legs(), "arms": "bool", "seats": (2, 4)}, colors_doc="primary=upholstery, secondary=legs",
         doc="main seating, faces the TV"),
    Role("accent_chair", "armchair", "armchair", "chair", (78, 80, 80), (66, 92, 70, 92, 70, 92), "floor", "conversation",
         params={"legs": _legs(), "arms": "bool"}, colors_doc="primary=upholstery, secondary=legs", doc="one lounge chair beside the table"),
    Role("coffee_table", "coffee_table", "coffee_table", "table", (105, 60, 42), (70, 130, 45, 80, 32, 48), "floor", "conversation", required=True,
         params={"shape": ["rect", "round", "oval"], "legs": _legs("pedestal"), "shelf": "bool"}, colors_doc="primary=top, secondary=legs/base"),
    Role("side_table", "side_table", "side_table", "side", (45, 45, 50), (32, 60, 32, 60, 42, 62), "floor", "conversation",
         params={"shape": ["rect", "round", "oval"], "legs": _legs("pedestal")}, colors_doc="primary=top, secondary=legs"),
    Role("rug", "rug", "rug", "rug", (200, 140, 1), (150, 280, 100, 200, 1, 2), "floor", "conversation", required=True,
         params={"shape": ["rect"]}, colors_doc="primary=field, secondary=border stripe",
         doc="area rug under the coffee table (large colour field: carries the palette)"),
    Role("bookshelf", "bookshelf", "bookshelf", "shelf", (80, 32, 180), (60, 110, 28, 40, 140, 200), "floor", "music_corner", required=True,
         params={"shelves": (3, 6), "doors": "bool", "legs": _legs()}, colors_doc="primary=carcass",
         doc="display shelf in the music corner next to the owned amp and guitar (holds records, books, objects)"),
    Role("floor_lamp", "floor_lamp", "floor_lamp", "lamp", (40, 40, 160), (28, 55, 28, 55, 130, 180), "floor", "conversation", required=True,
         params={"shade": ["drum", "cone", "globe", "dome", "none"], "arc": "bool"}, colors_doc="primary=stand, secondary=shade",
         doc="reading light beside the sofa (task layer)"),
    Role("table_lamp", "table_lamp", "table_lamp", "tlamp", (26, 26, 44), (18, 40, 18, 40, 30, 60), "surface", "conversation",
         params={"shade": ["drum", "cone", "globe", "dome", "none"], "base": ["gourd", "cylinder"]}, colors_doc="primary=base, secondary=shade",
         doc="warm accent lamp on the side table"),
    Role("pendant", "pendant", "pendant", "pendant", (40, 40, 40), (25, 70, 25, 70, 20, 60), "ceiling", "ceiling", required=True,
         params={"shade": ["globe", "dome", "cone", "drum"]}, colors_doc="primary=shade, secondary=fittings",
         doc="ambient ceiling light over the coffee table"),
    Role("sconce", "sconce", "sconce", "sconce", (16, 16, 24), (10, 26, 10, 26, 16, 40), "wall", "wall",
         params={"shade": ["dome", "cone", "drum", "globe"]}, colors_doc="primary=metal, secondary=shade", doc="wall light (accent layer)"),
    Role("floor_plant", "plant", "plant", "plant", (45, 45, 130), (32, 70, 32, 70, 90, 180), "floor", "window",
         params={}, colors_doc="primary=leaves, secondary=pot", doc="tall plant by the window"),
    Role("art_main", "art", "art", "art", (90, 4, 60), (60, 130, 3, 6, 45, 100), "wall", "wall", required=True,
         params={}, colors_doc="primary=ground, secondary=shape colour, accent=highlight (these colours ARE the picture)",
         doc="large framed print on the west wall, picture-lit"),
    Role("art_secondary", "poster", "poster", "poster", (50, 3, 70), (30, 80, 2, 5, 40, 100), "wall", "wall", max_count=2,
         params={"frame": ["thin", "none"]}, colors_doc="primary=ground, secondary=shape colour, accent=highlight",
         doc="smaller print(s)"),
    Role("cushion", "cushion", "cushion", "cushion", (40, 14, 40), (30, 55, 10, 18, 30, 55), "surface", "conversation", max_count=3, major=False,
         params={}, colors_doc="primary=cover", doc="scatter cushion on the sofa"),
    Role("throw", "throw", "throw", "throw", (46, 44, 3), (35, 60, 35, 60, 2, 5), "surface", "conversation", major=False,
         params={"drape": ["left", "right"]}, colors_doc="primary=main, secondary=stripe", doc="blanket on the sofa"),
    Role("vase", "vase", "vase", "vase", (12, 12, 24), (8, 22, 8, 22, 16, 40), "surface", "music_corner", max_count=2, major=False,
         params={"form": ["bottle", "round", "cylinder"]}, colors_doc="primary=glaze", doc="ceramic on the shelf"),
    Role("book_stack", "book_stack", "book_stack", "book-stack", (24, 17, 12), (16, 32, 12, 24, 8, 20), "surface", "music_corner", max_count=2, major=False,
         params={"count": (3, 7)}, colors_doc="primary/secondary/accent=spine colours", doc="stack of books on the shelf"),
    Role("vinyl_crate", "vinyl_crate", "vinyl_crate", "vinyl-crate", (33, 26, 28), (28, 42, 22, 34, 22, 36), "surface", "music_corner", major=False,
         params={}, colors_doc="primary=crate", doc="record crate on the lowest shelf (music corner)"),
    Role("tray", "tray", "tray", "tray", (34, 22, 3), (25, 45, 16, 32, 2, 5), "surface", "conversation", major=False,
         params={}, colors_doc="primary=tray", doc="tray on the coffee table"),
    Role("candle", "candle", "candle", "candle", (8, 8, 10), (5, 12, 5, 12, 6, 20), "surface", "conversation", major=False,
         params={}, colors_doc="primary=wax", doc="candle on the tray"),
    Role("small_plant", "small_plant", "small_plant", "splant", (11, 11, 22), (8, 20, 8, 20, 14, 34), "surface", "conversation", max_count=2, major=False,
         params={}, colors_doc="primary=leaves, secondary=pot", doc="small plant on the side table"),
    Role("shelf_strip", "led_strip", "led_strip", "strip", (80, 2, 2), (50, 110, 2, 3, 2, 3), "attached", "music_corner", major=False,
         colors_doc="primary=glow colour", doc="LED strip behind the shelf (accent glow)"),
]}
ROLE_ORDER = list(ROLES)


# ---------------------------------------------------------------------------------------------------------------- spec
def _hex(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip().lower()
    if re.fullmatch(r"[0-9a-f]{6}", s):
        s = "#" + s
    if re.fullmatch(r"#[0-9a-f]{3}", s):
        s = "#" + "".join(c * 2 for c in s[1:])
    return s


def _slug(v: Any) -> Any:
    return v.strip().lower().replace(" ", "_").replace("-", "_") if isinstance(v, str) else v


class Hex(str):
    pass


def _check_hex(v: str) -> str:
    v = _hex(v)
    if not isinstance(v, str) or not _HEX.match(v):
        raise ValueError(f"not a #rrggbb colour: {v!r}")
    return v


class Colors(BaseModel):
    primary: str
    secondary: Optional[str] = None
    accent: Optional[str] = None

    @field_validator("primary", "secondary", "accent", mode="before")
    @classmethod
    def _v(cls, v):
        return None if v in (None, "", "null") else _check_hex(v)


class Materials(BaseModel):
    primary: Material
    secondary: Optional[Material] = None

    @field_validator("primary", "secondary", mode="before")
    @classmethod
    def _v(cls, v):
        return None if v in (None, "", "null") else _slug(v)


Range = Tuple[float, float]


class DimsRange(BaseModel):
    w: Range
    d: Range
    h: Range

    @field_validator("w", "d", "h", mode="before")
    @classmethod
    def _v(cls, v):
        if isinstance(v, (int, float)):
            return (v, v)
        if isinstance(v, (list, tuple)) and len(v) == 1:
            return (v[0], v[0])
        return v

    @model_validator(mode="after")
    def _order(self):
        for k in ("w", "d", "h"):
            lo, hi = getattr(self, k)
            if lo > hi:
                setattr(self, k, (hi, lo))
            if min(lo, hi) <= 0:
                raise ValueError(f"dims {k} must be positive")
        return self


class TagRef(BaseModel):
    kind: TagKind
    name: str
    affinity: Optional[float] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _k(cls, v):
        return _slug(v)


class WhyChain(BaseModel):
    """input entities / themes -> Qloo tags (+ affinity) -> design decision."""

    inputs: List[str] = Field(default_factory=list)
    tags: List[TagRef] = Field(default_factory=list)
    decision: str = ""


class PieceSlot(BaseModel):
    role: str
    name: str
    colors: Colors
    materials: Materials
    params: Dict[str, Any] = Field(default_factory=dict)
    dims_cm: Optional[DimsRange] = None
    style_keywords: List[str] = Field(default_factory=list)
    inspired_by: Optional[str] = None
    why_chain: WhyChain
    # derived by finalize()
    id: str = ""
    template: str = ""
    category: str = ""
    mount: str = ""
    zone: str = ""

    @field_validator("role", mode="before")
    @classmethod
    def _r(cls, v):
        return _slug(v)

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        v = re.sub(r"\s*\(designed for you\)\s*$", "", v.strip(), flags=re.I)
        if not v:
            raise ValueError("empty name")
        return v

    @field_validator("style_keywords")
    @classmethod
    def _kw(cls, v):
        v = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if not 2 <= len(v) <= 8:
            raise ValueError("style_keywords needs 2-8 shopping keywords")
        return v


class Palette(BaseModel):
    wall_hex: str
    wall_name: str
    floor: Floor
    accents: List[str]

    @field_validator("wall_hex", mode="before")
    @classmethod
    def _w(cls, v):
        return _check_hex(v)

    @field_validator("accents", mode="before")
    @classmethod
    def _a(cls, v):
        if not isinstance(v, list):
            raise ValueError("accents must be a list of hex colours")
        out = [_check_hex(x) for x in v]
        if not 3 <= len(out) <= 5:
            raise ValueError("accents needs 3-5 colours")
        return out

    @field_validator("floor", mode="before")
    @classmethod
    def _f(cls, v):
        return _slug(v)


class MaterialDirection(BaseModel):
    summary: str
    materials: List[Material]
    avoid: List[str] = Field(default_factory=list)

    @field_validator("materials", mode="before")
    @classmethod
    def _m(cls, v):
        out = [_slug(x) for x in v] if isinstance(v, list) else v
        if isinstance(out, list) and not 3 <= len(out) <= 7:
            raise ValueError("material_direction.materials needs 3-7 entries")
        return out


class LightingMood(BaseModel):
    ambient_k: int = Field(ge=1800, le=4500)
    task_k: int = Field(ge=1800, le=4500)
    accent_k: int = Field(ge=1800, le=4500)
    emphasis: List[Layer]
    default_scene: Scene = "evening"
    description: str
    accent_glow_hex: Optional[str] = None

    @field_validator("emphasis", mode="before")
    @classmethod
    def _e(cls, v):
        out = [_slug(x) for x in v] if isinstance(v, list) else v
        if isinstance(out, list) and not 1 <= len(out) <= 2:
            raise ValueError("emphasis needs 1-2 layers")
        return out

    @field_validator("default_scene", mode="before")
    @classmethod
    def _s(cls, v):
        return _slug(v)

    @field_validator("accent_glow_hex", mode="before")
    @classmethod
    def _g(cls, v):
        return None if v in (None, "", "null") else _check_hex(v)


class DesignSpec(BaseModel):
    concept_name: str
    concept: str
    palette: Palette
    palette_why: WhyChain
    material_direction: MaterialDirection
    materials_why: WhyChain
    lighting_mood: LightingMood
    lighting_why: WhyChain
    pieces: List[PieceSlot]
    warnings: List[str] = Field(default_factory=list)  # non-fatal repairs (clamped sizes, dropped params)


# ---------------------------------------------------------------------------------------------------------------- inputs
@dataclass
class DesignInputs:
    profile: Dict[str, Any]
    brief: Dict[str, Any]
    room: Dict[str, Any]
    owned: List[Dict[str, Any]]


def load_inputs(profile_path: Path, brief_path: Path, room_path: Path, items_path: Path) -> DesignInputs:
    profile = json.loads(Path(profile_path).read_text())
    brief = json.loads(Path(brief_path).read_text())
    room = json.loads(Path(room_path).read_text())
    items = json.loads(Path(items_path).read_text())
    owned = [i for i in items if i.get("owned")]
    return DesignInputs(profile, brief, room, owned)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


@dataclass
class Vocab:
    """Everything a why_chain may reference. entities: norm name -> (canonical name, kind, affinity|None);
    tags: (kind, norm name) -> (canonical name, affinity|None)."""

    entities: Dict[str, Tuple[str, str, Optional[float]]] = field(default_factory=dict)
    tags: Dict[Tuple[str, str], Tuple[str, Optional[float]]] = field(default_factory=dict)

    def add_entity(self, name: Any, kind: str, affinity: Optional[float] = None) -> None:
        if isinstance(name, str) and name.strip():
            self.entities.setdefault(_norm(name), (name.strip(), kind, affinity))

    def add_tag(self, kind: str, name: Any, affinity: Optional[float] = None) -> None:
        if isinstance(name, str) and name.strip():
            self.tags.setdefault((kind, _norm(name)), (name.strip(), affinity))


def build_vocab(inp: DesignInputs) -> Vocab:
    v = Vocab()
    p, b = inp.profile, inp.brief
    for e in p.get("entities", []):
        v.add_entity(e.get("name"), f"{e.get('type', 'entity')}")
    for t in p.get("themes", []):
        v.add_entity(t.get("name"), "theme")
    for t in p.get("aesthetic_tags", []):
        v.add_entity(t, "aesthetic tag")
    for i in b.get("inputs", []):
        v.add_entity(i.get("name"), i.get("type") or "input")
    if b.get("location"):
        v.add_entity(b["location"], "location")
    for pl in b.get("inspiration_places", []):
        v.add_entity(pl.get("name"), f"{pl.get('kind', 'place')} (Qloo)", pl.get("affinity"))
        for t in pl.get("decor_tags", []) + pl.get("ambience_tags", []):
            v.add_tag("place_tag", t)
    for br in b.get("brands", []):
        v.add_entity(br.get("name"), "brand (Qloo)", br.get("affinity"))
    for d in b.get("discovery", []):
        v.add_entity(d.get("name"), f"{d.get('type', 'pick')} (Qloo pick)", d.get("affinity"))
        for t in d.get("tags", []):
            v.add_tag("discovery_tag", t)
    for t in b.get("taste", {}).get("decor", []):
        v.add_tag("decor", t.get("name"), t.get("affinity"))
    for t in b.get("taste", {}).get("ambience", []):
        v.add_tag("ambience", t.get("name"), t.get("affinity"))
    for o in inp.owned:
        v.add_entity(o.get("name"), "owned item")
    return v


# ---------------------------------------------------------------------------------------------------------------- validation
def _check_chain(chain: WhyChain, where: str, vocab: Vocab, errors: List[str], need_tag: bool, repair: bool = False,
                 warnings: Optional[List[str]] = None) -> None:
    """Every input / tag must exist in the vocabulary and tag affinities must equal the brief's. With ``repair`` (last resort when the
    LLM budget is spent) ungrounded references are DROPPED (never kept) and wrong affinities are replaced by the brief's number."""
    warnings = warnings if warnings is not None else []
    fixed_inputs: List[str] = []
    for n in chain.inputs:
        hit = vocab.entities.get(_norm(n))
        if hit is None:
            if repair:
                warnings.append(f"{where}: dropped ungrounded input {n!r}")
            else:
                errors.append(f"{where}: input {n!r} is not an entity/theme/place/brand/pick in the inputs")
        else:
            fixed_inputs.append(hit[0])
    chain.inputs = fixed_inputs
    fixed_tags: List[TagRef] = []
    for t in chain.tags:
        hit = vocab.tags.get((t.kind, _norm(t.name)))
        if hit is None:
            if repair:
                warnings.append(f"{where}: dropped ungrounded tag {t.kind}:{t.name!r}")
            else:
                errors.append(f"{where}: tag {t.kind}:{t.name!r} does not exist in the brief")
            continue
        canon, aff = hit
        if t.affinity is not None:
            if aff is None:
                if repair:
                    warnings.append(f"{where}: tag {canon!r} has no affinity in the brief; set to null")
                else:
                    errors.append(f"{where}: tag {t.kind}:{canon!r} has no affinity in the brief (leave it null)")
            elif abs(float(t.affinity) - float(aff)) > 0.006:
                if repair:
                    warnings.append(f"{where}: tag {canon!r} affinity {t.affinity} replaced by the brief's {aff}")
                else:
                    errors.append(f"{where}: tag {t.kind}:{canon!r} affinity is {aff}, not {t.affinity}")
        fixed_tags.append(TagRef(kind=t.kind, name=canon, affinity=aff))
    chain.tags = fixed_tags
    if repair:
        if not chain.inputs and not chain.tags:
            errors.append(f"{where}: nothing grounded is left in the why_chain")
    else:
        if not chain.inputs:
            errors.append(f"{where}: why_chain.inputs is empty (name at least one entity/theme from the inputs)")
        if need_tag and not chain.tags:
            errors.append(f"{where}: why_chain.tags is empty (name at least one Qloo tag from the decor/ambience lists)")
    if not chain.decision.strip():
        errors.append(f"{where}: why_chain.decision is empty")


def _fit_range(lo: float, hi: float, blo: int, bhi: int, what: str, warnings: List[str]) -> Tuple[float, float]:
    nlo, nhi = max(lo, blo), min(hi, bhi)
    if nlo > nhi:  # range entirely outside the plausible bounds: snap to the nearest bound
        nlo = nhi = blo if hi < blo else bhi
    if (nlo, nhi) != (lo, hi):
        warnings.append(f"{what}: size range {lo:g}-{hi:g} cm clamped to {nlo:g}-{nhi:g}")
    return float(nlo), float(nhi)


def _fix_params(role: Role, params: Dict[str, Any], what: str, warnings: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (params or {}).items():
        spec = role.params.get(k)
        ok = False
        if spec == "bool":
            if isinstance(v, str) and v.lower() in ("true", "false"):
                v = v.lower() == "true"
            ok = isinstance(v, bool)
        elif isinstance(spec, tuple):
            try:
                v = int(v)
                ok = spec[0] <= v <= spec[1]
            except (TypeError, ValueError):
                ok = False
        elif isinstance(spec, list):
            v = _slug(v) if isinstance(v, str) else v
            ok = v in spec
        if ok:
            out[k] = v
        else:
            warnings.append(f"{what}: param {k}={v!r} not supported by template {role.template}, dropped")
    return out


def default_range(role: Role) -> DimsRange:
    w, d, h = role.dims
    f = lambda x: (round(x * 0.88), round(x * 1.12)) if x > 6 else (x, x)  # noqa: E731
    return DimsRange(w=f(w), d=f(d), h=f(h))


def validate_spec(raw: Dict[str, Any], vocab: Vocab, repair: bool = False) -> DesignSpec:
    """Structure + grounding check. Raises DesignError with every problem; returns the normalised spec (ids assigned,
    affinities taken from the brief, sizes clamped to plausible bounds). ``repair=True`` (last resort) drops ungrounded references and
    pieces that do not parse instead of rejecting the whole answer; nothing invented is ever kept."""
    pre_warnings: List[str] = []
    if repair and isinstance(raw.get("pieces"), list):
        raw = dict(raw)
        good = []
        for i, pr in enumerate(raw["pieces"]):
            try:
                PieceSlot.model_validate(pr)
                good.append(pr)
            except ValidationError as e:
                pre_warnings.append(f"pieces[{i}] dropped: {e.errors()[0]['loc']} {e.errors()[0]['msg'][:60]}")
        raw["pieces"] = good
    try:
        spec = DesignSpec.model_validate(raw)
        spec.warnings.extend(pre_warnings)
    except ValidationError as e:
        errs = []
        for x in e.errors()[:25]:
            loc = ".".join(str(p) for p in x["loc"])
            errs.append(f"{loc}: {x['msg']}")
        raise DesignError(errs)
    errors: List[str] = []
    for nm, ch in (("palette_why", spec.palette_why), ("materials_why", spec.materials_why), ("lighting_why", spec.lighting_why)):
        _check_chain(ch, nm, vocab, errors, need_tag=True, repair=repair, warnings=spec.warnings)

    counts: Dict[str, int] = {}
    kept: List[PieceSlot] = []
    for i, pc in enumerate(spec.pieces):
        where = f"pieces[{i}] ({pc.role}: {pc.name})"
        role = ROLES.get(pc.role)
        if role is None:
            if repair:
                spec.warnings.append(f"{where}: unknown role dropped")
            else:
                errors.append(f"{where}: unknown role {pc.role!r}; allowed: {', '.join(ROLES)}")
            continue
        if repair and counts.get(pc.role, 0) >= role.max_count:
            spec.warnings.append(f"{where}: more than {role.max_count} of this role, dropped")
            continue
        counts[pc.role] = counts.get(pc.role, 0) + 1
        kept.append(pc)
        _check_chain(pc.why_chain, where, vocab, errors, need_tag=role.major, repair=repair, warnings=spec.warnings)
        if pc.role.startswith("art_"):
            if not pc.inspired_by:
                errors.append(f"{where}: inspired_by is required for art pieces (a film / book / artist / place from the inputs)")
            elif _norm(pc.inspired_by) not in vocab.entities:
                if repair and pc.why_chain.inputs:
                    spec.warnings.append(f"{where}: inspired_by {pc.inspired_by!r} not in the inputs; using {pc.why_chain.inputs[0]!r}")
                    pc.inspired_by = pc.why_chain.inputs[0]
                else:
                    errors.append(f"{where}: inspired_by {pc.inspired_by!r} is not in the inputs")
            else:
                pc.inspired_by = vocab.entities[_norm(pc.inspired_by)][0]
        elif pc.inspired_by:
            pc.inspired_by = vocab.entities[_norm(pc.inspired_by)][0] if _norm(pc.inspired_by) in vocab.entities else None
        pc.params = _fix_params(role, pc.params, where, spec.warnings)
        bl = role.bounds
        if pc.dims_cm is None:
            pc.dims_cm = default_range(role)
        pc.dims_cm = DimsRange(
            w=_fit_range(*pc.dims_cm.w, bl[0], bl[1], where + " w", spec.warnings),
            d=_fit_range(*pc.dims_cm.d, bl[2], bl[3], where + " d", spec.warnings),
            h=_fit_range(*pc.dims_cm.h, bl[4], bl[5], where + " h", spec.warnings),
        )
        pc.template, pc.category, pc.mount, pc.zone = role.template, role.category, role.mount, role.zone
    spec.pieces = kept if repair else spec.pieces
    for r in ROLES.values():
        n = counts.get(r.name, 0)
        if r.required and n == 0:
            errors.append(f"missing required piece role {r.name!r}")
        if n > r.max_count:
            errors.append(f"role {r.name!r} appears {n} times (max {r.max_count})")
    if not 11 <= len(spec.pieces) <= 26:
        errors.append(f"pieces has {len(spec.pieces)} entries; expected 11-26")
    if errors:
        raise DesignError(errors)
    seen: Dict[str, int] = {}
    for pc in spec.pieces:
        r = ROLES[pc.role]
        seen[r.prefix] = seen.get(r.prefix, 0) + 1
        pc.id = f"{r.prefix}-{seen[r.prefix]}"
    return spec


# ---------------------------------------------------------------------------------------------------------------- prompt
def _r2(x: Any) -> Any:
    return round(x, 3) if isinstance(x, float) else x


def prompt_context(inp: DesignInputs) -> Dict[str, Any]:
    """The compact taste data the LLM designs from (everything in it is also the vocabulary the answer is validated against)."""
    p, b = inp.profile, inp.brief
    taste = b.get("taste", {})
    return {
        "style_summary": p.get("style_summary"),
        "aesthetic_tags": p.get("aesthetic_tags", []),
        "themes": [{"name": t["name"], "posts": t.get("post_count")} for t in p.get("themes", [])],
        "entities": [{"name": e["name"], "type": e["type"]} for e in p.get("entities", [])],
        "location": b.get("location"),
        "qloo_decor_tags": [{"name": t["name"], "affinity": _r2(t["affinity"])} for t in taste.get("decor", [])[:10]],
        "qloo_ambience_tags": [{"name": t["name"], "affinity": _r2(t["affinity"])} for t in taste.get("ambience", [])[:10]],
        "qloo_brands": [{"name": x["name"], "tier": x.get("tier"), "affinity": _r2(x["affinity"])} for x in b.get("brands", [])[:12]],
        "qloo_inspiration_places": [
            {"name": x["name"], "kind": x.get("kind"), "affinity": _r2(x["affinity"]),
             "decor_tags": x.get("decor_tags", [])[:6], "ambience_tags": x.get("ambience_tags", [])[:4]}
            for x in b.get("inspiration_places", [])[:8]],
        "qloo_discovery_picks": [{"name": x["name"], "type": x.get("type"), "affinity": _r2(x["affinity"]), "tags": x.get("tags", [])[:4]}
                                 for x in b.get("discovery", [])[:10]],
    }


def room_context(inp: DesignInputs) -> Dict[str, Any]:
    r = inp.room
    xs = [pt[0] for pt in r["floor"]]
    zs = [pt[1] for pt in r["floor"]]
    return {
        "size_cm": {"w": max(xs) - min(xs), "d": max(zs) - min(zs), "ceiling_h": r["height"]},
        "openings": [{k: o[k] for k in ("id", "type", "wall", "offset", "width", "height") if k in o} for o in r.get("openings", [])],
        "layout_plan": ("TV is wall-mounted on the north wall (focal wall) above the media console; the sofa faces it from the south; "
                        "the owned guitar hangs on the east wall above the owned amp (music corner); the window is on the south wall, "
                        "the door on the north wall; the west wall is free for art."),
        "owned_items_that_stay": [{"name": o["name"], "category": o["category"], "colors": (o.get("user_colors") or o.get("colors"))} for o in inp.owned],
    }


def role_table() -> str:
    lines = []
    for r in ROLES.values():
        ps = []
        for k, v in r.params.items():
            ps.append(f"{k}={'true|false' if v == 'bool' else f'{v[0]}..{v[1]}' if isinstance(v, tuple) else '|'.join(v)}")
        b = r.bounds
        size = f"w{b[0]}-{b[1]} d{b[2]}-{b[3]} h{b[4]}-{b[5]}"
        req = "REQUIRED" if r.required else f"optional, max {r.max_count}"
        lines.append(f"- {r.name} [{req}] {r.doc}. size cm: {size}. params: {', '.join(ps) or 'none'}. colors: {r.colors_doc}")
    return "\n".join(lines)


EXAMPLE = """{
 "concept_name": "<2-4 words>",
 "concept": "<2 sentences: the room's idea, in terms of the person's taste>",
 "palette": {"wall_hex": "#rrggbb", "wall_name": "<paint-style name>", "floor": "<floor preset>", "accents": ["#rrggbb", "#rrggbb", "#rrggbb"]},
 "palette_why": {"inputs": ["<name copied from the data>"], "tags": [{"kind": "decor", "name": "<tag name>", "affinity": 0.95}], "decision": "<why these colours>"},
 "material_direction": {"summary": "<1-2 sentences>", "materials": ["walnut", "linen", "brass"], "avoid": ["<what to avoid>"]},
 "materials_why": {"inputs": ["..."], "tags": [{"kind": "decor", "name": "...", "affinity": 0.0}], "decision": "..."},
 "lighting_mood": {"ambient_k": 2400, "task_k": 2700, "accent_k": 2200, "emphasis": ["accent", "ambient"], "default_scene": "evening",
                   "description": "<1-2 sentences>", "accent_glow_hex": null},
 "lighting_why": {"inputs": ["..."], "tags": [{"kind": "ambience", "name": "...", "affinity": 0.0}], "decision": "..."},
 "pieces": [
  {"role": "main_sofa", "name": "<short product-like name, e.g. Moss green bouclé sofa>",
   "colors": {"primary": "#rrggbb", "secondary": "#rrggbb", "accent": null}, "materials": {"primary": "boucle", "secondary": "walnut"},
   "params": {"legs": "tapered", "arms": true}, "dims_cm": {"w": [170, 200], "d": [85, 95], "h": [76, 86]},
   "style_keywords": ["<2-6 shopping search words>"], "inspired_by": null,
   "why_chain": {"inputs": ["<name>", "<name>"], "tags": [{"kind": "decor", "name": "<tag>", "affinity": 0.0}], "decision": "<why this piece>"}}
 ]
}"""


def build_messages(inp: DesignInputs) -> List[Dict[str, str]]:
    system = ("You are an interior designer who designs ONLY from a person's cultural taste data (taste profile + Qloo taste-graph results). "
              "You answer with exactly one JSON object and nothing else: no prose, no markdown fences.")
    user = f"""TASTE DATA (JSON). Every name you cite must be copied exactly from here.
{json.dumps(prompt_context(inp), ensure_ascii=False)}

ROOM (fixed; do not change):
{json.dumps(room_context(inp), ensure_ascii=False)}

PIECE ROLES you may use (one JSON entry per piece; pick 14-20 pieces; all REQUIRED roles must appear):
{role_table()}

Allowed values: floor = {' | '.join(FLOORS)}. materials = {' | '.join(MATERIALS)}.
lighting: *_k are colour temperatures in kelvin (1800-4500; lower = warmer); emphasis = 1-2 of ambient|task|accent; default_scene = day|evening|reading|night; accent_glow_hex = optional coloured LED glow (hex) or null.

TASK: design this living room so that the 3D render visibly reads as THIS person's taste: not a generic white-wall, oak-and-grey room.
- Give the walls a distinctive colour with character, pick the floor preset, and 3-5 accent colours, all derived from the taste data (films, music, places, decor tags).
- Use shapes and materials that carry the style (e.g. round or oval tables, arc or cone lamps, brass, velvet, stone, rattan), at least 3 different material classes.
- Art: framed prints "inspired_by" one of the discovery picks or profile entities (cite it); choose the picture colours (primary/secondary/accent) from that work's mood. No literal hobby props.
- The owned TV (black), cream guitar and black amp stay. Design around them: the music corner should feel intentional.
- Tasteful and uncluttered: few strong pieces, restrained decor.
- Lamps: primary = stand/body colour, secondary = shade colour.
- Each piece has its own why_chain: inputs (1-3 names copied from the data) -> tags (decor/ambience copied from qloo_decor_tags / qloo_ambience_tags with the SAME affinity numbers, or place_tag copied from a place's decor_tags/ambience_tags, or discovery_tag from a pick's tags; affinity null for place_tag/discovery_tag) -> decision (one sentence). Major pieces need at least one tag. Never invent a name, tag or number.
- dims_cm are shopping ranges [min, max] inside the role's size bounds (the room is 420 x 360 cm: keep the sofa and table proportionate).
- "name" is a short product-like name without brand names, e.g. "Walnut sideboard".
- Keep every "decision" under 20 words. Output the JSON object only, with no commentary and no thinking text.

OUTPUT FORMAT (shape only; fill with your design; emit all 14-20 pieces):
{EXAMPLE}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------------------------------------------- LLM
def _ledger_path() -> Path:
    return ROOT / "data" / "design_llm_calls.json"


def ledger_count() -> int:
    p = _ledger_path()
    return len(json.loads(p.read_text())) if p.exists() else 0


def _ledger_add(purpose: str, model: str) -> None:
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(p.read_text()) if p.exists() else []
    rows.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "purpose": purpose, "model": model})
    p.write_text(json.dumps(rows, indent=1))


def extract_json(text: str) -> Dict[str, Any]:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.S)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise DesignError(["the reply contains no JSON object"])
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise DesignError([f"the reply is not valid JSON: {e}"])


def call_llm(messages: List[Dict[str, str]], purpose: str, timeout: float = 120.0, max_calls: int = MAX_LLM_CALLS,
             deadline_s: float = 150.0) -> str:
    """ONE OpenRouter chat completion with PTAH_SUB_LM (no SDK retries: every attempt counts against the daily quota; streamed so
    the partial answer is saved to data/design_cache/last_llm_raw.txt even if the process is interrupted).
    Refuses to run when the persisted call ledger already holds ``max_calls`` attempts."""
    from .env import load_dotenv

    load_dotenv()
    if ledger_count() >= max_calls:
        raise RuntimeError(f"LLM call budget exhausted ({max_calls} calls recorded in {_ledger_path()})")
    model = os.environ.get("PTAH_SUB_LM") or os.environ.get("PTAH_LM")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not model or not key:
        raise RuntimeError("PTAH_SUB_LM / OPENROUTER_API_KEY not set (see .env)")
    model = model.split("openrouter/", 1)[-1]
    from openai import OpenAI

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=timeout, max_retries=0)
    _ledger_add(purpose, model)
    raw_file = ROOT / "data" / "design_cache" / "last_llm_raw.txt"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    parts: List[str] = []
    reasoning: List[str] = []
    finish = None
    stream = client.chat.completions.create(
        model=model, messages=messages, temperature=0.6, max_tokens=7000, stream=True,
        response_format={"type": "json_object"}, extra_body={"reasoning": {"effort": "low"}})
    try:
        for ev in stream:
            if not ev.choices:
                continue
            ch0 = ev.choices[0]
            finish = getattr(ch0, "finish_reason", None) or finish
            delta = ch0.delta
            if delta is not None:
                if getattr(delta, "content", None):
                    parts.append(delta.content)
                    if len(parts) % 20 == 0:
                        raw_file.write_text("".join(parts))
                rs = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                if rs:
                    reasoning.append(rs)
            if time.time() - t0 > deadline_s:
                raise TimeoutError(f"LLM answer not finished after {deadline_s:.0f}s ({len(''.join(parts))} chars so far)")
    finally:
        raw_file.write_text("".join(parts))
        raw_file.with_name("last_llm_meta.json").write_text(json.dumps(
            {"seconds": round(time.time() - t0, 1), "finish_reason": finish, "content_chars": len("".join(parts)),
             "reasoning_chars": len("".join(reasoning)), "model": model}, indent=1))
    text = "".join(parts)
    if not text.strip() and reasoning:  # some reasoning models put the answer in the reasoning channel
        text = "".join(reasoning)
    return text


def input_hash(inp: DesignInputs) -> str:
    blob = json.dumps({"ctx": prompt_context(inp), "room": room_context(inp), "roles": role_table()}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def generate_spec(inp: DesignInputs, cache_dir: Path, force: bool = False, log=print) -> Tuple[DesignSpec, Dict[str, Any]]:
    """One LLM call (plus one retry with the validation errors if the first answer is rejected). Raw replies are cached under
    ``cache_dir`` keyed by the input hash, so re-running with the same inputs costs no LLM call."""
    vocab = build_vocab(inp)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cf = cache_dir / f"design_{input_hash(inp)}.json"
    meta: Dict[str, Any] = {"llm_calls": 0, "attempts": []}
    if cf.exists() and not force:
        cached = json.loads(cf.read_text())
        try:
            spec = validate_spec(cached["raw"], vocab)
            meta.update(cached=True, attempts=cached.get("attempts", []))
            log(f"design: using cached LLM answer {cf.name} (0 LLM calls)")
            return spec, meta
        except DesignError as e:
            log(f"design: cached answer no longer validates ({e}); asking the LLM")
    messages = build_messages(inp)
    last_err: Optional[DesignError] = None
    last_raw = ""
    for attempt in (1, 2):
        if attempt == 2:
            assert last_err is not None
            messages = messages + [
                {"role": "assistant", "content": last_raw[:12000]},
                {"role": "user", "content": "That JSON was rejected:\n- " + "\n- ".join(last_err.errors[:20]) +
                 "\nFix every problem and answer again with the complete corrected JSON object only."}]
        log(f"design: LLM call {attempt} ...")
        t0 = time.time()
        try:
            last_raw = call_llm(messages, purpose=f"design_spec attempt {attempt}")
        except Exception as e:  # noqa: BLE001 - transport errors count as an attempt
            meta["attempts"].append({"attempt": attempt, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            meta["llm_calls"] += 1
            log(f"design: LLM call {attempt} failed: {type(e).__name__}: {str(e)[:200]}")
            last_err = DesignError([f"LLM call failed: {type(e).__name__}"])
            if attempt == 2 or ledger_count() >= MAX_LLM_CALLS:
                break
            continue
        meta["llm_calls"] += 1
        (cache_dir / f"design_{input_hash(inp)}.raw{attempt}.txt").write_text(last_raw)
        try:
            raw = extract_json(last_raw)
            spec = validate_spec(raw, vocab)
        except DesignError as e:
            last_err = e
            meta["attempts"].append({"attempt": attempt, "seconds": round(time.time() - t0, 1), "rejected": e.errors[:20]})
            log(f"design: answer {attempt} rejected: {e}")
            if ledger_count() >= MAX_LLM_CALLS or attempt == 2:
                try:  # last resort: no LLM budget left to ask again, so drop what is ungrounded (never keep it)
                    spec = validate_spec(extract_json(last_raw), vocab, repair=True)
                    spec.warnings.append("REPAIRED: ungrounded references dropped because the LLM budget was spent; original errors: " + "; ".join(e.errors[:6]))
                    meta["attempts"][-1]["repaired"] = True
                    cf.write_text(json.dumps({"raw": extract_json(last_raw), "attempts": meta["attempts"], "repaired": True}, ensure_ascii=False, indent=1))
                    return spec, meta
                except DesignError as e2:
                    last_err = e2
                    log(f"design: repair failed: {e2}")
                    break
            continue
        meta["attempts"].append({"attempt": attempt, "seconds": round(time.time() - t0, 1), "ok": True})
        cf.write_text(json.dumps({"raw": raw, "attempts": meta["attempts"]}, ensure_ascii=False, indent=1))
        return spec, meta
    assert last_err is not None
    raise last_err

# Ptah

Taste-driven room furnishing agent (Qloo Agentic Hackathon). This is the taste extractor: it reads an
Instagram export, finds durable taste themes, grounds entities with Qloo, and outputs a `TasteProfile`.

## Setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
`dspy.RLM` runs model-written code in a Deno/Pyodide sandbox, so install [Deno](https://deno.com) (`deno --version`).

## Data
`python ingest_instagram.py` produces `data/posts.jsonl`, `data/music.json`, `data/summary.json`.

## Env vars
- `PTAH_LM` (required for real runs): litellm-style model, e.g. `anthropic/<model>`; plus the provider key (e.g. `ANTHROPIC_API_KEY`).
- `PTAH_SUB_LM` (optional): cheaper model for RLM sub-queries; defaults to `PTAH_LM`.
- `PTAH_MAX_ITERS` (default 20), `PTAH_MAX_LLM_CALLS` (default 50): RLM limits.
- `QLOO_API_KEY`: Qloo hackathon key (environment variable, or a `QLOO_API_KEY=...` line in the git-ignored `.env`). Without it the Qloo tools return clearly marked stubs (`"stub": true`) and `run_brief.py` refuses to run.
- `QLOO_BASE_URL` (default `https://hackathon.api.qloo.com`), `PTAH_QLOO_CACHE_DIR` (default `cache/qloo`).

## Commands
```
python run_taste.py --dry-run   # no LLM; checks tools, schemas, RLM construction
python run_taste.py             # real run -> data/taste_profile.json
python run_taste.py --qloo-cap 30
python run_brief.py             # Qloo design brief -> data/brief.json (see "Qloo usage" below)
```
Layout: `ptah/schemas.py` (models), `ptah/data.py` (post tools), `ptah/qloo.py` (Qloo client, cache, call cap, LLM tools), `ptah/taste.py` (RLM program), `ptah/brief.py` (design brief), `ptah/catalog/home_brands.json` (editable brand candidates).

## Viewer
3D web viewer (Vite + React + TypeScript + react-three-fiber) in `viewer/`. Reads `room.json`, `items.json`, `layout.json` (and optional `taste.json`) and shows the furnished room as a dollhouse; click items for details and a Buy link.
```
cd viewer && npm install
npm run dev          # http://localhost:5173, loads viewer/public/sample/
npm run build        # static site in viewer/dist (relative paths, deploy to Vercel/Netlify)
npm run sync-sample  # re-copy ../samples/*.json into public/sample/
```
Data path: defaults to `sample/`; override with `?data=<url prefix>` (e.g. `?data=http://localhost:8000/runs/abc/`; must serve the JSON with CORS). `room.scan.glb` (resolved against the data path) enables a "Show scan" toggle.

**Viewer v2 (models, lights, finishes).** Every item is a low-poly procedural model chosen by `model.template` (fitted to its w x d x h, honouring `y`),
with PBR materials from `material.*` and colours from `colors.*`. Items with `light` become real lights (point / spot / rect-area for LED strips) at
`emit_offset`, coloured by Kelvin or `rgb`, with glowing shades. Pipeline: three.js + ambient occlusion + glow + filmic tone mapping (URL params `ao=0`, `bloom=0`, `tone=aces` for slow machines; no UI toggles).
- Scene chips under the room: Day, Evening, Reading, Night, All off are presets over the ambient/task/accent layers (default: Evening). The time-of-day slider lives in the Lighting tab under "Adjust lighting" and drives the sun, which only enters through the real window openings, plus sky/IBL falling off at night.
- Lighting tab: a plain-language summary (light mix, zones, suggestions; per scene when `scenes.<name>` is present) and one collapsed "Adjust lighting" section with time of day, group brightness, individual lights (on/off, brightness, warmer to cooler) and the light map overlay (the only place lux appears).
- Room tab: floor presets (light_oak, mid_oak, dark_wood, herringbone, concrete, tile_light, tile_dark, carpet) at real-world tile scale, wall colour, suggested paint swatch ("can't paint" state when `can_paint` is false).
- Floor textures are procedural by default. To use real CC0 PBR maps put `diffuse|normal|roughness.jpg` in `viewer/public/textures/<preset>/` (`npm run fetch-textures`, see `viewer/public/textures/DOWNLOAD.md`); files win when present.
- All v2 fields are optional; old data renders with default models and no lights. Test data: `?data=sample-lit/` (fully lit room), `?data=sample-legacy/` (v1 fields only). Other URL params: `scene=day|evening|reading|night|all_off`, `time=21.5`, `floor=herringbone`, `heat=1`, `tab=lighting|room`, `ao=0`, `bloom=0`, `tone=aces`, `select=<item id>`, `cam=x,y,z,tx,ty,tz`.
- Screenshots: `viewer/screenshots/`.


## Layout planner
Deterministic furniture placement: given `room.json`, `items.json` and LLM-written `constraints.json` (formats in `docs/contracts.md`), it places each
item on the floor (position + rotation) and writes `layout.json`. Items that cannot be placed go to `unplaced` with a reason and the largest
`max_w`/`max_d` that would have fit, so the product search can retry smaller.
```
python -m ptah.layout samples/room.json samples/items.json samples/constraints.json -o samples/layout.json
python tools/render_layout.py samples/room.json samples/items.json samples/layout.json      # -> samples/layout.png (add --ascii for text)
pytest tests/test_layout.py
```
Constraint vocabulary: `edge` (back flush to a wall, facing in), `middle` (away from walls), `near`/`far` (target), `in_front_of`, `side_of`, `face`,
`center_align`, `under` (rugs), plus `on_top_of`. Per item: `"anchor": true` (placed first), optional `"hint": {x,z,rotation_deg}` (rough pose),
optional `"zone"` (soft cohesion). Ergonomic objectives (coffee table 35-50 cm from the sofa, seating faces the centre, tall items on walls, window light)
are added automatically. Hard rules: inside the floor, no overlaps, door swing zones clear, tall items not in front of windows, >= 60 cm walkway from each door
to the room centre. Rotation follows three.js (0 faces +z, 90 faces +x).

### Mounted items
`on_top_of`, `wall_mounted {height, wall?}`, `ceiling {above?, drop}` and `attach_to {target, side}` constraints take items off the floor solver: their poses
(and `y`) are derived after the floor items are placed and checked in 3D against floor items, wall openings and each other (LED strips never collide). Details and
field semantics: `docs/contracts.md` (v2.1). Placements get `y`, `mount`, `parent`, `wall`, `side`.

Approach adapted from Holodeck (Yang et al., CVPR 2024), informed by LayoutVLM (Sun et al., CVPR 2025).


## Product enrichment (`ptah/products/enrich.py`)
Offline helpers for shortlisted listings: `extract_colors(image)` (studio background masked, small numpy k-means -> primary/secondary/accent hex or None),
`classify_material(title, description)` (keyword rules with synonyms: acacia -> light_wood, bouclé -> boucle, brushed gold -> brass ...),
`parse_light_spec(title, description, category, dims=)` (colour temperature, lumens or estimated from watts: LED ~90 lm/W, incandescent ~14 lm/W; dimmable; RGB;
layer/kind defaults per category; `emit_offset` from dims), `infer_template(category, title)` (shade / legs / shape params) and `enrich_listing(item, image)`.

## Lighting (`ptah/lighting.py`)
```
python -m ptah.lighting samples/room.json samples/items.json samples/layout.json -o samples/lighting.json [--mode night|day --hour 9 --scene reading]
python tools/render_layout.py samples/room.json samples/items.json samples/layout.json --lighting samples/lighting.json --scene reading -o samples/layout_lux.png
```
Lights are switched by layer, so the room is evaluated in scenes (layer presets): `day` (lamps off, daylight), `evening` (ambient 0.4, accent 1.0),
`reading` (task 1.0, ambient 0.3, accent 0.3), `night` (accent 0.6). Each scene gets a lux grid (25 cm, 75 cm work plane), zones and issues; the top-level fields of
`lighting.json` repeat the evening scene (day with `--mode day`). Layers: ambient (pendants, uplighters), task (floor/desk lamps, reading sconces), accent (table lamps,
sconces, LED strips, picture lights).

Targets are **approximate design guidance**, not a lighting simulation: reading zone ~300 lux and desk ~500 lux at 75 cm (ok from 80 %, scene `reading`), ambient
average ~100 lux at night (evening scene counts >= ~50 lux as adequate), focal points (bookshelf, art, plant, bed, tv_stand) accent-lit by an accent light that is attached to
them, aims at them or sits within 100 cm. Model: isotropic / shaded point sources (I = lumens / 4 pi, shade transmission and downward lobe per shade type), spot cones, LED
strips as Lambertian point rows, cosine and inverse-square law, no occlusion by furniture, plus a uniform inter-reflection term from room finishes. Daylight: overcast-style
window patches, strength by hour (0 before 06:00 / after 18:00, ~15 klux outdoors at noon). Issues: `DIM_TASK_ZONE`, `NO_AMBIENT`, `NO_ACCENT_LAYER`, `UNLIT_FOCAL_POINT`,
`GLARE_RISK`, each with a `suggest` ({category, near | attach+side | above}) the agent can turn into a product search plus a mounting constraint.

Tests: `pytest tests` (layout, mounts, enrichment, lighting).


## UI notes (viewer polish pass)
- **One primary control:** the lighting scene chips floating under the room. Everything advanced (time of day, group and per-light brightness, warmer/cooler tone, light map) sits behind a single collapsed "Adjust lighting" section in the Lighting tab. No rendering jargon in the UI.
- **Camera:** opens with a short swoop to a default view chosen so the key pieces face the camera (here: TV wall, sofa and the guitar corner). Selecting a piece eases in on its front; Esc, "All items" or deselecting eases back to where you were; "Reset view" returns to the default. Zoom and pan are limited so you cannot get lost or go under the floor.
- **Lights** are a fixed set that fades in and out (about 600 ms) instead of being added and removed, so switching scenes never stutters or recompiles shaders. Selected and hovered pieces get a soft outline rather than a box.
- **Detail panel:** live turnable 3D preview (photo toggle when a product image exists), human placement wording ("Against the wall", "Facing the sofa", "On shelf 2"), and "Why this fits you". Put a short explanation chain on an item as `"why_chain": {"signals": ["A", "B"], "reading": "C"}`; without it the panel shows free-text `why` if real, otherwise a dashed, labelled "Sample" placeholder.
- **Design system** (`viewer/src/styles.css`): type scale 11/13/14/17/20, 4/8 px spacing, radii 8/12/16/pill, one terracotta accent for primary actions, selected states and switches, one focus ring. All controls are keyboard operable.
- **Mobile (<= 820 px):** compact header; the panel becomes a bottom sheet (drag handle toggles peek / expanded).
- **States:** loading screen while the scene compiles, friendly errors if data fails, WebGL cannot start or the GPU context is lost.
- **Sample room:** a living room. The sofa faces a wall-mounted TV (owned, size assumed: 55-inch, about 123 x 71 cm) above a media console; the bookshelf, guitar, amp and spotlight form a secondary music corner on the east wall.


## Qloo usage

Qloo (https://hackathon.api.qloo.com, GET only, `X-Api-Key` header) is the cultural-intelligence layer: it supplies what an LLM alone would
guess at. `ptah/qloo.py` is the client; every call is cached on disk, retried on 429/5xx, and counted against a per-run cap.

| Qloo call | Used for |
|---|---|
| `/search` (`types=urn:entity:*`) | `qloo_search` tool and brief seeding: name -> Qloo entity id. Candidates are accepted only on a near-exact name match, so a loose hit never becomes a taste signal. |
| `/v2/insights`, `filter.type=urn:tag`, `filter.tag.types=urn:tag:decor:qloo` or `urn:tag:ambience:qloo`, `filter.parents.types=urn:entity:place`, `signal.interests.entities` | **Taste analysis** (`qloo_taste_tags`, brief `taste`): which interior decor styles and ambiences the person's entities map to. Run once for the whole taste and once per entity to explain each tag. |
| `/v2/insights`, `filter.type=urn:entity:place`, `filter.tags=<category>,<decor tag>` + `operator.filter.tags=intersection`, `filter.location.query`, `filter.exclude.tags` (landmarks, counter-service) | Brief `inspiration_places`: hotels, restaurants, bars and cafes with strong interiors in the top decor styles, in the person's city. |
| same, `filter.tags=<shop category>`, `filter.exclude.tags` (department store, warehouse club, home improvement, mall, wholesaler, gift shop, appliance store) | Brief `local_shops`: furniture, home goods, antiques, flea markets, galleries, record stores. |
| `/v2/insights`, `filter.type=urn:entity:brand`, `filter.results.entities=<catalog ids>` | Brief `brands`: ranks the editable candidate catalog (`ptah/catalog/home_brands.json`) for this taste. Qloo has no home-brand category tag, so we score a named candidate list instead of filtering. |
| `/v2/insights`, `filter.type=urn:entity:movie\|artist\|book`, `filter.exclude.entities=<inputs>` | Brief `discovery`: films, artists and books the taste predicts but the person did not list (poster / print ideas). |
| `feature.explainability=true` on every entity query | Each result's `why`: which input entities contributed most, with scores. `qloo_related` returns it too. |
| `/v2/tags` | `qloo.tags()` helper for finding valid tag ids (not used at runtime by the brief). |

Noise is removed with documented filters only (tag exclusions, intersections, location), never name blocklists. Invalid Qloo parameters are
silently ignored by the API, so every parameter above was checked against the Entity Type Parameter Guide.

Brief: `python run_brief.py [--profile data/taste_profile.json] [--entity "Name:type" ...] [--location "New York City"]`. Without a
profile it seeds from `data/music.json` (each artist resolved via `/search`, near-exact matches only, deduplicated). Output contract:
`docs/contracts.md` ("brief.json").

Caching and terms: responses are cached privately under `cache/qloo/` (keyed by a hash of path + query params, no TTL; the API key is never
part of a key, a cache file, a log line or an error). Qloo's terms allow private server-side caching but forbid publishing response data, so
`cache/`, `data/` (including `brief.json`) and `docs/qloo_probe/` are git-ignored and tests use synthetic fixtures (`tests/qloo_fakes.py`).
Cache hits are free; `--qloo-cap` (brief, default 400) and `qloo.reset(cap)` (LLM tools, default 60) limit network calls.

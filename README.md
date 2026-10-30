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
- `QLOO_API_KEY`: the Qloo tools are stubs; if this is set they raise `NotImplementedError` until the real client exists.

## Commands
```
python run_taste.py --dry-run   # no LLM; checks tools, schemas, RLM construction
python run_taste.py             # real run -> data/taste_profile.json
python run_taste.py --qloo-cap 30
```
Layout: `ptah/schemas.py` (models), `ptah/data.py` (post tools), `ptah/qloo.py` (stub tools, cache, call cap), `ptah/taste.py` (RLM program).

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
`emit_offset`, coloured by Kelvin or `rgb`, with glowing shades. Pipeline: three.js + N8AO ambient occlusion + bloom + AgX tone mapping (toggle ACES in the Room tab).
- Dock under the room: scenes (Day, Evening, Reading, Night, All off) are presets over the ambient/task/accent layers (default: Evening) and a time-of-day slider drives the sun, which only enters through the real window openings, plus sky/IBL falling off at night.
- Lighting tab: each layer has an on/off and dimmer, fixtures (collapsed) have on/off, dimmer and colour temperature. If `lighting.json` exists: lux heatmap toggle, zone pins, layer coverage, focal points and issues (per scene when `scenes.<name>` is present).
- Room tab: floor presets (light_oak, mid_oak, dark_wood, herringbone, concrete, tile_light, tile_dark, carpet) at real-world tile scale, wall colour, suggested paint swatch ("can't paint" state when `can_paint` is false), render quality toggles.
- Floor textures are procedural by default. To use real CC0 PBR maps put `diffuse|normal|roughness.jpg` in `viewer/public/textures/<preset>/` (`npm run fetch-textures`, see `viewer/public/textures/DOWNLOAD.md`); files win when present.
- All v2 fields are optional; old data renders with default models and no lights. Test data: `?data=sample-lit/` (fully lit room), `?data=sample-legacy/` (v1 fields only). Other URL params: `scene=day|evening|reading|night|all_off`, `time=21.5`, `floor=herringbone`, `heat=1`, `tab=lighting|room`, `ao=0`, `bloom=0`, `tone=aces`, `select=<item id>`, `cam=x,y,z,tx,ty,tz`.
- Needs `npm install` again (new deps: `n8ao`, `postprocessing`). Screenshots: `viewer/screenshots/`.


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

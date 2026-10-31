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

**Onboarding + live progress (same app).** Open `/` and the app starts at a welcome screen: *Design my room* (taste step: Instagram ZIP and/or things you love through the Qloo typeahead; room step: sample room or your own `room.json`, things you own, budget, "I can paint") or *Try with a sample person* (`POST /api/demo`). Submitting posts `POST /api/jobs`, then the progress screen follows `GET /api/jobs/{id}/events` and, on `result`, fades into the 3D room. Routes are query strings, so they survive a refresh: `/` welcome, `?step=taste|room`, `?job=<id>` progress, `?data=<prefix>` the viewer alone (a finished job is `?data=/api/jobs/<id>/viewer/`; `?sample=1` the bundled sample). The frontend talks to the same origin; with the Vite dev server use `?api=http://localhost:8000` (the backend allows localhost origins; the choice is kept for the tab). Code: `viewer/src/Shell.tsx` (routing, hand-off), `viewer/src/onboarding/*` (screens, `useJobStream.ts` = EventSource + polling fallback, `jobEvents.ts` = event reducer).
The welcome screen is a small chunk; the form, the progress screen and the 3D viewer (three.js, the bulk of the bytes) are separate lazy chunks that are fetched in the background (the viewer when the browser is idle after the first paint).
**Logo:** the Djed-P knocked out of a sharp square of ink (`Logo` component, inline SVG that follows the day / night tokens). `public/brand/logo.svg` (day), `logo-dark.svg` (night) and `favicon.svg` (switches with `prefers-color-scheme`) are the same drawing for the favicon and anything outside the app; the pre-React loading screen in `index.html` inlines it. The glyph path lives in `src/brand/Logo.tsx` (`DJED_PATH`).
**Dev-only mock stream** (development builds only, absent from `npm run build`): `?mockEvents=/dev-fixtures/mock-events.json&mockSpeed=4&mockData=/sample/` replays a recorded event stream client-side (`dev-fixtures/mock-events-error.json` ends in a retryable error and replays a resume), used for screenshots. Regenerate the sanitised fixtures with `python3 scripts/make-mock-events.py`.

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
**Ostracon design language.** The app is an architect's drawing set (spec and mockups in `design/directions/ostracon/`). Every screen is a sheet: outer hairline, margin references (A to H, 1 to 6), a 2 px frame, a body on a 24 px canon grid and a title block (mark, project, sheet number, status, the one persistent action, day / night switch). The seven pipeline stages are sheets S-01 to S-07 and the item list is a schedule of furnishings.
- **Tokens** (one source of truth): `src/tokens.css` (colour, type scale 12.5 / 14 / 17 / 20 / 28 / 44 / 64 / 114 px, space on a 24 px module, rules 1 / 1.5 / 2 px, radius 0, no shadows, one easing and two durations, layers) and `src/tokens.ts` (the same colours for code that cannot read CSS: the 3D outline colours, the light-map ramp, the zone status marks). Palette: stone `#d5d3ca`, paper `#e6e4dc`, chalk, well, carbon `#1b1a18`, graphite, pencil, and rubric `#b1232b`, used only for what is live or chosen (the active stage, the chosen piece, a running service, a selected control). Night sheet: `[data-theme='night']` (stone `#101112`, paper `#1a1b1d`, carbon `#dcdad1`, rubric `#e5594f`). `index.html` picks day or night before first paint (saved choice, else `prefers-color-scheme`); the title block's last cell switches and remembers it (`src/theme.ts`). Small red text uses `--rubric-ink` (4.6:1 or better on stone, paper, chalk, well and the selected-row wash); `--pencil` is for lines only.
- **Fonts** are self-hosted in `public/fonts/` (Newsreader variable roman with optical size, Newsreader italic 400, Barlow Semi Condensed 400 / 500 / 600; latin subset, SIL OFL, licence texts alongside), `font-display: swap`, the three primary files preloaded in `index.html`.
- **Components:** square buttons (primary = carbon with rubric registration corners on the one main action), rectangular tags (never pills), box-and-square-knob switch, ruled slider with a rubric diamond, tabs with a rubric underline, panels with a lettered title cell, hatched band for faults, dimension lines with 45 degree ticks. Icons are one 1.25 px single-stroke set (24 grid) and furniture pictograms (48 grid, 1.5 px) in `src/ui.tsx`.
- **Room sheet:** the 3D view is a plate on the canon grid (the canvas is transparent; the plate is the `--well` colour in CSS, so it never drifts with tone mapping). Numbered balloons on the room match the schedule (first nine rows, plus whichever piece you point at or choose); the chosen piece gets a tag with a leader line and a rubric outline. The control strip under the plate has the time-of-day scale (scene stops) and the ambient warmth dial (a drafted semicircle with 6 degree ticks and an inked arc). Choosing a piece opens its card inside the schedule (photo or 3D, price, Buy at store or Shop similar, the "Why this fits you" annotation, placement, light controls).
- **Motion:** things are plotted, not faded. Lines draw (stroke-dashoffset), the progress ruler fills, the caption has a blinking plotter cursor, pieces appear on the plan with a stepped reveal. `prefers-reduced-motion` shows the finished drawing.
- **Mobile (<= 820 px):** the frame and references go, the title block becomes the header (room view) or the action bar (forms, progress); the schedule is a bottom sheet; the warmth dial is hidden (use the Lighting tab).

- **One primary control:** the time-of-day scale under the room (scene stops). Everything advanced (time of day, group and per-light brightness, warmer/cooler tone, light map) sits behind a single collapsed "Adjust lighting" section in the Lighting tab. No rendering jargon in the UI.
- **Warmth dial:** pick a light group (Ambient / Task / Accent) and turn one semicircular dial around a bulb from Candlelight to Daylight; the bulb, its glow (follows the brightness slider) and the room all use the same Kelvin-to-colour function. Select a lamp and the same dial controls just that lamp. Drag the knob, or focus it and use arrow keys / PageUp / PageDown / Home / End (`role="slider"`; the Kelvin figure is shown next to the name, as on a drawing). Changing warmth does not turn the scene into "Custom".
- **Camera:** opens with a short swoop to a default view chosen so the key pieces face the camera (here: TV wall, sofa and the guitar corner). Selecting a piece eases in on its front; Esc, "All items" or deselecting eases back to where you were; "Reset view" returns to the default. Zoom and pan are limited so you cannot get lost or go under the floor.
- **Lights** are a fixed set that fades in and out (about 600 ms) instead of being added and removed, so switching scenes never stutters or recompiles shaders. Selected and hovered pieces get a soft outline rather than a box.
- **Item previews:** list rows show the product photo (`image`), otherwise a 3D snapshot of the piece drawn once by one shared offscreen renderer and cached (never one WebGL context per row), with the piece's pictogram while it is made. A one-line description ("Sage boucle 3-seat sofa · 170 cm · tapered oak legs") is generated from template, params, material, colour and size; the retailer `product.title` wins when present.
- **Detail panel:** product photo first (`image`) with a 3D toggle (photo or live 3D sits in the card that opens under the chosen row of the schedule), retailer card (`product: {title, store, price, url}`) with a Buy button, "Yours" for `owned`, "Designed for you" for `designed: true`, live turnable 3D preview otherwise, human placement wording ("Against the wall", "Facing the sofa", "On shelf 2"), and "Why this fits you". Put a short explanation chain on an item as `"why_chain": {"signals": ["A", "B"], "reading": "C"}`; without it the panel shows free-text `why` if real, otherwise a dashed, labelled "Sample" placeholder.
- **Design system:** see the Ostracon notes above (`src/tokens.css`, `src/css/*.css`, `src/onboarding/onboarding.css`). All controls are keyboard operable; one rubric focus ring.
- **Mobile (<= 820 px):** compact header; the panel becomes a bottom sheet (drag handle toggles peek / expanded).
- **States:** one loading screen from first paint (inline in `index.html`) through data fetch and shader compile, then the intro swoop; friendly errors (with collapsible technical details) if data fails, WebGL cannot start or the GPU context is lost; empty rooms, missing photos, prices, lamps and taste data all degrade quietly.
- **Floor texture files** are opt-in: add `?textures=1` to look for `public/textures/<preset>/` files (probing by default only produced console 404s).
- **Onboarding (welcome, taste, room):** one task per screen, two form steps with the action in the title block; "Continue" stays disabled with a visible reason until there is at least one source. Instagram: drag-and-drop or file picker, client-side `.zip`/size check, three-line instructions and a plain statement of what is read (saved posts, likes, saved music) and that the ZIP is deleted after reading. Favourites: ARIA combobox on `/api/qloo/search` (250 ms debounce, in-flight requests cancelled, arrow keys + Enter + Esc, Backspace removes the last chip; falls back to "add as typed" when Qloo is unavailable). Room: sample rooms from `/api/rooms`, own `room.json` (checked client-side), "scan" shown as coming soon; owned items by quick chips (TV with size, guitar, amp, custom); budget slider and a paint toggle. Upload shows real progress (XHR) and can be cancelled; server errors (HTML export, empty export, bad room) are put on the card they belong to and the step is reopened.
- **Live progress:** one monotonic bar (eased with a CSS transform, never moves backwards, resumes correctly after a refresh because the stream replays from event 1), the stage caption from `stage_progress`/`stage_start`, a "now" line with the latest request (each shown for at least 650 ms so it is readable), the stage list with done / active / pending / skipped and durations, and three service cells (Qloo, AI, Shopping) that turn rubric while at least one request of that service is open (a counter per service, `activity_start`/`activity_end` paired by id; the pill also shows how many were done and a tooltip explaining what the service does for you). The "Activity" log (always visible beside the plan) keeps the last 400 lines. State changes reach React at most 10 times per second; screen readers are told only when the step changes. `prefers-reduced-motion` turns the animations off (the active service keeps its rubric fill and underline). Errors use the friendly `label`/`hint` from the server; a retryable error offers **Resume** (`POST /api/jobs/{id}/resume`, the stream continues from the last `seq`), every error offers Start over, and "Cancel and delete my data" / "Delete my data" call `DELETE /api/jobs/{id}`. The 3D view's title-block menu has "Design another room" and "Delete my data".
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


## Backend (upload -> background pipeline -> live progress -> viewer)

FastAPI app in `server/`, orchestration in `ptah/pipeline.py`, events in `ptah/events.py` (contract: `docs/contracts.md`, "Pipeline events").
```
pip install -r requirements.txt           # adds fastapi, uvicorn, python-multipart, httpx
cd viewer && npm run build && cd ..        # optional: the server then serves the viewer (and the onboarding UI) at /
python -m server --port 8000               # http://127.0.0.1:8000  (API docs: /api/docs)
python tools/record_demo.py                # once: records data/demo (events + bundle) from a finished run for POST /api/demo
pytest -q tests                            # backend tests need no keys, network or LLM
```
One worker thread runs one job at a time (Qloo state, DSPy settings and the posts folder are process-wide); the event loop never does file parsing or
solving. Keys come from `.env`; the server never logs or returns them (errors are scrubbed). Jobs live in `data/jobs/<id>/` (git-ignored; override with
`PTAH_JOBS_DIR`), are removed by `DELETE`, and expire after 72 h. The uploaded Instagram zip is deleted as soon as it is ingested; only derived files stay.

| Endpoint | |
|---|---|
| `POST /api/jobs` | multipart: `instagram` (zip, JSON export), `loves` (JSON list of `{name, type?, qloo_id?}` or `"A, B"`), `room_id` (sample room id, default `sample-living-room`) or `room` (room.json file), `scan` (stored as a stub, not used yet), `owned` (JSON `{sample: bool, items: [{name, category, w, d, h}]}`), `preferences` (JSON `{budget, can_paint, location}`). Needs `instagram` and/or `loves`. 201 `{job_id, events_url, viewer_url, ...}`; 400 `{error: {code, message}}` (HTML-format export, not a zip, bad room, ...); 413 over 300 MB (`PTAH_MAX_UPLOAD_MB`). Streams to disk, constant memory. |
| `GET /api/jobs/{id}` | status (`queued\|running\|done\|error\|cancelled\|interrupted`), `progress`, `stage`, `plan`, `queue_position`, `viewer_url` when done |
| `GET /api/jobs/{id}/events` | SSE, see the contract; `Last-Event-ID` header or `?last_event_id=` resumes; 204 when there is nothing left |
| `GET /api/jobs/{id}/viewer/<file>` | the bundle (path-safe, gzip, ETag); open with `/?data=/api/jobs/{id}/viewer/` |
| `POST /api/jobs/{id}/resume` | retry a failed / interrupted job; finished stages are skipped |
| `DELETE /api/jobs/{id}` | cancel and delete everything (200 deleted, 202 cancelling) |
| `GET /api/qloo/search?q=&type=&take=` | typeahead for "things you love" (cached, does not count as job activity); 503 `qloo_not_configured` without a key |
| `GET /api/rooms`, `GET /api/health` | sample rooms; capabilities (`qloo`, `llm`, `demo`, `max_upload_mb`) |
| `POST /api/demo?seconds=45` | replays a recorded run (events + finished bundle) through the same SSE path: no keys, no network, no LLM |

Stages: `ingest` (zip -> posts.jsonl) -> `taste` (DSPy RLM + Qloo; result cached by a hash of the posts, so the same export is read once; typed-in favourites
are resolved through Qloo and merged; if the AI step fails, favourites or saved-music artists still give a profile) -> `brief` (Qloo) -> `design` (one LLM call,
cached by input hash) -> `products` (cohesive SerpApi curation; a failure keeps the designed pieces) -> `layout` -> `lighting`. `products`, `layout` and `lighting`
failures end in a `result` with `partial: true`; the others end in an `error`.

Env: `PTAH_LM`, `PTAH_SUB_LM`, `OPENROUTER_API_KEY`, `QLOO_API_KEY`, `SERPAPI_KEY` (see above), `PTAH_DESIGN_MAX_LLM_CALLS` (design-call ledger cap, default 3 for the CLIs, 50 for
the server), `PTAH_SCOUT_MAX_SEARCHES` (live shopping searches per job, default 12), `PTAH_QLOO_CAP_TASTE` / `PTAH_QLOO_CAP_BRIEF`, `PTAH_DEFAULT_LOCATION`, `PTAH_CORS_ORIGINS`
(localhost origins are always allowed, for the Vite dev server).

Measured on this machine (loopback): a 105 MB zip uploads in ~0.13 s with the server at 48 MB RSS (nothing is buffered); ingesting a real 1,157-post export takes ~35 ms; SSE
events reach the client in under 1 ms (median) after they are emitted; `items.json` (62 KB) goes out as 16 KB gzip. Slow parts are the model calls (taste several minutes, design ~45 s) and
`products` image analysis (~40 s even with every search cached).

# Technical notes

Reference for configuration, commands, the API and how each stage works. Start with the [README](../README.md); JSON formats are in [contracts.md](contracts.md).

## Configuration

All settings come from the environment or the git-ignored `.env` file.

| Variable | Default | What it does |
|---|---|---|
| `QLOO_API_KEY` | | Qloo key. Without it the pipeline refuses to run (the CLIs need `--allow-stub-qloo` for offline tests). |
| `QLOO_BASE_URL` | `https://hackathon.api.qloo.com` | Qloo endpoint |
| `PTAH_LM` | | Main model, litellm style (e.g. `openrouter/<model>`), plus that provider's key (e.g. `OPENROUTER_API_KEY`) |
| `PTAH_SUB_LM` | `PTAH_LM` | Cheaper model for the taste agent's sub-queries |
| `PTAH_MAX_ITERS`, `PTAH_MAX_LLM_CALLS` | 20, 50 | Taste agent limits |
| `PTAH_DESIGN_MAX_LLM_CALLS` | 3 (CLI), 10 (server) | Cap on design-stage model calls |
| `SERPAPI_KEY` | | Google Shopping search |
| `PTAH_SCOUT_MAX_SEARCHES` | 12 | Live shopping searches per job (cache hits are free) |
| `PTAH_QLOO_CAP_TASTE`, `PTAH_QLOO_CAP_BRIEF` | 60, 400 | Qloo network calls per stage (cache hits are free) |
| `PTAH_DEFAULT_LOCATION` | New York City | City for inspiration places and local shops |
| `PTAH_JOBS_DIR` | `data/jobs` | Where server jobs live |
| `PTAH_MAX_UPLOAD_MB` | 300 | Upload limit |
| `PTAH_CORS_ORIGINS` | | Extra allowed origins (localhost is always allowed) |
| `PTAH_VERBOSE` | 1 | Print the taste agent's reasoning while it runs |

## Commands

```bash
# pipeline, one stage at a time (all outputs go to the git-ignored data/)
python ingest_instagram.py <export folder or .zip>       # data/posts.jsonl, music.json, summary.json
python run_taste.py [--dry-run] [--qloo-cap N]            # data/taste_profile.json
python run_brief.py [--entity "Name:type" ...] [--location "City"]   # data/brief.json
python run_design.py [--spec design_spec.json] [--room room.json]    # data/runs/design_<time>/ (--spec skips the model call)
python run_scout.py --only all [--cohesive | --linked]    # real products for the newest design bundle
python tools/relayout_bundle.py <bundle> [--out <new bundle>] [--add items.json]   # re-solve layout + lighting, no model or shopping calls

# layout and lighting on their own
python -m ptah.layout room.json items.json constraints.json -o layout.json
python -m ptah.lighting room.json items.json layout.json -o lighting.json [--mode day|night --hour 9 --scene reading]
python tools/render_layout.py room.json items.json layout.json [--lighting lighting.json --scene reading] [--ascii]

# app
python -m server --port 8000          # serves the API and, if built, the viewer at /
python tools/record_demo.py           # records a finished run as the server's "Try with a sample person" replay
pytest -q tests                       # no keys, network or model calls needed
```

Viewer development (`viewer/`): `npm run dev` (http://localhost:5173, sample room), `npm run build` (static site in `viewer/dist`), `npx vite build --mode pages --base /ptah/` (the GitHub Pages demo, see `.github/workflows/pages.yml`).

## Pipeline stages

`ingest → taste → brief → design → products → layout → lighting`, run by `ptah/pipeline.py` on one worker thread (one job at a time).

| Stage | What happens | On failure |
|---|---|---|
| ingest | Reads saved posts, likes and saved music from the Instagram zip; the zip is deleted right after | error |
| taste | DSPy `RLM` agent explores the posts and calls Qloo tools (cached per export). Typed favourites are resolved through Qloo `/search` and merged. With favourites only, no model call is made | falls back to favourites or saved-music artists |
| brief | Qloo taste analysis, places, shops, brands and discovery (see below) | error |
| design | One model call turns taste + brief into a spec: palette, materials, lighting mood, pieces with "why" chains (cached by input) | error |
| products | SerpApi Google Shopping per piece, cohesion-scored, real merchant links, alternatives | keeps the designed pieces (`partial: true`) |
| layout | Constraint solver (below) | `partial: true` |
| lighting | Lux estimate per scene, lamp strengths tuned until the checks pass | `partial: true` |

## Backend API

FastAPI in `server/`. Keys are never logged or returned. Jobs live in `data/jobs/<id>/` and expire after 72 h. Interactive docs at `/api/docs`.

| Endpoint | |
|---|---|
| `POST /api/jobs` | multipart: `instagram` (zip) and/or `loves` (JSON list of `{name, type?, qloo_id?}`), `room_id` or `room` (room.json), `owned`, `preferences` (`{budget, can_paint, location}`) |
| `GET /api/jobs/{id}` | status, progress, current stage, queue position |
| `GET /api/jobs/{id}/events` | live progress (server-sent events); resumes with `Last-Event-ID` |
| `GET /api/jobs/{id}/viewer/<file>` | the finished room; open with `/?data=/api/jobs/{id}/viewer/` |
| `POST /api/jobs/{id}/resume` | retry a failed job, skipping finished stages |
| `DELETE /api/jobs/{id}` | cancel and delete everything |
| `GET /api/qloo/search?q=&type=` | typeahead for "things you love" |
| `GET /api/rooms`, `GET /api/health` | sample rooms; what is configured |
| `POST /api/demo` | replays a recorded run through the same progress stream (no keys needed) |

## Qloo calls

`ptah/qloo.py` caches every response on disk (`cache/qloo/`), retries 429 and 5xx, and counts calls against a per-stage cap. Qloo's terms allow private caching but not publishing response data, so `cache/` and `data/` are git-ignored and tests use synthetic fixtures (`tests/qloo_fakes.py`).

| Call | Used for |
|---|---|
| `/search` | Name → Qloo entity. Only near-exact name matches count; the agent cannot cite an entity Qloo did not return (UUID, provenance, name and type checks) |
| `/v2/insights`, `filter.type=urn:tag`, `filter.tag.types=urn:tag:decor:qloo` / `urn:tag:ambience:qloo`, `filter.parents.types=urn:entity:place` | Decor and ambience styles of places people with this taste love; once for the whole taste and once per entity to explain each tag |
| `/v2/insights`, `filter.type=urn:entity:place`, category + decor tag intersection, `filter.location.query` | Inspiration places with strong interiors |
| same with shop categories and exclusions (department stores, malls, ...) | Local furniture, antique and record shops |
| `/v2/insights`, `filter.type=urn:entity:brand`, `filter.results.entities` | Ranks the home-brand catalog in `ptah/catalog/home_brands.json` |
| `/v2/insights` for movies, artists and books, excluding the inputs | Discovery picks (cross-domain signal and print ideas) |
| `feature.explainability=true` | Which inputs drove each result |

## Layout solver

`ptah/layout/` places floor items by depth-first search over candidate poses with backtracking (after Holodeck, CVPR 2024, and LayoutVLM, CVPR 2025). It is deterministic and bounded by an expansion budget.

- **Hard rules:** inside the floor, no overlaps, door swings clear, tall pieces not in front of windows, a 60 cm walkway from every door to the room centre (75 cm preferred).
- **Soft constraints** (from `constraints.json`): `edge`, `middle`, `near`, `far`, `in_front_of`, `side_of`, `face`, `center_align`, `under`; per item `anchor`, `hint {x, z, rotation_deg}` and `zone`. Ergonomics (table distance from the sofa, seating faces in, window light) are added automatically.
- **Balance:** complete layouts lose score when the furniture's visual mass sits far from the room centre (`stats.balance_offset`).
- **Mounted items** (`on_top_of`, `wall_mounted`, `ceiling`, `attach_to`) are placed after the floor and checked in 3D.
- Unplaceable items are reported with a reason and the largest size that would have fit.
- Rotation follows three.js: 0 faces +z, 90 faces +x.

## Lighting model

`ptah/lighting.py` is approximate design guidance, not a simulation. Lights belong to layers (ambient, task, accent), and scenes are layer presets: `day`, `evening`, `reading`, `night`. Each scene gets a lux grid at work-plane height (point sources with shade lobes, spots, LED strips, inverse-square and cosine, a bounce term from the finishes, daylight through the windows by hour) and is checked against targets: about 300 lux for reading, about 100 lux ambient at night, and focal pieces accent-lit. Issues (`DIM_TASK_ZONE`, `NO_AMBIENT`, `NO_ACCENT_LAYER`, `UNLIT_FOCAL_POINT`, `GLARE_RISK`) each carry a suggestion the design step can act on.

## Viewer

React, three.js and react-three-fiber. Every item is a procedural low-poly model chosen by `model.template`, sized to its dimensions, with PBR materials; items with `light` become real light sources.

- **Data:** `?data=<url prefix>` loads a bundle (`room.json`, `items.json`, `layout.json`, optional `lighting.json` and `taste.json`); default is the bundled sample (the demo room in the Pages build).
- **URL parameters:** `scene=day|evening|reading|night|all_off`, `time=21.5`, `floor=<preset>`, `tab=lighting|room`, `select=<item id>`, `cam=x,y,z,tx,ty,tz`, and `ao=0`, `bloom=0`, `tone=aces` for slow machines.
- **Dev-only replay:** `?mockEvents=/demo/events.json&mockData=/demo/room/&mockSpeed=4` replays the demo's progress stream without a backend. `python3 scripts/make-mock-events.py` rebuilds that file from a recorded run, with personal labels removed.
- **Floor textures** are procedural; to use real PBR maps put `diffuse.jpg`, `normal.jpg` and `roughness.jpg` in `public/textures/<preset>/` and add `?textures=1`.
- **Design tokens** (colour, type, spacing) live in `src/tokens.css` and `src/tokens.ts`.

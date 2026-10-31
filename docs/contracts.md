# Data contracts

The JSON files that pass between Ptah's stages and the viewer. Every field beyond the basic ones is optional; older data still renders.

**Conventions.** Lengths are centimetres. The floor plane is x (right) and z (depth), y is up, and the origin is a floor corner. Positions are item centres on the floor. An item's `w` runs along its local x, `d` along its local z (front to back), `h` is height; for wall-hung items `w` runs along the wall and `d` out of it. `rotation_deg` follows three.js about +y: 0 = the front faces +z, 90 = +x, 180 = −z, 270 = −x (front vector = (sin r, cos r)). An item with its back on the wall at z=0 has rotation 0; with its back on the wall at x=0, rotation 90.

## room.json

```json
{
  "name": "Living room",
  "height": 260,
  "floor": [[0,0],[580,0],[580,480],[0,480]],
  "walls": [{"id": "w1", "start": [0,0], "end": [580,0]}],
  "openings": [
    {"id": "door1", "type": "door",   "wall": "w1", "offset": 40,  "width": 85,  "height": 205, "swing": 90},
    {"id": "win1",  "type": "window", "wall": "w3", "offset": 200, "width": 180, "height": 140, "sill": 90}
  ],
  "existing": [{"id": "rad1", "category": "radiator", "x": 470, "z": 472, "w": 100, "d": 10, "h": 60, "rotation_deg": 180}],
  "finishes": {"floor": "light_oak", "wall_color": "#f2efe8",
               "wall_paint": {"name": "...", "brand": "...", "hex": "#...", "url": null, "price": null}},
  "can_paint": true,
  "scan": {"glb": null}
}
```

- `floor` is a simple counter-clockwise polygon; `walls[i]` runs from `floor[i]` to `floor[i+1]`.
- `offset` is the distance along the wall from its `start` to the opening's near edge; `swing` is the depth of the clear zone in front of a door.
- `existing` is furniture already in the room that must not be overlapped.
- `finishes.floor` presets: `light_oak`, `mid_oak`, `dark_wood`, `herringbone`, `concrete`, `tile_light`, `tile_dark`, `carpet`. With `can_paint: false` the viewer shows the walls as fixed.

## items.json

```json
[{
  "id": "lamp-1", "category": "floor_lamp", "name": "...",
  "w": 40, "d": 40, "h": 162, "dims_estimated": false,
  "image": "images/lamp-1.jpg", "price": "$246", "url": "https://...", "source": "Store", "why": "...",
  "designed": false, "owned": false, "focal": false,
  "model": {"template": "floor_lamp", "params": {"shade": "cone", "arc": true}},
  "colors": {"primary": "#c9a45c", "secondary": "#f1e8d3", "accent": null},
  "material": {"primary": "brass", "secondary": "linen"},
  "light": {"layer": "task", "kind": "point", "color_temp_k": 2700, "lumens": 2400, "dimmable": true, "rgb": null, "emit_offset": [0, 152, 0]},
  "why_chain": {"signals": ["Cozy (ambience tag, Qloo affinity 0.77)"], "inputs": [], "tags": [{"kind": "ambience", "name": "Cozy", "affinity": 0.766}],
                "reading": "...", "decision": "..."},
  "alternatives": []
}]
```

**Basics.** `image` is a same-origin path relative to the bundle (or null). `url` is a merchant page, or the Google Shopping page for the listing; links are never built by hand. `designed: true` marks a piece the design specified but no real product matched; it shows as "Designed for you". `why_chain` records the taste signals (Qloo tags and the person's entities) behind a piece.

**Appearance.**
- `model.template` picks the procedural model: `sofa`, `armchair`, `chair`, `coffee_table`, `side_table`, `dining_table`, `desk`, `bed`, `nightstand`, `bookshelf`, `cabinet`, `tv_stand`, `tv`, `rug`, `floor_lamp`, `table_lamp`, `pendant`, `sconce`, `led_strip`, `picture_light`, `plant`, `small_plant`, `art`, `poster`, `frame`, `mirror`, `guitar`, `guitar_hanger`, `amp`, `book_stack`, `vinyl_crate`, `vase`, `cushion`, `throw`, `candle`, `tray`, `generic`.
- `model.params` depend on the template, for example `shade` (`drum|cone|globe|dome|none`), `legs` (`tapered|straight|hairpin|block|none`), `arms`, `arc` (arc floor lamps), `shelves`, `body` (`superstrat|single_cut|acoustic`), `form` (`bottle|round|cylinder`), `count` (book stack), `frame` (`thin|none`), `drape` (`left|right`).
- `material.*`: `oak`, `light_wood`, `walnut`, `dark_wood`, `linen`, `cotton`, `velvet`, `boucle`, `wool`, `leather`, `rattan`, `brass`, `black_metal`, `chrome`, `ceramic`, `glass`, `marble`, `stone`, `plastic`, `painted`.
- Category aliases (`guitar_amp` → `amp`, `pillow` → `cushion`, ...) live in `ptah/products/enrich.py`.

**Lights.** An item with `light` is a light source. `layer`: `ambient|task|accent`. `kind`: `point|spot|area|line` (line = LED strip as long as `w`). `rgb` overrides the colour temperature. `emit_offset` is the bulb position in item-local cm from the base centre (world position = (x, y + oy, z) with ox, oz rotated by `rotation_deg`). Optional: `cone_deg` (spot beam, default 60), `aim` (item-local direction), `target` (item a light highlights), `color_changing`, `estimated` (fields guessed from the listing).

**Owned items.** `owned: true` means the person already has it: it is always included, has no price or link, is left out of every total and shows as "Yours". `user_colors` (same shape as `colors`) wins over photo-extracted colours key by key; a null value does not erase a photo colour. `focal: true` makes a piece a focal point that must be accent-lit; `focal: false` opts a default focal category (bookshelf, art, ...) out.

**Alternatives.** Up to 4 real candidates per piece, best first (passing options, then the closest near-misses):

```json
{"key": "a31455c264", "title": "...", "store": "Wayfair", "price": "$1,959.99",
 "url": null, "google_url": "https://www.google.com/search?ibp=oshop&...", "other_stores": [],
 "image": "images/alt/sofa-1-a31455c264.jpg", "colors": {"primary": "#25364e", "secondary": "#516479", "accent": "#708aa2"},
 "score": 0.81, "passes": false, "misses": ["noticeably darker than designed"],
 "cohesion": {"total": 0.81, "parts": {"palette": 0.92, "material": 0.65, "style": 0.73, "scale": 0.7, "quality": 1.0}, "gate": {"accepted": false, "reasons": []}},
 "product_id": "...", "query": "...", "w": 40, "h": 60}
```

- `key` is stable within the item. `url` is set only when the product detail gave a merchant page. `misses` are plain-language differences (at most 3). `w`/`h` appear on wall art only (the size this print would hang at).
- A designed piece the cohesion gate could not match keeps its best candidate as `product_candidate`.
- Small textiles (`throw`, `cushion`) get a wider colour tolerance in the gate (`cohesion.TEXTILE_GATE`).
- In the viewer, picking an alternative replaces the piece's name, image, price, link, store and primary colour (geometry and layout stay) and is remembered per bundle in localStorage (`ptah.choices.v1:<bundle url>`).

## constraints.json

```json
{
  "console-1": {"anchor": true, "hint": {"x": 290, "z": 20, "rotation_deg": 0}, "constraints": [{"type": "edge"}]},
  "sofa-1":    {"anchor": true, "zone": "conversation", "constraints": [{"type": "face", "target": "console-1"}, {"type": "center_align", "target": "console-1"}]},
  "table-1":   {"zone": "conversation", "constraints": [{"type": "middle"}, {"type": "in_front_of", "target": "sofa-1"}, {"type": "near", "target": "sofa-1"}]},
  "tv-1":      {"constraints": [{"type": "wall_mounted", "height": 100}, {"type": "center_align", "target": "console-1"}]},
  "vase-1":    {"constraints": [{"type": "on_top_of", "target": "shelf-1", "level": 2}]}
}
```

**Per item.** `anchor: true` places it first. `hint {x, z, rotation_deg}` is a rough pose; it adds candidates around it and a soft pull (exp(−dist/80 cm), ×0.7 if the rotation is off by more than 45°) but never overrides hard rules. `zone` gives items a shared cohesion score (full within 150 cm of the other members, zero at 400 cm), scored for wall items too.

**Floor constraints.** `edge` (back against a wall), `middle` (away from walls), `near` / `far`, `in_front_of`, `side_of`, `face`, `center_align`, `under` (flat items such as rugs). Targets can be item ids, opening ids (`win1`, `door1`), existing-object ids or `room_center`; `face`, `near` and `far` without a target use the room centre. `near`/`far` between a floor item and a mounted item is scored from the mounted side.

**Mounted constraints** take an item off the floor and derive its pose after the floor is solved:
- `{"type": "on_top_of", "target": "side-1", "surface": "top|seat|shelf", "level": 0}`: sits on the target. `seat` is the sofa or armchair seat in front of the back cushions; `level` is a bookshelf board counted from the bottom (implies `shelf`). Without either it sits centred on top.
- `{"type": "wall_mounted", "height": 160, "wall": "w2"}`: back flush to a wall, base at `height`; the position along the wall follows the item's other constraints. `wall` is optional.
- `{"type": "ceiling", "above": "table-1", "drop": 40}`: hangs `drop` cm below the ceiling (y = room height − drop − h, default drop 30), centred over `above` or the room centre.
- `{"type": "attach_to", "target": "shelf-1", "side": "back|under|top|above", "height": 168}`: follows the target. `back` = flush behind it, `under` = beneath, `top` = on top, `above` = 3 cm above the back edge (picture lights). Defaults: picture lights `above`, LED strips `back`, otherwise `top`.

## layout.json

```json
{
  "room": "room.json",
  "placements": [
    {"item_id": "sofa-1", "x": 290, "z": 388, "rotation_deg": 180, "y": 0, "satisfied": ["face:console-1", "zone:conversation"], "violated": []},
    {"item_id": "vase-1", "x": 565, "z": 157, "rotation_deg": 270, "y": 76, "mount": "on_top_of", "parent": "shelf-1", "surface": "shelf", "level": 2, "elevation": 76,
     "satisfied": ["on_top_of:shelf-1"], "violated": []}
  ],
  "unplaced": [{"item_id": "shelf-2", "reason": "no wall space", "max_w": 60, "max_d": 30}],
  "score": 0.989,
  "warnings": []
}
```

- Every placement has `y` (base elevation). Mounted items also carry `mount` (`on_top_of|wall|ceiling|attach`), `parent` (for `on_top_of` and `attach`), `wall`, `side`, `above`, and for surfaces `surface` and `level`; `elevation` repeats `y` for `on_top_of`.
- `satisfied` / `violated` list constraint names: `type` or `type:target`, `zone:<name>`, `hint`, `ergo:*`. A constraint counts as satisfied at a 0–1 score of 0.7 or more (0.8 for `in_front_of`, `face`, `center_align`; 0.95 for `under`; 0.5 for `zone` and `hint`).
- `score` is the mean over items of each item's weighted constraint score (unplaced items count 0), minus 0.05 when the preferred 75 cm walkway is lost.
- `unplaced[].max_w` / `max_d` give the largest footprint (same proportions) that would have fit, so a product search can retry smaller; 0 when nothing fits or a parent is missing.
- `warnings` lists ignored constraints and waived rules.

**Rules the solver enforces.**
- Hard: inside the floor; no overlaps with items or `existing` (a flat item, h ≤ 2, may lie under others; two flat items may not overlap); door swing zones clear; items taller than a window sill stay out of the 40 cm in front of it; a 60 cm path from every door to the open centre of the room (a door that cannot reach it even in the empty room is waived with a warning).
- Ergonomic, added automatically (weight 0.5, prefixed `ergo:`): `table_gap` (coffee table 35–50 cm from the sofa front), `seating_face` (seats face the room centre unless they have their own `face`), `tall_wall` (items over 150 cm prefer walls), `window_light` (tall items stay out of the 120 cm in front of a window). A 75 cm walkway is preferred.
- Balance: complete layouts are also ranked by how centred the furniture's visual mass is.
- Mounted items are 3D boxes that may not intersect floor items, `existing`, each other (parent and child excepted) or door and window spans; LED strips never collide. Wall items keep 6 cm apart (a wall item and its attachments count as one group).
- Styling density (`ptah/layout/metrics.py`): at most 55 % of a surface covered and at most 3 items per shelf board or 4 per seat or top. Several items on one surface are packed without overlap. Surface heights mirror the viewer's models (`viewer/src/models.ts`).

## lighting.json

Lights are switched by layer, and each scene is a layer preset:

| Scene | Ambient | Task | Accent | Checked |
|---|---|---|---|---|
| `day` | 0 | 0 | 0 | daylight coverage only (informational) |
| `evening` | 0.4 | 0 | 1.0 | ambient average adequate (≥ ~50 lux, target ~100) and focal points accent-lit |
| `reading` | 0.3 | 1.0 | 0.3 | every task zone reaches ~300 lux (reading) or ~500 (desk), ok from 80 % |
| `night` | 0 | 0 | 0.6 | an accent layer exists and nothing glares |

```json
{
  "mode": "night", "scene": "evening", "hour": 12.0, "grid_cm": 25, "plane_cm": 75, "origin": [0,0], "size": [24,20],
  "lux": [[null, 41, 44]], "lux_max": 668, "ambient_avg_lux": 58,
  "zones": [{"name": "reading:sofa-1", "kind": "reading", "item_ids": ["sofa-1","lamp-1"], "layer": "task", "lux": 292, "target": 300, "status": "ok", "center": [57,237], "radius_cm": 20}],
  "focal_points": [{"item_id": "shelf-1", "category": "bookshelf", "accent_lit": true, "lit_by": ["strip-1"]}],
  "layers": {"ambient": true, "task": true, "accent": true}, "layer_counts": {"ambient": 1, "task": 1, "accent": 4},
  "scenes": {"evening": {"factors": {}, "lux": [], "zones": [], "focal_points": [], "issues": []},
             "day": {"daylight": {"hour": 12, "outdoor_lux": 15000, "avg_lux": 352, "coverage_pct": 100, "informational": true}}},
  "scene_presets": {"evening": {"ambient": 0.4, "task": 0.0, "accent": 1.0}},
  "lights": [{"item_id": "lamp-1", "layer": "task", "kind": "point", "lumens": 1600, "pos": [20,150,288]}],
  "issues": [{"code": "NO_AMBIENT", "message": "...", "suggest": {"category": "pendant", "above": "table-1"}}],
  "issues_all": [{"code": "DIM_TASK_ZONE", "zone": "reading:sofa-1", "scenes": ["reading"], "message": "...", "suggest": {"category": "floor_lamp", "near": "sofa-1"}}]
}
```

- The top-level `lux`, `zones`, `focal_points` and `issues` repeat one scene (`evening` by default); `scenes` always has all four, and `issues_all` merges every scene's issues.
- `lux[row][col]`: row = z index, col = x index, cell centre = origin + (index + 0.5) × `grid_cm`, `null` outside the floor; horizontal illuminance at `plane_cm`.
- Zone `status` is `ok|dim` in the scene that judges it (task zones in `reading`, ambient in `evening`), otherwise `info`. There is one task zone per seat and desk.
- Issue codes: `DIM_TASK_ZONE`, `NO_AMBIENT`, `NO_ACCENT_LAYER`, `UNLIT_FOCAL_POINT`, `GLARE_RISK`. `suggest` is `{category, near|attach|above, side?}`, where `attach` + `side` map onto an `attach_to` constraint and `above` onto a `ceiling` constraint.
- The model is approximate design guidance (no occlusion, a uniform bounce term, daylight as overcast window patches).

## brief.json

`run_brief.py` writes `data/brief.json` (git-ignored, since it holds Qloo response data). It is built from the person's Qloo taste entities for a location. Affinities are Qloo's relative 0–1 scores. Every entry carries `why`: up to three of the person's input entities that Qloo's explainability credits most, as `[{"qloo_id", "name", "score"}]`.

```json
{
  "location": "New York City",
  "inputs": [{"qloo_id": "...", "name": "...", "type": "artist"}],
  "skipped_inputs": [{"name": "...", "reason": "no near-exact Qloo artist match"}],
  "taste": {
    "decor":    [{"tag_id": "urn:tag:decor:qloo:classic", "name": "Classic", "affinity": 0.95, "popularity": 1.0, "why": []}],
    "ambience": [{"tag_id": "urn:tag:ambience:qloo:cozy",  "name": "Cozy",    "affinity": 0.80, "popularity": 1.0, "why": []}]
  },
  "inspiration_places": [{"qloo_id": "...", "name": "...", "kind": "hotel|restaurant|bar|cafe", "category": "Hotel", "address": "...", "neighborhood": "...",
                          "website": "...", "images": ["..."], "decor_tags": [], "ambience_tags": [], "matched_decor": ["classic"],
                          "affinity": 0.85, "popularity": 0.93, "score": 0.89, "why": []}],
  "local_shops": [{"...": "same fields as places", "shop_kind": "furniture store|home goods store|antiques|flea market|art gallery|record store"}],
  "brands": [{"qloo_id": "...", "name": "...", "catalog_name": "...", "tier": "budget|mid|premium|luxury", "affinity": 0.86, "tags": [], "description": "...", "images": [], "why": []}],
  "brands_unresolved": ["catalog names Qloo does not know"],
  "discovery": [{"qloo_id": "...", "name": "...", "type": "movie|artist|book", "affinity": 0.91, "tags": [], "images": [], "description": "...", "why": []}],
  "qloo": {"network_calls": 57, "filters": {}}
}
```

- Place and shop `score` = `affinity` + 0.02 per top-3 decor tag the place carries; lists are sorted by it. Places need a photo; at most 4 per kind and 3 shops per `shop_kind`.
- `brands` are sorted by affinity; `tier` comes from `ptah/catalog/home_brands.json`. `discovery` excludes the inputs.

## Pipeline events

One job is one ordered stream of small JSON events, stored in `events.jsonl` and served as server-sent events at `GET /api/jobs/{id}/events` (`id: <seq>` + `data: <json>`, no named events; reconnects resume from `Last-Event-ID`; a finished, fully delivered job answers 204; a `: hb` comment every 15 s). Null fields are left out on the wire.

```
{ "job_id": "9f1c...", "seq": 41, "ts": 1793380000.123,
  "type": "stage_start|stage_progress|stage_end|activity_start|activity_end|log|result|error",
  "stage": "ingest|taste|brief|design|products|layout|lighting",
  "progress": 0.37,            // overall 0-1, never decreases, exactly 1.0 on result
  "stage_progress": 0.5,       // this stage 0-1
  "service": "qloo|llm|serpapi|compute",   // activity_* only
  "id": "a1b2c3d4",            // pairs activity_start with activity_end
  "label": "Qloo: tag lookup \"cozy\"",    // safe to show as-is
  "detail": {},                // at most 24 keys, short strings, never secrets
  "duration_ms": 812 }         // activity_end, stage_end
```

Stage weights of the overall bar: ingest 5, taste 45, brief 15, design 15, products 12, layout 5, lighting 3. Without an Instagram upload there is no `ingest` stage and `taste` weighs 6; weights are renormalised. The first `stage_start` carries `detail.plan = [{stage, label, weight}]`.

| Type | Meaning and detail |
|---|---|
| `stage_start`, `stage_end` | `label` is the stage title. `stage_end.detail` has counts (`posts`, `entities`, `products`, ...), `cached: true` when skipped on resume, or `skipped: true, reason` when a soft stage failed |
| `stage_progress` | progress within the stage and a label such as `Thinking about your posts (round 3 of 20)`; at most 10 per second |
| `activity_start`, `activity_end` | one live external request: `qloo` (cache hits emit nothing), `llm`, `serpapi`, or `compute` (parsing, solving, lighting). Several can overlap. `activity_end.detail.ok` is false on failure |
| `log` | a short sentence; `detail.warning: true` marks a degradation worth telling the person |
| `result` | last event of a successful job: `{viewer, partial, skipped, warnings, items, products, owned, cost?}`. `partial: true` means products, layout or lighting failed and the earlier result is shown |
| `error` | last event of a failed job: a friendly `label` and `{code, hint, retryable, stage, technical?}`. Codes: `bad_export`, `empty_export`, `bad_room`, `missing_input`, `qloo_not_configured`, `qloo_unavailable`, `qloo_cap`, `llm_not_configured`, `llm_budget`, `no_taste`, `design_rejected`, `layout_failed`, `cancelled`, `internal`. A retryable job resumes with `POST /api/jobs/{id}/resume`; finished stages replay as cached |

**Viewer bundle** (`/api/jobs/{id}/viewer/`): `room.json`, `items.json`, `layout.json`, `lighting.json`, `taste.json` (plus `budget` when given) and `images/*.jpg`, exactly what the viewer reads with `?data=<prefix>`. JSON is gzip-encoded with an ETag; images are cached for a day once the job is done.

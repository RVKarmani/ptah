# Ptah data contracts (room, items, constraints, layout)

All lengths in **centimetres**. Floor plane is x (right) / z (depth); y is up. Origin = one floor corner.
Positions are item **centres** on the floor. `rotation_deg` is rotation about y; 0 means the item's
front faces +z. An item's `w` runs along its local x, `d` along its local z (front-to-back), `h` is height.

## room.json
```json
{
  "name": "Living room",
  "height": 260,
  "floor": [[0,0],[400,0],[400,350],[0,350]],
  "walls": [ {"id":"w1","start":[0,0],"end":[400,0]}, ... ],
  "openings": [
    {"id":"door1","type":"door","wall":"w1","offset":40,"width":85,"height":205,"swing":90},
    {"id":"win1","type":"window","wall":"w3","offset":120,"width":160,"height":140,"sill":90}
  ],
  "existing": [ {"id":"rad1","category":"radiator","x":200,"z":340,"w":100,"d":10,"h":60,"rotation_deg":180} ],
  "scan": {"glb": null}
}
```
- `floor` is a simple polygon, counter-clockwise; `walls[i]` runs floor[i] -> floor[i+1].
- `offset` = distance along the wall from its `start` to the opening's near edge.
- `swing` (doors) = depth of the clear zone in front of the door, into the room.
- `existing` = furniture already in the room that must not be overlapped.

## items.json (from the product scout)
```json
[ {"id":"sofa-1","category":"sofa","name":"...","w":210,"d":90,"h":80,
   "dims_estimated":false,"image":null,"price":"$899","url":null,"why":"..."} ]
```

## constraints.json (from the LLM, Holodeck-style vocabulary)
```json
{ "sofa-1": {"anchor": true, "constraints": [{"type":"edge"}, {"type":"face","target":"tv-1"}]},
  "table-1": {"constraints": [{"type":"middle"}, {"type":"in_front_of","target":"sofa-1"},
                              {"type":"near","target":"sofa-1"}, {"type":"center_align","target":"sofa-1"}]} }
```
Types: `edge` (back against a wall), `middle` (away from walls), `near` / `far` (target),
`in_front_of` (target), `side_of` (target), `face` (target), `center_align` (target),
`on_top_of` (target; for rugs under tables use `under`), `under` (target, h<=2 items like rugs),
`not_blocking_window` is always enforced as a hard rule for tall items.

## layout.json (from the layout planner, read by the viewer)
```json
{ "room": "room.json",
  "placements": [ {"item_id":"sofa-1","x":200,"z":50,"rotation_deg":0,
                   "satisfied":["edge"],"violated":[]} ],
  "unplaced": [ {"item_id":"shelf-1","reason":"no wall space","max_w":60,"max_d":30} ],
  "score": 0.86 }
```


## Layout planner notes

**Rotation convention (locked with the viewer).** `rotation_deg` follows three.js rotation about +y: 0 = item front faces +z;
positive `rotation_deg` turns the front from +z toward +x (90 = front faces +x, 180 = -z, 270 = -x). Front vector = (sin r, cos r) in (x, z).
So an item with its back on the wall at z=0 has rotation 0, and one with its back on the wall at x=0 has rotation 90.

**Solver.** Holodeck-style: candidate poses on a ~10 cm grid x {0,90,180,270} (`edge` items: poses flush to each wall, 5 cm steps; relational
items: poses generated around already-placed targets), depth-first search with backtracking, best complete solution kept. Deterministic
(bounded by an expansion budget; the 2 s wall-clock limit is only a safety net).

**Hard rules (never violated):** inside the floor polygon; no overlap with other items or `existing` (a flat item, h <= 2, e.g. a rug, may lie
under non-flat items; two flat items may not overlap; `on_top_of` may overlap its target); door swing zone (door width x `swing` depth) clear;
items with h > window `sill` stay out of the 40 cm strip in front of a window span; a >= 60 cm wide path (grid BFS, 5 cm cells) from every door
to the open centre area (a disc around the room centre; half of its free cells must be reachable). A door that cannot reach the centre even in
the empty room is waived (and listed in `warnings`). An item that cannot be placed goes to `unplaced`.

**constraints.json additions (all optional).**
- `"hint": {"x": cm, "z": cm, "rotation_deg": deg}` per item: rough pose proposed by the LLM (LayoutVLM-style initial pose). Candidates around the hint are
  added and a distance-decayed soft score `hint` (exp(-dist/80 cm), x0.7 if the rotation is off by >45 deg) pulls the item towards it. Never overrides hard rules.
- `"zone": "conversation"` per item: items sharing a zone get a soft `zone:<name>` cohesion score (full marks within 150 cm of the other members, 0 at 400 cm).
- Targets may be item ids, opening ids (`win1`, `door1`), existing-object ids, or `room_center`. `face`/`near`/`far` without a target use the room centre.
- Constraint type `on_top_of` (target) is accepted; the placement then carries an optional `elevation` (= target height, cm).
- `not_blocking_window` is ignored (always enforced).

**Ergonomic soft objectives (added automatically; weight 0.5 each, reported in `satisfied`/`violated` with an `ergo:` prefix):**
`ergo:table_gap` coffee-table front edge 35-50 cm from the sofa front; `ergo:seating_face` seating (sofa/chair/...) faces the room centre unless it has its own
`face`; `ergo:tall_wall` items taller than 150 cm prefer walls/corners; `ergo:window_light` items taller than a window sill stay out of the 120 cm in front of it.
A soft 75 cm main walkway is preferred (candidates that keep it are chosen first; if it is lost the layout score is reduced by 0.05) while 60 cm stays the hard minimum.

**layout.json details.** Constraint names in `satisfied`/`violated` are `type` or `type:target` (e.g. `near:sofa-1`), `zone:<name>`, `hint`, `ergo:*`.
A constraint is satisfied when its 0-1 score >= 0.7 (in_front_of/face/center_align 0.8, under 0.95, zone/hint 0.5). `score` = mean over all items of the
weighted mean constraint score (unplaced items count 0). `unplaced[].max_w/max_d` = largest footprint (same aspect ratio as requested) that still
fits somewhere the item could go (0 if nothing fits); product search can retry with w <= max_w and d <= max_d. Optional top-level `warnings` (strings)
lists ignored constraints / waived rules.

## v2 additions: models, colours, materials, lights, finishes (all optional fields)

### items.json, extra per-item fields
```json
{
  "model": {"template": "floor_lamp", "params": {"shade": "drum", "legs": "none"}},
  "colors": {"primary": "#6b4f3a", "secondary": "#e9e1d3", "accent": null},
  "material": {"primary": "walnut", "secondary": "linen"},
  "light": {"layer": "task", "kind": "point", "color_temp_k": 2700, "lumens": 800,
            "dimmable": true, "rgb": null, "emit_offset": [0, 150, 0]}
}
```
- `model.template`: sofa, armchair, coffee_table, side_table, dining_table, desk, chair, bed, nightstand,
  bookshelf, cabinet, tv_stand, rug, floor_lamp, table_lamp, pendant, sconce, led_strip, picture_light,
  plant, art, mirror, generic. `params` is template-specific (e.g. shade: drum|cone|globe|dome|none;
  legs: tapered|straight|hairpin|none; arms: true|false).
- `material.*`: oak, light_wood, walnut, dark_wood, linen, cotton, velvet, boucle, wool, leather, rattan,
  brass, black_metal, chrome, ceramic, glass, marble, stone, plastic, painted.
- `light` present = the item is a light source. `layer`: ambient|task|accent. `kind`: point|spot|area|line
  (line = LED strip, length = item w). `rgb` overrides colour temperature (coloured accent strips).
  `emit_offset` = bulb position in item-local cm (x, y up, z) from the item's floor-centre.

### constraints.json, mounting constraints (non-floor items)
- `{"type":"on_top_of","target":"side-1"}`: sits on target's top surface (y = target h).
- `{"type":"wall_mounted","height":160,"wall":"w2"}`: back on a wall, base at `height` cm; wall optional.
- `{"type":"ceiling","above":"table-1","drop":60}`: hangs from the ceiling, `drop` cm below it,
  centred over `above` if given.
- `{"type":"attach_to","target":"shelf-1","side":"back|under|top|above"}`: pose derived from the target;
  moves with it. Used for LED strips and picture lights.
Floor-collision rules apply only to floor items; mounted items must not overlap each other or openings.

### layout.json, extra per-placement field
- `"y"`: base elevation in cm (0 for floor items).

### room.json, finishes
```json
"finishes": {"floor": "light_oak", "wall_color": "#f2efe8",
             "wall_paint": {"name": "...", "brand": "...", "hex": "#...", "url": null, "price": null}},
"can_paint": true
```
`floor` presets: light_oak, mid_oak, dark_wood, herringbone, concrete, tile_light, tile_dark, carpet.

### v2.1 additions (python side: mounts, lights, lighting; all optional fields)

**layout.json placements, extra fields.** Every placement has `"y"` (base elevation, cm; 0 for floor items). Mounted items (no floor rules, see below) also carry:
`"mount"`: `"on_top_of"` | `"wall"` | `"ceiling"` | `"attach"` (absent = floor item); `"parent"` (target id for `on_top_of` / `attach`); `"wall"` (wall id, wall mounts);
`"side"` (`back|under|top|above`, attach); `"above"` (target id of a ceiling item); `"elevation"` (= `y`, only `on_top_of`; kept for older readers).
Unplaced mounted items appear in `unplaced` like floor items (`max_w`/`max_d` are the largest wall footprint that would hang; 0 when the cause is a missing parent).
Placement order = items.json order.

**Mount semantics (ptah/layout/mounts.py).**
- `on_top_of`: `y = target.y + target.h`, xy centred on the target (shifted aside if another mounted item already sits there; wide desks prefer the back-left corner), same rotation as the target.
- `wall_mounted {height, wall?}`: back flush to a wall, base at `height`; the position along the wall is searched and steered by the item's other constraints (`near`, `far`, `side_of`, `in_front_of`, `center_align`, `hint`). `rotation_deg` = the wall's inward rotation.
- `ceiling {above?, drop}`: `drop` = cm of stem/cord between the ceiling and the item's TOP, so `y = room.height - drop - h` (`h` = height of the fixture body without stem); default drop 30. Centred over `above` (an item id) or the room centre, nudged up to 80 cm to avoid collisions.
- `attach_to {target, side, height?}`: pose derived from the target's final pose; `back` = back edges flush, top edge flush with the target's top; `under` = centred, y = 6 cm (floor target) or target.y - h; `top` = centred on top; `above` = back-aligned, 3 cm above the target (picture lights). `height` overrides y. Defaults by category: picture_light -> above, led_strip -> back, else top. Attached items of a wall/ceiling item are checked together with their parent when it is positioned.
- Collisions: mounted items are boxes (footprint x [y, y+h]); they may not intersect floor items, `existing`, each other (parent/child pairs excepted) or a door/window span (3D: windows cover [sill, sill+height], doors [0, height]). Floor items keep the 2D rules (rugs excepted). Items whose category/template is `led_strip` or `light.kind == "line"` have no collision. A floor item may not reference a mounted item as target (ignored with a warning).
- `hard_violations()` re-checks mounted placements in 3D.

**items.json, extra optional fields.** `model.params.arc: true` (arc floor lamps), `light.cone_deg` (spot beam angle, default 60; 35-40 for picture lights),
`light.aim` ([x,y,z] item-local direction for spot/line; default: spot -> toward its parent/target else down, strip -> by attach side), `light.target` (item id a light is meant to highlight),
`light.color_changing` (true for RGB-capable lights; `rgb` stays null unless a colour is chosen), `light.estimated` (list of fields guessed by `ptah.products.enrich.parse_light_spec`).
`emit_offset` is relative to the item's base centre, so the world position is `(x, y + oy, z)` with the local (ox, oz) rotated by `rotation_deg` (front = +z local).

### lighting.json (from ptah.lighting, read by the viewer)

Lights are switched by layer, so the room is evaluated in SCENES (layer presets, factor on each layer's lumens):

| scene | ambient | task | accent | other | checked |
|---|---|---|---|---|---|
| `day` | 0 | 0 | 0 | daylight on (hour) | informational: daylight coverage only, no issues |
| `evening` | 0.4 | 0 | 1.0 | | ambient average adequate (>= ~50 lux, target ~100; NO_AMBIENT) and focal points accent-lit (UNLIT_FOCAL_POINT / NO_ACCENT_LAYER) |
| `reading` | 0.3 | 1.0 | 0.3 | | every task zone meets ~300 lux (reading) / ~500 (desk) from 80 % up (DIM_TASK_ZONE) |
| `night` | 0 | 0 | 0.6 | | accent layer exists (NO_ACCENT_LAYER), nothing glaring (GLARE_RISK) |

```json
{ "mode": "night", "scene": "evening", "hour": 12.0, "grid_cm": 25, "plane_cm": 75, "origin": [0,0], "size": [17,15],
  "lux": [[...]], "lux_max": 668, "ambient_avg_lux": 58,
  "zones": [{"name": "reading:sofa-1", "kind": "reading", "item_ids": ["sofa-1","lamp-1"], "layer": "task", "lux": 292, "target": 300,
             "status": "ok", "center": [57,237], "radius_cm": 20},
            {"name": "ambient", "kind": "ambient", "item_ids": ["pendant-1"], "layer": "ambient", "lux": 58, "target": 100, "status": "ok"}],
  "focal_points": [{"item_id": "shelf-1", "category": "bookshelf", "accent_lit": true, "lit_by": ["strip-1"]}],
  "layers": {"ambient": true, "task": true, "accent": true}, "layer_counts": {"ambient": 1, "task": 1, "accent": 4},
  "scenes": {"evening": {"factors": {"ambient": 0.4, "task": 0.0, "accent": 1.0}, "lux": [[...]], "lux_max": 668, "ambient_avg_lux": 58,
                         "zones": [...], "focal_points": [...], "issues": [...]},
             "reading": {...}, "night": {...},
             "day": {"lux": [[...]], "zones": [...], "issues": [], "daylight": {"hour": 12, "outdoor_lux": 15000, "avg_lux": 352, "coverage_pct": 100, "informational": true}}},
  "scene_presets": {"evening": {"ambient": 0.4, "task": 0.0, "accent": 1.0}, "...": {}},
  "lights": [{"item_id": "lamp-1", "layer": "task", "kind": "point", "lumens": 1600, "pos": [20,150,288]}],
  "issues": [{"code": "NO_AMBIENT", "message": "...", "suggest": {"category": "pendant", "above": "table-1"}}],
  "issues_all": [{"code": "DIM_TASK_ZONE", "zone": "reading:sofa-1", "scenes": ["reading"], "message": "...", "suggest": {"category": "floor_lamp", "near": "sofa-1"}}] }
```
- The top-level `mode/scene/lux/lux_max/ambient_avg_lux/zones/focal_points/issues` repeat ONE scene for backwards compatibility: `evening` by default, `day` with `--mode day`, or `--scene X`. `scenes.<name>` always has all four. `issues_all` = the issues of every scene merged (each lists the `scenes` it was found in).
- `lux[row][col]`: row = z index, col = x index; cell centre = `origin + (index + 0.5) * grid_cm`; `null` outside the floor polygon. Horizontal illuminance at `plane_cm`.
- Zone `status`: `ok|dim` where the zone is judged in that scene (task zones in `reading`, `ambient` in `evening`), otherwise `info`. Task zones: one per seat (sofa/armchair: best of up to 3 seat positions; bed: head end) and desk; `item_ids` = the seat + lights giving >= 15 % of its direct light.
- `layers`: ambient = an ambient-layer fixture exists AND the evening ambient average is adequate; task = a task fixture exists AND every task zone is ok in `reading`; accent = an accent fixture exists.
- Issue codes: `DIM_TASK_ZONE`, `NO_AMBIENT`, `NO_ACCENT_LAYER`, `UNLIT_FOCAL_POINT`, `GLARE_RISK`. `suggest` = `{category, near|attach|above, side?}` where `near`/`attach`/`above` is an item id (or `room_center`) and `attach` + `side` map directly to an `attach_to` constraint, `above` to a `ceiling` constraint.
- Everything is approximate design guidance (no occlusion, uniform inter-reflection term, daylight = overcast window patch model); see README.

## v3: owned items and decor (all fields optional)

### items.json, owned items
```json
{"id":"guitar-1","category":"guitar","name":"Ibanez Roadstar RG140 (1987)","w":33,"d":6,"h":98,"dims_estimated":true,
 "price":null,"url":null,"owned":true,"focal":true,
 "colors":{"primary":"#d6cbb0","secondary":"#5a3a2a","accent":"#222222"},
 "user_colors":{"primary":"#e9dfc8","secondary":"#4a2f22","accent":"#111113"},
 "model":{"template":"guitar","params":{"body":"superstrat","mount":"wall_hanger"}}}
```
- `"owned": true`: the user already has it. It must be included and designed around, has no price / buy link (`price`/`url` null), is
  **excluded from every total** (`ptah.owned.total_cost`, the viewer's room total) and is shown as **"Yours"** in the item list and detail panel.
- `"user_colors"`: same shape as `colors`; the user's own colours win over the photo-extracted `colors`, key by key (`ptah.owned.effective_colors`;
  the viewer applies the same rule). A null user colour does not erase a photo colour.
- `"focal": true`: the item is a focal point for the lighting check (it must be accent-lit in the evening scene, else `UNLIT_FOCAL_POINT`);
  `"focal": false` opts a default focal category (bookshelf, art, ...) out. The suggestion for a guitar / poster / frame is a `picture_light` `attach` `above`.
- Dimensions of a wall-hung item follow the wall convention: `w` along the wall, `d` out of the wall, `h` vertical. The guitar is 33 wide (body),
  6 deep, 98 long (hanging neck up, body bottom at the mount height).

### New model templates (`model.template`, drawn procedurally by the viewer)
| template | params | notes |
|---|---|---|
| `guitar` | `body`: `superstrat`\|`single_cut`\|`acoustic`; `mount`: `wall_hanger`\|`stand` | `colors.primary` body, `.secondary` neck / fretboard, `.accent` hardware |
| `guitar_hanger` | | small wall yoke; `attach_to` the guitar (`side: back`, absolute `height`) |
| `amp` | | combo amp: `colors.primary` tolex, `.secondary` grille cloth |
| `book_stack` | `count` | spines coloured from `colors.primary/secondary/accent` |
| `vinyl_crate` | | open crate with record edges |
| `vase` | `form`: `bottle`\|`round`\|`cylinder` | glossy ceramic |
| `frame` | `stand`: bool | photo frame (wall or table) |
| `cushion` | | upright scatter cushion leaning on the sofa back; footprint = w x thickness |
| `throw` | `drape`: `left`\|`right` | blanket on the seat, draped over the arm on that side (item-local x) |
| `candle`, `small_plant`, `tray` | | |
| `poster` | `frame`: `thin`\|`none` | alias of `art` with a print (unframed / thin frame) |
Category aliases are in `ptah/products/enrich.py` (`guitar_amp` -> `amp`, `pillow` -> `cushion`, `succulent` -> `small_plant`, ...).

### constraints.json: `on_top_of` with `surface` and `level`
```json
{"cushion-1": {"constraints": [{"type":"on_top_of","target":"sofa-1","surface":"seat"}]},
 "vase-1":    {"constraints": [{"type":"on_top_of","target":"shelf-1","level":2}]},
 "plant-2":   {"constraints": [{"type":"on_top_of","target":"side-1","surface":"top"}]}}
```
- `surface`: `"top"` (table top / top of any item), `"seat"` (sofa / armchair seat cushion height, not the backrest) or `"shelf"`;
  `level` = bookshelf board index from the bottom (0 = lowest board; implies `surface: "shelf"`). Without either the old behaviour applies (top, centred).
- Surface metrics come from ONE Python place, `ptah/layout/metrics.py` (`surfaces`, `sofa_metrics`, `shelf_level`), which mirrors the viewer templates
  (`sofaMetrics` / `shelfMetrics` in `viewer/src/models.ts`). Seat: `y = 0.5 h` (armchair 0.52 h) between the arms, in front of the back cushions.
  Shelf board `k`: `y = 6 + k * step (+1 for k > 0)`, `step = (h - 8.4) / max(2, round(h / 36))`, free height up to the next board.
- Several items per surface are packed without overlap (>= 1.5 cm apart, 1 cm from the edge; round items such as lamps / vases / plants are circles, so
  a round side table takes a lamp and a plant together). Items of one surface share its span evenly in items.json order; a `throw` with `drape` sticks to
  that end of the seat; cushions lean against the back of the seat.
- **Styling density cap** (`metrics.STYLING`): at most 55 % of a surface covered, at most 3 items per shelf board, 4 per seat / top. A styling item
  that would break the cap, is taller than the free height of its board, or has no such surface goes to `unplaced` with a `reason` ("styling density cap", "too tall for ...").
- layout.json placements of such items carry `"surface"` (`top|seat|shelf`) and, for shelves, `"level"`, besides `mount: "on_top_of"`, `parent`, `y`, `elevation`.
- **Wall items** (art, guitar, poster, sconce, ...) keep `WALL_GAP` = 6 cm between each other in the wall plane (a wall-mounted item together with its
  attached children, e.g. guitar + hanger + spotlight, counts as one group); otherwise the 3D box rules of the v2.1 notes apply.
- **Zones for mounted items**: `"zone"` is scored for wall items too (`zone:<name>`), against the floor members of the zone.
- `near` / `far` from a floor item to a mounted item (amp `near` guitar) is no longer ignored: it is scored from the mounted side (proximity is symmetric),
  so the guitar is hung near the amp (`near:amp-1` in the guitar's `satisfied`).
- Sample music corner: `amp-1` (floor, `edge`, hint, zone `music_corner`), `guitar-1` (`wall_mounted` at 100 cm, `center_align` + `near` the amp, same zone),
  `hanger-1` (`attach_to` the guitar, `side: back`, `height: 168`), `spot-1` (picture-light spot, `attach_to` the guitar, `side: above`, `light.target: "guitar-1"`).

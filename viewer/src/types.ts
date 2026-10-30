export interface Wall { id: string; start: [number, number]; end: [number, number] }
export interface Opening {
  id: string
  type: 'door' | 'window'
  wall: string
  offset: number
  width: number
  height: number
  sill?: number
  swing?: number
}
export interface ExistingObj {
  id: string
  category: string
  x: number
  z: number
  w: number
  d: number
  h: number
  rotation_deg?: number
}
export interface WallPaint { name?: string; brand?: string; hex?: string; url?: string | null; price?: string | null }
export interface Finishes {
  floor?: string | null
  wall_color?: string | null
  wall_paint?: WallPaint | null
}
export interface Room {
  name?: string
  height: number
  floor: [number, number][]
  walls: Wall[]
  openings?: Opening[]
  existing?: ExistingObj[]
  scan?: { glb?: string | null }
  finishes?: Finishes | null
  can_paint?: boolean | null
}

export type LightLayer = 'ambient' | 'task' | 'accent'
export type LightKind = 'point' | 'spot' | 'area' | 'line'
export interface LightDef {
  layer?: LightLayer
  kind?: LightKind
  color_temp_k?: number | null
  lumens?: number | null
  dimmable?: boolean | null
  rgb?: [number, number, number] | string | null
  emit_offset?: [number, number, number] | null
  /** optional item-local emission direction (spot / area / line) */
  direction?: [number, number, number] | null
  /** optional spot half-angle in degrees */
  angle_deg?: number | null
}
export interface ModelDef { template?: string | null; params?: Record<string, unknown> | null }
export interface ColorsDef { primary?: string | null; secondary?: string | null; accent?: string | null }
export interface MaterialDef { primary?: string | null; secondary?: string | null }

export interface Item {
  id: string
  category: string
  name: string
  w: number
  d: number
  h: number
  dims_estimated?: boolean
  image?: string | null
  price?: string | null
  url?: string | null
  why?: string | null
  model?: ModelDef | null
  colors?: ColorsDef | null
  material?: MaterialDef | null
  light?: LightDef | null
  /** the user already owns it: no price / buy link, excluded from totals, shown as "Yours" */
  owned?: boolean
  /** colours given by the user; win over the photo-extracted `colors` key by key */
  user_colors?: ColorsDef | null
  /** focal point for the lighting check */
  focal?: boolean
  /** optional structured explanation: signals (A + B) -> taste reading (C) -> this piece */
  why_chain?: WhyChain | null
}
export interface WhyChain { signals?: string[]; reading?: string }
export interface Placement {
  item_id: string
  x: number
  z: number
  rotation_deg: number
  /** base elevation in cm (0 / absent for floor items) */
  y?: number | null
  elevation?: number | null
  /** placement mode: on_top_of | wall | ceiling | attach (absent = floor item) */
  mount?: string | null
  parent?: string | null
  /** styling items: the surface of the parent they stand on (top | seat | shelf) and the shelf board index (0 = lowest) */
  surface?: string | null
  level?: number | null
  satisfied?: string[]
  violated?: string[]
}
export interface Unplaced { item_id: string; reason?: string; max_w?: number; max_d?: number }
export interface Layout {
  room?: string
  placements: Placement[]
  unplaced?: Unplaced[]
  score?: number
}
export interface Taste { style_summary?: string; aesthetic_tags?: string[] }

export interface LightingZone {
  name: string
  item_ids?: string[]
  layer?: LightLayer
  lux?: number
  target?: number
  status?: string
  /** optional floor position [x, z] of the zone's centre */
  center?: [number, number] | null
}
export interface LightingIssue {
  code?: string
  item_id?: string
  message: string
  suggest?: { category?: string; near?: string; [k: string]: unknown } | null
}
export interface Lighting {
  mode?: string
  grid_cm?: number
  plane_cm?: number
  origin?: [number, number]
  lux?: number[][]
  zones?: LightingZone[]
  focal_points?: { item_id: string; accent_lit?: boolean }[]
  layers?: Partial<Record<LightLayer, boolean>>
  issues?: LightingIssue[]
  /** optional per-scene results, keyed by scene (day, evening, reading, night, all_off) */
  scenes?: Record<string, Partial<Omit<Lighting, 'scenes'>>> | null
}

export interface Scene {
  baseUrl: string
  room: Room
  items: Item[]
  layout: Layout
  taste: Taste | null
  lighting: Lighting | null
}

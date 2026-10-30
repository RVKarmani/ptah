import * as THREE from 'three'
import type { Item, LightLayer, Lighting, Placement, Room } from './types'
import { lampMetrics, templateOf } from './models'
import { kelvinToRGB, lerp, smoothstep } from './color'
import { wallInfos } from './geometry'

/** 1 renderer irradiance unit == 100 lux (so a 300 lux desk reads ~3). */
export const LUX_UNIT = 100

export interface LightSpec {
  id: string
  item: Item
  template: string
  kind: 'point' | 'spot' | 'area'
  layer: LightLayer
  pos: [number, number, number]
  dir: [number, number, number]
  lumens: number
  kelvin: number
  rgb: unknown
  dimmable: boolean
  halfAngle: number
  size: [number, number]
  /** fraction of lumens that leaves the fixture (shades absorb) */
  efficiency: number
  /** base elevation of the item (cm) */
  baseY: number
}

export interface LightState { on: boolean; dim: number; k: number }
export type LightStates = Record<string, LightState>

const norm = (v: [number, number, number]): [number, number, number] => {
  const l = Math.hypot(...v) || 1
  return [v[0] / l, v[1] / l, v[2] / l]
}

export function lightSpecs(items: Item[], placements: Placement[], _room: Room): LightSpec[] {
  void _room
  const out: LightSpec[] = []
  const byId = new Map(placements.map((p) => [p.item_id, p]))
  for (const item of items) {
    const L = item.light
    const pl = byId.get(item.id)
    if (!L || !pl) continue
    const template = templateOf(item)
    const { w, d, h } = item
    const hd = d / 2
    const shade = String(item.model?.params?.shade ?? (template === 'sconce' ? 'cone' : template === 'pendant' ? 'dome' : 'drum'))
    let kind: LightSpec['kind'] = 'point'
    if (L.kind === 'spot') kind = 'spot'
    else if (L.kind === 'area' || L.kind === 'line') kind = 'area'
    else if (template === 'pendant') kind = 'spot' // a shade throws light down
    else if (template === 'picture_light') kind = 'spot'
    else if (template === 'led_strip') kind = 'area'

    let pos: [number, number, number]
    if (L.emit_offset && L.emit_offset.length >= 3) pos = [L.emit_offset[0], L.emit_offset[1], L.emit_offset[2]]
    else if (template === 'floor_lamp' || template === 'table_lamp') pos = [0, lampMetrics(template, w, d, h).bulbY, 0]
    else if (template === 'pendant') pos = [0, h * 0.45, 0]
    else if (template === 'sconce') pos = [0, h * 0.5, hd - Math.min(w / 2, hd - 1, h * 0.3)]
    else if (template === 'led_strip') pos = [0, Math.min(0.3, h), 0]
    else if (template === 'picture_light') pos = [0, h / 2 - 2, hd - 3.5]
    else pos = [0, h / 2, 0]

    let dir: [number, number, number] = [0, -1, 0]
    if (L.direction && L.direction.length >= 3) dir = norm([L.direction[0], L.direction[1], L.direction[2]])
    else if (template === 'picture_light') dir = norm([0, -1, -0.3])

    const halfAngle = ((L.angle_deg ?? (template === 'picture_light' ? 38 : template === 'pendant' ? 62 : 48)) * Math.PI) / 180
    const size: [number, number] = L.kind === 'line' || template === 'led_strip' ? [Math.max(w, 2), Math.max(d, 2)] : [Math.max(Math.min(w, 120), 5), Math.max(Math.min(d, 120), 5)]
    const lumens = L.lumens && L.lumens > 0 ? L.lumens : template === 'led_strip' ? 450 : kind === 'spot' ? 320 : 800
    const shaded = (template === 'floor_lamp' || template === 'table_lamp' || template === 'sconce' || template === 'pendant') && shade !== 'none'
    out.push({
      id: item.id,
      item,
      template,
      kind,
      layer: L.layer ?? 'ambient',
      pos,
      dir,
      lumens,
      kelvin: L.color_temp_k && L.color_temp_k > 0 ? L.color_temp_k : 2700,
      rgb: L.rgb ?? null,
      dimmable: L.dimmable !== false,
      halfAngle: Math.min(Math.max(halfAngle, 0.15), 1.4),
      size,
      efficiency: shaded ? 0.62 : 1,
      baseY: pl.y ?? 0,
    })
  }
  return out
}

/** Physically-based three.js intensity for a spec at full dimmer. */
export function baseIntensity(s: LightSpec): number {
  const lm = s.lumens * s.efficiency
  if (s.kind === 'point') return (lm / (4 * Math.PI)) * LUX_UNIT
  if (s.kind === 'spot') return (lm / (2 * Math.PI * (1 - Math.cos(s.halfAngle)))) * LUX_UNIT
  // area: luminance of a Lambertian emitter (nits) / unit
  const aM2 = (s.size[0] / 100) * (s.size[1] / 100)
  return lm / (Math.PI * aM2) / LUX_UNIT
}

export const dimCurve = (d: number) => Math.pow(Math.min(1, Math.max(0, d)), 1.5)

export type LayerState = { on: boolean; dim: number }
export type LayerStates = Record<LightLayer, LayerState>
export const LAYERS: LightLayer[] = ['ambient', 'task', 'accent']

/** Per-fixture state: on by default at full dim, so scenes and layers do the shaping. */
export function initialStates(specs: LightSpec[]): LightStates {
  const m: LightStates = {}
  for (const s of specs) m[s.id] = { on: true, dim: 1, k: s.kelvin }
  return m
}

/** Fixture state combined with its layer (what actually reaches the renderer). */
export function effectiveStates(specs: LightSpec[], own: LightStates, layers: LayerStates): LightStates {
  const m: LightStates = {}
  for (const s of specs) {
    const o = own[s.id] ?? { on: true, dim: 1, k: s.kelvin }
    const l = layers[s.layer]
    m[s.id] = { on: o.on && l.on && l.dim > 0.001, dim: Math.min(1, o.dim * l.dim), k: o.k }
  }
  return m
}

// ---------------------------------------------------------------- scenes (presets over layers)

export type SceneName = 'Day' | 'Evening' | 'Reading' | 'Night' | 'All off' | 'Custom'
export const SCENES: SceneName[] = ['Day', 'Evening', 'Reading', 'Night', 'All off']
export const SCENE_TIME: Record<string, number> = { Day: 13, Evening: 21, Reading: 21.5, Night: 23.5, 'All off': 23.5 }
export const DEFAULT_SCENE: SceneName = 'Evening'

const off = { on: false, dim: 1 }
export const SCENE_LAYERS: Record<string, LayerStates> = {
  Day: { ambient: off, task: off, accent: off },
  Evening: { ambient: { on: true, dim: 0.4 }, task: off, accent: { on: true, dim: 1 } },
  Reading: { ambient: { on: true, dim: 0.3 }, task: { on: true, dim: 1 }, accent: { on: true, dim: 0.3 } },
  Night: { ambient: off, task: off, accent: { on: true, dim: 0.6 } },
  'All off': { ambient: off, task: off, accent: off },
}
export function sceneLayers(name: SceneName, prev?: LayerStates): LayerStates {
  return SCENE_LAYERS[name] ?? prev ?? SCENE_LAYERS['All off']
}

/** key used by lighting.json `scenes` (e.g. "evening", "all_off") */
export const sceneKey = (n: string) => n.toLowerCase().replace(/[\s/-]+/g, '_')

// ---------------------------------------------------------------- sun / sky

export interface SunState {
  alt: number // degrees
  azRel: number // degrees relative to the window's outward normal
  dayFactor: number // sky brightness 0..1
  sunFactor: number // direct sun 0..1
  night: number
  sunColor: THREE.Color
  skyColor: THREE.Color
}

export function sunState(t: number): SunState {
  const rise = 6.5, set = 19.5
  let alt: number
  if (t >= rise && t <= set) alt = 50 * Math.sin((Math.PI * (t - rise)) / (set - rise))
  else {
    const n = t > set ? t - set : t + 24 - set
    alt = -32 * Math.sin((Math.PI * n) / (24 - (set - rise)))
  }
  const dayFactor = smoothstep(-7, 9, alt)
  const sunFactor = smoothstep(0, 7, alt)
  const azRel = Math.max(-72, Math.min(72, ((t - 13) / 6.5) * 72))
  const [r, g, b] = kelvinToRGB(lerp(2300, 6000, smoothstep(0, 32, alt)))
  const sunColor = new THREE.Color().setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace)
  const dusk = smoothstep(-8, 2, alt) * (1 - smoothstep(2, 14, alt))
  const sky = new THREE.Color('#e4ecff').lerp(new THREE.Color('#ffb98a'), dusk * 0.7)
  sky.lerp(new THREE.Color('#5a77b5'), 1 - dayFactor)
  return { alt, azRel, dayFactor, sunFactor, night: 1 - dayFactor, sunColor, skyColor: sky }
}

/** Outward normal (x,z) of the biggest window, or +z. */
export function mainWindow(room: Room) {
  const infos = wallInfos(room)
  let best: { cx: number; cz: number; nx: number; nz: number; area: number } | null = null
  for (const o of room.openings ?? []) {
    if (o.type !== 'window') continue
    const wi = infos.find((i) => i.wall.id === o.wall)
    if (!wi) continue
    const a = o.width * o.height
    if (!best || a > best.area) {
      const ux = (wi.wall.end[0] - wi.wall.start[0]) / wi.length
      const uz = (wi.wall.end[1] - wi.wall.start[1]) / wi.length
      const c = o.offset + o.width / 2
      best = { cx: wi.wall.start[0] + ux * c, cz: wi.wall.start[1] + uz * c, nx: -wi.inward[0], nz: -wi.inward[1], area: a }
    }
  }
  return best
}

export interface WindowLightDef { id: string; cx: number; cy: number; cz: number; w: number; h: number; inward: [number, number] }
export function windowLights(room: Room): WindowLightDef[] {
  const infos = wallInfos(room)
  const out: WindowLightDef[] = []
  for (const o of room.openings ?? []) {
    if (o.type !== 'window') continue
    const wi = infos.find((i) => i.wall.id === o.wall)
    if (!wi) continue
    const ux = (wi.wall.end[0] - wi.wall.start[0]) / wi.length
    const uz = (wi.wall.end[1] - wi.wall.start[1]) / wi.length
    const c = o.offset + o.width / 2
    const sill = o.sill ?? 90
    out.push({
      id: o.id,
      cx: wi.wall.start[0] + ux * c + wi.inward[0] * 2,
      cy: sill + o.height / 2,
      cz: wi.wall.start[1] + uz * c + wi.inward[1] * 2,
      w: o.width - 8,
      h: o.height - 8,
      inward: wi.inward,
    })
  }
  return out
}

export const fmtTime = (t: number) => {
  const hh = Math.floor(t) % 24
  const mm = Math.round((t - Math.floor(t)) * 60) % 60
  return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`
}

/** lighting.json view for the selected scene: per-scene results (if any) layered over the top-level ones. */
export function lightingFor(l: Lighting | null, scene: SceneName): Lighting | null {
  if (!l) return null
  const sc = l.scenes?.[sceneKey(scene)]
  if (!sc) return l
  return { ...l, ...sc, scenes: l.scenes }
}

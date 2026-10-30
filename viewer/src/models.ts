import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { Item } from './types'
import { getMaterial } from './materials'
import { boxProjectUV, fitGeometry, makeCanvas, toTexture } from './gfx'
import { categoryColor } from './palette'
import { effColors, hashStr, mulberry32, safeColor } from './color'
import { sofaMetrics, shelfMetrics } from './metrics'
import { amp, bookStack, candle, cushion, guitar, guitarHanger, smallPlant, throwT, tray, vase, vinylCrate } from './decor'

export { sofaMetrics, shelfMetrics }

export interface Glow { mat: THREE.MeshStandardMaterial; strength: number }
export interface Built { group: THREE.Group; glows: Glow[]; template: string }
export interface ShelfBlock { level: number; x0: number; x1: number }
export interface BuildOpts {
  ceilingGap?: number
  /** decor standing on bookshelf boards: x ranges (shelf-local, cm) where no filler books are drawn */
  shelfBlocks?: ShelfBlock[]
  /** the item this one stands on (a throw drapes over the arm of its sofa) */
  parent?: Item | null
}

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

export function templateOf(item: Item): string {
  const t = item.model?.template
  if (t) return String(t).toLowerCase()
  const c = item.category.toLowerCase().replace(/[-\s]/g, '_')
  const rules: [RegExp, string][] = [
    [/(^|_)amp(lifier)?(_|$)/, 'amp'],
    [/guitar_?hang|hanger/, 'guitar_hanger'],
    [/guitar|ukulele/, 'guitar'],
    [/book_?stack|stack_of_books/, 'book_stack'],
    [/vinyl|record_?crate/, 'vinyl_crate'],
    [/vase/, 'vase'],
    [/photo_?frame|picture_?frame/, 'frame'],
    [/cushion|pillow/, 'cushion'],
    [/throw|blanket/, 'throw'],
    [/candle/, 'candle'],
    [/small_?plant|succulent|desk_?plant/, 'small_plant'],
    [/tray/, 'tray'],
    [/poster/, 'poster'],
    [/floor_?lamp|standing_?lamp|arc_?lamp|torchi/, 'floor_lamp'],
    [/pendant|chandelier|ceiling_?(light|lamp)/, 'pendant'],
    [/sconce|wall_?(light|lamp)/, 'sconce'],
    [/picture_?light/, 'picture_light'],
    [/led|light_?strip|strip_?light/, 'led_strip'],
    [/lamp/, 'table_lamp'],
    [/sofa|couch|sectional|loveseat/, 'sofa'],
    [/arm_?chair|accent_?chair|lounge_?chair|recliner/, 'armchair'],
    [/chair|stool/, 'chair'],
    [/dining/, 'dining_table'],
    [/side_?table|end_?table/, 'side_table'],
    [/coffee|bench|ottoman|pouf/, 'coffee_table'],
    [/desk/, 'desk'],
    [/table/, 'coffee_table'],
    [/night_?stand|bedside/, 'nightstand'],
    [/\bbed\b|^bed|mattress/, 'bed'],
    [/book|shelf|shelv/, 'bookshelf'],
    [/tv|media/, 'tv_stand'],
    [/cabinet|dresser|sideboard|console|credenza|storage|wardrobe|drawers/, 'cabinet'],
    [/rug|carpet/, 'rug'],
    [/plant|tree|flower/, 'plant'],
    [/mirror/, 'mirror'],
    [/art|print|poster|painting|frame|canvas/, 'art'],
  ]
  for (const [re, tpl] of rules) if (re.test(c)) return tpl
  return 'generic'
}

/** Shade metrics shared by the model and the light placement code. */
export function lampMetrics(template: string, w: number, d: number, h: number) {
  const r = Math.min(w, d) / 2
  if (template === 'floor_lamp') {
    const shadeH = clamp(h * 0.2, 18, 40)
    return { shadeH, shadeR: r, bulbY: h - shadeH * 0.55 }
  }
  if (template === 'table_lamp') {
    const shadeH = h * 0.42
    return { shadeH, shadeR: r, bulbY: h - shadeH * 0.5 }
  }
  if (template === 'pendant') return { shadeH: h, shadeR: r, bulbY: h * 0.5 }
  return { shadeH: h * 0.4, shadeR: r, bulbY: h * 0.5 }
}

const DEFAULTS: Record<string, [string, string]> = {
  sofa: ['linen', 'walnut'], armchair: ['linen', 'walnut'], chair: ['linen', 'oak'],
  coffee_table: ['oak', 'oak'], side_table: ['oak', 'black_metal'], dining_table: ['oak', 'oak'], desk: ['oak', 'black_metal'],
  bed: ['linen', 'oak'], nightstand: ['oak', 'brass'], bookshelf: ['oak', 'oak'], cabinet: ['oak', 'brass'], tv_stand: ['oak', 'black_metal'],
  rug: ['wool', 'wool'], floor_lamp: ['black_metal', 'linen'], table_lamp: ['ceramic', 'linen'], pendant: ['linen', 'brass'],
  sconce: ['brass', 'linen'], led_strip: ['black_metal', 'plastic'], picture_light: ['brass', 'brass'], plant: ['ceramic', 'ceramic'],
  art: ['painted', 'oak'], mirror: ['glass', 'brass'], generic: ['painted', 'oak'],
  guitar: ['painted', 'dark_wood'], amp: ['painted', 'plastic'], guitar_hanger: ['black_metal', 'black_metal'],
  book_stack: ['painted', 'painted'], vinyl_crate: ['light_wood', 'light_wood'], vase: ['ceramic', 'ceramic'],
  frame: ['painted', 'oak'], cushion: ['linen', 'linen'], throw: ['wool', 'wool'], candle: ['painted', 'painted'],
  small_plant: ['ceramic', 'ceramic'], poster: ['black_metal', 'painted'], tray: ['walnut', 'walnut'],
}
const DEFAULT_SEC_COLOR: Record<string, string> = { floor_lamp: '#efe6d3', table_lamp: '#efe6d3', pendant: '#efe6d3', sconce: '#efe6d3' }

const SHADE_CLASSES = new Set(['linen', 'glass', 'paper', 'fabric', 'plastic', 'ceramic', 'boucle', 'wool', 'rattan', 'painted'])
function safeShadeColor(hex: string): THREE.Color {
  try { return new THREE.Color(hex) } catch { return new THREE.Color('#f4efe4') }
}

export class Builder {
  group = new THREE.Group()
  glows: Glow[] = []
  w: number; d: number; h: number
  hw: number; hd: number
  cast: boolean
  item: Item
  template: string
  P: THREE.MeshStandardMaterial
  S: THREE.MeshStandardMaterial
  private slots: { cls: string; hex: string | null; def: string }[]
  constructor(item: Item, template: string) {
    this.item = item
    this.template = template
    this.w = item.w; this.d = item.d; this.h = item.h
    this.hw = item.w / 2; this.hd = item.d / 2
    this.cast = !item.light
    const [dp, ds] = DEFAULTS[template] ?? DEFAULTS.generic
    const ec = effColors(item) // user_colors win over photo colours
    const pc = ec.primary || categoryColor(item.category, item.id)
    const primCls = item.material?.primary || dp
    const secCls = item.material?.secondary || ds
    this.P = getMaterial(primCls, pc, dp)
    const secColor = ec.secondary || DEFAULT_SEC_COLOR[template] || null
    // fall back to a darker take on the primary colour when only the finish was given
    this.S = getMaterial(secCls, secColor ?? undefined, ds)
    this.slots = [{ cls: primCls, hex: pc, def: dp }, { cls: secCls, hex: secColor, def: ds }]
  }
  /**
   * Base material for lamp shades / globes. Picks whichever slot is a soft or translucent class
   * (linen, glass, paper...) rather than metal, so an OFF shade keeps its real colour (frosted
   * glass reads as off-white) instead of inheriting the black fitting.
   */
  shadeBase(): THREE.MeshStandardMaterial {
    const order = this.template === 'floor_lamp' || this.template === 'sconce' ? [1, 0] : [0, 1]
    const pick = order.map((i) => this.slots[i]).find((sl) => SHADE_CLASSES.has(sl.cls)) ?? this.slots[1]
    if (pick.cls === 'glass') {
      // frosted glass: matte, slightly translucent, tinted by the item colour
      const hex = pick.hex || '#f4efe4'
      return new THREE.MeshStandardMaterial({ color: safeShadeColor(hex), roughness: 0.62, metalness: 0, transparent: true, opacity: 0.92 })
    }
    return getMaterial(pick.cls, pick.hex ?? undefined, pick.def)
  }
  p<T>(key: string, def: T): T {
    const v = this.item.model?.params?.[key]
    return (v === undefined || v === null ? def : (v as T))
  }
  mesh(geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, parent: THREE.Object3D = this.group, cast = this.cast): THREE.Mesh {
    const m = new THREE.Mesh(geo, mat)
    m.position.set(x, y, z)
    m.castShadow = cast
    m.receiveShadow = true
    parent.add(m)
    return m
  }
  /** rounded box; y = bottom */
  box(mat: THREE.Material, w: number, h: number, d: number, x: number, y: number, z: number, r = 0.6, parent: THREE.Object3D = this.group, vertical = false) {
    const rr = Math.max(0, Math.min(r, Math.min(w, h, d) / 2 - 0.02))
    const g = rr > 0.15 ? new RoundedBoxGeometry(w, h, d, 2, rr) : new THREE.BoxGeometry(w, h, d)
    boxProjectUV(g, vertical)
    return this.mesh(g, mat, x, y + h / 2, z, parent)
  }
  /** cylinder; y = bottom */
  cyl(mat: THREE.Material, rt: number, rb: number, h: number, x: number, y: number, z: number, seg = 20, open = false, parent: THREE.Object3D = this.group, vertical = true) {
    const g = new THREE.CylinderGeometry(rt, rb, h, seg, 1, open)
    boxProjectUV(g, vertical)
    return this.mesh(g, mat, x, y + h / 2, z, parent)
  }
  /** ellipse-capable cylinder (x radius rx, z radius rz) */
  ell(mat: THREE.Material, rx: number, rz: number, h: number, x: number, y: number, z: number, seg = 40) {
    const g = new THREE.CylinderGeometry(1, 1, h, seg)
    g.scale(rx, 1, rz)
    g.computeVertexNormals()
    boxProjectUV(g)
    return this.mesh(g, mat, x, y + h / 2, z)
  }
  glowMat(base: THREE.MeshStandardMaterial, strength: number, double = true) {
    const m = base.clone()
    m.userData.shared = false
    if (double) m.side = THREE.DoubleSide
    m.emissive = new THREE.Color('#000000')
    m.emissiveIntensity = 1
    this.glows.push({ mat: m, strength })
    return m
  }
  legs(mat: THREE.Material, style: string, height: number, inset = 5, r = 1.9) {
    if (style === 'none' || height <= 0.1) return
    const xs = [-1, 1], zs = [-1, 1]
    for (const sx of xs) for (const sz of zs) {
      const x = sx * (this.hw - inset), z = sz * (this.hd - inset)
      if (style === 'hairpin') {
        for (const o of [-1.1, 1.1]) this.cyl(mat, 0.45, 0.45, height, x, 0, z + o, 8)
      } else if (style === 'tapered') {
        this.cyl(mat, r * 1.15, r * 0.6, height, x, 0, z, 14)
      } else if (style === 'block') {
        this.box(mat, r * 2.4, height, r * 2.4, x, 0, z, 0.3, this.group, true)
      } else {
        this.cyl(mat, r, r, height, x, 0, z, 14)
      }
    }
  }
}

// ---------------------------------------------------------------- shades

function shadeGeo(kind: string, radius: number, height: number): THREE.BufferGeometry | null {
  switch (kind) {
    case 'none': return null
    case 'cone': return new THREE.CylinderGeometry(radius * 0.55, radius, height, 36, 1, true)
    case 'globe': {
      const g = new THREE.SphereGeometry(1, 36, 24)
      g.scale(radius, height / 2, radius)
      g.translate(0, height / 2, 0)
      return g
    }
    case 'dome': {
      const g = new THREE.SphereGeometry(1, 36, 18, 0, Math.PI * 2, 0, Math.PI / 2)
      g.scale(radius, height, radius)
      return g
    }
    default: {
      // drum, slightly open at both ends
      const g = new THREE.CylinderGeometry(radius, radius, height, 36, 1, true)
      g.translate(0, height / 2, 0)
      return g
    }
  }
}

function addShade(B: Builder, kind: string, radius: number, height: number, y0: number, x = 0, z = 0, strength = 1) {
  const bulbR = Math.min(radius * 0.28, 4.2)
  if (kind === 'none') {
    const m = new THREE.MeshStandardMaterial({ color: '#fff7e6', roughness: 0.3 })
    m.emissive = new THREE.Color('#000')
    B.glows.push({ mat: m, strength: 3 })
    const g = new THREE.SphereGeometry(bulbR, 20, 14)
    B.mesh(g, m, x, y0 + height / 2, z, B.group, false)
    return
  }
  const geo = shadeGeo(kind, radius, height)!
  if (kind === 'cone') geo.translate(0, height / 2, 0)
  boxProjectUV(geo)
  const mat = B.glowMat(B.shadeBase(), strength)
  B.mesh(geo, mat, x, y0, z, B.group, false)
  // little finial/bulb for realism inside translucent shades
  const bulbM = new THREE.MeshStandardMaterial({ color: '#fff3dc', roughness: 0.4 })
  bulbM.emissive = new THREE.Color('#000')
  B.glows.push({ mat: bulbM, strength: 4 })
  B.mesh(new THREE.SphereGeometry(bulbR * 0.8, 16, 12), bulbM, x, y0 + height * (kind === 'dome' ? 0.35 : 0.5), z, B.group, false)
}

// ---------------------------------------------------------------- templates

function sofa(B: Builder, armchair: boolean) {
  const { w, d, h, hw, hd } = B
  const legStyle = B.p<string>('legs', 'tapered')
  const arms = B.p<boolean>('arms', true)
  const { legH: lh, armW, backT: bt, seatTop, baseTop, innerW } = sofaMetrics(w, d, h, armchair, arms, legStyle) // shared with the layout planner
  const n = armchair ? 1 : w > 195 ? 3 : w > 135 ? 2 : 1

  B.box(B.P, w, baseTop - lh, d, 0, lh, 0, 3)
  // legs
  B.legs(B.S, legStyle, lh, Math.min(8, hw * 0.3), 2.2)
  // seat cushions
  const cw = innerW / n
  const cd = d - bt - 0.4
  for (let i = 0; i < n; i++) {
    const x = -innerW / 2 + cw * (i + 0.5)
    B.box(B.P, cw - 0.7, seatTop - baseTop, cd, x, baseTop, hd - 0.2 - cd / 2, 4.2)
  }
  // back frame
  B.box(B.P, w, h - baseTop - 6, bt, 0, baseTop, -hd + bt / 2, 4)
  // back cushions, slightly reclined (pivot at their base)
  const bh = h - seatTop - 3.2
  const bd = bt * 0.66
  for (let i = 0; i < n; i++) {
    const x = -innerW / 2 + cw * (i + 0.5)
    const pivot = new THREE.Group()
    pivot.position.set(x, seatTop - 0.5, -hd + bt + bd * 0.35 - 3)
    pivot.rotation.x = -0.15
    B.group.add(pivot)
    B.box(B.P, cw - 0.7, bh, bd, 0, 0, 0, 4.5, pivot)
  }
  // top rail to reach exactly h
  B.box(B.P, w, 6, bt, 0, h - 6, -hd + bt / 2, 3)
  // arms
  if (arms) {
    const armTop = seatTop + (h - seatTop) * 0.32
    for (const s of [-1, 1]) B.box(B.P, armW, armTop - lh, d, s * (hw - armW / 2), lh, 0, Math.min(armW / 2 - 0.5, 6))
  }
}

function chairT(B: Builder) {
  const { w, d, h, hw, hd } = B
  const sh = h * 0.52
  const legH = sh - 4
  const legStyle = B.p<string>('legs', 'tapered')
  B.legs(B.S, legStyle === 'none' ? 'tapered' : legStyle, legH, 3.2, 1.6)
  B.box(B.P, w, 4.5, d, 0, legH, 0, 1.6)
  const pad = B.box(B.P, w - 3, 3.5, d - 3, 0, sh, 1, 1.4)
  void pad
  // backrest: slightly reclined slab
  const bh = h - sh
  const pivot = new THREE.Group()
  pivot.position.set(0, sh + 3, -hd + 1.8)
  pivot.rotation.x = -0.1
  B.group.add(pivot)
  B.box(B.P, w, bh - 3, 3, 0, 0, 0, 1.4, pivot)
  // rear posts
  for (const s of [-1, 1]) B.cyl(B.S, 1.3, 1.5, h - 2, s * (hw - 3.2), 0, -hd + 3.4, 10, false, B.group, true)
  B.box(B.S, w - 4, 3, 2.5, 0, h - 3, -hd + 2.2, 1)
}

function tableLike(B: Builder, kind: 'coffee_table' | 'side_table' | 'dining_table' | 'desk') {
  const { w, d, h, hw, hd } = B
  const shapeDef = kind === 'side_table' && Math.abs(w - d) < 3 ? 'round' : 'rect'
  const shape = B.p<string>('shape', shapeDef)
  const tt = clamp(h * (kind === 'dining_table' || kind === 'desk' ? 0.05 : 0.07), 2.2, 4.5)
  const legH = h - tt
  const legs = B.p<string>('legs', kind === 'side_table' ? 'hairpin' : kind === 'coffee_table' ? 'tapered' : 'straight')
  const topMat = B.P
  if (legs === 'pedestal') {
    const r = Math.min(w, d) * 0.07
    B.cyl(B.S, r, r * 1.1, legH - 2, 0, 2, 0, 24)
    B.cyl(B.S, Math.min(w, d) * 0.3, Math.min(w, d) * 0.32, 2, 0, 0, 0, 36)
  } else if (kind === 'desk' && legs !== 'tapered' && legs !== 'hairpin') {
    // panel ends + modesty panel
    for (const s of [-1, 1]) B.box(B.S, 2.4, legH, d - 6, s * (hw - 3), 0, 0, 0.5)
    B.box(B.S, w - 12, legH * 0.45, 1.6, 0, legH * 0.5, -hd + 6, 0.4)
  } else if (shape === 'round' || shape === 'oval') {
    const r = Math.min(w, d) * 0.3
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 + Math.PI / 6
      B.cyl(B.S, 1.2, 0.8, legH, Math.cos(a) * r * 0.9, 0, Math.sin(a) * r * 0.9, 10)
    }
  } else {
    B.legs(B.S, legs, legH, Math.min(6, hw * 0.25), kind === 'dining_table' ? 3 : 2.2)
    if (kind === 'dining_table' && w > 120) {
      // apron under the top
      B.box(B.S, w - 12, 6, 1.6, 0, legH - 6, hd - 6, 0.3)
      B.box(B.S, w - 12, 6, 1.6, 0, legH - 6, -hd + 6, 0.3)
    }
  }
  if (B.p<boolean>('shelf', false) && kind === 'coffee_table') {
    B.box(B.S, w - 10, 2, d - 10, 0, legH * 0.28, 0, 0.4)
  }
  if (shape === 'round' || shape === 'oval') B.ell(topMat, hw, hd, tt, 0, legH, 0)
  else B.box(topMat, w, tt, d, 0, legH, 0, Math.min(1.2, tt * 0.4))
}

function bedT(B: Builder) {
  const { w, d, h, hw, hd } = B
  const baseH = clamp(h * 0.28, 18, 34)
  const legH = 8
  const hbT = 7
  B.box(B.P, w, baseH - legH, d, 0, legH, 0, 1.5)
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) B.box(B.S, 6, legH, 6, sx * (hw - 6), 0, sz * (hd - 6), 0.5)
  // headboard to full height
  B.box(B.P, w, h, hbT, 0, 0, -hd + hbT / 2, 2.5)
  // mattress
  const mH = 22
  B.box(getMaterial('cotton', '#f1ede6'), w - 6, mH, d - hbT - 3, 0, baseH, hbT / 2 + 0.5, 4)
  // bedding over the lower 2/3
  const bedLen = (d - hbT - 3) * 0.68
  B.box(B.S, w - 3.5, mH + 3.2, bedLen, 0, baseH - 0.3, hd - 1.5 - bedLen / 2, 5)
  // pillows
  const pw = (w - 20) / 2
  for (const s of [-1, 1]) B.box(getMaterial('cotton', '#f6f3ee'), pw, 11, 30, s * (pw / 2 + 4), baseH + mH, -hd + hbT + 18, 5)
}

function nightstand(B: Builder) {
  const { w, d, h, hd } = B
  const legStyle = B.p<string>('legs', 'tapered')
  const legH = legStyle === 'none' ? 0 : clamp(h * 0.22, 8, 16)
  B.legs(B.S, legStyle, legH, 3.5, 1.5)
  B.box(B.P, w, h - legH, d, 0, legH, 0, 1.2)
  const dh = (h - legH - 6) / 2
  for (let i = 0; i < 2; i++) {
    const y = legH + 2 + i * (dh + 1.5)
    B.box(B.P, w - 5, dh - 1, 0.8, 0, y, hd + 0.05, 0.3)
    B.cyl(B.S, 1.1, 1.1, 1.8, 0, y + dh / 2, hd + 1.0, 12, false, B.group, false).rotation.x = Math.PI / 2
  }
}

function bookshelf(B: Builder, blocks: ShelfBlock[] = []) {
  const { w, d, h, hw, hd } = B
  const t = 2.4
  const kick = 6
  const back = 0.9
  // sides, top, kick, back
  for (const s of [-1, 1]) B.box(B.P, t, h, d, s * (hw - t / 2), 0, 0, 0.3)
  B.box(B.P, w - 2 * t, t, d, 0, h - t, 0, 0.3)
  B.box(B.P, w - 2 * t, kick, d - 2, 0, 0, -1, 0.2)
  B.box(B.S, w - 2 * t, h - 2 * t, back, 0, t, -hd + back / 2, 0.1)
  const { n, step } = shelfMetrics(w, d, h) // shared with the layout planner
  const rnd = mulberry32(hashStr(B.item.id))
  const palette = ['#b85c38', '#d9b26f', '#4d6b5a', '#2f4858', '#e7dccb', '#8c4a4a', '#6b7f95', '#c9894b', '#3d3d3d', '#a9b9a0']
  const books: THREE.BufferGeometry[] = []
  for (let i = 0; i < n; i++) {
    const y = kick + step * (i + 1) - (i === n - 1 ? 0 : 0)
    if (i < n - 1) B.box(B.P, w - 2 * t, 2, d - 1.2, 0, y - 1, 0.2, 0.2)
    // books standing on the shelf below this board
    const yBase = kick + step * i + (i === 0 ? 0 : 1)
    const room = step - (i === n - 1 ? t + 1 : 3)
    if (rnd() < 0.18) continue
    let x = -hw + t + 1
    const x1 = hw - t - 1
    const fill = 0.55 + rnd() * 0.4
    const xEnd = -hw + t + 1 + (x1 - x) * fill
    const taken = blocks.filter((b) => b.level === i)
    while (x < xEnd) {
      const bw = 1.6 + rnd() * 2.8
      if (taken.some((b) => x + bw > b.x0 && x < b.x1)) { x += bw + 0.15; rnd(); rnd(); rnd(); continue } // decor stands here
      const bh = Math.min(room, 14 + rnd() * (room - 12))
      const bd = Math.min(d - 6, 14 + rnd() * 12)
      const g = new THREE.BoxGeometry(bw, bh, bd)
      g.translate(x + bw / 2, yBase + bh / 2, -hd + 2 + back + bd / 2 + 1.0)
      const c = new THREE.Color(palette[Math.floor(rnd() * palette.length)])
      const arr = new Float32Array(g.getAttribute('position').count * 3)
      for (let k = 0; k < arr.length; k += 3) { arr[k] = c.r; arr[k + 1] = c.g; arr[k + 2] = c.b }
      g.setAttribute('color', new THREE.BufferAttribute(arr, 3))
      g.deleteAttribute('uv')
      books.push(g)
      x += bw + 0.15
      if (rnd() < 0.08) x += 3 + rnd() * 8 // gap
    }
  }
  if (books.length) {
    const merged = mergeGeometries(books, false)
    const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.75, metalness: 0 })
    B.mesh(merged, mat, 0, 0, 0)
  }
}

function cabinet(B: Builder, tvStand: boolean) {
  const { w, d, h, hw, hd } = B
  const tall = h > 110
  const legStyle = B.p<string>('legs', tall ? 'none' : 'tapered')
  const legH = tall || legStyle === 'none' ? 0 : clamp(h * 0.2, 9, 18)
  const plinth = tall ? 7 : 0
  B.legs(B.S, legStyle, legH, 6, 1.7)
  B.box(B.P, w, h - legH, d, 0, legH, 0, 1.0)
  if (plinth) B.box(B.S, w - 4, plinth, d - 4, 0, 0, 0, 0.4)
  const doors = B.p<number>('doors', w > 150 ? 4 : w > 90 ? 3 : 2)
  const dw = (w - 4) / doors
  const dy = legH + plinth + 2
  const dH = h - legH - plinth - 4
  for (let i = 0; i < doors; i++) {
    const x = -hw + 2 + dw * (i + 0.5)
    const open = tvStand && i === Math.floor(doors / 2) && doors % 2 === 1
    if (open) {
      B.box(getMaterial('black_metal', '#222'), dw - 1.2, dH - 1, 0.8, x, dy + 0.5, hd - 1.5, 0.2)
      continue
    }
    B.box(B.P, dw - 0.8, dH, 0.9, x, dy, hd + 0.05, 0.3)
    const hy = tall ? dy + dH * 0.5 : dy + dH * 0.62
    const hx = x + (i % 2 === 0 ? 1 : -1) * (dw * 0.5 - 4)
    B.cyl(B.S, 0.9, 0.9, 2.2, hx, hy, hd + 1.3, 10, false, B.group, false).rotation.x = Math.PI / 2
  }
}

function roundedRectShape(w: number, d: number, r: number, inset = 0): THREE.Shape {
  const hw = w / 2 - inset, hd = d / 2 - inset
  const rr = Math.max(0.01, Math.min(r, hw, hd))
  const s = new THREE.Shape()
  s.moveTo(-hw + rr, -hd)
  s.lineTo(hw - rr, -hd); s.quadraticCurveTo(hw, -hd, hw, -hd + rr)
  s.lineTo(hw, hd - rr); s.quadraticCurveTo(hw, hd, hw - rr, hd)
  s.lineTo(-hw + rr, hd); s.quadraticCurveTo(-hw, hd, -hw, hd - rr)
  s.lineTo(-hw, -hd + rr); s.quadraticCurveTo(-hw, -hd, -hw + rr, -hd)
  return s
}

function rug(B: Builder) {
  const { w, d } = B
  const h = Math.max(B.h, 1.2)
  const r = Math.min(w, d) * 0.05
  const shape = roundedRectShape(w, d, r)
  const g = new THREE.ExtrudeGeometry(shape, { depth: h, bevelEnabled: false })
  g.rotateX(-Math.PI / 2)
  // ExtrudeGeometry maps shape y -> -z after rotation: flip so +y shape = +z
  g.scale(1, 1, -1)
  const flip = g.index
  if (flip) { // reverse winding after the mirror
    const idx = flip.array as Uint16Array | Uint32Array
    for (let i = 0; i < idx.length; i += 3) { const t = idx[i + 1]; idx[i + 1] = idx[i + 2]; idx[i + 2] = t }
  }
  g.computeVertexNormals()
  boxProjectUV(g)
  // lift the rug 1.5 mm off the floor so its underside never z-fights with the floor plane
  B.mesh(g, B.P, 0, 0.15, 0, B.group, false)
  // border stripe in the secondary colour
  const bw = Math.min(w, d) * 0.045
  const ring = roundedRectShape(w, d, r, Math.min(w, d) * 0.06)
  const hole = roundedRectShape(w, d, r * 0.7, Math.min(w, d) * 0.06 + bw)
  ring.holes.push(new THREE.Path(hole.getPoints(12).reverse()))
  const rg = new THREE.ShapeGeometry(ring, 12)
  rg.rotateX(-Math.PI / 2)
  boxProjectUV(rg)
  // Own material (B.S is shared with other parts) and a clear 2.5 mm lift above the rug top,
  // so the border never z-fights with the rug surface (was 0.6 mm: visible flicker).
  const stripeMat = (B.S as THREE.Material).clone()
  stripeMat.polygonOffset = true
  stripeMat.polygonOffsetFactor = -2
  stripeMat.polygonOffsetUnits = -2
  const m = B.mesh(rg, stripeMat, 0, h + 0.25, 0, B.group, false)
  m.renderOrder = 1
  m.castShadow = false
}

function lamp(B: Builder, floor: boolean) {
  const { w, d, h } = B
  const kind = B.p<string>('shade', 'drum')
  const m = lampMetrics(floor ? 'floor_lamp' : 'table_lamp', w, d, h)
  const shadeH = kind === 'none' ? Math.min(10, h * 0.1) : m.shadeH
  const shadeR = Math.min(w, d) / 2
  const y0 = h - shadeH
  if (floor) {
    const baseR = Math.min(shadeR * 0.6, 17)
    B.cyl(B.P, baseR, baseR * 1.04, 2.2, 0, 0, 0, 36)
    B.cyl(B.P, 0.9, 0.9, y0 - 2.2, 0, 2.2, 0, 10)
    B.cyl(B.P, 1.6, 1.6, 4, 0, y0 - 4, 0, 10)
  } else {
    // ceramic body from a lathe profile: gourd or cylinder
    const bodyH = y0 - 0.5
    const shape = B.p<string>('base', 'gourd')
    const R = Math.min(shadeR * 0.62, bodyH * 0.45)
    const pts: THREE.Vector2[] = []
    if (shape === 'cylinder') {
      pts.push(new THREE.Vector2(0.01, 0), new THREE.Vector2(R * 0.8, 0), new THREE.Vector2(R * 0.8, bodyH * 0.9), new THREE.Vector2(R * 0.3, bodyH), new THREE.Vector2(0.01, bodyH))
    } else {
      for (let i = 0; i <= 14; i++) {
        const t = i / 14
        const rad = R * (0.18 + 0.82 * Math.sin(Math.PI * Math.pow(t, 0.8)) ) + 0.01
        pts.push(new THREE.Vector2(i === 0 ? 0.01 : rad, t * bodyH))
      }
    }
    const lg = new THREE.LatheGeometry(pts, 36)
    boxProjectUV(lg)
    B.mesh(lg, B.P, 0, 0, 0, B.group, false)
    B.cyl(B.P, 0.8, 0.8, 2, 0, bodyH, 0, 8)
  }
  addShade(B, kind, shadeR, shadeH, y0)
}

function pendant(B: Builder, gap: number) {
  const { w, d, h } = B
  const kind = B.p<string>('shade', 'dome')
  const r = Math.min(w, d) / 2
  if (kind === 'globe') {
    const g = new THREE.SphereGeometry(1, 40, 28)
    g.scale(r, h / 2, r)
    g.translate(0, h / 2, 0)
    const mat = B.glowMat(B.shadeBase(), 1)
    mat.side = THREE.FrontSide
    B.mesh(g, mat, 0, 0, 0, B.group, false)
  } else {
    addShade(B, kind === 'none' ? 'dome' : kind, r, h, 0)
  }
  // cord + canopy up to the ceiling
  const cord = Math.max(gap, 0)
  if (cord > 0.5) B.cyl(getMaterial('black_metal', '#1d1d1f'), 0.35, 0.35, cord, 0, h, 0, 6)
  if (gap > -1) B.cyl(getMaterial('black_metal', '#1d1d1f'), 4.5, 5.5, 1.6, 0, h + Math.max(cord, 0) - 1.6, 0, 20)
  B.cyl(getMaterial('brass', '#c9a45c'), 0.9, 0.9, 3, 0, h - 0.5, 0, 8)
}

function sconce(B: Builder) {
  const { w, d, h, hd } = B
  const kind = B.p<string>('shade', 'cone')
  const sr = Math.min(w / 2, d / 2 - 1, h * 0.3)
  const shadeH = Math.min(h * 0.55, sr * 1.8)
  const sy = h * 0.5 - shadeH * 0.1
  const sz = hd - sr
  // wall plate
  B.box(B.P, Math.min(w * 0.45, 9), h * 0.85, 1.4, 0, h * 0.075, -hd + 0.7, 0.5)
  // arm
  const armLen = Math.max(1, sz + hd - 1.4)
  const arm = B.cyl(B.P, 0.6, 0.6, armLen, 0, 0, 0, 8, false, B.group, false)
  arm.rotation.x = Math.PI / 2
  arm.position.set(0, sy - shadeH * 0.1, -hd + 1.4 + armLen / 2)
  const shadeKind = kind === 'none' ? 'none' : kind
  addShade(B, shadeKind, sr, shadeH, sy - shadeH * 0.5, 0, sz)
}

function ledStrip(B: Builder) {
  const { w, d, h } = B
  const dir = B.item.light?.direction
  const down = !dir || dir[1] <= 0.3
  const chan = getMaterial('black_metal', '#2a2c2f')
  B.box(chan, w, Math.max(h * 0.6, 0.6), d, 0, down ? h * 0.4 : 0, 0, 0.2)
  const em = new THREE.MeshStandardMaterial({ color: '#fff4de', roughness: 0.5 })
  em.emissive = new THREE.Color('#000')
  B.glows.push({ mat: em, strength: 3 })
  const e = new THREE.BoxGeometry(Math.max(w - 0.6, 0.5), Math.max(h * 0.4, 0.35), Math.max(d - 0.6, 0.5))
  B.mesh(e, em, 0, down ? h * 0.2 : h * 0.8, 0, B.group, false)
}

function pictureLight(B: Builder) {
  const { w, d, h, hd } = B
  const r = Math.min(h / 2, 2.2)
  const barZ = hd - r - 0.1
  B.box(B.P, Math.min(w * 0.12, 5), h, 1.2, 0, 0, -hd + 0.6, 0.3)
  for (const s of [-1, 1]) {
    const arm = B.cyl(B.P, 0.55, 0.55, d - r, s * w * 0.32, 0, 0, 8, false, B.group, false)
    arm.rotation.x = Math.PI / 2
    arm.position.set(s * w * 0.32, h / 2, -hd + (d - r) / 2)
  }
  const bar = B.cyl(B.P, r, r, w, 0, 0, 0, 16, false, B.group, false)
  bar.rotation.z = Math.PI / 2
  bar.position.set(0, h / 2, barZ)
  const em = new THREE.MeshStandardMaterial({ color: '#fff4de', roughness: 0.5 })
  em.emissive = new THREE.Color('#000')
  B.glows.push({ mat: em, strength: 3 })
  B.mesh(new THREE.BoxGeometry(w - 2, 0.4, r), em, 0, h / 2 - r + 0.1, barZ + r * 0.35, B.group, false)
}

function plant(B: Builder) {
  const { w, d, h } = B
  const rnd = mulberry32(hashStr(B.item.id))
  const potH = clamp(h * 0.26, 14, 40)
  const potR = Math.min(w, d) * 0.5 * 0.78
  B.cyl(B.P, potR, potR * 0.72, potH, 0, 0, 0, 28)
  B.cyl(getMaterial('stone', '#3b2f26'), potR * 0.92, potR * 0.92, 1, 0, potH - 1.2, 0, 24)
  const foliageH = h - potH
  const leaves: THREE.BufferGeometry[] = []
  const greens = ['#3f7d4e', '#4f9560', '#2f6a41', '#5ba46b'].map((c) => new THREE.Color(c))
  const tiers = 5
  for (let t = 0; t < tiers; t++) {
    const k = (t + 0.6) / tiers
    const ring = 5 + Math.floor(rnd() * 3)
    for (let i = 0; i < ring; i++) {
      const a = (i / ring) * Math.PI * 2 + rnd() * 0.8 + t
      const rad = (0.15 + 0.85 * Math.sin(k * Math.PI * 0.9 + 0.2)) * Math.min(w, d) * 0.45
      const lh = foliageH * (0.2 + rnd() * 0.08)
      const lw = lh * 0.5
      const g = new THREE.SphereGeometry(1, 10, 8)
      g.scale(lw * 0.5, 0.06 * lh + 0.4, lh * 0.5)
      // pitch outward/up: leaf long axis (z) tilts to point away from the stem
      g.translate(0, 0, lh * 0.45)
      g.rotateX(-0.55 - rnd() * 0.5)
      g.rotateY(a)
      g.translate(Math.cos(a) * rad * 0.35, potH + k * foliageH * 0.9, Math.sin(a) * rad * 0.35)
      const c = greens[Math.floor(rnd() * greens.length)].clone().multiplyScalar(0.85 + rnd() * 0.3)
      const arr = new Float32Array(g.getAttribute('position').count * 3)
      for (let q = 0; q < arr.length; q += 3) { arr[q] = c.r; arr[q + 1] = c.g; arr[q + 2] = c.b }
      g.setAttribute('color', new THREE.BufferAttribute(arr, 3))
      g.deleteAttribute('uv')
      leaves.push(g)
    }
  }
  const merged = mergeGeometries(leaves, false)
  fitGeometry(merged, w * 0.96, foliageH, d * 0.96, potH - 1)
  const lm = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, side: THREE.DoubleSide })
  B.mesh(merged, lm, 0, 0, 0)
  if (h > 70) {
    const trunkM = getMaterial('walnut', '#5b4331')
    B.cyl(trunkM, 1.0, 1.7, foliageH * 0.7, 0, potH - 1, 0, 8)
  }
}

function abstractArt(item: Item, aspect: number): THREE.CanvasTexture {
  const H = 256, W = Math.max(64, Math.round(H * aspect))
  const c = makeCanvas(W, H)
  const ctx = c.getContext('2d')!
  const rnd = mulberry32(hashStr(item.id))
  const ec = effColors(item)
  const a = safeColor(ec.primary, '#d9cdb8')
  const b = safeColor(ec.secondary, '#6e7f6a')
  const acc = safeColor(ec.accent, '#c8553d')
  const css = (col: THREE.Color) => '#' + col.getHexString(THREE.SRGBColorSpace)
  ctx.fillStyle = css(a.clone().lerp(new THREE.Color('#ffffff'), 0.45))
  ctx.fillRect(0, 0, W, H)
  const shapes = 3 + Math.floor(rnd() * 3)
  for (let i = 0; i < shapes; i++) {
    ctx.fillStyle = css(i % 3 === 0 ? b : i % 3 === 1 ? acc : a)
    ctx.globalAlpha = 0.55 + rnd() * 0.4
    const cx = rnd() * W, cy = rnd() * H, r = (0.15 + rnd() * 0.3) * Math.min(W, H)
    if (rnd() < 0.5) { ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill() }
    else ctx.fillRect(cx - r, cy - r * 0.6, r * 2, r * 1.2)
  }
  ctx.globalAlpha = 1
  return toTexture(c, true)
}

function artT(B: Builder, mirror: boolean, poster = false) {
  const { w, d, h, hd } = B
  if (poster) return posterT(B)
  const ft = clamp(Math.min(w, h) * 0.05, 1.6, 4.5)
  const fm = B.S
  B.box(fm, w, ft, d, 0, h - ft, 0, 0.3)
  B.box(fm, w, ft, d, 0, 0, 0, 0.3)
  for (const s of [-1, 1]) B.box(fm, ft, h - 2 * ft, d, s * (w / 2 - ft / 2), ft, 0, 0.3)
  const iw = w - 2 * ft, ih = h - 2 * ft
  if (mirror) {
    const mm = new THREE.MeshStandardMaterial({ color: '#dfe6ea', metalness: 1, roughness: 0.04 })
    B.mesh(new THREE.PlaneGeometry(iw, ih), mm, 0, h / 2, hd - ft * 0.35)
  } else {
    const tex = abstractArt(B.item, iw / ih)
    const cm = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.85 })
    B.mesh(new THREE.PlaneGeometry(iw, ih), cm, 0, h / 2, hd - ft * 0.5)
    B.box(getMaterial('painted', '#f2efe9'), iw, ih, Math.max(0.5, d - ft), 0, ft, -ft * 0.3, 0.1)
  }
}

/** poster / print: paper with a white margin, thin metal frame (frame: 'thin') or none with two rails */
function posterT(B: Builder) {
  const { w, d, h, hd } = B
  const framed = B.p<string>('frame', 'thin') !== 'none'
  const ft = framed ? 1.0 : 0
  const margin = clamp(Math.min(w, h) * 0.07, 2, 4)
  const paper = getMaterial('painted', '#f4f0e8')
  const iw = w - 2 * ft, ih = h - 2 * ft
  B.box(paper, iw, ih, Math.max(0.4, d - 0.8), 0, ft, -0.2, 0.1)
  const tex = abstractArt(B.item, (iw - 2 * margin) / (ih - 2 * margin))
  const pm = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.8 })
  B.mesh(new THREE.PlaneGeometry(iw - 2 * margin, ih - 2 * margin), pm, 0, h / 2, hd - 0.38)
  if (framed) {
    const fm = B.P
    B.box(fm, w, ft, d, 0, h - ft, 0, 0.2)
    B.box(fm, w, ft, d, 0, 0, 0, 0.2)
    for (const s of [-1, 1]) B.box(fm, ft, h - 2 * ft, d, s * (w / 2 - ft / 2), ft, 0, 0.2)
  } else {
    const rail = getMaterial('black_metal', '#1d1d1f')
    B.box(rail, w + 0.4, 1.0, 1.2, 0, h - 0.6, 0, 0.3)
    B.box(rail, w + 0.4, 1.0, 1.2, 0, -0.4, 0, 0.3)
  }
}

/** small photo frame: stands on a surface when params.stand (default), else hangs */
function frameT(B: Builder) {
  const { w, d, h, hd } = B
  const stand = B.p<boolean>('stand', true)
  const ft = clamp(Math.min(w, h) * 0.09, 1.2, 3)
  const pivot = new THREE.Group()
  pivot.rotation.x = stand ? -0.2 : 0
  pivot.position.set(0, 0, stand ? 0 : 0)
  B.group.add(pivot)
  const fm = B.S
  B.box(fm, w, ft, d, 0, h - ft, 0, 0.3, pivot)
  B.box(fm, w, ft, d, 0, 0, 0, 0.3, pivot)
  for (const s of [-1, 1]) B.box(fm, ft, h - 2 * ft, d, s * (w / 2 - ft / 2), ft, 0, 0.3, pivot)
  const iw = w - 2 * ft, ih = h - 2 * ft
  B.box(getMaterial('painted', '#f2efe9'), iw, ih, Math.max(0.3, d - 0.6), 0, ft, -0.2, 0.1, pivot)
  const cm = new THREE.MeshStandardMaterial({ map: abstractArt(B.item, iw / ih), roughness: 0.8 })
  B.mesh(new THREE.PlaneGeometry(iw * 0.82, ih * 0.82), cm, 0, h / 2, hd - 0.28, pivot)
  if (stand) {
    const leg = B.box(fm, w * 0.18, h * 0.8, 0.8, 0, 0, -hd - 1, 0.2, B.group)
    leg.rotation.x = 0.3
  }
}

function generic(B: Builder) {
  B.box(B.P, B.w, B.h, B.d, 0, 0, 0, Math.min(2, Math.min(B.w, B.d, B.h) * 0.15))
}

export function buildModel(item: Item, opts: BuildOpts = {}): Built {
  const template = templateOf(item)
  const B = new Builder(item, template)
  switch (template) {
    case 'sofa': sofa(B, false); break
    case 'armchair': sofa(B, true); break
    case 'chair': chairT(B); break
    case 'coffee_table': case 'side_table': case 'dining_table': case 'desk': tableLike(B, template); break
    case 'bed': bedT(B); break
    case 'nightstand': nightstand(B); break
    case 'bookshelf': bookshelf(B, opts.shelfBlocks); break
    case 'cabinet': cabinet(B, false); break
    case 'tv_stand': cabinet(B, true); break
    case 'rug': rug(B); break
    case 'floor_lamp': lamp(B, true); break
    case 'table_lamp': lamp(B, false); break
    case 'pendant': pendant(B, opts.ceilingGap ?? 0); break
    case 'sconce': sconce(B); break
    case 'led_strip': ledStrip(B); break
    case 'picture_light': pictureLight(B); break
    case 'plant': plant(B); break
    case 'art': artT(B, false); break
    case 'poster': artT(B, false, true); break
    case 'frame': frameT(B); break
    case 'mirror': artT(B, true); break
    case 'guitar': guitar(B); break
    case 'guitar_hanger': guitarHanger(B); break
    case 'amp': amp(B); break
    case 'book_stack': bookStack(B); break
    case 'vinyl_crate': vinylCrate(B); break
    case 'vase': vase(B); break
    case 'cushion': cushion(B); break
    case 'throw': throwT(B, opts.parent); break
    case 'candle': candle(B); break
    case 'small_plant': smallPlant(B); break
    case 'tray': tray(B); break
    default: generic(B)
  }
  return { group: B.group, glows: B.glows, template }
}

import * as THREE from 'three'
import { surfaceTexture, type SurfaceKind } from './gfx'
import { safeColor } from './color'

export interface Preset {
  tex?: SurfaceKind
  /** default colour when the item gives none */
  color: string
  /** metals ignore item colours (a brass lamp stays brass) */
  fixed?: boolean
  rough: number
  metal?: number
  sheen?: number
  clearcoat?: number
  glass?: boolean
  texStrength?: number // how much the albedo texture modulates (kept via colour; bump scale multiplier)
}

export const PRESETS: Record<string, Preset> = {
  oak: { tex: 'wood', color: '#c9a273', rough: 0.58 },
  light_wood: { tex: 'wood', color: '#dfc79c', rough: 0.55 },
  walnut: { tex: 'wood', color: '#6a4833', rough: 0.5 },
  dark_wood: { tex: 'wood', color: '#3b2a21', rough: 0.5 },
  linen: { tex: 'linen', color: '#d9d0bf', rough: 0.95 },
  cotton: { tex: 'linen', color: '#e6e1d6', rough: 0.92 },
  wool: { tex: 'wool', color: '#cfc7b8', rough: 1.0 },
  boucle: { tex: 'boucle', color: '#e4dccd', rough: 1.0 },
  velvet: { tex: 'linen', color: '#6b7f6a', rough: 0.85, sheen: 1 },
  leather: { tex: 'leather', color: '#7a4a2e', rough: 0.48, clearcoat: 0.18 },
  rattan: { tex: 'rattan', color: '#c8a46a', rough: 0.8 },
  brass: { color: '#c9a45c', rough: 0.3, metal: 1, fixed: true },
  chrome: { color: '#e4e7ea', rough: 0.1, metal: 1, fixed: true },
  black_metal: { color: '#222427', rough: 0.45, metal: 0.85, fixed: true },
  ceramic: { color: '#ece6da', rough: 0.2, clearcoat: 0.6 },
  glass: { color: '#d8ecf0', rough: 0.04, glass: true, fixed: true },
  marble: { tex: 'marble', color: '#ece9e3', rough: 0.22, clearcoat: 0.3 },
  stone: { tex: 'stone', color: '#9c978e', rough: 0.8 },
  plastic: { color: '#d8d8d8', rough: 0.4 },
  painted: { tex: 'stone', color: '#dcd6ca', rough: 0.55 },
}

const cache = new Map<string, THREE.MeshStandardMaterial>()

export function presetFor(cls: string | null | undefined, fallback = 'painted'): Preset {
  const key = (cls ?? '').toLowerCase().replace(/[\s-]/g, '_')
  return PRESETS[key] ?? PRESETS[fallback]
}

export function getMaterial(cls: string | null | undefined, hex: string | null | undefined, fallbackCls = 'painted', extra?: { double?: boolean; vertexColors?: boolean }): THREE.MeshStandardMaterial {
  const p = presetFor(cls, fallbackCls)
  const colour = p.fixed ? safeColor(p.color, p.color) : safeColor(hex, p.color)
  const key = `${cls}|${colour.getHexString()}|${p === PRESETS[fallbackCls] ? fallbackCls : ''}|${extra?.double ? 'd' : ''}${extra?.vertexColors ? 'v' : ''}`
  const hit = cache.get(key)
  if (hit) return hit

  let m: THREE.MeshStandardMaterial
  if (p.sheen || p.clearcoat || p.glass) {
    const pm = new THREE.MeshPhysicalMaterial({ color: colour, roughness: p.rough, metalness: p.metal ?? 0 })
    if (p.sheen) {
      pm.sheen = p.sheen
      pm.sheenRoughness = 0.45
      pm.sheenColor = colour.clone().lerp(new THREE.Color('#ffffff'), 0.55)
    }
    if (p.clearcoat) { pm.clearcoat = p.clearcoat; pm.clearcoatRoughness = 0.18 }
    m = pm
  } else {
    m = new THREE.MeshStandardMaterial({ color: colour, roughness: p.rough, metalness: p.metal ?? 0 })
  }
  if (p.glass) {
    m.transparent = true
    m.opacity = 0.28
    m.depthWrite = false
    m.envMapIntensity = 1.4
  }
  if (p.tex) {
    const t = surfaceTexture(p.tex)
    const rep = 1 / t.tileCm
    for (const tex of [t.map, t.bump]) tex?.repeat.set(rep, rep)
    if (t.map) m.map = t.map
    if (t.bump) { m.bumpMap = t.bump; m.bumpScale = t.bumpScale }
  }
  if (extra?.double) m.side = THREE.DoubleSide
  if (extra?.vertexColors) m.vertexColors = true
  m.userData.shared = true
  cache.set(key, m)
  return m
}

/** Materials are shared; texture repeat is per-texture so the same canvas can't serve two tile sizes. */
export function plainMaterial(hex: string, rough = 0.7, metal = 0): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color: safeColor(hex, '#cccccc'), roughness: rough, metalness: metal })
}

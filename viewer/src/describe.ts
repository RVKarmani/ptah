import type { Item } from './types'

const WOODS = new Set(['oak', 'walnut', 'light_wood', 'dark_wood', 'ash', 'birch', 'pine', 'teak', 'maple', 'cherry', 'bamboo', 'rattan'])
const METALS = new Set(['black_metal', 'brass', 'steel', 'chrome', 'iron', 'copper', 'gold', 'metal'])
const SKIP_MATERIAL = new Set(['painted', 'plastic', 'mixed'])
const spaced = (s: string) => s.replace(/_/g, ' ')

/** A plain colour word for a hex like #8a8f7a ("sage"). */
export function colorName(hex?: string | null): string | null {
  const m = (hex ?? '').trim().match(/^#?([0-9a-f]{6})$/i)
  if (!m) return null
  const n = parseInt(m[1], 16)
  const r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1))
  let h = 0
  if (d) h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
  h = (h * 60 + 360) % 360
  if (l < 0.13) return 'black'
  if (s < 0.1) return l < 0.32 ? 'charcoal' : l < 0.66 ? 'grey' : l < 0.9 ? 'light grey' : 'white'
  if (l > 0.82 && s < 0.5) return h > 20 && h < 70 ? 'cream' : 'pale ' + hue(h, s, l)
  return hue(h, s, l)
}
function hue(h: number, s: number, l: number): string {
  if (h < 12 || h >= 345) return l < 0.32 ? 'burgundy' : l > 0.7 ? 'blush' : 'red'
  if (h < 25) return l < 0.3 ? 'brown' : l > 0.7 ? 'peach' : s > 0.4 ? 'terracotta' : 'clay'
  if (h < 42) return l < 0.3 ? 'brown' : l < 0.62 ? (s < 0.4 ? 'tan' : 'caramel') : 'sand'
  if (h < 68) return l < 0.62 ? 'mustard' : 'butter'
  if (h < 100) return s < 0.3 ? 'sage' : l < 0.35 ? 'olive' : 'lime'
  if (h < 165) return s < 0.3 ? 'sage' : l < 0.3 ? 'forest green' : 'green'
  if (h < 195) return 'teal'
  if (h < 235) return l < 0.3 ? 'navy' : l > 0.72 ? 'sky blue' : 'blue'
  if (h < 265) return l < 0.45 ? 'indigo' : 'periwinkle'
  if (h < 300) return l > 0.7 ? 'lavender' : 'plum'
  return l > 0.7 ? 'pink' : 'rose'
}

const FLAT = new Set(['art', 'poster', 'mirror', 'tv', 'frame', 'picture_light'])
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && /^\d+$/.test(v) ? +v : null)

/** One calm line: "Sage boucle 3-seat sofa · 170 cm · tapered oak legs". The retailer's title wins when there is one. */
export function describeItem(item: Item): string {
  const p = (item.model?.params ?? {}) as Record<string, unknown>
  const mat = item.material?.primary ?? null
  const mat2 = item.material?.secondary ?? null
  const col = colorName(item.user_colors?.primary || item.colors?.primary)
  const cm = (v: number) => `${Math.round(v)} cm`
  const size = FLAT.has(item.category) ? `${cm(item.w)} × ${cm(item.h).replace(' cm', '')} cm` : item.category === 'rug' ? `${cm(item.w)} × ${cm(item.d).replace(' cm', '')} cm` : cm(Math.max(item.w, item.d, item.category === 'floor_lamp' || item.category === 'plant' ? item.h : 0))
  const title = item.product?.title?.trim()
  if (title) return `${title} · ${size}`

  const noun = spaced(item.category)
  const seats = num(p.seats)
  const shape = typeof p.shape === 'string' && /round|oval|curved|arc/.test(p.shape) ? p.shape : null
  const head: string[] = []
  const matWord = mat && !SKIP_MATERIAL.has(mat) ? spaced(mat) : null
  if (matWord && (WOODS.has(mat!) || METALS.has(mat!))) { if (shape) head.push(shape); head.push(matWord) }
  else { if (col) head.push(col); if (shape) head.push(shape); if (matWord) head.push(matWord) }
  if (seats && seats > 0) head.push(`${seats}-seat`)
  head.push(noun)

  let feature: string | null = null
  const legs = typeof p.legs === 'string' && p.legs !== 'none' ? p.legs : null
  const shelves = num(p.shelves), doors = num(p.doors)
  if (legs) feature = `${spaced(legs)}${mat2 && WOODS.has(mat2) ? ' ' + spaced(mat2) : ''} legs`
  else if (typeof p.shade === 'string' && p.shade !== 'none') feature = `${spaced(p.shade)} shade${mat2 === 'linen' ? ' in linen' : ''}`
  else if (shelves) feature = `${shelves} shelves`
  else if (doors) feature = `${doors} doors`
  else if (typeof p.kind === 'string') feature = spaced(p.kind)
  else if (mat2 && !SKIP_MATERIAL.has(mat2) && mat2 !== mat) feature = `${spaced(mat2)} details`
  const first = head.join(' ')
  return [first.charAt(0).toUpperCase() + first.slice(1), size, feature].filter(Boolean).join(' · ')
}

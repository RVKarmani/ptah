import * as THREE from 'three'

const clamp255 = (v: number) => Math.min(255, Math.max(0, v))

/** Tanner Helland's blackbody approximation: Kelvin -> sRGB 0..255. */
export function kelvinToRGB(kelvin: number): [number, number, number] {
  const t = Math.min(Math.max(kelvin, 1000), 40000) / 100
  let r: number, g: number, b: number
  if (t <= 66) {
    r = 255
    g = 99.4708025861 * Math.log(t) - 161.1195681661
    b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307
  } else {
    r = 329.698727446 * Math.pow(t - 60, -0.1332047592)
    g = 288.1221695283 * Math.pow(t - 60, -0.0755148492)
    b = 255
  }
  return [clamp255(r), clamp255(g), clamp255(b)]
}

/** Parse `#rrggbb`, `rgb(...)` or an [r,g,b] triple (0-255, or 0-1 if all <= 1). */
export function parseRgb(v: unknown): THREE.Color | null {
  if (!v) return null
  if (typeof v === 'string') {
    try { return new THREE.Color(v) } catch { return null }
  }
  if (Array.isArray(v) && v.length >= 3) {
    const n = v.slice(0, 3).map(Number)
    if (n.some((x) => !Number.isFinite(x))) return null
    const unit = n.every((x) => x <= 1)
    const k = unit ? 1 : 255
    return new THREE.Color().setRGB(n[0] / k, n[1] / k, n[2] / k, THREE.SRGBColorSpace)
  }
  return null
}

/** Display tint for a colour temperature (not luminance-normalised). */
export function kelvinTint(k: number, out = new THREE.Color()): THREE.Color {
  const [r, g, b] = kelvinToRGB(k)
  return out.setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace)
}

/** Light colour for a temperature, normalised to unit luminance so that lumens stay meaningful. */
export function kelvinLightColor(k: number, out = new THREE.Color()): THREE.Color {
  kelvinTint(k, out)
  const y = 0.2126 * out.r + 0.7152 * out.g + 0.0722 * out.b
  return out.multiplyScalar(1 / Math.max(y, 0.05))
}

export function lightColorFrom(rgb: unknown, k: number, out = new THREE.Color()): THREE.Color {
  const c = parseRgb(rgb)
  if (c) {
    out.copy(c)
    const y = 0.2126 * out.r + 0.7152 * out.g + 0.0722 * out.b
    return out.multiplyScalar(1 / Math.max(y, 0.05))
  }
  return kelvinLightColor(k, out)
}

export function hexOf(c: THREE.Color): string {
  return '#' + c.getHexString(THREE.SRGBColorSpace)
}

export function safeColor(hex: string | null | undefined, fallback: string): THREE.Color {
  if (hex) {
    try { return new THREE.Color(hex) } catch { /* fall through */ }
  }
  return new THREE.Color(fallback)
}

export const smoothstep = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t

export function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
export function hashStr(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

export interface ColorSet { primary?: string | null; secondary?: string | null; accent?: string | null }
/** Item colours with the user's own `user_colors` taking precedence over photo-extracted `colors`, key by key. */
export function effColors(item: { colors?: ColorSet | null; user_colors?: ColorSet | null }): { primary: string | null; secondary: string | null; accent: string | null } {
  const u = item.user_colors ?? {}
  const c = item.colors ?? {}
  return { primary: u.primary || c.primary || null, secondary: u.secondary || c.secondary || null, accent: u.accent || c.accent || null }
}

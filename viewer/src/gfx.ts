import * as THREE from 'three'
import { mulberry32 } from './color'

// ---------------------------------------------------------------- tileable noise

export function makeCanvas(w: number, h = w): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  return c
}

/** Periodic value noise on an N x N grid with `cx` x `cy` lattice cells. Values 0..1. */
export function tileNoise(N: number, cx: number, cy: number, seed: number): Float32Array {
  const rnd = mulberry32(seed)
  const lat = new Float32Array(cx * cy)
  for (let i = 0; i < lat.length; i++) lat[i] = rnd()
  const out = new Float32Array(N * N)
  for (let y = 0; y < N; y++) {
    const fy = (y / N) * cy
    const y0 = Math.floor(fy)
    const ty = fy - y0
    const sy = ty * ty * (3 - 2 * ty)
    const ya = y0 % cy, yb = (y0 + 1) % cy
    for (let x = 0; x < N; x++) {
      const fx = (x / N) * cx
      const x0 = Math.floor(fx)
      const tx = fx - x0
      const sx = tx * tx * (3 - 2 * tx)
      const xa = x0 % cx, xb = (x0 + 1) % cx
      const a = lat[ya * cx + xa], b = lat[ya * cx + xb]
      const c = lat[yb * cx + xa], d = lat[yb * cx + xb]
      out[y * N + x] = a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy
    }
  }
  return out
}

export function fbm(N: number, cx: number, cy: number, octaves: number, seed: number, gain = 0.5): Float32Array {
  const out = new Float32Array(N * N)
  let amp = 1, norm = 0
  for (let o = 0; o < octaves; o++) {
    const n = tileNoise(N, cx * (1 << o), cy * (1 << o), seed + o * 101)
    for (let i = 0; i < out.length; i++) out[i] += n[i] * amp
    norm += amp
    amp *= gain
  }
  for (let i = 0; i < out.length; i++) out[i] /= norm
  return out
}

export function grayCanvas(arr: Float32Array, N: number, lo = 0, hi = 255): HTMLCanvasElement {
  const c = makeCanvas(N)
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(N, N)
  for (let i = 0; i < arr.length; i++) {
    const v = Math.max(0, Math.min(255, lo + (hi - lo) * arr[i]))
    img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = v
    img.data[i * 4 + 3] = 255
  }
  ctx.putImageData(img, 0, 0)
  return c
}

/** Height field (0..1, periodic) -> tangent-space normal map canvas (OpenGL convention, +Y up). */
export function heightToNormal(h: Float32Array, N: number, strength: number): HTMLCanvasElement {
  const c = makeCanvas(N)
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(N, N)
  const at = (x: number, y: number) => h[((y + N) % N) * N + ((x + N) % N)]
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * strength
      const dy = (at(x, y + 1) - at(x, y - 1)) * strength
      // canvas y runs down; texture v runs up -> flip dy
      let nx = -dx, ny = dy, nz = 1
      const l = Math.hypot(nx, ny, nz)
      nx /= l; ny /= l; nz /= l
      const i = (y * N + x) * 4
      img.data[i] = (nx * 0.5 + 0.5) * 255
      img.data[i + 1] = (ny * 0.5 + 0.5) * 255
      img.data[i + 2] = (nz * 0.5 + 0.5) * 255
      img.data[i + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  return c
}

export function toTexture(c: HTMLCanvasElement, srgb: boolean, repeat?: [number, number]): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c)
  t.wrapS = t.wrapT = THREE.RepeatWrapping
  t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace
  t.anisotropy = 8
  t.generateMipmaps = true
  t.minFilter = THREE.LinearMipmapLinearFilter
  if (repeat) t.repeat.set(repeat[0], repeat[1])
  t.needsUpdate = true
  return t
}

// ---------------------------------------------------------------- furniture surface textures (cm-based UV)

export interface SurfaceTex { map: THREE.Texture | null; bump: THREE.Texture | null; rough: THREE.Texture | null; tileCm: number; bumpScale: number }
const surfCache = new Map<string, SurfaceTex>()

function woodGrain(): SurfaceTex {
  const N = 512
  const broad = fbm(N, 2, 3, 3, 11)
  const fine = tileNoise(N, 3, 180, 29)
  const fine2 = tileNoise(N, 5, 90, 31)
  const h = new Float32Array(N * N)
  const m = new Float32Array(N * N)
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const i = y * N + x
      const rings = Math.sin(((y / N) * 20 + broad[i] * 1.7) * Math.PI * 2)
      const ring = 0.5 + 0.5 * rings
      const streak = fine[i] * 0.6 + fine2[i] * 0.4
      const g = 0.62 * ring + 0.38 * streak
      h[i] = g
      m[i] = 0.78 + 0.22 * (1 - g * 0.9) // lighter = brighter, dark rings
    }
  }
  return {
    map: toTexture(grayCanvas(m, N, 0, 255), true),
    bump: toTexture(grayCanvas(h, N), false),
    rough: null,
    tileCm: 70,
    bumpScale: 0.7,
  }
}

function weave(kind: 'linen' | 'wool' | 'boucle' | 'rattan'): SurfaceTex {
  const N = 128
  const noise = fbm(N, 16, 16, 3, kind === 'boucle' ? 7 : 5)
  const h = new Float32Array(N * N)
  const m = new Float32Array(N * N)
  const cell = kind === 'rattan' ? 16 : kind === 'wool' ? 4 : 2
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const i = y * N + x
      let g: number
      if (kind === 'boucle') {
        g = Math.pow(noise[i], 1.6) * 1.4
      } else if (kind === 'rattan') {
        const cx = Math.floor(x / cell), cy = Math.floor(y / cell)
        const over = (cx + cy) % 2 === 0
        const u = over ? (x % cell) / cell : (y % cell) / cell
        g = 0.55 + 0.45 * Math.sin(u * Math.PI) * (over ? 1 : 0.8) + (noise[i] - 0.5) * 0.15
      } else {
        const cx = Math.floor(x / cell), cy = Math.floor(y / cell)
        const over = (cx + cy) % 2 === 0
        const u = over ? (x % cell) / cell : (y % cell) / cell
        g = (over ? 0.62 : 0.45) + 0.2 * Math.sin(u * Math.PI) + (noise[i] - 0.5) * 0.25
      }
      h[i] = g
      m[i] = 0.86 + 0.14 * g
    }
  }
  const tile = kind === 'rattan' ? 18 : kind === 'boucle' ? 24 : kind === 'wool' ? 20 : 9
  return {
    map: toTexture(grayCanvas(m, N, 0, 255), true),
    bump: toTexture(grayCanvas(h, N), false),
    rough: null,
    tileCm: tile,
    bumpScale: kind === 'boucle' ? 2.2 : kind === 'rattan' ? 1.4 : 1.0,
  }
}

function grainBump(scaleCells: number, tile: number, strength: number, seed: number): SurfaceTex {
  const N = 256
  const n = fbm(N, scaleCells, scaleCells, 4, seed)
  const m = new Float32Array(N * N)
  for (let i = 0; i < m.length; i++) m[i] = 0.9 + 0.1 * n[i]
  return { map: toTexture(grayCanvas(m, N, 0, 255), true), bump: toTexture(grayCanvas(n, N), false), rough: null, tileCm: tile, bumpScale: strength }
}

function marbleTex(): SurfaceTex {
  const N = 512
  const warp = fbm(N, 3, 3, 5, 77)
  const warp2 = fbm(N, 6, 6, 3, 78)
  const m = new Float32Array(N * N)
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const i = y * N + x
      const v = Math.abs(Math.sin(((x + y) / N) * Math.PI * 2 * 2 + warp[i] * 9 + warp2[i] * 2.5))
      const vein = Math.pow(1 - v, 9)
      m[i] = 1 - vein * 0.5 - warp2[i] * 0.12
    }
  }
  return { map: toTexture(grayCanvas(m, N, 40, 255), true), bump: null, rough: null, tileCm: 90, bumpScale: 0 }
}

export type SurfaceKind = 'wood' | 'linen' | 'wool' | 'boucle' | 'rattan' | 'leather' | 'stone' | 'marble' | 'plaster'

export function surfaceTexture(kind: SurfaceKind): SurfaceTex {
  const hit = surfCache.get(kind)
  if (hit) return hit
  let t: SurfaceTex
  switch (kind) {
    case 'wood': t = woodGrain(); break
    case 'linen': t = weave('linen'); break
    case 'wool': t = weave('wool'); break
    case 'boucle': t = weave('boucle'); break
    case 'rattan': t = weave('rattan'); break
    case 'leather': t = grainBump(34, 14, 1.2, 3); break
    case 'stone': t = grainBump(18, 40, 0.8, 9); break
    case 'plaster': t = grainBump(10, 80, 0.4, 13); break
    case 'marble': t = marbleTex(); break
    default: t = woodGrain()
  }
  surfCache.set(kind, t)
  return t
}

// ---------------------------------------------------------------- UVs in centimetres

/**
 * Replace UVs with box-projected world-size UVs (1 unit = 1 cm) so a texture's `repeat = 1/tileCm`
 * gives real-world scale regardless of how big the part is. `vertical` rotates side-face UVs so
 * wood grain runs along the part's height (legs, posts).
 */
export function boxProjectUV(geo: THREE.BufferGeometry, vertical = false): THREE.BufferGeometry {
  const pos = geo.getAttribute('position')
  let nor = geo.getAttribute('normal')
  if (!nor) { geo.computeVertexNormals(); nor = geo.getAttribute('normal') }
  const uv = new Float32Array(pos.count * 2)
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i)
    const ax = Math.abs(nor.getX(i)), ay = Math.abs(nor.getY(i)), az = Math.abs(nor.getZ(i))
    let u: number, v: number
    if (ay >= ax && ay >= az) { u = x; v = z }
    else if (ax >= az) { u = vertical ? y : z; v = vertical ? z : y }
    else { u = vertical ? y : x; v = vertical ? x : y }
    uv[i * 2] = u
    uv[i * 2 + 1] = v
  }
  geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
  return geo
}

/** Scale+translate a geometry so its bounding box becomes exactly w x h x d with min y = y0, centred in x/z. */
export function fitGeometry(geo: THREE.BufferGeometry, w: number, h: number, d: number, y0 = 0): THREE.BufferGeometry {
  geo.computeBoundingBox()
  const b = geo.boundingBox!
  const sx = w / Math.max(1e-6, b.max.x - b.min.x)
  const sy = h / Math.max(1e-6, b.max.y - b.min.y)
  const sz = d / Math.max(1e-6, b.max.z - b.min.z)
  geo.translate(-(b.max.x + b.min.x) / 2, -b.min.y, -(b.max.z + b.min.z) / 2)
  geo.scale(sx, sy, sz)
  geo.translate(0, y0, 0)
  geo.computeBoundingBox()
  return geo
}

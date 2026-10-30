import * as THREE from 'three'
import { useEffect, useMemo, useState } from 'react'
import { fbm, heightToNormal, makeCanvas, tileNoise, toTexture } from './gfx'
import { mulberry32 } from './color'

export interface FloorPreset { id: string; label: string; swatch: string; tileCm: number; rough: number; normalScale: number }

export const FLOORS: FloorPreset[] = [
  { id: 'light_oak', label: 'Light oak', swatch: '#d8bc8d', tileCm: 120, rough: 0.55, normalScale: 0.6 },
  { id: 'mid_oak', label: 'Mid oak', swatch: '#b48a58', tileCm: 120, rough: 0.55, normalScale: 0.6 },
  { id: 'dark_wood', label: 'Dark wood', swatch: '#4a3426', tileCm: 120, rough: 0.5, normalScale: 0.6 },
  { id: 'herringbone', label: 'Herringbone', swatch: '#b99366', tileCm: 60, rough: 0.5, normalScale: 0.7 },
  { id: 'concrete', label: 'Concrete', swatch: '#9d9b96', tileCm: 200, rough: 0.78, normalScale: 0.5 },
  { id: 'tile_light', label: 'Light tile', swatch: '#e4e0d7', tileCm: 120, rough: 0.35, normalScale: 0.5 },
  { id: 'tile_dark', label: 'Dark tile', swatch: '#3b3f45', tileCm: 120, rough: 0.35, normalScale: 0.5 },
  { id: 'carpet', label: 'Carpet', swatch: '#b9b1a3', tileCm: 80, rough: 1, normalScale: 1.2 },
]
export const floorById = (id: string | null | undefined) => FLOORS.find((f) => f.id === id) ?? FLOORS[0]

export interface FloorTex {
  map: THREE.Texture
  normalMap: THREE.Texture | null
  roughnessMap: THREE.Texture | null
  tileCm: number
  source: 'files' | 'procedural'
}

const N = 1024

interface Painted { color: HTMLCanvasElement; height: Float32Array; rough: HTMLCanvasElement }

function readGray(c: HTMLCanvasElement): Float32Array {
  const ctx = c.getContext('2d')!
  const d = ctx.getImageData(0, 0, c.width, c.height).data
  const out = new Float32Array(c.width * c.height)
  for (let i = 0; i < out.length; i++) out[i] = d[i * 4] / 255
  return out
}

function hsl(h: number, s: number, l: number) { return `hsl(${h.toFixed(1)} ${(s * 100).toFixed(1)}% ${(l * 100).toFixed(1)}%)` }

function paintPlanks(base: { h: number; s: number; l: number }, plankCm: number, tileCm: number, seed: number): Painted {
  const color = makeCanvas(N), hc = makeCanvas(N), rc = makeCanvas(N)
  const cx = color.getContext('2d')!, hx = hc.getContext('2d')!, rx = rc.getContext('2d')!
  const rnd = mulberry32(seed)
  const pxcm = N / tileCm
  const rows = Math.round(tileCm / plankCm)
  const ph = N / rows
  cx.fillStyle = '#1d140e'; cx.fillRect(0, 0, N, N)
  hx.fillStyle = '#000'; hx.fillRect(0, 0, N, N)
  rx.fillStyle = '#fff'; rx.fillRect(0, 0, N, N)
  const gap = Math.max(2, 0.35 * pxcm)
  for (let r = 0; r < rows; r++) {
    const joint = rnd() * N
    const segs: [number, number][] = [[joint, joint + N * (0.35 + rnd() * 0.3)], [0, 0]]
    segs[1] = [segs[0][1], joint + N]
    for (const [a, b] of segs) {
      const l = base.l * (0.9 + rnd() * 0.2)
      const hue = base.h + (rnd() - 0.5) * 5
      const x0 = a, w = b - a - gap, y0 = r * ph + gap / 2, hh = ph - gap
      for (const dx of [-N, 0, N]) {
        const px = x0 + dx
        cx.save(); cx.beginPath(); cx.rect(px, y0, w, hh); cx.clip()
        cx.fillStyle = hsl(hue, base.s, l); cx.fillRect(px, y0, w, hh)
        // grain
        const lines = 60
        for (let i = 0; i < lines; i++) {
          const y = y0 + rnd() * hh
          const amp = 1 + rnd() * 3, f = 0.002 + rnd() * 0.01, ph0 = rnd() * 6.28
          cx.strokeStyle = `rgba(${rnd() < 0.7 ? '40,22,8' : '255,240,220'},${0.04 + rnd() * 0.09})`
          cx.lineWidth = 0.6 + rnd() * 1.6
          cx.beginPath()
          for (let x = px; x <= px + w; x += 24) {
            const yy = y + Math.sin(x * f + ph0) * amp
            if (x === px) cx.moveTo(x, yy); else cx.lineTo(x, yy)
          }
          cx.stroke()
        }
        // knot-ish soft blotch
        if (rnd() < 0.25) {
          const kx = px + rnd() * w, ky = y0 + hh * (0.25 + rnd() * 0.5)
          const g = cx.createRadialGradient(kx, ky, 1, kx, ky, 20 + rnd() * 22)
          g.addColorStop(0, 'rgba(50,28,10,0.30)'); g.addColorStop(1, 'rgba(50,28,10,0)')
          cx.fillStyle = g; cx.fillRect(px - 40, y0, w + 80, hh)
        }
        cx.restore()
        hx.fillStyle = '#d0d0d0'; hx.fillRect(px, y0, w, hh)
        hx.save(); hx.beginPath(); hx.rect(px, y0, w, hh); hx.clip()
        for (let i = 0; i < 40; i++) {
          const y = y0 + rnd() * hh
          hx.strokeStyle = rnd() < 0.5 ? 'rgba(0,0,0,0.12)' : 'rgba(255,255,255,0.10)'
          hx.lineWidth = 0.8 + rnd() * 1.2
          hx.beginPath(); hx.moveTo(px, y); hx.lineTo(px + w, y + (rnd() - 0.5) * 6); hx.stroke()
        }
        hx.restore()
        rx.fillStyle = '#8c8c8c'; rx.fillRect(px, y0, w, hh)
      }
    }
  }
  return { color, height: readGray(hc), rough: rc }
}

function paintHerringbone(base: { h: number; s: number; l: number }, seed: number): Painted {
  const color = makeCanvas(N), hc = makeCanvas(N), rc = makeCanvas(N)
  const cx = color.getContext('2d')!, hx = hc.getContext('2d')!, rx = rc.getContext('2d')!
  const rnd = mulberry32(seed)
  const L = 4, n = 2 * L, cs = N / n
  cx.fillStyle = '#1d140e'; cx.fillRect(0, 0, N, N)
  hx.fillStyle = '#000'; hx.fillRect(0, 0, N, N)
  rx.fillStyle = '#fff'; rx.fillRect(0, 0, N, N)
  const gap = 2.5
  const piece = (x: number, y: number, w: number, h: number, horizontal: boolean) => {
    const l = base.l * (0.88 + rnd() * 0.24)
    const hue = base.h + (rnd() - 0.5) * 6
    for (const dx of [-N, 0, N]) for (const dy of [-N, 0, N]) {
      const px = x + dx + gap / 2, py = y + dy + gap / 2, pw = w - gap, ph = h - gap
      if (px > N || py > N || px + pw < 0 || py + ph < 0) continue
      cx.save(); cx.beginPath(); cx.rect(px, py, pw, ph); cx.clip()
      cx.fillStyle = hsl(hue, base.s, l); cx.fillRect(px, py, pw, ph)
      for (let i = 0; i < 26; i++) {
        cx.strokeStyle = `rgba(${rnd() < 0.7 ? '40,22,8' : '255,240,220'},${0.05 + rnd() * 0.09})`
        cx.lineWidth = 0.6 + rnd() * 1.4
        cx.beginPath()
        if (horizontal) { const yy = py + rnd() * ph; cx.moveTo(px, yy); cx.lineTo(px + pw, yy + (rnd() - 0.5) * 4) }
        else { const xx = px + rnd() * pw; cx.moveTo(xx, py); cx.lineTo(xx + (rnd() - 0.5) * 4, py + ph) }
        cx.stroke()
      }
      cx.restore()
      hx.fillStyle = '#d0d0d0'; hx.fillRect(px, py, pw, ph)
      rx.fillStyle = '#8c8c8c'; rx.fillRect(px, py, pw, ph)
    }
  }
  for (let q = -1; q <= 1; q++) {
    for (let y = 0; y < n; y++) piece((y + 2 * L * q) * cs, y * cs, L * cs, cs, true)
    for (let x = 0; x < n; x++) piece(x * cs, (x + 1 + 2 * L * q) * cs, cs, L * cs, false)
  }
  return { color, height: readGray(hc), rough: rc }
}

function grayToCanvas(arr: Float32Array, fn: (v: number, i: number) => [number, number, number]): HTMLCanvasElement {
  const c = makeCanvas(N)
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(N, N)
  for (let i = 0; i < arr.length; i++) {
    const [r, g, b] = fn(arr[i], i)
    img.data[i * 4] = r; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = b; img.data[i * 4 + 3] = 255
  }
  ctx.putImageData(img, 0, 0)
  return c
}

function paintConcrete(): Painted {
  const big = fbm(N, 3, 3, 4, 5)
  const mid = fbm(N, 14, 14, 3, 6)
  const fine = tileNoise(N, 256, 256, 7)
  const h = new Float32Array(N * N)
  for (let i = 0; i < h.length; i++) h[i] = big[i] * 0.4 + mid[i] * 0.35 + fine[i] * 0.25
  const color = grayToCanvas(h, (v, i) => {
    const t = 150 + (big[i] - 0.5) * 70 + (mid[i] - 0.5) * 35 + (fine[i] - 0.5) * 22 + (v - 0.5) * 10
    return [t + 4, t + 2, t - 2]
  })
  const rough = grayToCanvas(h, (v) => { const t = 170 + (v - 0.5) * 80; return [t, t, t] })
  return { color, height: h, rough }
}

function paintTiles(light: boolean, seed: number): Painted {
  const color = makeCanvas(N), hc = makeCanvas(N), rc = makeCanvas(N)
  const cx = color.getContext('2d')!, hx = hc.getContext('2d')!, rx = rc.getContext('2d')!
  const rnd = mulberry32(seed)
  const t = N / 2
  const warp = fbm(N, 3, 3, 5, seed + 1)
  const warp2 = fbm(N, 7, 7, 3, seed + 2)
  const speck = tileNoise(N, 220, 220, seed + 3)
  const base = light ? [232, 228, 218] : [58, 62, 69]
  const img = cx.createImageData(N, N)
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
    const i = y * N + x
    const tx = Math.floor(x / t), ty = Math.floor(y / t)
    const off = ((tx * 7 + ty * 13) % 5) * 0.012
    let v: number
    if (light) {
      const s = Math.abs(Math.sin(((x + y) / N) * Math.PI * 4 + warp[i] * 8 + warp2[i] * 2))
      v = 1 - Math.pow(1 - s, 10) * 0.28 - warp2[i] * 0.06 - off
    } else {
      v = 1 + (warp[i] - 0.5) * 0.35 + (warp2[i] - 0.5) * 0.18 - off * 3
    }
    v += (speck[i] - 0.5) * 0.04
    img.data[i * 4] = Math.min(255, base[0] * v)
    img.data[i * 4 + 1] = Math.min(255, base[1] * v)
    img.data[i * 4 + 2] = Math.min(255, base[2] * v)
    img.data[i * 4 + 3] = 255
  }
  cx.putImageData(img, 0, 0)
  void rnd
  const grout = Math.max(3, N * 0.0035)
  const grc = light ? '#aaa59a' : '#1d2024'
  hx.fillStyle = '#e0e0e0'; hx.fillRect(0, 0, N, N)
  rx.fillStyle = '#6a6a6a'; rx.fillRect(0, 0, N, N)
  for (const g of [0, t]) {
    cx.fillStyle = grc; cx.fillRect(g - grout / 2, 0, grout, N); cx.fillRect(0, g - grout / 2, N, grout)
    cx.fillRect(g - grout / 2 + N, 0, grout, N); cx.fillRect(0, g - grout / 2 + N, N, grout)
    hx.fillStyle = '#202020'; hx.fillRect(g - grout / 2, 0, grout, N); hx.fillRect(0, g - grout / 2, N, grout)
    hx.fillRect(g - grout / 2 + N, 0, grout, N); hx.fillRect(0, g - grout / 2 + N, N, grout)
    rx.fillStyle = '#f0f0f0'; rx.fillRect(g - grout / 2, 0, grout, N); rx.fillRect(0, g - grout / 2, N, grout)
    rx.fillRect(g - grout / 2 + N, 0, grout, N); rx.fillRect(0, g - grout / 2 + N, N, grout)
  }
  return { color, height: readGray(hc), rough: rc }
}

function paintCarpet(): Painted {
  const fine = tileNoise(N, 300, 300, 21)
  const mid = fbm(N, 20, 20, 3, 22)
  const big = fbm(N, 4, 4, 2, 23)
  const h = new Float32Array(N * N)
  for (let i = 0; i < h.length; i++) h[i] = fine[i] * 0.6 + mid[i] * 0.4
  const color = grayToCanvas(h, (v, i) => {
    const t = 1 + (v - 0.5) * 0.45 + (big[i] - 0.5) * 0.12
    return [185 * t, 177 * t, 165 * t]
  })
  const rough = grayToCanvas(h, () => [250, 250, 250])
  return { color, height: h, rough }
}

const procCache = new Map<string, FloorTex>()

export function proceduralFloor(id: string): FloorTex {
  const hit = procCache.get(id)
  if (hit) return hit
  const f = floorById(id)
  let p: Painted
  switch (f.id) {
    case 'mid_oak': p = paintPlanks({ h: 30, s: 0.42, l: 0.46 }, 15, f.tileCm, 3); break
    case 'dark_wood': p = paintPlanks({ h: 24, s: 0.3, l: 0.2 }, 15, f.tileCm, 4); break
    case 'herringbone': p = paintHerringbone({ h: 29, s: 0.38, l: 0.5 }, 5); break
    case 'concrete': p = paintConcrete(); break
    case 'tile_light': p = paintTiles(true, 11); break
    case 'tile_dark': p = paintTiles(false, 12); break
    case 'carpet': p = paintCarpet(); break
    default: p = paintPlanks({ h: 34, s: 0.45, l: 0.68 }, 15, f.tileCm, 2)
  }
  const rep = 1 / f.tileCm
  const tex: FloorTex = {
    map: toTexture(p.color, true, [rep, rep]),
    normalMap: toTexture(heightToNormal(p.height, N, f.id === 'carpet' ? 6 : f.id === 'concrete' ? 3 : 4), false, [rep, rep]),
    roughnessMap: toTexture(p.rough, false, [rep, rep]),
    tileCm: f.tileCm,
    source: 'procedural',
  }
  procCache.set(id, tex)
  return tex
}

// ---------------------------------------------------------------- real texture files (preferred when present)

const fileCache = new Map<string, Promise<FloorTex | null>>()

async function probe(url: string): Promise<boolean> {
  try {
    const r = await fetch(url, { method: 'HEAD', cache: 'no-store' })
    if (!r.ok) return false
    const ct = r.headers.get('content-type') ?? ''
    return ct.startsWith('image/')
  } catch { return false }
}

function loadTex(url: string, srgb: boolean, rep: number): Promise<THREE.Texture> {
  return new Promise((res, rej) => {
    new THREE.TextureLoader().load(url, (t) => {
      t.wrapS = t.wrapT = THREE.RepeatWrapping
      t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace
      t.anisotropy = 8
      t.repeat.set(rep, rep)
      t.needsUpdate = true
      res(t)
    }, undefined, rej)
  })
}

async function findFile(base: string, name: string): Promise<string | null> {
  for (const ext of ['jpg', 'jpeg', 'png', 'webp']) {
    const u = `${base}${name}.${ext}`
    if (await probe(u)) return u
  }
  return null
}

export function loadFloorFiles(id: string): Promise<FloorTex | null> {
  const hit = fileCache.get(id)
  if (hit) return hit
  const f = floorById(id)
  const base = `${import.meta.env.BASE_URL}textures/${f.id}/`
  const p = (async () => {
    const d = await findFile(base, 'diffuse')
    if (!d) return null
    const rep = 1 / f.tileCm
    const [n, r] = await Promise.all([findFile(base, 'normal'), findFile(base, 'roughness')])
    const [map, nm, rm] = await Promise.all([loadTex(d, true, rep), n ? loadTex(n, false, rep) : null, r ? loadTex(r, false, rep) : null])
    return { map, normalMap: nm, roughnessMap: rm, tileCm: f.tileCm, source: 'files' as const }
  })().catch(() => null)
  fileCache.set(id, p)
  return p
}

/** Procedural immediately, upgraded to real PBR files when they exist under public/textures/<preset>/. */
export function useFloorTextures(id: string): FloorTex {
  const proc = useMemo(() => proceduralFloor(id), [id])
  const [files, setFiles] = useState<{ id: string; tex: FloorTex } | null>(null)
  useEffect(() => {
    let alive = true
    loadFloorFiles(id).then((t) => { if (alive && t) setFiles({ id, tex: t }) })
    return () => { alive = false }
  }, [id])
  return files && files.id === id ? files.tex : proc
}

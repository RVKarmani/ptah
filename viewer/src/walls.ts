import type { Room, WallPaint } from './types'

export const DEFAULT_WALL = '#f7f3ec'
export const HEX = /^#[0-9a-f]{6}$/i

export interface WallChoice { key: string; label: string; hex: string; kind: 'current' | 'designed' | 'alt'; paint?: WallPaint }
export interface WallSel { sel: string; custom: string }

/** A small curated set used when the design brings no palette of its own: calm, muted, wall-friendly. */
const CURATED: { hex: string; name: string }[] = [
  { hex: '#f1ebe0', name: 'Warm white' },
  { hex: '#d9d0c1', name: 'Greige' },
  { hex: '#b9c2ab', name: 'Soft sage' },
  { hex: '#b3c0c9', name: 'Dusty blue' },
  { hex: '#d3b5a2', name: 'Clay' },
  { hex: '#a99a8a', name: 'Mushroom' },
  { hex: '#7d8791', name: 'Slate' },
]

const norm = (h: string) => h.trim().toLowerCase()

/** Current walls, the designed suggestion (when there is one) and 4-6 alternatives from the design's palette (or the curated set). */
export function wallChoices(room: Room): WallChoice[] {
  const fin = room.finishes ?? {}
  const out: WallChoice[] = []
  const rgb = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16))
  // skip swatches that would look like one we already show
  const add = (c: WallChoice) => {
    if (!HEX.test(c.hex)) return
    const a = rgb(c.hex)
    if (out.some((o) => { const b = rgb(o.hex); return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) < 14 })) return
    out.push(c)
  }
  const cur = fin.wall_color && HEX.test(fin.wall_color) ? fin.wall_color : DEFAULT_WALL
  add({ key: 'current', label: 'Your walls', hex: cur, kind: 'current' })
  const paint = fin.wall_paint
  if (room.can_paint !== false && paint?.hex && HEX.test(paint.hex)) add({ key: 'designed', label: paint.name || 'Suggested paint', hex: paint.hex, kind: 'designed', paint })
  if (room.can_paint !== false) {
    const raw = (fin.palette ?? fin.alternatives ?? []) as unknown[]
    const fromDesign = raw.flatMap((e) => {
      if (typeof e === 'string') return [{ hex: e, name: '' }]
      if (e && typeof e === 'object' && typeof (e as { hex?: unknown }).hex === 'string') { const o = e as { hex: string; name?: string }; return [{ hex: o.hex, name: o.name ?? '' }] }
      return []
    })
    const alts = fromDesign.length >= 2 ? fromDesign : CURATED
    let n = 0
    for (const a of alts) { if (n >= 6) break; const before = out.length; add({ key: norm(a.hex), label: a.name || 'Alternative', hex: a.hex, kind: 'alt' }); if (out.length > before) n++ }
  }
  return out
}

export function resolveWall(room: Room, w: WallSel): { hex: string; choice: WallChoice | null } {
  const choices = wallChoices(room)
  const hit = choices.find((c) => c.key === w.sel)
  if (hit) return { hex: hit.hex, choice: hit }
  if (room.can_paint !== false && HEX.test(w.sel)) return { hex: w.sel, choice: null }
  const cur = choices[0]
  return { hex: cur.hex, choice: cur }
}

const keyOf = (base: string) => 'ptah.wall:' + base
export function loadWall(base: string): WallSel | null {
  try {
    const raw = window.localStorage.getItem(keyOf(base))
    if (!raw) return null
    const o = JSON.parse(raw) as Partial<WallSel>
    if (typeof o.sel !== 'string') return null
    return { sel: o.sel, custom: typeof o.custom === 'string' && HEX.test(o.custom) ? o.custom : '#c9b8a4' }
  } catch { return null }
}
export function saveWall(base: string, w: WallSel) {
  try { window.localStorage.setItem(keyOf(base), JSON.stringify(w)) } catch { /* private mode / quota: the choice just isn't remembered */ }
}

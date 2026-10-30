/**
 * Surface metrics of the procedural templates.  MIRRORED in Python: ptah/layout/metrics.py (the layout planner places
 * cushions on the sofa seat and decor on shelf boards with these numbers).  Change both together.
 */
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

export interface SofaMetrics {
  legH: number
  armW: number
  backT: number
  seatTop: number
  baseTop: number
  innerW: number
  armTop: number
  /** front face (at seat level) of the reclined back cushions */
  backFaceZ: number
  seatFrontZ: number
}

export function sofaMetrics(w: number, d: number, h: number, armchair = false, arms = true, legs = 'tapered'): SofaMetrics & { arm_w: number; arm_top: number; seat_top: number } {
  const legH = legs === 'none' ? 3 : clamp(h * 0.14, 8, 16)
  const armW = arms ? clamp(w * (armchair ? 0.14 : 0.075), 11, 22) : 0
  const backT = clamp(d * (armchair ? 0.3 : 0.26), 14, 28)
  const seatTop = h * (armchair ? 0.52 : 0.5)
  const baseTop = legH + (seatTop - legH) * 0.5
  const bd = backT * 0.66
  const armTop = seatTop + (h - seatTop) * 0.32
  return {
    legH, armW, backT, seatTop, baseTop, innerW: w - 2 * armW, armTop,
    backFaceZ: -d / 2 + backT + bd * 0.85 - 3,
    seatFrontZ: d / 2 - 0.2,
    arm_w: armW, arm_top: armTop, seat_top: seatTop,
  }
}

export interface ShelfMetrics { t: number; kick: number; back: number; n: number; step: number }

export function shelfMetrics(_w: number, _d: number, h: number): ShelfMetrics {
  const t = 2.4, kick = 6, back = 0.9
  const n = Math.max(2, Math.round(h / 36))
  return { t, kick, back, n, step: (h - t - kick) / n }
}

/** base height of shelf board/compartment `k` (0 = lowest) */
export const shelfLevelY = (m: ShelfMetrics, k: number) => m.kick + m.step * k + (k === 0 ? 0 : 1)

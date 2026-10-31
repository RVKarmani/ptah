import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from 'react'
import { kelvinToRGB } from './color'
import { Slider, Switch } from './ui'

/** Colour-temperature range the dial covers (warm end on the left, cool end on the right). */
export const K_MIN = 1800
export const K_MAX = 6500
const K_STEP = 100

export function warmthLabel(k: number): { name: string; hint: string } {
  if (k < 2300) return { name: 'Candlelight', hint: 'soft amber glow' }
  if (k < 3300) return { name: 'Warm', hint: 'cosy and inviting' }
  if (k < 4600) return { name: 'Neutral', hint: 'clean and even' }
  return { name: 'Daylight', hint: 'crisp and bright' }
}

/** The very same Kelvin -> RGB the 3D lights use. */
const rgbOf = (k: number): [number, number, number] => { const [r, g, b] = kelvinToRGB(k); return [Math.round(r), Math.round(g), Math.round(b)] }
/** `rgb(...)` of a colour temperature, same function as the 3D lights */
export const rgbCss = (k: number) => { const c = rgbOf(k); return `rgb(${c[0]},${c[1]},${c[2]})` }
const clampK = (k: number) => Math.min(K_MAX, Math.max(K_MIN, k))
const tOf = (k: number) => (clampK(k) - K_MIN) / (K_MAX - K_MIN)

/* the drafted semicircle: a centre point, an outer and an inner rule, ticks every 6 degrees (long every 30) */
const W = 330, H = 146, CX = 165, CY = 130, R_OUT = 135, R_IN = 113, R_INK = 124
const pt = (t: number, r: number): [number, number] => {
  const a = Math.PI * (1 - t)
  return [CX + r * Math.cos(a), CY - r * Math.sin(a)]
}
const arcPath = (t0: number, t1: number, r: number) => {
  const [x0, y0] = pt(t0, r), [x1, y1] = pt(t1, r)
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${r} ${r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
}
/** the recessed field inside the inner rule, where the light shows */
const FIELD = `M${CX - R_IN + 1} ${CY}A${R_IN - 1} ${R_IN - 1} 0 0 1 ${CX + R_IN - 1} ${CY}Z`
const TICKS = (() => {
  const out: string[] = []
  for (let a = 6; a < 180; a += 6) {
    const t = a / 180, len = a % 30 === 0 ? 14 : 7
    const [x1, y1] = pt(t, R_OUT), [x2, y2] = pt(t, R_OUT - len)
    out.push(`M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`)
  }
  return out.join('')
})()

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Eases towards `target` (~160 ms). Snaps while dragging or when the user prefers reduced motion. */
function useEased(target: number, snap: boolean): number {
  const [v, setV] = useState(target)
  const cur = useRef(target)
  useEffect(() => {
    if (snap || reduced()) { cur.current = target; setV(target); return }
    const from = cur.current
    if (from === target) return
    const t0 = performance.now()
    let raf = 0
    const step = (now: number) => {
      const k = Math.min(1, (now - t0) / 160)
      cur.current = from + (target - from) * (1 - Math.pow(1 - k, 3))
      setV(cur.current)
      if (k < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, snap])
  return v
}

/* ---------------------------------------------------------------------------------------------
   The bulb's glow. Colour is the very Kelvin colour the 3D lights use, size and strength follow the brightness.
   While the dial is being turned (or its warmth or brightness changes by any means) the bloom swells quickly and
   then settles back over about half a second. Written straight to CSS variables, so no React renders per frame.
   With reduced motion the glow is simply the current value: no easing, no swell.
   --------------------------------------------------------------------------------------------- */
const GLOW_X = 165, GLOW_Y = 63
const clamp01 = (v: number) => Math.min(1, Math.max(0, v))
/** brightness (0..1) and on/off to the glow's strength; a dim lamp still shows a little colour */
export const glowAmount = (dim: number, on: boolean) => (on ? 0.22 + 0.78 * Math.pow(clamp01(dim), 0.8) : 0)

function useGlow(root: RefObject<HTMLDivElement | null>, halo: RefObject<SVGCircleElement | null>, k: number, amt: number, drag: boolean) {
  const S = useRef({ k, a: amt, drag, touch: 0, ck: k, ca: amt, ce: 0, last: 0, raf: 0 })
  const paint = () => {
    const s = S.current, el = root.current
    if (!el) return
    const [r, g, b] = kelvinToRGB(s.ck)
    const a = s.ca, e = s.ce
    el.style.setProperty('--glow', `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})`)
    el.style.setProperty('--glow-a0', (a * (0.62 + 0.22 * e)).toFixed(3))
    el.style.setProperty('--glow-a1', (a * (0.3 + 0.16 * e)).toFixed(3))
    el.style.setProperty('--glow-glass', a > 0.004 ? (0.4 + 0.55 * a).toFixed(3) : '0')
    halo.current?.setAttribute('r', (a > 0.004 ? 28 + 38 * a + 18 * e * a : 28).toFixed(1))
  }
  useLayoutEffect(paint, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const s = S.current
    const moved = Math.abs(s.k - k) > 0.5 || Math.abs(s.a - amt) > 0.001
    s.k = k; s.a = amt; s.drag = drag
    if (moved) s.touch = performance.now()
    if (reduced()) { s.ck = k; s.ca = amt; s.ce = 0; paint(); return }
    cancelAnimationFrame(s.raf)
    s.last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min(250, Math.max(1, now - s.last))
      s.last = now
      s.ck += (s.k - s.ck) * (1 - Math.exp(-dt / 80))
      s.ca += (s.a - s.ca) * (1 - Math.exp(-dt / 140))
      const te = s.drag || now - s.touch < 700 ? 1 : 0
      s.ce += (te - s.ce) * (1 - Math.exp(-dt / (te > s.ce ? 60 : 480)))
      paint()
      const settled = Math.abs(s.k - s.ck) < 1 && Math.abs(s.a - s.ca) < 0.002 && te === 0 && s.ce < 0.01
      if (settled) { s.ck = s.k; s.ca = s.a; s.ce = 0; paint() } else s.raf = requestAnimationFrame(tick)
    }
    s.raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(s.raf)
  }, [k, amt, drag]) // eslint-disable-line react-hooks/exhaustive-deps
}

export interface WarmthDialProps {
  /** colour temperature, 1800..6500 */
  kelvin: number
  /** dimmer 0..1 (not drawn; kept so callers can pass the whole light state) */
  dim?: number
  on: boolean
  onChange: (k: number) => void
  /** accessible name, e.g. "Ambient lights warmth" */
  label: string
  disabled?: boolean
  compact?: boolean
}

/**
 * Warmth as a drafted dial. The inked arc runs from the warm end to the chosen value, a rubric diamond marks it,
 * and the name and the Kelvin figure sit under a line drawing of a bulb. Drag the mark along the arc, or focus the dial and
 * use the arrow keys (Home / End jump to the warmest / coolest).
 */
export function WarmthDial({ kelvin, dim = 1, on, onChange, label, disabled = false, compact = false }: WarmthDialProps) {
  const uid = useId().replace(/:/g, '')
  const [drag, setDrag] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const haloRef = useRef<SVGCircleElement>(null)
  const k = useEased(clampK(kelvin), drag)
  const t = tOf(k)
  const { name, hint } = warmthLabel(clampK(kelvin))
  const [kx, ky] = pt(t, R_INK)
  const rot = -(180 * (1 - t) - 90) + 45
  const ink = useMemo(() => arcPath(0, Math.max(0.002, t), R_INK), [t])
  useGlow(rootRef, haloRef, clampK(kelvin), glowAmount(dim, on), drag)

  const fromPointer = (e: PointerEvent) => {
    const svg = svgRef.current
    if (!svg) return
    const r = svg.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * W - CX
    const y = CY - ((e.clientY - r.top) / r.height) * H
    let a = Math.atan2(y, x) // 0 = right end, PI = left end, negative = below the centre line
    if (a < 0) a = a < -Math.PI / 2 ? Math.PI : 0
    const next = K_MIN + (1 - a / Math.PI) * (K_MAX - K_MIN)
    onChange(Math.round(clampK(next) / 50) * 50)
  }
  const down = (e: PointerEvent) => {
    if (disabled) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDrag(true)
    fromPointer(e)
  }
  const move = (e: PointerEvent) => { if (drag) fromPointer(e) }
  const up = (e: PointerEvent) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    setDrag(false)
  }
  const key = (e: KeyboardEvent) => {
    if (disabled) return
    const big = e.shiftKey ? 5 : 1
    let next: number | null = null
    switch (e.key) {
      case 'ArrowRight': case 'ArrowUp': next = kelvin + K_STEP * big; break
      case 'ArrowLeft': case 'ArrowDown': next = kelvin - K_STEP * big; break
      case 'PageUp': next = kelvin + K_STEP * 5; break
      case 'PageDown': next = kelvin - K_STEP * 5; break
      case 'Home': next = K_MIN; break
      case 'End': next = K_MAX; break
    }
    if (next == null) return
    e.preventDefault()
    onChange(clampK(next))
  }

  const pct = Math.round(tOf(kelvin) * 100)
  return (
    <div
      ref={rootRef}
      className={'dial' + (compact ? ' compact' : '') + (on ? '' : ' off') + (drag ? ' dragging' : '') + (disabled ? ' disabled' : '')}
      role="slider"
      tabIndex={disabled ? -1 : 0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
      aria-valuetext={`${name}, ${hint}${on ? '' : ', light is off'}`}
      aria-disabled={disabled || undefined}
      onKeyDown={key}
    >
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} aria-hidden focusable="false">
        <defs>
          <radialGradient id={uid + 'g'}>
            <stop offset="0" className="gs0" />
            <stop offset="0.4" className="gs1" />
            <stop offset="1" className="gs2" />
          </radialGradient>
          <clipPath id={uid + 'c'}><path d={FIELD} /></clipPath>
        </defs>
        <path className="d-field" d={FIELD} />
        <g clipPath={`url(#${uid}c)`}>
          <circle ref={haloRef} className="d-halo" cx={GLOW_X} cy={GLOW_Y} r={28} fill={`url(#${uid}g)`} />
        </g>
        <g className="d-line">
          <path d={arcPath(0, 1, R_OUT)} strokeWidth={1.5} />
          <path d={arcPath(0, 1, R_IN)} strokeWidth={1.25} />
          <path d={TICKS} strokeWidth={1.25} />
        </g>
        <path className="d-ink" d={ink} />
        <g transform={`translate(${kx.toFixed(2)} ${ky.toFixed(2)}) rotate(${rot.toFixed(1)})`}>
          <rect className="d-knob" x={-6.5} y={-6.5} width={13} height={13} />
        </g>
        <g transform="translate(143 46) scale(1.833)" opacity={on ? 1 : 0.6}>
          <path className="d-glass" d="M12 2.5a6.5 6.5 0 0 0-4 11.6V17h8v-2.9A6.5 6.5 0 0 0 12 2.5z" />
          <path className="d-bulb" vectorEffect="non-scaling-stroke" strokeLinecap="square" d="M12 2.5a6.5 6.5 0 0 0-4 11.6V17h8v-2.9A6.5 6.5 0 0 0 12 2.5zM9 20h6M10 22.5h4" />
        </g>
        <text className="d-name" x={CX} y={110} textAnchor="middle">{name}</text>
        <text className="d-hint" x={CX} y={127} textAnchor="middle">{on ? hint : 'Light is off'}</text>
        <text className="d-end" x={18} y={144}>Warmer</text>
        <text className="d-end" x={312} y={144} textAnchor="end">Cooler</text>
        {/* generous hit area along the arc (finger-sized) */}
        <path
          id={uid}
          d={arcPath(0, 1, R_INK)}
          fill="none"
          stroke="transparent"
          strokeWidth={52}
          strokeLinecap="butt"
          style={{ cursor: disabled ? 'default' : drag ? 'grabbing' : 'grab', pointerEvents: disabled ? 'none' : 'stroke' }}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
        />
      </svg>
    </div>
  )
}

/** The dial plus its dimmer and (optionally) an on/off switch, in one ruled card. */
export function WarmthControl({
  title, sub, kelvin, dim, on, onKelvin, onDim, onToggle, dimmable = true, label, changed, onReset,
}: {
  title?: string
  sub?: string
  kelvin: number
  dim: number
  on: boolean
  onKelvin: (k: number) => void
  onDim: (v: number) => void
  onToggle?: (v: boolean) => void
  dimmable?: boolean
  label: string
  changed?: boolean
  onReset?: () => void
}) {
  const base = label.replace(/ warmth$/, '')
  return (
    <div className="warmth-card">
      {(title || onToggle) && (
        <div className="warmth-head">
          <div className="grow">{title && <b>{title}</b>}{sub && <small>{sub}</small>}</div>
          {onToggle && <Switch on={on} onChange={onToggle} label={`${base}: on or off`} />}
        </div>
      )}
      <WarmthDial kelvin={kelvin} dim={dim} on={on} onChange={onKelvin} label={label} compact />
      {dimmable && (
        <label className="warmth-dim">
          <span>Brightness</span>
          <Slider value={dim} min={0.05} max={1} step={0.01} disabled={!on} onChange={onDim} label={`${base} brightness`} />
          <span className="val">{Math.round(dim * 100)}%</span>
        </label>
      )}
      {changed && onReset && <button type="button" className="btn quiet sm warmth-reset" onClick={onReset}>Reset warmth</button>}
    </div>
  )
}

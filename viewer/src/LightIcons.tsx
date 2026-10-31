import type { ReactNode } from 'react'
import { kelvinToRGB } from './color'

/**
 * Lighting pictograms. The same 24 grid, 1.25 px square-capped carbon line as the rest of the icon set, plus one wash of
 * pigment inside the drawn shape (sun disc, page, night sky), like a colour pencil rubbed over an ink drawing. The line
 * carries the meaning and the contrast; the wash only tells you which part of the day you are looking at.
 */

export type TimeBand = 'dawn' | 'morning' | 'noon' | 'golden' | 'dusk' | 'night'

export const TIME_BANDS: { band: TimeBand; label: string; from: number }[] = [
  { band: 'night', label: 'Night', from: 0 },
  { band: 'dawn', label: 'Dawn', from: 5.75 },
  { band: 'morning', label: 'Morning', from: 7 },
  { band: 'noon', label: 'Midday', from: 10 },
  { band: 'golden', label: 'Golden hour', from: 16.5 },
  { band: 'dusk', label: 'Dusk', from: 19 },
  { band: 'night', label: 'Night', from: 21.25 },
]

export function timeBand(h: number): { band: TimeBand; label: string } {
  const t = ((h % 24) + 24) % 24
  let cur = TIME_BANDS[0]
  for (const b of TIME_BANDS) if (t >= b.from) cur = b
  return cur
}

/** The drawing of each band: `pa` is the main wash, `pb` the second one. */
const TIME_ART: Record<TimeBand, ReactNode> = {
  dawn: (
    <>
      <path className="pb" d="M2.5 17h19v3.5h-19z" stroke="none" />
      <path className="pa" d="M7 17a5 5 0 0 1 10 0z" />
      <path d="M2.5 17h19M9.5 6.5L12 4l2.5 2.5M4.5 11l1.8 1.8M19.5 11l-1.8 1.8" />
    </>
  ),
  morning: (
    <>
      <circle className="pa" cx="12" cy="14" r="3.6" />
      <path d="M2.5 19h19M12 5.5v2.6M5.2 8.7l1.9 1.9M18.8 8.7l-1.9 1.9M2.8 14h2.6M18.6 14h2.6" />
    </>
  ),
  noon: (
    <>
      <circle className="pa" cx="12" cy="12" r="4.6" />
      <circle className="pb" cx="12" cy="12" r="2" stroke="none" />
      <path d="M12 1.8v3M12 19.2v3M1.8 12h3M19.2 12h3M4.8 4.8l2 2M17.2 17.2l2 2M19.2 4.8l-2 2M6.8 17.2l-2 2" />
    </>
  ),
  golden: (
    <>
      <path className="pb" d="M2 17.5h20v3H2z" stroke="none" />
      <path className="pa" d="M6 17.5a6 6 0 0 1 12 0z" />
      <path d="M2 17.5h20M12 8.5v-3M4.8 8.6l2 2M19.2 8.6l-2 2" />
    </>
  ),
  dusk: (
    <>
      <path className="pb" d="M2.5 16h19v5h-19z" stroke="none" />
      <path className="pa" d="M7 16a5 5 0 0 1 10 0z" />
      <path d="M2.5 16h19M5 8h3M6.5 6.5v3M17 6h2.5M18.25 4.75v2.5" />
    </>
  ),
  night: (
    <>
      <circle className="pa" cx="12" cy="12" r="9.5" />
      <path className="moon" transform="translate(12.4 12.2) scale(0.62) translate(-11.5 -12)" d="M19 14.5A8 8 0 0 1 9.5 5 8 8 0 1 0 19 14.5z" />
      <path className="spark" d="M17.5 6.5v2.4M16.3 7.7h2.4M7.3 15.8v1.8M6.4 16.7h1.8" />
    </>
  ),
}

/** Scene icons: Day, Evening and Night borrow their hour's drawing; Reading and All off have their own. */
const SCENE_ART: Record<string, { cls: string; art: ReactNode }> = {
  Day: { cls: 'li-noon', art: TIME_ART.noon },
  Evening: { cls: 'li-dusk', art: TIME_ART.dusk },
  Night: { cls: 'li-night', art: TIME_ART.night },
  Reading: {
    cls: 'li-read',
    art: (
      <>
        <path className="pa" d="M12 6C9.5 4.5 6 4.5 3 5.5v13c3-1 6.5-1 9 .5 2.5-1.5 6-1.5 9-.5v-13c-3-1-6.5-1-9 .5z" />
        <path d="M12 6v14M12 2v1.6M7.6 2.9l.9 1.3M16.4 2.9l-.9 1.3" />
      </>
    ),
  },
  'All off': { cls: 'li-off', art: <path d="M12 3v8M6.5 6.5a8 8 0 1 0 11 0" /> },
}

function Glyph({ cls, size, children }: { cls: string; size: number; children: ReactNode }) {
  return (
    <svg className={'icon li ' + cls} width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      {children}
    </svg>
  )
}

export function TimeIcon({ band, size = 24 }: { band: TimeBand; size?: number }) {
  return <Glyph cls={'li-' + band} size={size}>{TIME_ART[band]}</Glyph>
}

export function SceneIcon({ scene, size = 24 }: { scene: string; size?: number }) {
  const s = SCENE_ART[scene] ?? SCENE_ART.Day
  return <Glyph cls={s.cls} size={size}>{s.art}</Glyph>
}

/** What a layer does, drawn: ambient fills the room, task is a beam onto the work surface, accent is a single highlight. */
export function LayerIcon({ layer, size = 28 }: { layer: 'ambient' | 'task' | 'accent'; size?: number }) {
  return (
    <Glyph cls="li-layer" size={size}>
      {layer === 'ambient' && <><circle className="pa" cx="12" cy="12" r="2.8" /><path d="M7.2 7.2a6.8 6.8 0 0 0 0 9.6M16.8 7.2a6.8 6.8 0 0 1 0 9.6M4 4a11.3 11.3 0 0 0 0 16M20 4a11.3 11.3 0 0 1 0 16" /></>}
      {layer === 'task' && <><path className="pa" d="M7 10h10l3.5 9.5h-17z" /><path d="M9.5 3h5l2 4.5h-9zM2 19.5h20" /></>}
      {layer === 'accent' && <path className="pa" d="M12 2.5l2.2 7.3 7.3 2.2-7.3 2.2L12 21.5l-2.2-7.3L2.5 12l7.3-2.2z" />}
    </Glyph>
  )
}

const rgb = (k: number) => { const [r, g, b] = kelvinToRGB(k); return `rgb(${Math.round(r)},${Math.round(g)},${Math.round(b)})` }

/**
 * A small line bulb whose glass is filled with the light's own colour and whose halo follows its brightness.
 * Unlit, it is just the drawn bulb. `color` overrides the Kelvin colour (for fixed-colour lights).
 */
export function BulbMark({ k, on, dim = 1, size = 24, color }: { k: number; on: boolean; dim?: number; size?: number; color?: string }) {
  const c = color ?? rgb(k)
  const a = on ? 0.3 + 0.7 * Math.pow(Math.min(1, Math.max(0, dim)), 0.8) : 0
  return (
    <span className={'bulb-mark' + (on ? ' lit' : '')} style={{ width: size, height: size, ['--glow' as string]: c, ['--glow-a' as string]: a.toFixed(3) }}>
      <i aria-hidden />
      <svg className="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
        <path className="glass" d="M12 2.5a6.5 6.5 0 0 0-4 11.6V17h8v-2.9A6.5 6.5 0 0 0 12 2.5z" />
        <path d="M9 20h6M10 22.5h4" />
      </svg>
    </span>
  )
}

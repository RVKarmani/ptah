import type { CSSProperties, ReactNode } from 'react'

/** Shared UI primitives: one switch, one slider, one icon set and one set of furniture pictograms, drawn in the same pen. */

export function Switch({ on, onChange, label, disabled = false }: { on: boolean; onChange: (v: boolean) => void; label: string; dark?: boolean; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={'switch' + (on ? ' on' : '')}
      onClick={() => onChange(!on)}
    >
      <span />
    </button>
  )
}

export function Slider({
  value, min, max, step, onChange, label, disabled = false, className = '', trackStyle,
}: { value: number; min: number; max: number; step: number; onChange: (v: number) => void; label: string; disabled?: boolean; dark?: boolean; className?: string; trackStyle?: CSSProperties }) {
  const p = ((value - min) / (max - min)) * 100
  return (
    <input
      type="range"
      className={'rng' + (className ? ' ' + className : '')}
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={label}
      style={{ ['--p' as string]: p + '%', ...trackStyle }}
      onChange={(e) => onChange(+e.target.value)}
    />
  )
}

/* ---------------------------------------------------------------------------------------------
   Icons: 24 grid, one 1.25 px stroke, square caps, mitred joins, engineer's lettering.
   --------------------------------------------------------------------------------------------- */
export type IconName =
  | 'sliders' | 'chevron-down' | 'chevron-up' | 'chevron-right' | 'sun' | 'moon' | 'dusk' | 'home' | 'close' | 'back' | 'external' | 'check' | 'cross'
  | 'cube' | 'plus' | 'reset' | 'arrow-down' | 'book' | 'power' | 'dividers' | 'bag' | 'alert' | 'lock' | 'more' | 'trash' | 'upload' | 'file' | 'camera'
  | 'heart' | 'search' | 'music' | 'film' | 'tv' | 'pin' | 'tag' | 'user' | 'globe' | 'play' | 'qloo' | 'brush' | 'bulb'

const PATHS: Record<IconName, ReactNode> = {
  sliders: <path d="M3 7h10M17 7h4M3 17h4M11 17h10M13 4.5h4v5h-4zM7 14.5h4v5H7z" />,
  'chevron-down': <path d="M6 9l6 6 6-6" />,
  'chevron-up': <path d="M6 15l6-6 6 6" />,
  'chevron-right': <path d="M9 6l6 6-6 6" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" /></>,
  dusk: <path d="M3 17h18M6.5 17a5.5 5.5 0 0 1 11 0M12 6v3M4.5 9l2 2M19.5 9l-2 2" />,
  moon: <path d="M19 14.5A8 8 0 0 1 9.5 5 8 8 0 1 0 19 14.5z" />,
  book: <path d="M12 6v14M12 6C9.5 4.5 6 4.5 3 5.5v13c3-1 6.5-1 9 .5 2.5-1.5 6-1.5 9-.5v-13c-3-1-6.5-1-9 .5z" />,
  power: <path d="M12 3v8M6.5 6.5a8 8 0 1 0 11 0" />,
  bulb: <path d="M12 2.5a6.5 6.5 0 0 0-4 11.6V17h8v-2.9A6.5 6.5 0 0 0 12 2.5zM9 20h6M10 22.5h4" />,
  home: <path d="M3.5 11.5L12 4l8.5 7.5M6 10v10h12V10M10 20v-6h4v6" />,
  close: <path d="M5.5 5.5l13 13M18.5 5.5l-13 13" />,
  cross: <path d="M5.5 5.5l13 13M18.5 5.5l-13 13" />,
  back: <path d="M15 5l-7 7 7 7" />,
  external: <path d="M9 5H4v15h15v-5M13 4h7v7M20 4l-9 9" />,
  check: <path d="M5 12.5l4.5 4.5L19 7" />,
  cube: <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM4 7.5l8 4.5 8-4.5M12 12v9" />,
  plus: <path d="M12 5v14M5 12h14" />,
  reset: <path d="M5 12a7 7 0 1 1 2 5M5 6v6h6" />,
  'arrow-down': <path d="M12 4v16M6 14l6 6 6-6" />,
  dividers: <><path d="M12 2.5v5M12 7.5L5.5 21.5M12 7.5l6.5 14M8 16h8" /><circle cx="12" cy="5" r="1.2" /></>,
  bag: <path d="M5 8.5h14l-1 12H6zM9 8.5V6.5a3 3 0 0 1 6 0v2M5 8.5L3.5 5" />,
  alert: <path d="M12 3.5l9.5 17h-19zM12 10v5M12 17.2v1.6" />,
  lock: <path d="M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3" />,
  more: <path d="M4 11h2v2H4zM11 11h2v2h-2zM18 11h2v2h-2z" />,
  trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />,
  upload: <path d="M12 16V4M7 9l5-5 5 5M4 20h16" />,
  file: <path d="M6 3h8l4 4v14H6zM14 3v4h4" />,
  camera: <><path d="M3 8h4l2-3h6l2 3h4v12H3z" /><circle cx="12" cy="13.5" r="3.5" /></>,
  heart: <path d="M12 20.5L4 12.5V8.5L7.5 5H10l2 2.5L14 5h2.5L20 8.5v4z" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21" /></>,
  qloo: <><circle cx="11" cy="11" r="6.5" /><path d="M15.5 15.5L21 21M11 8v6M8 11h6" /></>,
  music: <path d="M9 18V5l10-2v13M9 18H5v-3h4M19 16h-4v-3h4" />,
  film: <path d="M4 5h16v14H4zM8 5v14M16 5v14M4 9h4M4 15h4M16 9h4M16 15h4" />,
  tv: <path d="M3 5h18v13H3zM8 21h8M12 18v3" />,
  pin: <path d="M12 21L5.5 11A6.5 6.5 0 0 1 18.5 11zM10 9.5h4v3h-4z" />,
  tag: <path d="M3.5 12V3.5H12l8.5 8.5-8.5 8.5zM7.5 7.5h2v2h-2z" />,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21v-3a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v3" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3.5 3 3.5 15 0 18M12 3c-3.5 3-3.5 15 0 18" /></>,
  play: <path d="M8 5v14l11-7z" />,
  brush: <path d="M4 20l1-5L16 4l4 4L9 19zM14 6l4 4" />,
}

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden focusable="false">
      {PATHS[name]}
    </svg>
  )
}

/* ---------------------------------------------------------------------------------------------
   Pictograms: furniture in side elevation, 48 grid, 1.5 px line. Used in the schedule and as the
   "X in a box" stand-in for a product photo.
   --------------------------------------------------------------------------------------------- */
export type GlyphName =
  | 'sofa' | 'chair' | 'console' | 'tv' | 'strip' | 'table' | 'side' | 'rug' | 'lamp' | 'pendant' | 'sconce' | 'shelf' | 'plant' | 'art' | 'cushion' | 'vase'
  | 'guitar' | 'amp' | 'crate' | 'bed' | 'mirror' | 'books' | 'candle' | 'throw' | 'tray' | 'hanger' | 'plight' | 'box'

const GLYPHS: Record<GlyphName, ReactNode> = {
  sofa: <path d="M10 25V14h28v11M5 25h7v8h24v-8h7v13H5zM9 38v3M39 38v3" />,
  chair: <path d="M14 24V10h20v14M8 24h8v8h16v-8h8v14H8zM12 38v4M36 38v4" />,
  console: <path d="M6 17h36v15H6zM24 17v15M6 24.5h36M10 32l-1 8M38 32l1 8" />,
  tv: <path d="M5 11h38v25H5zM17 41h14M24 36v5" />,
  strip: <path d="M5 24h38M5 20v8M43 20v8M14 14v-3M24 13v-4M34 14v-3" />,
  table: <path d="M6 19h36l-4 7H10zM12 26v13M36 26v13M18 26v8M30 26v8" />,
  side: <path d="M11 17a13 4 0 1 0 26 0 13 4 0 1 0-26 0zM15 20l-4 20M33 20l4 20M24 21v18" />,
  rug: <path d="M5 15h38v22H5zM10 20h28v12H10z" />,
  lamp: <path d="M16 7h16l5 11H11zM24 18v22M15 41h18" />,
  pendant: <path d="M24 4v12M12 16h24l4 14H8zM20 30a4 4 0 0 0 8 0" />,
  sconce: <path d="M7 7v34M7 24h9M17 13h14l5 17H12z" />,
  shelf: <path d="M11 5h26v38H11zM11 16h26M11 27h26M11 37h26M16 8v5M20 9v4M25 18v7M29 19v6" />,
  plant: <path d="M17 32h14l-2 10H19zM24 32V17M24 25c-6 0-10-4-10-10 6 0 10 4 10 10zM24 20c5 0 9-3 9-9-5 0-9 3-9 9z" />,
  art: <path d="M8 8h32v32H8zM13 13h22v22H13zM13 31l7-8 5 5 4-4 6 7" />,
  cushion: <path d="M9 11h30l-3 13 3 13H9l3-13z" />,
  vase: <path d="M19 6h10M20 6v8c-7 4-9 8-9 14 0 7 5 12 13 12s13-5 13-12c0-6-2-10-9-14V6" />,
  guitar: <path d="M20 22l8 8-3 3-3 1-2 4-6 1-3-3 1-6 4-2 1-3zM27 29L41 11M39 9l4 4" />,
  amp: <path d="M7 10h34v28H7zM7 17h34M13 13.5h2M19 13.5h2M25 13.5h2M33 13.5h5M14 21h20v13H14z" />,
  crate: <path d="M6 20h36v20H6zM6 20l4-9h28l4 9M14 28h20" />,
  bed: <path d="M6 10v30M6 30h36v10M42 40V30M10 22h11v8H10zM25 22h12a5 5 0 0 1 5 5v3H25z" />,
  mirror: <path d="M14 5h20v38H14zM18 9h12v30H18zM21 22l6-7" />,
  books: <path d="M8 40h32M10 40V31h28v9M12 31v-9h24v9M14 22v-8h20v8M17 26h8M19 18h6" />,
  candle: <path d="M19 22h10v18H19zM24 22v-4M24 8c-3.5 3.5-3.5 7 0 10 3.5-3 3.5-6.5 0-10zM14 40h20" />,
  throw: <path d="M10 10h28v9l-6 6h-8l-6 6v7H10zM10 19h8" />,
  tray: <path d="M6 24h36v4l-4 10H10L6 28zM14 24v-5h20v5" />,
  hanger: <path d="M11 8h26v5H11zM17 13v14a7 7 0 0 0 14 0V13" />,
  plight: <path d="M9 7h30M24 7v6M16 13h16l-2 7H18zM9 25h30v17H9z" />,
  box: <path d="M7 14l17-8 17 8v20L24 42 7 34zM7 14l17 8 17-8M24 22v20" />,
}

export function Glyph({ name, className = '' }: { name: GlyphName; className?: string }) {
  return (
    <svg className={'gl ' + className} viewBox="0 0 48 48" aria-hidden focusable="false">
      {GLYPHS[name]}
    </svg>
  )
}

/** Which pictogram draws a piece of this category. */
export function glyphFor(category: string): GlyphName {
  const c = category.toLowerCase()
  if (/sofa|couch|sectional|loveseat/.test(c)) return 'sofa'
  if (/arm ?chair|chair|stool|bench|ottoman|pouf/.test(c)) return 'chair'
  if (/tv_?stand|console|sideboard|credenza|dresser|cabinet|media/.test(c)) return 'console'
  if (/^tv$|television|monitor/.test(c)) return 'tv'
  if (/led|strip/.test(c)) return 'strip'
  if (/side_?table|nightstand|end_?table/.test(c)) return 'side'
  if (/picture_?light/.test(c)) return 'plight'
  if (/pendant|chandelier/.test(c)) return 'pendant'
  if (/sconce/.test(c)) return 'sconce'
  if (/lamp|light/.test(c)) return 'lamp'
  if (/coffee|table|desk/.test(c)) return 'table'
  if (/rug|carpet/.test(c)) return 'rug'
  if (/shelf|shelv|book_?case|bookshelf|storage|rack/.test(c)) return 'shelf'
  if (/plant|tree|flower/.test(c)) return 'plant'
  if (/^art$|poster|frame|print|painting/.test(c)) return 'art'
  if (/mirror/.test(c)) return 'mirror'
  if (/cushion|pillow/.test(c)) return 'cushion'
  if (/vase/.test(c)) return 'vase'
  if (/hanger/.test(c)) return 'hanger'
  if (/guitar/.test(c)) return 'guitar'
  if (/^amp$|amplifier/.test(c)) return 'amp'
  if (/crate|vinyl|record/.test(c)) return 'crate'
  if (/bed|mattress/.test(c)) return 'bed'
  if (/book/.test(c)) return 'books'
  if (/candle/.test(c)) return 'candle'
  if (/throw|blanket/.test(c)) return 'throw'
  if (/tray/.test(c)) return 'tray'
  return 'box'
}

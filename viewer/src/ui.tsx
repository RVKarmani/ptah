import type { CSSProperties, ReactNode } from 'react'

/** Shared UI primitives: one switch, one slider, one icon set, so every control looks and behaves the same. */

export function Switch({ on, onChange, label, dark = false, disabled = false }: { on: boolean; onChange: (v: boolean) => void; label: string; dark?: boolean; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={'switch' + (on ? ' on' : '') + (dark ? ' dark' : '')}
      onClick={() => onChange(!on)}
    >
      <span />
    </button>
  )
}

export function Slider({
  value, min, max, step, onChange, label, disabled = false, dark = false, className = '', trackStyle,
}: { value: number; min: number; max: number; step: number; onChange: (v: number) => void; label: string; disabled?: boolean; dark?: boolean; className?: string; trackStyle?: CSSProperties }) {
  const p = ((value - min) / (max - min)) * 100
  return (
    <input
      type="range"
      className={'rng' + (dark ? ' dark' : '') + (className ? ' ' + className : '')}
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

export type IconName = 'sliders' | 'chevron-down' | 'chevron-up' | 'chevron-right' | 'sun' | 'moon' | 'dusk' | 'home' | 'close' | 'back' | 'external' | 'check' | 'cross' | 'cube' | 'plus' | 'reset' | 'arrow-down'

const PATHS: Record<IconName, ReactNode> = {
  sliders: <><path d="M4 7h9M17 7h3M4 17h3M11 17h9" /><circle cx="15" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></>,
  'chevron-down': <path d="m6 9 6 6 6-6" />,
  'chevron-up': <path d="m6 15 6-6 6 6" />,
  'chevron-right': <path d="m9 6 6 6-6 6" />,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" /></>,
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z" />,
  dusk: <><path d="M5 17a7 7 0 0 1 14 0M3 17h18M12 5v2M5 9l1.4 1.4M19 9l-1.4 1.4" /></>,
  home: <path d="M4 11.5 12 5l8 6.5M6 10v9h12v-9" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  back: <path d="M15 5 8 12l7 7" />,
  external: <path d="M14 5h5v5M19 5l-8 8M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  cross: <path d="M7 7l10 10M17 7 7 17" />,
  cube: <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm0 9 8-4.5M12 12v9M12 12 4 7.5" />,
  plus: <path d="M12 5v14M5 12h14" />,
  reset: <path d="M4 12a8 8 0 1 0 2.6-5.9M4 4v4.5h4.5" />,
  'arrow-down': <path d="M12 5v14M6 13l6 6 6-6" />,
}

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden focusable="false">
      {PATHS[name]}
    </svg>
  )
}

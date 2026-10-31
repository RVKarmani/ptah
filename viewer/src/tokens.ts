/**
 * Ostracon colour tokens for code that cannot read CSS (the 3D scene, the heat map).
 * Mirror of src/tokens.css: change both together.
 */
export type Theme = 'day' | 'night'

export const COLORS = {
  day: {
    stone: '#d5d3ca', paper: '#e6e4dc', chalk: '#f1efe8', well: '#ccc9be',
    carbon: '#1b1a18', graphite: '#5b5953', pencil: '#8c8a82', rubric: '#b1232b',
  },
  night: {
    stone: '#101112', paper: '#1a1b1d', chalk: '#232426', well: '#141516',
    carbon: '#dcdad1', graphite: '#a3a199', pencil: '#6f6d67', rubric: '#e5594f',
  },
} as const

/** Selection and hover outlines in the 3D view (hex numbers for three.js). The selection is rubric: it is the chosen piece. */
export const OUTLINE = {
  day: { sel: 0xb1232b, selHidden: 0x6a1a1f, hov: 0xf1efe8, hovHidden: 0x4a4943 },
  night: { sel: 0xe5594f, selHidden: 0x7d3a35, hov: 0xdcdad1, hovHidden: 0x4a4943 },
} as const

/** Floor light-level ramp: ink for dark, chalk for bright (a drawn lightmap, not a rainbow). */
export const HEAT_STOPS: [number, string][] = [
  [0, '#1b1a18'], [0.22, '#4b4944'], [0.45, '#86847c'], [0.68, '#cfcdc3'], [0.88, '#f1efe8'], [1, '#ffffff'],
]

/** Status marks on the zone pins: carbon is fine, graphite is a bit dim, rubric needs attention. */
export const STATUS = { ok: '#1b1a18', dim: '#8c8a82', high: '#b1232b', none: '#8c8a82' } as const

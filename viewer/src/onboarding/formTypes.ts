import type { Love } from './api'

export interface OwnedPiece { key: string; name: string; category: string; w: number; d: number; h: number; template?: string; note?: string }
export interface FormState {
  zip: File | null
  loves: (Love & { label?: string })[]
  /** 'sample:<id>' for a sample room, 'file' for an uploaded room.json */
  room: string
  roomFile: File | null
  owned: OwnedPiece[]
  /** null = no limit */
  budget: number | null
  canPaint: boolean
  location: string
}
export const emptyForm = (): FormState => ({ zip: null, loves: [], room: '', roomFile: null, owned: [], budget: null, canPaint: true, location: '' })

export const MAX_LOVES = 20
export const MAX_OWNED = 20
export const fmtBytes = (n: number): string => (n >= 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e8 ? 0 : 1) + ' MB' : Math.max(1, Math.round(n / 1e3)) + ' KB')
export const money = (n: number): string => '$' + n.toLocaleString('en-US')

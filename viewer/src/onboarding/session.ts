/** Remembers the last job in this browser tab so a refresh or a return visit can pick it up. Nothing personal is stored: only the opaque id. */
const KEY = 'ptah.job'
import { PAGES } from '../pages'
export function rememberJob(id: string, done: boolean): void {
  if (PAGES) return
  try { sessionStorage.setItem(KEY, JSON.stringify({ id, done })) } catch { /* private mode */ }
}
export function forgetJob(id?: string): void {
  try {
    if (id) { const cur = recallJob(); if (cur && cur.id !== id) return }
    sessionStorage.removeItem(KEY)
  } catch { /* ignore */ }
}
export function recallJob(): { id: string; done: boolean } | null {
  if (PAGES) return null
  try {
    const j = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
    return j && typeof j.id === 'string' ? { id: j.id, done: !!j.done } : null
  } catch { return null }
}

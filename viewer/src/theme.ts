import { useSyncExternalStore } from 'react'
import type { Theme } from './tokens'

const KEY = 'ptah.theme'
const EVT = 'ptah:theme'

/** Stored choice, else the system setting. (index.html sets data-theme before the first paint.) */
export function initialTheme(): Theme {
  try {
    const s = window.localStorage.getItem(KEY)
    if (s === 'day' || s === 'night') return s
  } catch { /* storage blocked */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'night' : 'day'
}

export function getTheme(): Theme {
  return document.documentElement.dataset.theme === 'night' ? 'night' : 'day'
}

export function setTheme(t: Theme): void {
  document.documentElement.dataset.theme = t
  try { window.localStorage.setItem(KEY, t) } catch { /* storage blocked */ }
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'night' ? '#101112' : '#d5d3ca')
  window.dispatchEvent(new Event(EVT))
}

const subscribe = (cb: () => void) => { window.addEventListener(EVT, cb); return () => window.removeEventListener(EVT, cb) }

/** [theme, toggle] */
export function useTheme(): [Theme, () => void] {
  const t = useSyncExternalStore(subscribe, getTheme, () => 'day' as Theme)
  return [t, () => setTheme(t === 'night' ? 'day' : 'night')]
}

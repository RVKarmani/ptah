import type { Item, Layout, Lighting, Room, Scene, Taste } from './types'

/** Resolve the data base path: ?data=<prefix> or the bundled /sample/ folder. Always ends with '/'. */
let override: string | null = null
/** The onboarding flow hands a finished job's bundle to the viewer without a page load; the viewer calls this once on mount. */
export function setDataBase(url: string | null | undefined): void { override = url && url.trim() ? url.trim() : null }

export function dataBase(): string {
  const q = override ?? new URLSearchParams(window.location.search).get('data')
  let base = q && q.trim() ? q.trim() : import.meta.env.BASE_URL + 'sample/'
  if (!base.endsWith('/')) base += '/'
  return new URL(base, document.baseURI).href
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} while loading ${url}`)
  return (await r.json()) as T
}

export async function loadScene(): Promise<Scene> {
  const baseUrl = dataBase()
  const layout = await getJson<Layout>(baseUrl + 'layout.json')
  const roomFile = typeof layout.room === 'string' && layout.room ? layout.room : 'room.json'
  const [room, items, taste, lighting] = await Promise.all([
    getJson<Room>(new URL(roomFile, baseUrl).href),
    getJson<Item[]>(baseUrl + 'items.json'),
    getJson<Taste>(baseUrl + 'taste.json').catch(() => null), // optional
    getJson<Lighting>(baseUrl + 'lighting.json').catch(() => null), // optional
  ])
  if (!room || typeof room !== 'object' || !Array.isArray(room.floor) || !Array.isArray(room.walls)) throw new Error('room.json is missing the floor outline or walls')
  // tolerate optional / empty pieces: an empty room is still a room
  const safeItems = Array.isArray(items) ? items.filter((i) => i && typeof i.id === 'string') : []
  const safeLayout: Layout = { ...layout, placements: Array.isArray(layout.placements) ? layout.placements : [], unplaced: Array.isArray(layout.unplaced) ? layout.unplaced : [] }
  return { baseUrl, room, items: safeItems, layout: safeLayout, taste: taste && typeof taste === 'object' ? taste : null, lighting: lighting && typeof lighting === 'object' ? lighting : null }
}

/** Resolve an item image / scan path relative to the data base. */
export function resolveUrl(u: string | null | undefined, baseUrl: string): string | null {
  if (!u) return null
  try { return new URL(u, baseUrl).href } catch { return null }
}

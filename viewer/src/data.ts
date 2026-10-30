import type { Item, Layout, Lighting, Room, Scene, Taste } from './types'

/** Resolve the data base path: ?data=<prefix> or the bundled /sample/ folder. Always ends with '/'. */
export function dataBase(): string {
  const q = new URLSearchParams(window.location.search).get('data')
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
  return { baseUrl, room, items, layout, taste, lighting: lighting && typeof lighting === 'object' ? lighting : null }
}

/** Resolve an item image / scan path relative to the data base. */
export function resolveUrl(u: string | null | undefined, baseUrl: string): string | null {
  if (!u) return null
  try { return new URL(u, baseUrl).href } catch { return null }
}

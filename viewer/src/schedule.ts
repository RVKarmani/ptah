import type { Item, Placement } from './types'

export type PlacedRow = { item: Item; placement: Placement; imageUrl: string | null }

/** Small styling pieces that stand on something else are grouped under it and get no balloon of their own. */
const DECOR = new Set(['cushion', 'throw', 'book_stack', 'vinyl_crate', 'vase', 'candle', 'small_plant', 'tray', 'frame'])

/** Top-level rows plus, per parent piece, the styling pieces standing on it (a candle on a tray groups under the tray's parent). */
export function groupDecor(placed: PlacedRow[]): { top: PlacedRow[]; kids: Map<string, PlacedRow[]> } {
  const byId = new Map(placed.map((r) => [r.item.id, r]))
  const isDecor = (r: PlacedRow) => DECOR.has(r.item.category) && r.placement.mount === 'on_top_of' && !!r.placement.parent && byId.has(r.placement.parent)
  const root = (r: PlacedRow): string => {
    let cur = r
    for (let i = 0; i < 6 && isDecor(cur); i++) cur = byId.get(cur.placement.parent!)!
    return cur.item.id
  }
  const top: PlacedRow[] = []
  const kids = new Map<string, PlacedRow[]>()
  for (const r of placed) {
    if (isDecor(r)) {
      const k = root(r)
      kids.set(k, [...(kids.get(k) ?? []), r])
    } else top.push(r)
  }
  return { top, kids }
}

/** Row numbers of the schedule: 1, 2, 3 for pieces; 3.1, 3.2 for styling pieces standing on piece 3. The room's balloons use the same numbers. */
export function numbering(g: { top: PlacedRow[]; kids: Map<string, PlacedRow[]> }): Map<string, string> {
  const out = new Map<string, string>()
  g.top.forEach((r, i) => {
    out.set(r.item.id, String(i + 1))
    ;(g.kids.get(r.item.id) ?? []).forEach((k, j) => out.set(k.item.id, `${i + 1}.${j + 1}`))
  })
  return out
}

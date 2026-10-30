import { useState } from 'react'
import type { Item, Placement, Scene } from './types'
import { toInches } from './geometry'
import { categoryColor } from './palette'
import { LightRow, LightingPanel, type LightingPanelProps } from './LightingUI'
import { RoomPanel, type RoomPanelProps } from './RoomPanel'

export type Tab = 'items' | 'lighting' | 'room'

const nice = (s: string) => s.replace(/_/g, ' ')

function Thumb({ item, src }: { item: Item; src: string | null }) {
  const [bad, setBad] = useState(false)
  if (src && !bad) {
    return <img className="thumb" src={src} alt="" referrerPolicy="no-referrer" onError={() => setBad(true)} />
  }
  return <span className="thumb swatch" style={{ background: categoryColor(item.category, item.id) }} />
}

function parsePrice(p?: string | null): { sym: string; val: number } | null {
  if (!p) return null
  const m = p.replace(/,/g, '').match(/^\s*([^\d.\s]*)\s*([\d.]+)/)
  if (!m) return null
  const val = parseFloat(m[2])
  return Number.isFinite(val) ? { sym: m[1], val } : null
}

/** Total of the purchasable items; owned items ("Yours") never count. */
function total(all: Item[]): string | null {
  const items = all.filter((i) => !i.owned)
  const parsed = items.map((i) => parsePrice(i.price))
  if (!parsed.length || parsed.some((p) => !p)) return null
  const sym = parsed[0]!.sym
  if (parsed.some((p) => p!.sym !== sym)) return null
  return sym + Math.round(parsed.reduce((s, p) => s + p!.val, 0)).toLocaleString()
}

const DECOR = new Set(['cushion', 'throw', 'book_stack', 'vinyl_crate', 'vase', 'candle', 'small_plant', 'tray', 'frame'])
type PlacedRow = { item: Item; placement: Placement; imageUrl: string | null }

/** Top-level rows plus, per parent piece, the styling pieces standing on it (candle on a tray groups under the tray's parent). */
function groupDecor(placed: PlacedRow[]): { top: PlacedRow[]; kids: Map<string, PlacedRow[]> } {
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

function ItemRow({ r, hoverId, onSelect, onHover, sub = false }: { r: PlacedRow; hoverId: string | null; onSelect: (id: string | null) => void; onHover: (id: string | null) => void; sub?: boolean }) {
  const { item, imageUrl } = r
  return (
    <button
      className={(hoverId === item.id ? 'row hot' : 'row') + (sub ? ' sub' : '')}
      onClick={() => onSelect(item.id)}
      onMouseEnter={() => onHover(item.id)}
      onMouseLeave={() => onHover(null)}
    >
      <Thumb item={item} src={imageUrl} />
      <span className="grow">
        <b>{item.name}</b>
        <small>{nice(item.category)}{item.dims_estimated && <span className="chip est tiny">est.</span>}</small>
      </span>
      {item.owned ? <span className="badge yours sm">Yours</span> : item.price && <span className="price sm">{item.price}</span>}
    </button>
  )
}

function DecorGroup({ kids, hoverId, selectedId, onSelect, onHover }: { kids: PlacedRow[]; hoverId: string | null; selectedId: string | null; onSelect: (id: string | null) => void; onHover: (id: string | null) => void }) {
  const [open, setOpen] = useState(false)
  const active = kids.some((k) => k.item.id === selectedId)
  const show = open || active
  return (
    <div className="decor-group">
      <button className="decor-toggle" aria-expanded={show} onClick={() => setOpen(!show)}>
        <span className="caret">{show ? '▾' : '▸'}</span> {kids.length} styling piece{kids.length === 1 ? '' : 's'}
      </button>
      {show && (
        <ul className="list sublist">
          {kids.map((k) => <li key={k.item.id}><ItemRow r={k} sub hoverId={hoverId} onSelect={onSelect} onHover={onHover} /></li>)}
        </ul>
      )}
    </div>
  )
}

function Constraint({ c, ok, names }: { c: unknown; ok: boolean; names: Map<string, string> }) {
  let text: string
  if (typeof c === 'string') text = c
  else if (c && typeof c === 'object') {
    const o = c as { type?: string; target?: string }
    text = `${o.type ?? ''}${o.target ? ':' + o.target : ''}`
  } else text = String(c)
  const [type, target] = text.split(/[:\s]/, 2)
  const board = type === 'surface' && target?.match(/^shelf#(\d+)$/)
  const label = board ? `Shelf board ${Number(board[1]) + 1}` : nice(type) + (target ? ` ${names.get(target) ?? target}` : '')
  return <li className={ok ? 'ok' : 'bad'}>{label}</li>
}

interface Props {
  scene: Scene
  itemsById: Map<string, Item>
  placed: { item: Item; placement: Placement; imageUrl: string | null }[]
  selectedId: string | null
  hoverId: string | null
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  tab: Tab
  setTab: (t: Tab) => void
  lighting: LightingPanelProps
  roomProps: RoomPanelProps
}

export default function Panel({ scene, itemsById, placed, selectedId, hoverId, onSelect, onHover, tab, setTab, lighting, roomProps }: Props) {
  const names = new Map([...itemsById].map(([id, it]) => [id, it.name]))
  const sel = placed.find((p) => p.item.id === selectedId)

  if (sel) {
    const { item, placement, imageUrl } = sel
    const sat = placement.satisfied ?? []
    const vio = placement.violated ?? []
    return (
      <aside className="panel">
        <button className="back" onClick={() => onSelect(null)}>← All items</button>
        <div className="hero">
          <Thumb item={item} src={imageUrl} />
        </div>
        <div className="title-row">
          <h2>{item.name}</h2>
          {item.owned ? <span className="badge yours">Yours</span> : item.price && <span className="price">{item.price}</span>}
        </div>
        <div className="chips">
          {item.owned && <span className="chip yours" title="You already own this; it is part of the design but not part of the cost">owned</span>}
          <span className="chip">{nice(item.category)}</span>
          {item.dims_estimated && <span className="chip est" title="Dimensions were estimated, not read from the product page">est.</span>}
        </div>
        <dl className="dims">
          <dt>Size</dt>
          <dd>
            {item.w} × {item.d} × {item.h} cm
            <small>{toInches(item.w)} × {toInches(item.d)} × {toInches(item.h)} in (W × D × H)</small>
          </dd>
        </dl>
        {lighting.specs.find((s) => s.id === item.id) && (
          <section>
            <h3>Light</h3>
            <ul className="list lights">
              {lighting.specs.filter((s) => s.id === item.id).map((s) => (
                <LightRow key={s.id} spec={s} st={lighting.states[s.id] ?? { on: true, dim: 1, k: s.kelvin }} onChange={(pp) => lighting.setLight(s.id, pp)} onHover={() => {}} hot={false} />
              ))}
            </ul>
          </section>
        )}
        {item.why && (
          <section>
            <h3>Why this fits you</h3>
            <p>{item.why}</p>
          </section>
        )}
        {(sat.length > 0 || vio.length > 0) && (
          <section>
            <h3>Placement</h3>
            <ul className="cons">
              {sat.map((c, i) => <Constraint key={'s' + i} c={c} ok names={names} />)}
              {vio.map((c, i) => <Constraint key={'v' + i} c={c} ok={false} names={names} />)}
            </ul>
          </section>
        )}
        {!item.owned && item.url && (
          <a className="buy" href={item.url} target="_blank" rel="noreferrer noopener">
            Buy{item.price ? ` · ${item.price}` : ''} ↗
          </a>
        )}
      </aside>
    )
  }

  const unplaced = scene.layout.unplaced ?? []
  const sum = total(placed.map((p) => p.item))
  const grouped = groupDecor(placed)
  const nOwned = placed.filter((p) => p.item.owned).length
  const tabs: [Tab, string][] = [['items', 'Items'], ['lighting', 'Lighting'], ['room', 'Room']]
  const tabBar = (
    <div className="tabs" role="tablist">
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'tab on' : 'tab'} onClick={() => setTab(id)}>{label}</button>
      ))}
    </div>
  )
  if (tab === 'lighting') return <aside className="panel">{tabBar}<LightingPanel {...lighting} /></aside>
  if (tab === 'room') return <aside className="panel">{tabBar}<RoomPanel {...roomProps} /></aside>
  return (
    <aside className="panel">
      {tabBar}
      <div className="title-row">
        <h2>Your room</h2>
        {sum && <span className="price" title="Total of placed items">{sum}</span>}
      </div>
      <p className="muted">
        {placed.length} piece{placed.length === 1 ? '' : 's'} placed{nOwned > 0 ? ` (${nOwned} yours, not in the total)` : ''}. Click an item in the room, or here, for details.
      </p>
      <ul className="list">
        {grouped.top.map((r) => (
          <li key={r.item.id}>
            <ItemRow r={r} hoverId={hoverId} onSelect={onSelect} onHover={onHover} />
            {grouped.kids.has(r.item.id) && (
              <DecorGroup kids={grouped.kids.get(r.item.id)!} hoverId={hoverId} selectedId={selectedId} onSelect={onSelect} onHover={onHover} />
            )}
          </li>
        ))}
      </ul>
      {unplaced.length > 0 && (
        <section>
          <h3>Couldn't place ({unplaced.length})</h3>
          <ul className="list">
            {unplaced.map((u) => {
              const it = itemsById.get(u.item_id)
              return (
                <li key={u.item_id} className="un">
                  <b>{it?.name ?? u.item_id}</b>
                  <small>
                    {u.reason ?? 'no room found'}
                    {u.max_w != null && u.max_d != null && ` — space for up to ${u.max_w} × ${u.max_d} cm`}
                  </small>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </aside>
  )
}

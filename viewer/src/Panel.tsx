import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Item, Placement, Scene } from './types'
import { toInches } from './geometry'
import { categoryColor } from './palette'
import { LightRow, LightingPanel, type LightingPanelProps } from './LightingUI'
import { RoomPanel, type RoomPanelProps } from './RoomPanel'
import ItemHero from './Thumb3D'
import { Icon } from './ui'
import { placementLabel } from './placement'

export type Tab = 'items' | 'lighting' | 'room'

const nice = (s: string) => s.replace(/_/g, ' ')

function Thumb({ item, src }: { item: Item; src: string | null }) {
  const [bad, setBad] = useState(false)
  if (src && !bad) {
    return <img className="thumb" src={src} alt="" referrerPolicy="no-referrer" onError={() => setBad(true)} />
  }
  return <span className="thumb swatch" style={{ background: categoryColor(item.category, item.id) }} aria-hidden />
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
      className={'row' + (hoverId === item.id ? ' hot' : '') + (sub ? ' sub' : '')}
      onClick={() => onSelect(item.id)}
      onMouseEnter={() => onHover(item.id)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(item.id)}
      onBlur={() => onHover(null)}
    >
      <Thumb item={item} src={imageUrl} />
      <span className="grow">
        <span className="name">{item.name}</span>
        <span className="sub-text">{nice(item.category)}{item.dims_estimated && !sub && <span className="badge est" title="Size estimated, not read from a product page">est. size</span>}</span>
      </span>
      {item.owned ? <span className="badge yours">Yours</span> : item.price && <span className="price">{item.price}</span>}
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
        <Icon name={show ? 'chevron-down' : 'chevron-right'} size={14} /> {kids.length} styling piece{kids.length === 1 ? '' : 's'}
      </button>
      {show && (
        <ul className="list sublist">
          {kids.map((k) => <li key={k.item.id}><ItemRow r={k} sub hoverId={hoverId} onSelect={onSelect} onHover={onHover} /></li>)}
        </ul>
      )}
    </div>
  )
}

/** "Why this fits you": a short chain (A + B -> C -> this piece) from `why_chain`; muted sample placeholder when there is no data. */
function WhyBlock({ item }: { item: Item }) {
  const chain = item.why_chain
  const hasChain = !!chain && ((chain.signals?.length ?? 0) > 0 || !!chain.reading)
  const text = (item.why ?? '').trim()
  const realText = text && !/^sample item\.?$/i.test(text) ? text : ''
  if (hasChain) {
    return (
      <section className="section why" aria-label="Why this fits you">
        <h3>Why this fits you</h3>
        <div className="chain">
          {(chain!.signals?.length ?? 0) > 0 && (
            <div className="chain-step chain-signals">
              {chain!.signals!.map((s, i) => (
                <span key={i} style={{ display: 'contents' }}>
                  {i > 0 && <span className="plus" aria-hidden>+</span>}
                  <span className="signal">{s}</span>
                </span>
              ))}
            </div>
          )}
          {chain!.reading && <><div className="chain-link"><Icon name="arrow-down" size={16} /></div><div className="chain-step chain-reading">{chain!.reading}</div></>}
          <div className="chain-link"><Icon name="arrow-down" size={16} /></div>
          <div className="chain-step chain-piece"><Icon name="check" size={16} /> {item.name}</div>
        </div>
        {realText && <p>{realText}</p>}
      </section>
    )
  }
  if (realText) {
    return (
      <section className="section why" aria-label="Why this fits you">
        <h3>Why this fits you</h3>
        <p>{realText}</p>
      </section>
    )
  }
  return (
    <section className="section why" aria-label="Why this fits you (sample placeholder)">
      <div className="why-head"><h3>Why this fits you</h3><span className="badge sample">Sample</span></div>
      <div className="chain sample" aria-hidden>
        <div className="chain-step chain-signals"><span className="signal">A taste signal</span><span className="plus">+</span><span className="signal">Another signal</span></div>
        <div className="chain-link"><Icon name="arrow-down" size={16} /></div>
        <div className="chain-step chain-reading">What Ptah reads from them</div>
        <div className="chain-link"><Icon name="arrow-down" size={16} /></div>
        <div className="chain-step chain-piece"><Icon name="check" size={16} /> This piece</div>
      </div>
      <p className="why-note">Placeholder only. Once Ptah analyses your taste, the real reasoning for this piece appears here.</p>
    </section>
  )
}

interface Props {
  scene: Scene
  itemsById: Map<string, Item>
  placed: PlacedRow[]
  selectedId: string | null
  hoverId: string | null
  onSelect: (id: string | null) => void
  onHover: (id: string | null) => void
  tab: Tab
  setTab: (t: Tab) => void
  lighting: LightingPanelProps
  roomProps: RoomPanelProps
  sheetOpen: boolean
  setSheetOpen: (v: boolean) => void
}

export default function Panel({ scene, itemsById, placed, selectedId, hoverId, onSelect, onHover, tab, setTab, lighting, roomProps, sheetOpen, setSheetOpen }: Props) {
  const sel = placed.find((p) => p.item.id === selectedId)
  const scroller = useRef<HTMLDivElement>(null)
  const backRef = useRef<HTMLButtonElement>(null)

  // new view -> scroll to top; keyboard users who picked a row land on the Back button
  useEffect(() => {
    scroller.current?.scrollTo({ top: 0 })
    if (selectedId && scroller.current?.contains(document.activeElement)) backRef.current?.focus()
  }, [selectedId, tab])

  const tabs: [Tab, string][] = [['items', 'Items'], ['lighting', 'Lighting'], ['room', 'Room']]
  const tabBar = (
    <div className="panel-head">
      <div className="tabs" role="tablist" aria-label="Panel sections">
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} className="tab" onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
    </div>
  )
  const grabber = (
    <button className="grabber" aria-expanded={sheetOpen} aria-label={sheetOpen ? 'Collapse panel' : 'Expand panel'} onClick={() => setSheetOpen(!sheetOpen)} />
  )
  const shell = (body: ReactNode) => (
    <aside className={'panel' + (sheetOpen ? ' open' : '')} aria-label="Room details">
      {grabber}
      <div className="panel-scroll" ref={scroller}>{body}</div>
    </aside>
  )

  if (sel) {
    const { item, placement, imageUrl } = sel
    const ctx = { items: itemsById, room: scene.room, hasOnTop: [...(placement.satisfied ?? []), ...(placement.violated ?? [])].some((c) => String(c).startsWith('on_top_of')) }
    const label = (c: unknown) => placementLabel(c, ctx)
    const uniq = (xs: (string | null)[]) => [...new Set(xs.filter((x): x is string => !!x))]
    const met = uniq((placement.satisfied ?? []).map(label))
    const unmet = uniq((placement.violated ?? []).map(label))
    const mySpecs = lighting.specs.filter((s) => s.id === item.id)
    return shell(
      <>
        <div className="detail-head" style={{ paddingTop: 16 }}>
          <button className="btn quiet back" ref={backRef} onClick={() => onSelect(null)}><Icon name="back" size={16} /> All items</button>
          <ItemHero item={item} imageUrl={imageUrl} />
          <div className="title-row top">
            <h2>{item.name}</h2>
            {item.owned ? <span className="badge yours">Yours</span> : item.price && <span className="price">{item.price}</span>}
          </div>
          <div className="chips">
            <span className="chip">{nice(item.category)}</span>
            {item.owned && <span className="chip" title="You already own this. It is part of the design but not part of the cost.">Not in the total</span>}
            {item.dims_estimated && <span className="badge est" title="Size estimated, not read from a product page">Estimated size</span>}
          </div>
        </div>
        <dl className="kv">
          <dt>Size</dt>
          <dd>
            {item.w} × {item.d} × {item.h} cm
            <small>{toInches(item.w)} × {toInches(item.d)} × {toInches(item.h)} in (W × D × H)</small>
          </dd>
        </dl>
        <WhyBlock item={item} />
        {(met.length > 0 || unmet.length > 0) && (
          <section className="section">
            <h3>Placement</h3>
            <ul className="place-list">
              {met.map((t) => <li key={'m' + t}><span className="ok"><Icon name="check" size={16} /></span>{t}</li>)}
              {unmet.map((t) => <li key={'u' + t} className="bad"><Icon name="cross" size={16} />Not met: {t}</li>)}
            </ul>
          </section>
        )}
        {mySpecs.length > 0 && (
          <section className="section">
            <h3>Light</h3>
            <ul className="list">
              {mySpecs.map((s) => (
                <LightRow key={s.id} spec={s} st={lighting.states[s.id] ?? { on: true, dim: 1, k: s.kelvin }} onChange={(pp) => lighting.setLight(s.id, pp)} />
              ))}
            </ul>
          </section>
        )}
        {!item.owned && item.url && (
          <div className="buy-bar">
            <a className="btn primary block" href={item.url} target="_blank" rel="noreferrer noopener">
              Buy{item.price ? ` · ${item.price}` : ''} <Icon name="external" size={16} />
            </a>
          </div>
        )}
      </>,
    )
  }

  if (tab === 'lighting') return shell(<>{tabBar}<LightingPanel {...lighting} /></>)
  if (tab === 'room') return shell(<>{tabBar}<RoomPanel {...roomProps} /></>)

  const unplaced = scene.layout.unplaced ?? []
  const sum = total(placed.map((p) => p.item))
  const grouped = groupDecor(placed)
  const nOwned = placed.filter((p) => p.item.owned).length
  const taste = scene.taste
  return shell(
    <>
      {tabBar}
      {taste?.style_summary && <p className="style-blurb">{taste.style_summary}</p>}
      <div className="section">
        <div className="title-row">
          <h2>Your room</h2>
          {sum && <span className="price" title="Total of the pieces to buy">{sum}</span>}
        </div>
        <p className="intro">
          {placed.length} pieces{nOwned > 0 ? `, ${nOwned} already yours` : ''}. Click a piece in the room or below for details.
        </p>
      </div>
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
        <section className="section">
          <h3>Didn't fit ({unplaced.length})</h3>
          <ul className="list">
            {unplaced.map((u) => {
              const it = itemsById.get(u.item_id)
              return (
                <li key={u.item_id} className="un">
                  <b>{it?.name ?? u.item_id}</b>
                  <small>
                    {u.reason ?? 'No room found for it'}
                    {u.max_w != null && u.max_d != null && u.max_w > 0 && ` · space for up to ${u.max_w} × ${u.max_d} cm`}
                  </small>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </>,
  )
}

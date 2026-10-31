import { useEffect, useRef, useState, type PointerEvent as RPointerEvent, type ReactNode } from 'react'
import type { Item, Scene } from './types'
import { toInches } from './geometry'
import { LightRow, LightingPanel, type LightingPanelProps } from './LightingUI'
import { RoomPanel, type RoomPanelProps } from './RoomPanel'
import ItemHero from './Thumb3D'
import { Glyph, Icon, glyphFor } from './ui'
import { useSnapshot } from './snapshot'
import { describeItem } from './describe'
import { placementLabel } from './placement'
import type { PlacedRow } from './schedule'
import { NearMisses, useChoiceProps } from './NearMisses'

export type Tab = 'items' | 'lighting' | 'room'

/** Row preview: the real product photo, else a cached 3D snapshot of the piece, else its pictogram while that is made. */
function Thumb({ item, src }: { item: Item; src: string | null }) {
  const [bad, setBad] = useState(false)
  const photo = src && !bad ? src : null
  const snap = useSnapshot(item, !!photo)
  return (
    <span className="thumb" aria-hidden>
      {photo ? <img src={photo} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBad(true)} />
        : snap ? <img className="snap" src={snap} alt="" width={40} height={40} />
        : <Glyph name={glyphFor(item.category)} />}
    </span>
  )
}

function parsePrice(p?: string | null): { sym: string; val: number } | null {
  if (!p) return null
  const m = p.replace(/,/g, '').match(/^\s*([^\d.\s]*)\s*([\d.]+)/)
  if (!m) return null
  const val = parseFloat(m[2])
  return Number.isFinite(val) ? { sym: m[1], val } : null
}

/** The price to show for a piece: the retailer's listing wins over the estimate. */
const priceOf = (i: Item): string | null => (i.owned ? null : i.product?.price || i.price || null)

/** Total of the purchasable items; owned items ("Yours") and concepts without a price never count. */
function total(all: Item[]): string | null {
  const items = all.filter((i) => !i.owned && !(i.designed && !priceOf(i)))
  const parsed = items.map((i) => parsePrice(priceOf(i)))
  if (!parsed.length || parsed.some((p) => !p)) return null
  const sym = parsed[0]!.sym
  if (parsed.some((p) => p!.sym !== sym)) return null
  return sym + Math.round(parsed.reduce((s, p) => s + p!.val, 0)).toLocaleString()
}

const safeHref = (u?: string | null): string | null => {
  if (!u) return null
  try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null } catch { return null }
}
const hostOf = (u: string): string | null => { try { return new URL(u).hostname.replace(/^www\./, '') } catch { return null } }

/** Fields the scout adds to an item / product (kept local: types.ts belongs to another stream). */
type LinkedItem = Item & { shop_similar?: string | null }
const isGoogle = (u: string | null): boolean => { const h = u ? hostOf(u) : null; return !!h && /(^|\.)google\.[a-z.]+$/.test(h) }
/** The store page of a real product. A Google Shopping page is never the merchant, so it does not count. */
const merchantHref = (i: Item): string | null => {
  if (i.owned) return null
  const u = safeHref(i.product?.url ?? i.url)
  return u && !isGoogle(u) ? u : null
}
/** Google Shopping page of a product whose store link is unknown. */
const googleHref = (i: Item): string | null => {
  const g = safeHref((i.product as { google_url?: string | null } | null | undefined)?.google_url) ?? safeHref(i.url)
  return g && isGoogle(g) ? g : null
}
/** Search link that finds pieces like a designed one (designed, not owned, not yet a real product). */
const similarHref = (i: Item): string | null => (i.owned ? null : safeHref((i as LinkedItem).shop_similar))

function ItemRow({ r, num, selected, hoverId, onSelect, onHover, sub = false, detailId }: {
  r: PlacedRow; num: string; selected: boolean; hoverId: string | null; onSelect: (id: string | null) => void; onHover: (id: string | null) => void; sub?: boolean; detailId: string
}) {
  const { item, imageUrl } = r
  const price = priceOf(item)
  const buy = merchantHref(item)
  const href = buy ?? similarHref(item)
  const store = item.product?.store?.trim()
  const label = buy ? `Buy ${item.name}${store ? ` at ${store}` : ''} (opens in a new tab)` : `Shop for pieces like ${item.name} (opens in a new tab)`
  return (
    <div className="row-wrap" data-id={item.id}>
      <button
        className={'row' + (hoverId === item.id ? ' hot' : '') + (selected ? ' sel' : '') + (sub ? ' sub' : '')}
        aria-expanded={selected}
        aria-controls={selected ? detailId : undefined}
        onClick={() => onSelect(selected ? null : item.id)}
        onMouseEnter={() => onHover(item.id)}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(item.id)}
        onBlur={() => onHover(null)}
      >
        <span className="n" aria-label={`Number ${num}`}>{num}</span>
        <Thumb item={item} src={imageUrl} />
        <span className="grow">
          <span className="name">{item.name}</span>
          <span className="sub-text" title={describeItem(item)}>{describeItem(item)}</span>
        </span>
        <span className="end">
          {item.owned ? <span className="badge yours">Yours</span> : price ? <span className="price">{price}</span> : item.designed ? <span className="badge designed">Designed</span> : null}
        </span>
      </button>
      {/* a link only when there is one; the gutter keeps the price column aligned */}
      <span className="lnk-gutter">
        {href && (
          <a className="lnk" href={href} target="_blank" rel="noopener noreferrer" aria-label={label} title={buy ? `Buy${store ? ` at ${store}` : ''}` : 'Shop similar'}>
            <Icon name="external" size={16} />
          </a>
        )}
      </span>
    </div>
  )
}

function DecorGroup({ kids, numbers, hoverId, selectedId, onSelect, onHover, children }: {
  kids: PlacedRow[]; numbers: Map<string, string>; hoverId: string | null; selectedId: string | null; onSelect: (id: string | null) => void; onHover: (id: string | null) => void
  children: (r: PlacedRow) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const active = kids.some((k) => k.item.id === selectedId)
  const show = open || active
  return (
    <div className="decor-group">
      <button className="decor-toggle" aria-expanded={show} onClick={() => setOpen(!show)}>
        <Icon name={show ? 'chevron-down' : 'chevron-right'} size={14} /> {kids.length} styling piece{kids.length === 1 ? '' : 's'}
      </button>
      {show && (
        <ul className="sublist">
          {kids.map((k) => (
            <li key={k.item.id} className={selectedId === k.item.id ? 'sel-li' : undefined}>
              <ItemRow r={k} num={numbers.get(k.item.id) ?? ''} sub selected={selectedId === k.item.id} hoverId={hoverId} onSelect={onSelect} onHover={onHover} detailId={'detail-' + k.item.id} />
              {selectedId === k.item.id && children(k)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** "Why this fits you": a drawn annotation. Your signals (A + B), what Ptah reads from them, the piece it chose. Falls back to plain text, then to a dashed sample. */
function WhyBlock({ item }: { item: Item }) {
  const chain = item.why_chain
  const signals = (chain?.signals ?? []).map((s) => String(s).trim()).filter(Boolean)
  const reading = (chain?.reading ?? '').trim()
  const hasChain = signals.length > 0 || !!reading
  const text = (item.why ?? '').trim()
  const realText = text && !/^sample item\.?$/i.test(text) ? text : ''
  const chainView = (sample: boolean, sig: string[], read: string, name: string) => (
    <ol className={'chain' + (sample ? ' sample' : '')} aria-hidden={sample || undefined}>
      {sig.length > 0 && (
        <li className="cn cn-signals">
          <span className="cn-label">You love</span>
          <ul>{sig.map((s, i) => <li key={i}>{s}</li>)}</ul>
        </li>
      )}
      {read && (
        <li className="cn cn-reading">
          <span className="cn-label">Ptah reads it as</span>
          <p>{read}</p>
        </li>
      )}
      <li className="cn cn-piece">
        <span className="cn-label">Ptah chose</span>
        <b>{name}</b>
      </li>
    </ol>
  )
  if (hasChain) {
    return (
      <section className="d-sec why" aria-label="Why this fits you">
        <h3>Why this fits you</h3>
        {chainView(false, signals, reading, item.name)}
        {realText && <p className="txt">{realText}</p>}
      </section>
    )
  }
  if (realText) {
    return (
      <section className="d-sec why" aria-label="Why this fits you">
        <h3>Why this fits you</h3>
        <p className="txt">{realText}</p>
      </section>
    )
  }
  return (
    <section className="d-sec why" aria-label="Why this fits you (sample placeholder)">
      <div className="why-head"><h3>Why this fits you</h3><span className="badge sample">Sample</span></div>
      {chainView(true, ['A taste signal', 'Another signal'], 'What Ptah reads from them', 'This piece')}
      <p className="why-note">Placeholder only. Once Ptah analyses your taste, the real reasoning for this piece appears here.</p>
    </section>
  )
}

/** The product card: where to buy (the merchant's own page), or a brief to shop for something similar. */
function BuyBlock({ item, hasProduct }: { item: Item; hasProduct: boolean }) {
  const prod = item.product ?? null
  const href = merchantHref(item)
  const gHref = href ? null : googleHref(item)
  const sim = similarHref(item)
  const price = priceOf(item)
  const store = prod?.store?.trim() || (href ? hostOf(href) : null)
  return (
    <>
      {(store || price) && (
        <div className="buy">
          <span className="store">{hasProduct && store ? <><Icon name="bag" size={15} /> {store}</> : null}</span>
          {price && <strong className="num">{price}</strong>}
        </div>
      )}
      {href ? (
        <a className="btn primary block" href={href} target="_blank" rel="noopener noreferrer">
          Buy{store ? ` at ${store}` : ''}<span className="sr"> (opens in a new tab)</span>
        </a>
      ) : gHref ? (
        <>
          <a className="btn block" href={gHref} target="_blank" rel="noopener noreferrer">View on Google Shopping<span className="sr"> (opens in a new tab)</span></a>
          <p className="muted soft">We couldn't find the store's own page for this one.</p>
        </>
      ) : sim ? (
        <a className="btn block" href={sim} target="_blank" rel="noopener noreferrer">Shop similar<span className="sr"> (opens in a new tab)</span></a>
      ) : hasProduct ? (
        <p className="muted soft">No store link for this one yet.</p>
      ) : null}
    </>
  )
}

interface Props {
  scene: Scene
  itemsById: Map<string, Item>
  placed: PlacedRow[]
  grouped: { top: PlacedRow[]; kids: Map<string, PlacedRow[]> }
  numbers: Map<string, string>
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

function PanelView({ scene, itemsById, placed, grouped, numbers, selectedId, hoverId, onSelect, onHover, tab, setTab, lighting, roomProps, sheetOpen, setSheetOpen }: Props) {
  const scroller = useRef<HTMLDivElement>(null)

  // a new tab starts at the top; a chosen piece scrolls its row to the top of the schedule
  useEffect(() => { scroller.current?.scrollTo({ top: 0 }) }, [tab])
  useEffect(() => {
    const sc = scroller.current
    if (!sc || !selectedId || tab !== 'items') return
    const raf = requestAnimationFrame(() => {
      const row = sc.querySelector<HTMLElement>(`[data-id="${CSS.escape(selectedId)}"]`)
      if (!row) return
      const top = row.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
      sc.scrollTo({ top: Math.max(0, top - 4), behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    })
    return () => cancelAnimationFrame(raf)
  }, [selectedId, tab])

  const tabs: [Tab, string][] = [['items', 'Items'], ['lighting', 'Lighting'], ['room', 'Room']]
  const tabBar = (
    <div className="tabs" role="tablist" aria-label="Panel sections">
      {tabs.map(([id, label]) => (
        <button key={id} role="tab" id={'tab-' + id} aria-selected={tab === id} aria-controls="panel-view" tabIndex={tab === id ? 0 : -1} className="tab" onClick={() => setTab(id)}
          onKeyDown={(e) => {
            const i = tabs.findIndex(([t]) => t === tab)
            const next = e.key === 'ArrowRight' ? tabs[(i + 1) % tabs.length] : e.key === 'ArrowLeft' ? tabs[(i + tabs.length - 1) % tabs.length] : null
            if (next) { e.preventDefault(); setTab(next[0]); requestAnimationFrame(() => document.getElementById('tab-' + next[0])?.focus()) }
          }}>{label}</button>
      ))}
    </div>
  )

  // mobile bottom sheet: tap the handle to toggle, or drag it up / down
  const drag = useRef<{ y: number; moved: boolean } | null>(null)
  const gDown = (e: RPointerEvent) => { drag.current = { y: e.clientY, moved: false }; e.currentTarget.setPointerCapture(e.pointerId) }
  const gMove = (e: RPointerEvent) => { const d = drag.current; if (d && Math.abs(e.clientY - d.y) > 8) d.moved = true }
  const gUp = (e: RPointerEvent) => {
    const d = drag.current
    drag.current = null
    if (!d) return
    const dy = e.clientY - d.y
    if (!d.moved) setSheetOpen(!sheetOpen)
    else if (dy < -24) setSheetOpen(true)
    else if (dy > 24) setSheetOpen(false)
  }
  const grabber = (
    <button
      className="grabber"
      aria-expanded={sheetOpen}
      aria-label={sheetOpen ? 'Collapse panel' : 'Expand panel'}
      onPointerDown={gDown}
      onPointerMove={gMove}
      onPointerUp={gUp}
      onPointerCancel={() => { drag.current = null }}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheetOpen(!sheetOpen) } }}
    />
  )
  const shell = (key: string, fixed: ReactNode, body: ReactNode, flush = false) => (
    <aside className={'panel' + (sheetOpen ? ' open' : '')} aria-label="Room details">
      {grabber}
      <div className="panel-fixed">{tabBar}{fixed}</div>
      <div className="panel-scroll" ref={scroller} role="tabpanel" id="panel-view" aria-labelledby={'tab-' + tab}>
        <div className={'view' + (flush ? ' flush' : '')} key={key}>{body}</div>
      </div>
    </aside>
  )

  if (tab === 'lighting') return shell('lighting', null, <LightingPanel {...lighting} />)
  if (tab === 'room') return shell('room', null, <RoomPanel {...roomProps} />)

  /** The product card for the chosen piece, opened in the schedule under its row. */
  const detail = (r: PlacedRow) => {
    const { item, placement, imageUrl } = r
    const ctx = { items: itemsById, room: scene.room, hasOnTop: [...(placement.satisfied ?? []), ...(placement.violated ?? [])].some((c) => String(c).startsWith('on_top_of')) }
    const label = (c: unknown) => placementLabel(c, ctx)
    const uniq = (xs: (string | null)[]) => [...new Set(xs.filter((x): x is string => !!x))]
    const met = uniq((placement.satisfied ?? []).map(label))
    const unmet = uniq((placement.violated ?? []).map(label))
    const mySpecs = lighting.specs.filter((s) => s.id === item.id)
    const hasProduct = !item.owned && (!!merchantHref(item) || !!googleHref(item) || !!item.product?.title || !!item.product?.store)
    const ptitle = item.product?.title?.trim() || null
    return (
      <div className="detail" id={'detail-' + item.id} role="region" aria-label={`Details: ${item.name}`}>
        <div className="top">
          <ItemHero key={item.id} item={item} imageUrl={imageUrl} />
          <div>
            <h3>{hasProduct && ptitle ? ptitle : item.name}</h3>
            <div className="d">
              {item.w} × {item.d} × {item.h} cm
              <br />{toInches(item.w)} × {toInches(item.d)} × {toInches(item.h)} in, W × D × H
            </div>
            <BuyBlock item={item} hasProduct={hasProduct} />
          </div>
        </div>
        <div className="chips">
          <span className="chip">{item.category.replace(/_/g, ' ')}</span>
          {item.owned && <span className="badge yours"><Icon name="check" size={12} /> Yours</span>}
          {item.designed && <span className="badge designed"><Icon name="dividers" size={12} /> Designed for you</span>}
          {item.dims_estimated && <span className="badge est" title="Size estimated, not read from a product page">Estimated size</span>}
          <button className="btn quiet sm close" onClick={() => onSelect(null)}>Close</button>
        </div>
        {item.owned && <p className="note-line"><Icon name="check" size={16} /> Already yours. It's part of the design but not part of the cost.</p>}
        {item.designed && !hasProduct && !item.owned && <p className="note-line"><Icon name="dividers" size={16} /> A custom piece shaped around your taste. It isn't sold as-is, so use it as a brief when you shop.</p>}
        <NearMisses item={item} baseUrl={scene.baseUrl} />
        <WhyBlock item={item} />
        {(met.length > 0 || unmet.length > 0) && (
          <section className="d-sec">
            <h3>Placement</h3>
            <ul className="place-list">
              {met.map((t) => <li key={'m' + t}><Icon name="check" size={16} />{t}</li>)}
              {unmet.map((t) => <li key={'u' + t} className="bad"><Icon name="cross" size={16} />Not met: {t}</li>)}
            </ul>
          </section>
        )}
        {mySpecs.length > 0 && (
          <section className="d-sec">
            <h3>Light</h3>
            <ul style={{ listStyle: 'none', margin: 0 }}>
              {mySpecs.map((s) => (
                <LightRow key={s.id} warmth spec={s} st={lighting.states[s.id] ?? { on: true, dim: 1, k: s.kelvin }} onChange={(pp) => lighting.setLight(s.id, pp)} />
              ))}
            </ul>
          </section>
        )}
      </div>
    )
  }

  const unplaced = scene.layout.unplaced ?? []
  const sum = total(placed.map((p) => p.item))
  const nOwned = placed.filter((p) => p.item.owned).length
  const nDesigned = placed.filter((p) => p.item.designed).length
  const taste = scene.taste
  const fixed = (
    <>
      <div className="sh">
        <div>
          <h2>Schedule of furnishings</h2>
          {placed.length > 0 && (
            <small>
              {placed.length} piece{placed.length === 1 ? '' : 's'}{nOwned > 0 ? `, ${nOwned} already yours` : ''}{nDesigned > 0 ? `, ${nDesigned} designed for you` : ''}
            </small>
          )}
        </div>
        {sum && <span className="total num" title="Total of the pieces to buy">{sum}</span>}
      </div>
      {taste?.style_summary && <p className="style-blurb">{taste.style_summary}</p>}
    </>
  )
  return shell('items', fixed,
    <>
      {placed.length === 0 ? (
        <div className="empty">
          <b>Nothing in the room yet</b>
          <span>Once Ptah has designed your space, every piece appears here with its price and why it fits you.</span>
        </div>
      ) : (
        <ul className="sched">
          {grouped.top.map((r) => {
            const sel = selectedId === r.item.id
            return (
              <li key={r.item.id} className={sel ? 'sel-li' : undefined}>
                <ItemRow r={r} num={numbers.get(r.item.id) ?? ''} selected={sel} hoverId={hoverId} onSelect={onSelect} onHover={onHover} detailId={'detail-' + r.item.id} />
                {sel && detail(r)}
                {grouped.kids.has(r.item.id) && (
                  <DecorGroup kids={grouped.kids.get(r.item.id)!} numbers={numbers} hoverId={hoverId} selectedId={selectedId} onSelect={onSelect} onHover={onHover}>{detail}</DecorGroup>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {unplaced.length > 0 && (
        <section className="section" style={{ padding: '24px 0 0' }}>
          <h3 className="sec-h" style={{ margin: '0 22px' }}><span>Didn't fit</span><span className="num">{unplaced.length}</span></h3>
          <ul style={{ listStyle: 'none', margin: 0 }}>
            {unplaced.map((u) => {
              const it = itemsById.get(u.item_id)
              return (
                <li key={u.item_id} className="un">
                  <b>{it?.name ?? u.item_id}</b>
                  <small>
                    {u.reason ?? 'No room found for it'}
                    {u.max_w != null && u.max_d != null && u.max_w > 0 && `. Space for up to ${u.max_w} × ${u.max_d} cm`}
                  </small>
                </li>
              )
            })}
          </ul>
        </section>
      )}
      <div className="sched-foot" />
    </>,
    true,
  )
}

/** The panel with the user's picks from "Real options" applied to the rows and the cards. */
export default function Panel(props: Props) {
  return <PanelView {...useChoiceProps(props)} />
}

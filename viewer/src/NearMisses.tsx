import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { Item, Scene } from './types'
import type { PlacedRow } from './schedule'
import { resolveUrl } from './data'
import { Icon } from './ui'
import './css/nearmisses.css'

/** A real product the scout found for this piece (items.json `alternatives`, see docs/contracts.md). */
export interface Alt {
  key: string
  title: string
  store?: string | null
  price?: string | null
  /** the merchant's own page, only when SerpApi gave one */
  url?: string | null
  /** the Google Shopping page of the listing */
  google_url?: string | null
  /** photo, relative to the data path */
  image?: string | null
  colors?: { primary?: string | null; secondary?: string | null; accent?: string | null } | null
  score?: number
  passes?: boolean
  /** plain-language reasons it is not the designed piece ("cotton, not linen") */
  misses?: string[]
  /** wall art only: the outer size of this print */
  w?: number
  h?: number
  product_id?: string | null
}
/** What a swap leaves on the item so the card can say what was chosen and offer the way back. */
interface Choice { key: string; was: { name: string; designed: boolean } }
export type OptionItem = Item & { alternatives?: Alt[]; choice?: Choice }

// ---------------------------------------------------------------------------------------------------------------- the user's picks, per data path

type Picks = Record<string, string>   // item id -> alternative key
const storeKey = (base: string) => 'ptah.choices.v1:' + base
const snapshots = new Map<string, Picks>()   // a stable object per data path (useSyncExternalStore compares by identity)
const listeners = new Set<() => void>()
const EMPTY: Picks = {}

function readPicks(base: string): Picks {
  try {
    const raw = window.localStorage.getItem(storeKey(base))
    const v = raw ? JSON.parse(raw) : {}
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
    return Object.fromEntries(Object.entries(v).filter(([k, x]) => typeof k === 'string' && typeof x === 'string')) as Picks
  } catch { return {} }    // private mode, blocked storage or a damaged value: nothing is remembered, nothing breaks
}
function picksOf(base: string): Picks {
  if (!base) return EMPTY
  let s = snapshots.get(base)
  if (!s) { s = readPicks(base); snapshots.set(base, s) }
  return s
}
function setPick(base: string, itemId: string, key: string | null): void {
  const next = { ...picksOf(base) }
  if (key) next[itemId] = key
  else delete next[itemId]
  snapshots.set(base, next)
  try { window.localStorage.setItem(storeKey(base), JSON.stringify(next)) } catch { /* not remembered across visits; still applied now */ }
  listeners.forEach((l) => l())
}
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

/** The user's picks for this data path, and the way to change them. */
export function useChoices(baseUrl: string | undefined): { picks: Picks; choose: (itemId: string, key: string | null) => void } {
  const base = baseUrl ?? ''
  const picks = useSyncExternalStore(subscribe, () => picksOf(base), () => EMPTY)
  return { picks, choose: (itemId, key) => setPick(base, itemId, key) }
}

// ---------------------------------------------------------------------------------------------------------------- swapping a piece in client state

const safeHref = (u?: string | null): string | null => {
  if (!u) return null
  try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null } catch { return null }
}
const isHex = (c?: string | null): c is string => !!c && /^#[0-9a-f]{6}$/i.test(c)

/** The item as it is once the user picked `alt`: product, photo, price, link and main colour change; size (except for wall art of the same frame shape),
 *  material, model and layout stay. */
export function applyChoice(item: OptionItem, alt: Alt): OptionItem {
  const prev = item.choice?.was ?? { name: item.name, designed: !!item.designed }
  const colors = { ...(item.colors ?? {}) }
  if (isHex(alt.colors?.primary)) colors.primary = alt.colors!.primary
  const diff = (alt.misses ?? []).length ? ` It differs from the design: ${(alt.misses ?? []).join('; ')}.` : ''
  return {
    ...item,
    name: alt.title,
    image: alt.image ?? null,
    price: alt.price ?? null,
    url: safeHref(alt.url) ?? safeHref(alt.google_url) ?? null,
    designed: false,
    colors,
    ...(alt.w && alt.h ? { w: alt.w, h: alt.h } : {}),
    why: `Your pick, instead of Ptah's first choice.${diff}`,
    product: {
      ...(item.product ?? {}),
      title: alt.title, store: alt.store ?? null, price: alt.price ?? null, url: safeHref(alt.url), google_url: safeHref(alt.google_url),
      product_id: alt.product_id ?? null, image_file: alt.image ?? null, source: alt.store ?? null,
    } as Item['product'],
    choice: { key: alt.key, was: prev },
  }
}

/** items with the user's picks applied (a pick whose option is no longer in the data is ignored). */
export function applyPicks<T extends Item>(items: T[], picks: Picks): (T | OptionItem)[] {
  if (!items.length || !Object.keys(picks).length) return items
  return items.map((it) => {
    const k = picks[it.id]
    const alt = k ? (it as OptionItem).alternatives?.find((a) => a.key === k) : undefined
    return alt ? applyChoice(it as OptionItem, alt) : it
  })
}

/** For App: the scene's items with the user's picks applied, so the room itself follows the card (`const items = useChoiceItems(scene?.items, scene?.baseUrl)`). */
export function useChoiceItems<T extends Item>(items: T[] | undefined, baseUrl: string | undefined): (T | OptionItem)[] | undefined {
  const { picks } = useChoices(baseUrl)
  return useMemo(() => (items ? applyPicks(items, picks) : items), [items, picks])
}

interface ChoiceProps {
  scene: Scene
  itemsById: Map<string, Item>
  placed: PlacedRow[]
  grouped: { top: PlacedRow[]; kids: Map<string, PlacedRow[]> }
}
/** For the panel: the same props with the picks applied to the rows and the details (App passes the unswapped scene). */
export function useChoiceProps<P extends ChoiceProps>(p: P): P {
  const { picks } = useChoices(p.scene.baseUrl)
  return useMemo(() => {
    if (!Object.keys(picks).length) return p
    const swap = (r: PlacedRow): PlacedRow => {
      const [it] = applyPicks([r.item], picks)
      return it === r.item ? r : { ...r, item: it, imageUrl: resolveUrl(it.image, p.scene.baseUrl) }
    }
    const placed = p.placed.map(swap)
    const byId = new Map(placed.map((r) => [r.item.id, r]))
    const grouped = { top: p.grouped.top.map((r) => byId.get(r.item.id) ?? r), kids: new Map([...p.grouped.kids].map(([k, v]) => [k, v.map((r) => byId.get(r.item.id) ?? r)])) }
    return { ...p, placed, grouped, itemsById: new Map([...p.itemsById].map(([id, it]) => [id, byId.get(id)?.item ?? it])) }
  }, [p, picks])
}

// ---------------------------------------------------------------------------------------------------------------- the card section

function OptionPhoto({ alt, baseUrl }: { alt: Alt; baseUrl: string }) {
  const [bad, setBad] = useState(false)
  const src = alt.image ? resolveUrl(alt.image, baseUrl) : null
  return (
    <span className="nm-ph" aria-hidden>
      {src && !bad ? <img src={src} alt="" loading="lazy" onError={() => setBad(true)} /> : <Icon name="bag" size={18} />}
    </span>
  )
}

/** "Real options": the closest real products for this piece, what each misses in plain words, and a way to put one in the room. */
export function NearMisses({ item, baseUrl }: { item: Item; baseUrl: string }) {
  const it = item as OptionItem
  const alts = (it.alternatives ?? []).filter((a) => a && typeof a.key === 'string' && typeof a.title === 'string')
  const { choose } = useChoices(baseUrl)
  const [said, setSaid] = useState('')
  useEffect(() => { setSaid('') }, [item.id])
  if (!alts.length) return null
  const chosen = it.choice?.key ?? null
  const asked = it.designed && !chosen   // nothing passed the bar for a designed piece
  const pick = (a: Alt | null) => {
    choose(item.id, a ? a.key : null)
    setSaid(a ? `${a.title} is now in the room.` : 'Back to Ptah’s first choice.')
  }
  return (
    <section className="d-sec nm" aria-label="Real options">
      <h3 className="nm-h"><span>Real options</span><span className="num">{alts.length}</span></h3>
      <p className="nm-lead">
        {chosen ? 'You picked one of these for the room.'
          : asked ? 'No real product matched this piece closely enough, so these are the nearest ones. Each says what it misses.'
          : 'Other real pieces for the same spot. Each says how it differs.'}
      </p>
      <ul className="nm-list">
        {alts.map((a) => {
          const on = chosen === a.key
          const buy = safeHref(a.url)
          const href = buy ?? safeHref(a.google_url)
          const misses = (a.misses ?? []).filter((m) => typeof m === 'string' && m.trim())
          return (
            <li key={a.key} className={'nm-row' + (on ? ' on' : '')}>
              <OptionPhoto alt={a} baseUrl={baseUrl} />
              <div className="nm-body">
                <div className="nm-top">
                  <b className="nm-title">{a.title}</b>
                  {a.price && <span className="num nm-price">{a.price}</span>}
                </div>
                <div className="nm-store">{a.store ?? 'Store not listed'}{on ? ' · In your room' : ''}</div>
                <p className={'nm-miss' + (misses.length ? '' : ' ok')}>
                  {misses.length > 0 ? <><span className="nm-k">Misses</span> {misses.join('; ')}.</>
                    : <><Icon name="check" size={13} /> <span className="nm-k">Matches the design</span></>}
                </p>
                <div className="nm-act">
                  {on ? <button type="button" className="btn sm" onClick={() => pick(null)}>Back to Ptah&rsquo;s choice</button>
                    : <button type="button" className="btn sm primary" onClick={() => pick(a)} aria-label={`Use ${a.title} in the room`}>Use this</button>}
                  {href && (
                    <a className="nm-link" href={href} target="_blank" rel="noopener noreferrer">
                      {buy ? `View at ${a.store ?? 'the store'}` : 'View on Google Shopping'}<span className="sr"> (opens in a new tab)</span><Icon name="external" size={13} />
                    </a>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
      <p className="sr" role="status" aria-live="polite">{said}</p>
    </section>
  )
}

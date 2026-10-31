import { useId, useState } from 'react'
import { Icon } from '../ui'
import { Slider, Switch } from '../ui'
import type { SampleRoom } from './api'
import { MAX_OWNED, money, type FormState, type OwnedPiece } from './formTypes'

/* ------------------------------------------------------------------------------------------ room */
const meters = (cm: number) => (cm / 100).toFixed(1)

function Plan({ w, d }: { w: number; d: number }) {
  const s = 64 / Math.max(w, d)
  return (
    <svg className="plan" viewBox="0 0 72 72" aria-hidden>
      <rect className="o" x={4 + (64 - w * s) / 2} y={4 + (64 - d * s) / 2} width={w * s} height={d * s} />
      <rect className="i" x={7 + (64 - w * s) / 2} y={7 + (64 - d * s) / 2} width={Math.max(2, w * s - 6)} height={Math.max(2, d * s - 6)} />
    </svg>
  )
}

/** Does this look like a Ptah room.json? (the server validates again; this just gives instant feedback) */
async function checkRoomFile(f: File): Promise<string | null> {
  if (f.size > 2 * 1024 * 1024) return 'That file is too large to be a room description.'
  try {
    const j = JSON.parse(await f.text())
    if (!Array.isArray(j?.floor) || j.floor.length < 3 || !Array.isArray(j?.walls) || !j.walls.length) return 'That does not look like a Ptah room: it needs a floor outline and walls.'
    return null
  } catch { return 'That file is not valid JSON.' }
}

export function RoomCard({ rooms, roomsError, form, set, error }: { rooms: SampleRoom[] | null; roomsError: boolean; form: FormState; set: (p: Partial<FormState>) => void; error: string | null }) {
  const [fileErr, setFileErr] = useState<string | null>(null)
  const name = useId()
  const pick = async (f: File | undefined) => {
    if (!f) return
    const bad = await checkRoomFile(f)
    setFileErr(bad)
    if (!bad) set({ room: 'file', roomFile: f })
  }
  return (
    <section className="ob-card" aria-labelledby="rm-h">
      <div className="ob-card-head">
        <span className="pn-ref" aria-hidden>C</span>
        <div><h2 id="rm-h">Your room</h2><p className="muted">Ptah furnishes and lights this space.</p></div>
      </div>
      <div className="room-grid" role="radiogroup" aria-label="Room">
        {rooms === null && !roomsError && [0, 1].map((i) => <div key={i} className="room-opt skel" aria-hidden />)}
        {(rooms ?? []).map((r) => (
          <label key={r.id} className={'room-opt' + (form.room === 'sample:' + r.id ? ' on' : '')}>
            <input type="radio" name={name} checked={form.room === 'sample:' + r.id} onChange={() => set({ room: 'sample:' + r.id, roomFile: null })} />
            <Plan w={r.width_cm} d={r.depth_cm} />
            <span className="grow"><b>{r.name}</b><small>{meters(r.width_cm)} × {meters(r.depth_cm)} m, sample room</small></span>
            <span className="tick" aria-hidden><Icon name="check" size={14} /></span>
          </label>
        ))}
        <label className={'room-opt' + (form.room === 'file' ? ' on' : '')}>
          <input type="file" accept=".json,application/json" className="sr-file" aria-label="Upload your own room.json" onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = '' }} />
          <span className="plan up" aria-hidden><Icon name="upload" size={20} /></span>
          <span className="grow">
            <b>{form.room === 'file' && form.roomFile ? form.roomFile.name : 'Use my own room.json'}</b>
            <small>{form.room === 'file' ? 'Your room file, choose another' : 'Upload a room description'}</small>
          </span>
          <span className="tick" aria-hidden><Icon name="check" size={14} /></span>
        </label>
        <div className="room-opt disabled" aria-disabled="true">
          <span className="plan up" aria-hidden><Icon name="cube" size={20} /></span>
          <span className="grow"><b>Scan my room</b><small>Coming soon</small></span>
          <span className="badge sample">Soon</span>
        </div>
      </div>
      {roomsError && <p className="muted soft">The list of sample rooms could not be loaded.</p>}
      {(fileErr || error) && <p className="field-err show" role="alert"><Icon name="alert" size={14} /> {fileErr ?? error}</p>}
    </section>
  )
}

/* ------------------------------------------------------------------------------------------ owned */
const TV_SIZES = [32, 43, 50, 55, 65, 75, 85]
const tvDims = (inch: number) => ({ w: Math.round(inch * 2.54 * 0.8716), h: Math.round(inch * 2.54 * 0.4903), d: 7 })
const CATS = ['sofa', 'armchair', 'table', 'desk', 'shelf', 'bed', 'lamp', 'rug', 'other']
let uid = 0
const nextKey = () => `o${++uid}`

export function OwnedCard({ owned, onChange }: { owned: OwnedPiece[]; onChange: (o: OwnedPiece[]) => void }) {
  const [adding, setAdding] = useState<null | 'tv' | 'custom'>(null)
  const [tv, setTv] = useState(55)
  const [c, setC] = useState({ name: '', cat: 'sofa', w: '', d: '', h: '' })
  const full = owned.length >= MAX_OWNED
  const add = (p: Omit<OwnedPiece, 'key'>) => { if (!full) onChange([...owned, { ...p, key: nextKey() }]) }
  const cnum = (v: string) => { const n = parseFloat(v); return Number.isFinite(n) && n > 0 ? Math.min(500, n) : undefined }
  const customOk = c.name.trim().length > 0
  const id = useId()

  return (
    <section className="ob-card" aria-labelledby="ow-h">
      <div className="ob-card-head">
        <span className="pn-ref" aria-hidden>D</span>
        <div><h2 id="ow-h">What you already own</h2><p className="muted">Ptah keeps these and designs around them. Optional.</p></div>
      </div>
      <div className="quick" role="group" aria-label="Quick add">
        <button type="button" className="qchip" disabled={full} aria-expanded={adding === 'tv'} onClick={() => setAdding(adding === 'tv' ? null : 'tv')}><Icon name="tv" size={14} /> TV</button>
        <button type="button" className="qchip" disabled={full} onClick={() => add({ name: 'Guitar', category: 'guitar', template: 'guitar', w: 33, d: 6, h: 98 })}><Icon name="music" size={14} /> Guitar</button>
        <button type="button" className="qchip" disabled={full} onClick={() => add({ name: 'Amp', category: 'amp', template: 'amp', w: 38, d: 22, h: 34 })}><Icon name="music" size={14} /> Amp</button>
        <button type="button" className="qchip" disabled={full} aria-expanded={adding === 'custom'} onClick={() => setAdding(adding === 'custom' ? null : 'custom')}><Icon name="plus" size={14} /> Something else</button>
      </div>

      {adding === 'tv' && (
        <div className="inline-form" role="group" aria-label="Add a TV">
          <label htmlFor={id + 'tv'}>Screen size</label>
          <select id={id + 'tv'} value={tv} onChange={(e) => setTv(+e.target.value)}>{TV_SIZES.map((s) => <option key={s} value={s}>{s} inch</option>)}</select>
          <button type="button" className="btn primary sm" onClick={() => { const dm = tvDims(tv); add({ name: `TV (${tv} inch)`, category: 'tv', template: 'tv', ...dm }); setAdding(null) }}>Add TV</button>
        </div>
      )}
      {adding === 'custom' && (
        <form className="inline-form wrap" aria-label="Add a piece" onSubmit={(e) => { e.preventDefault(); if (!customOk) return; add({ name: c.name.trim().slice(0, 60), category: c.cat, w: cnum(c.w) ?? 100, d: cnum(c.d) ?? 50, h: cnum(c.h) ?? 80, note: cnum(c.w) ? undefined : 'size guessed' }); setC({ name: '', cat: 'sofa', w: '', d: '', h: '' }); setAdding(null) }}>
          <div className="f2"><label htmlFor={id + 'n'}>Name</label><input id={id + 'n'} type="text" maxLength={60} placeholder="Green velvet sofa" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></div>
          <div><label htmlFor={id + 'c'}>Kind</label><select id={id + 'c'} value={c.cat} onChange={(e) => setC({ ...c, cat: e.target.value })}>{CATS.map((k) => <option key={k}>{k}</option>)}</select></div>
          {(['w', 'd', 'h'] as const).map((k) => (
            <div key={k} className="num"><label htmlFor={id + k}>{k === 'w' ? 'Width' : k === 'd' ? 'Depth' : 'Height'} <small>cm</small></label><input id={id + k} type="number" inputMode="numeric" min={5} max={500} placeholder="auto" value={c[k]} onChange={(e) => setC({ ...c, [k]: e.target.value })} /></div>
          ))}
          <button type="submit" className="btn primary sm" disabled={!customOk}>Add piece</button>
        </form>
      )}
      {owned.length > 0 && (
        <ul className="love-chips" aria-label="Things you own">
          {owned.map((o) => (
            <li key={o.key} className="love-chip">
              <span>{o.name}{o.note && !o.name.includes(o.note) ? ` · ${o.note}` : ''}</span>
              <button type="button" aria-label={`Remove ${o.name}`} onClick={() => onChange(owned.filter((x) => x.key !== o.key))}><Icon name="close" size={14} /></button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* ------------------------------------------------------------------------------------------ preferences */
export const BUDGETS: (number | null)[] = [500, 1000, 1500, 2000, 3000, 4000, 5000, 7500, 10000, null]

export function PrefsCard({ form, set }: { form: FormState; set: (p: Partial<FormState>) => void }) {
  const idx = Math.max(0, BUDGETS.indexOf(form.budget))
  const loc = useId()
  return (
    <section className="ob-card" aria-labelledby="pf-h">
      <div className="ob-card-head">
        <span className="pn-ref" aria-hidden>E</span>
        <div><h2 id="pf-h">Preferences</h2><p className="muted">Keeps the shopping list realistic.</p></div>
      </div>
      <div className="pref">
        <div className="pref-row"><span id="bud-l">Budget</span><b className="pref-val" aria-live="polite">{form.budget === null ? 'No limit' : `Up to ${money(form.budget)}`}</b></div>
        <Slider value={idx} min={0} max={BUDGETS.length - 1} step={1} label="Budget" onChange={(i) => set({ budget: BUDGETS[i] })} />
        <div className="pref-ends"><span>{money(500)}</span><span>No limit</span></div>
      </div>
      <div className="toggle-row">
        <span id="paint-l">I can paint the walls<small>Lets Ptah suggest a wall colour.</small></span>
        <Switch on={form.canPaint} onChange={(v) => set({ canPaint: v })} label="I can paint the walls" />
      </div>
      <div className="field">
        <label htmlFor={loc}>City <small>optional</small></label>
        <input id={loc} type="text" maxLength={60} autoComplete="address-level2" placeholder="For nearby shops and inspiration" value={form.location} onChange={(e) => set({ location: e.target.value })} />
      </div>
    </section>
  )
}

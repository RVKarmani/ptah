import { useEffect, useId, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import { Icon, type IconName } from '../ui'
import { ApiError, searchQloo, type Health, type QlooHit } from './api'
import { fmtBytes, MAX_LOVES, type FormState } from './formTypes'
import { PAGES } from '../pages'

const TYPE_ICON: Record<string, IconName> = {
  artist: 'music', movie: 'film', tv_show: 'tv', book: 'book', brand: 'tag', place: 'pin', destination: 'globe', designer: 'brush',
  podcast: 'music', video_game: 'play', actor: 'user', author: 'user', director: 'user', person: 'user',
}
const typeLabel = (t?: string) => (t ? t.replace(/^urn:entity:/, '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : 'Added by you')
const typeIcon = (t?: string): IconName => TYPE_ICON[(t ?? '').replace(/^urn:entity:/, '')] ?? 'heart'

/* ------------------------------------------------------------------------------------------ Instagram */
export function InstagramCard({ file, onFile, health, error }: { file: File | null; onFile: (f: File | null) => void; health: Health | null; error: string | null }) {
  const [drag, setDrag] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const maxMb = health?.max_upload_mb ?? 300

  const accept = (f: File | undefined | null) => {
    if (!f) return
    setProblem(null)
    if (!/\.zip$/i.test(f.name) && !/zip/.test(f.type)) return setProblem('That is not a .zip file. Upload the ZIP you downloaded from Instagram.')
    if (f.size === 0) return setProblem('That file is empty.')
    if (f.size > maxMb * 1024 * 1024) return setProblem(`That file is ${fmtBytes(f.size)}; this server accepts up to ${maxMb} MB. Request a smaller export (a shorter date range or only saved and liked posts).`)
    onFile(f)
  }
  const onDrop = (e: DragEvent) => { e.preventDefault(); setDrag(false); accept(e.dataTransfer.files?.[0]) }
  const msg = problem ?? error

  return (
    <section className="ob-card" aria-labelledby="ig-h">
      <div className="ob-card-head">
        <span className="pn-ref" aria-hidden>A</span>
        <div><h2 id="ig-h">Instagram</h2><p className="muted">Your saved posts say the most about your taste.</p></div>
      </div>

      {!file ? (
        <label
          className={'dropzone' + (drag ? ' drag' : '') + (msg ? ' bad' : '')}
          onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
        >
          <input ref={input} type="file" accept=".zip,application/zip,application/x-zip-compressed" className="sr-file" aria-describedby="ig-help ig-err" onChange={(e) => { accept(e.target.files?.[0]); e.target.value = '' }} />
          <Icon name="upload" size={22} />
          <b>Drop your Instagram ZIP here</b>
          <span className="muted">or <u>choose a file</u></span>
        </label>
      ) : (
        <div className={'file-pill' + (error ? ' bad' : '')}>
          <span className="ob-ico ok" aria-hidden><Icon name="file" size={18} /></span>
          <div className="grow"><b title={file.name}>{file.name}</b><span className="muted">{fmtBytes(file.size)}{error ? ', could not be used' : ', ready'}</span></div>
          <button type="button" className="btn quiet sm" onClick={() => { onFile(null); setProblem(null); window.setTimeout(() => input.current?.focus(), 0) }} aria-label={`Remove ${file.name}`}><Icon name="close" size={16} /></button>
        </div>
      )}
      <p id="ig-err" className={'field-err' + (msg ? ' show' : '')} role="alert">{msg ? <><Icon name="alert" size={14} /> {msg}</> : null}</p>

      <ol className="how-list" id="ig-help">
        <li>In Instagram, open <b>Accounts Center</b> and choose <b>Download your information</b>.</li>
        <li>Pick the <b>JSON</b> format (not HTML) and request it.</li>
        <li>When Instagram emails you, download the ZIP and drop it here.</li>
      </ol>
      <p className="read-note"><Icon name="lock" size={14} /> <span>Ptah reads only your <b>saved posts</b>, <b>likes</b> and <b>saved music</b>. The ZIP is deleted as soon as it has been read.</span></p>
    </section>
  )
}

/* ------------------------------------------------------------------------------------------ Loves */
type Opt = { key: string; love: { name: string; type?: string; qloo_id?: string; label?: string }; sub?: string; manual?: boolean }

export function LovesCard({ loves, onChange, health }: { loves: FormState['loves']; onChange: (l: FormState['loves']) => void; health: Health | null }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<QlooHit[]>([])
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [offline, setOffline] = useState<string | null>(null)
  const [said, setSaid] = useState('')
  const wantFirst = useRef(false)
  const id = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const abort = useRef<AbortController | null>(null)
  const full = loves.length >= MAX_LOVES
  const noQloo = health !== null && !health.qloo

  // debounced search: ~250 ms after the last keystroke, the previous request is cancelled
  useEffect(() => {
    abort.current?.abort()
    const term = q.trim()
    if (term.length < 2 || noQloo) { setHits([]); setBusy(false); return }
    setBusy(true)
    const ac = new AbortController()
    abort.current = ac
    const t = window.setTimeout(() => {
      searchQloo(term, ac.signal)
        .then((r) => { setHits(r); setOffline(null); setActive(0); setBusy(false) })
        .catch((e) => {
          if (e?.name === 'AbortError') return
          setHits([]); setBusy(false)
          setOffline(e instanceof ApiError && e.code === 'qloo_not_configured' ? 'Suggestions are not available on this server.' : 'Suggestions are not available right now.')
        })
    }, 250)
    return () => { window.clearTimeout(t); ac.abort() }
  }, [q, noQloo])

  const have = new Set(loves.map((l) => (l.qloo_id ?? l.name.toLowerCase())))
  const opts: Opt[] = hits
    .filter((h) => !have.has(h.qloo_id) && !have.has(h.name.toLowerCase()))
    .map((h) => ({ key: h.qloo_id, love: { name: h.name, type: h.type, qloo_id: h.qloo_id }, sub: [typeLabel(h.type), ...(h.tags ?? []).slice(0, 2).map((t) => t.replace(/^./, (c) => c.toUpperCase()))].join(', ') }))
  const term = q.trim()
  if (term.length >= 2 && !have.has(term.toLowerCase())) {
    opts.push({ key: 'manual:' + term, love: { name: term }, sub: 'Add exactly as typed', manual: true })
  }
  const show = open && term.length >= 2 && !full

  const add = (o: Opt) => {
    if (full) return
    onChange([...loves, o.love])
    setSaid(`Added ${o.love.name}`)
    setQ(''); setHits([]); setActive(0)
    inputRef.current?.focus()
  }
  const remove = (i: number) => { const gone = loves[i]; onChange(loves.filter((_, k) => k !== i)); setSaid(`Removed ${gone.name}`); inputRef.current?.focus() }
  // Enter pressed while suggestions are still loading: take the best one as soon as they arrive
  useEffect(() => {
    if (!busy && wantFirst.current) { wantFirst.current = false; if (opts[0]) add(opts[0]) }
  })
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, Math.max(0, opts.length - 1))) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter') { if (show && busy && hits.length === 0 && !noQloo) { e.preventDefault(); wantFirst.current = true } else if (show && opts[active]) { e.preventDefault(); add(opts[active]) } }
    else if (e.key === 'Escape') { if (open) { e.preventDefault(); e.stopPropagation(); setOpen(false) } }
    else if (e.key === 'Backspace' && !q && loves.length) remove(loves.length - 1)
  }

  return (
    <section className="ob-card" aria-labelledby="lv-h">
      <div className="ob-card-head">
        <span className="pn-ref" aria-hidden>B</span>
        <div><h2 id="lv-h">Add things you love</h2><p className="muted">Artists, films, shows, books, places, brands. Even two or three helps.</p></div>
      </div>
      <div className="combo">
        <label className="sr" htmlFor={id + '-in'}>Search for something you love</label>
        <div className="combo-field">
          <Icon name="search" size={16} />
          <input
            id={id + '-in'} ref={inputRef} type="text" role="combobox" autoComplete="off" spellCheck={false} enterKeyHint="done"
            aria-expanded={show} aria-controls={id + '-list'} aria-autocomplete="list" aria-activedescendant={show && opts[active] ? `${id}-o${active}` : undefined}
            placeholder={full ? 'You have added enough' : 'Try “Radiohead”, “Wes Anderson”, “Kinfolk”'} disabled={full}
            value={q} onChange={(e) => { wantFirst.current = false; setQ(e.target.value); setOpen(true) }} onFocus={() => { setOpen(true); if (window.matchMedia?.('(max-width: 820px)').matches) window.setTimeout(() => inputRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 250) }} onBlur={() => window.setTimeout(() => setOpen(false), 120)} onKeyDown={onKey}
          />
          {busy && <span className="spinner sm" aria-hidden />}
        </div>
        {show && (
          <ul className="combo-list" id={id + '-list'} role="listbox" aria-label="Suggestions">
            {opts.map((o, i) => (
              <li key={o.key} id={`${id}-o${i}`} role="option" aria-selected={i === active} className={'opt' + (i === active ? ' on' : '')} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); add(o) }}>
                <span className="opt-ico" aria-hidden><Icon name={o.manual ? 'plus' : typeIcon(o.love.type)} size={16} /></span>
                <span className="grow"><b>{o.love.name}</b><small>{o.sub}</small></span>
              </li>
            ))}
            {busy && opts.length <= 1 && <li className="opt-note" role="presentation">Searching Qloo</li>}
          </ul>
        )}
        {(noQloo || offline) && <p className="muted soft">{offline ?? (PAGES ? 'Qloo suggestions run on the full app: this static demo has no server (and no API keys). Type a name and press Enter to add it.' : 'Suggestions are not available on this server. Type a name and press Enter to add it.')}</p>}
      </div>
      <div className="sr" aria-live="polite" role="status">{said}</div>
      {loves.length > 0 ? (
        <ul className="love-chips" aria-label="Things you love">
          {loves.map((l, i) => (
            <li key={(l.qloo_id ?? l.name) + i} className="love-chip">
              <Icon name={typeIcon(l.type)} size={14} />
              <span title={typeLabel(l.type)}>{l.name}</span>
              <button type="button" onClick={() => remove(i)} aria-label={`Remove ${l.name}`}><Icon name="close" size={14} /></button>
            </li>
          ))}
        </ul>
      ) : <p className="muted soft">Nothing added yet.</p>}
      {full && <p className="muted soft">That is the most Ptah can use at once ({MAX_LOVES}).</p>}
    </section>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import { Cell, Sheet, TbMark, ThemeCell } from '../sheet/Sheet'
import { Icon } from '../ui'
import { ApiError, createJob, getHealth, getRooms, type Health, type SampleRoom } from './api'
import { emptyForm, type FormState } from './formTypes'
import { OwnedCard, PrefsCard, RoomCard } from './RoomStep'
import { InstagramCard, LovesCard } from './TastePicker'
import { PAGES, PAGES_ROOMS, REPO_URL } from '../pages'

type Step = 'taste' | 'room'
type Problem = { step: Step; where: 'zip' | 'room' | 'general'; title: string; text: string }

/** Turn an API failure into something a person can act on, and say which step it belongs to. */
function explain(e: unknown): Problem {
  const err = e instanceof ApiError ? e : null
  const code = err?.code ?? ''
  const msg = err?.message || 'Something went wrong. Please try again.'
  if (code === 'bad_export') return { step: 'taste', where: 'zip', title: 'That export could not be used', text: msg }
  if (code === 'empty_export') return { step: 'taste', where: 'zip', title: 'Nothing to read in that export', text: msg }
  if (code === 'upload_failed' || err?.status === 413) return { step: 'taste', where: 'zip', title: 'The upload did not work', text: msg }
  if (code === 'bad_room') return { step: 'room', where: 'room', title: 'That room could not be used', text: msg }
  if (code === 'offline') return { step: 'room', where: 'general', title: 'Ptah is not reachable', text: 'Check your connection and try again.' }
  if (code === 'no_input') return { step: 'taste', where: 'general', title: 'Add a source first', text: msg }
  return { step: 'room', where: 'general', title: 'We could not start', text: msg }
}

export default function Flow({ step, onStep, onHome, onStarted }: { step: Step; onStep: (s: Step) => void; onHome: () => void; onStarted: (jobId: string) => void }) {
  const [form, setForm] = useState<FormState>(emptyForm)
  const [health, setHealth] = useState<Health | null>(null)
  const [rooms, setRooms] = useState<SampleRoom[] | null>(null)
  const [roomsError, setRoomsError] = useState(false)
  const [uploading, setUploading] = useState<number | null>(null)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [staticNote, setStaticNote] = useState(false)
  const abort = useRef<(() => void) | null>(null)
  const headRef = useRef<HTMLHeadingElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const set = (p: Partial<FormState>) => setForm((f) => ({ ...f, ...p }))

  useEffect(() => {
    const ac = new AbortController()
    if (PAGES) {
      // static demo: no server to ask; the only room is the bundled sample
      setHealth({ ok: true, qloo: false, llm: false, demo: true, max_upload_mb: 300 })
      setRooms(PAGES_ROOMS)
      setForm((f) => (f.room ? f : { ...f, room: 'sample:' + PAGES_ROOMS[0].id }))
      return () => { abort.current?.() }
    }
    getHealth(ac.signal).then(setHealth).catch(() => setHealth(null))
    getRooms(ac.signal).then((r) => {
      setRooms(r)
      setForm((f) => (f.room || !r.length ? f : { ...f, room: 'sample:' + (r.find((x) => x.id === 'sample-living-room') ?? r[0]).id }))
    }).catch((e) => { if (e?.name !== 'AbortError') { setRooms([]); setRoomsError(true) } })
    return () => { ac.abort(); abort.current?.() }
  }, [])

  // a refresh on the second step has lost the answers: start at the first
  useEffect(() => { if (step === 'room') onStep('taste') }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // move focus to the step heading on step change (keyboard and screen reader users land at the top)
  useEffect(() => { headRef.current?.focus({ preventScroll: true }); scrollRef.current?.scrollTo(0, 0) }, [step])

  const hasSource = !!form.zip || form.loves.length > 0
  const busy = uploading !== null
  const title = useMemo(() => (step === 'taste' ? 'What do you love?' : 'Tell us about your room'), [step])

  const submit = async () => {
    if (busy) return
    setProblem(null)
    if (PAGES) { setStaticNote(true); return }
    const fd = new FormData()
    if (form.zip) fd.append('instagram', form.zip, form.zip.name)
    if (form.loves.length) fd.append('loves', JSON.stringify(form.loves.map(({ name, type, qloo_id }) => ({ name, ...(type ? { type } : {}), ...(qloo_id ? { qloo_id } : {}) }))))
    if (form.room === 'file' && form.roomFile) fd.append('room', form.roomFile, 'room.json')
    else if (form.room.startsWith('sample:')) fd.append('room_id', form.room.slice(7))
    fd.append('owned', JSON.stringify({ sample: false, items: form.owned.map(({ name, category, w, d, h, template }) => ({ name, category, w, d, h, ...(template ? { template } : {}) })) }))
    fd.append('preferences', JSON.stringify({ budget: form.budget, can_paint: form.canPaint, ...(form.location.trim() ? { location: form.location.trim() } : {}) }))
    setUploading(0)
    const job = createJob(fd, (f) => setUploading(f))
    abort.current = job.abort
    try {
      const r = await job.promise
      abort.current = null
      onStarted(r.job_id)
    } catch (e) {
      abort.current = null
      setUploading(null)
      if (e instanceof ApiError && e.code === 'aborted') return
      const p = explain(e)
      setProblem(p)
      if (p.step !== step) onStep(p.step)
    }
  }

  const zipErr = problem?.where === 'zip' ? `${problem.title}. ${problem.text}` : null
  const roomErr = problem?.where === 'room' ? `${problem.title}. ${problem.text}` : null
  const chosenRoom = form.room === 'file' ? (form.roomFile?.name ?? null) : form.room.startsWith('sample:') ? (rooms?.find((r) => 'sample:' + r.id === form.room)?.name ?? null) : null
  const roomReady = !!form.room && (form.room !== 'file' || !!form.roomFile)

  const hint = step === 'taste'
    ? hasSource ? `${[form.zip && 'Instagram export', form.loves.length && `${form.loves.length} favourite${form.loves.length > 1 ? 's' : ''}`].filter(Boolean).join(' + ')} ready` : 'Add an Instagram export or at least one thing you love.'
    : busy ? (form.zip ? `Uploading your export ${Math.round((uploading ?? 0) * 100)}%` : 'Starting') : !hasSource ? 'Go back and add a source.' : !roomReady ? 'Pick a room to furnish.' : 'Ready to draw.'

  const tb = (
    <>
      <TbMark onClick={onHome} label="Ptah home" />
      <Cell label="Drawing set" className="hide-s">Ptah, a room from your taste</Cell>
      <Cell label={step === 'taste' ? 'Step 1 of 2' : 'Step 2 of 2'} className="grow-s">{step === 'taste' ? 'Your taste' : 'Your room'}</Cell>
      <div className="tb-cell fill hide-s">
        <small>Status</small>
        {busy ? (
          <div className="upload-bar" role="progressbar" aria-label="Uploading" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((uploading ?? 0) * 100)}>
            <i style={{ width: `${Math.round((uploading ?? 0) * 100)}%` }} />
          </div>
        ) : null}
        <b className="hint" id="src-hint" aria-live="polite">{hint}</b>
      </div>
      <div className="tb-act">
        {step === 'taste' ? (
          <>
            <button className="btn quiet" onClick={onHome}>Back</button>
            <button className="btn primary reg" disabled={!hasSource} aria-describedby="src-hint" onClick={() => onStep('room')}>Continue</button>
          </>
        ) : (
          <>
            <button className="btn quiet" onClick={() => onStep('taste')} disabled={busy}>Back</button>
            {busy
              ? <button className="btn" onClick={() => abort.current?.()}>Cancel</button>
              : <button className="btn primary reg" disabled={!hasSource || !roomReady} aria-describedby="src-hint" onClick={submit}>Design my room</button>}
          </>
        )}
      </div>
      <ThemeCell />
    </>
  )

  return (
    <Sheet className="ob ob-flow" title="Your brief" tb={tb} bodyClass="flow-body">
      <aside className="flow-aside">
        <nav aria-label="Steps" className="ob-steps">
          <ol>
            <li className={step === 'taste' ? 'on' : 'done'} aria-current={step === 'taste' ? 'step' : undefined}><span className="n">{step === 'taste' ? '1' : <Icon name="check" size={14} />}</span> Your taste</li>
            <li className={step === 'room' ? 'on' : ''} aria-current={step === 'room' ? 'step' : undefined}><span className="n">2</span> Your room</li>
          </ol>
        </nav>
        <h1 ref={headRef} tabIndex={-1} className="ob-h1">{title}</h1>
        {step === 'taste' ? <p className="ob-sub">Give Ptah at least one source. The more it knows, the better the room fits you.</p> : <p className="ob-sub">Pick a room to furnish. Everything below is optional.</p>}
        <div className="brief canon" aria-label="Your brief so far">
          <div className="cap"><b>Your brief so far</b></div>
          <dl>
            <dt>Instagram export</dt><dd>{form.zip ? form.zip.name : 'None yet'}</dd>
            <dt>Things you love</dt><dd>{form.loves.length ? `${form.loves.length}` : 'None yet'}</dd>
            <dt>Room</dt><dd>{chosenRoom ?? 'Not chosen'}</dd>
            <dt>Already own</dt><dd>{form.owned.length ? `${form.owned.length}` : 'Nothing'}</dd>
          </dl>
        </div>
      </aside>

      <div className="flow-scroll" ref={scrollRef}>
        <div className="ob-main">
          {staticNote && (
            <div className="banner" role="status">
              <Icon name="alert" size={18} />
              <div>
                <b>Live design runs on the full app</b>
                <p>This hosted demo has no server, so it cannot read your taste or design a new room. Run Ptah locally to try it with your own data: see the <a href={REPO_URL} target="_blank" rel="noreferrer">README</a>. To see a finished room here, go back and choose "Try with a sample person".</p>
                <p><button type="button" className="btn sm" onClick={onHome}>Back to the start</button></p>
              </div>
            </div>
          )}
          {problem && problem.where === 'general' && (
            <div className="banner bad" role="alert"><Icon name="alert" size={18} /><div><b>{problem.title}</b><p>{problem.text}</p></div></div>
          )}
          {step === 'taste' ? (
            <div className="ob-stack">
              <InstagramCard file={form.zip} onFile={(f) => { set({ zip: f }); if (problem?.where === 'zip') setProblem(null) }} health={health} error={zipErr} />
              <LovesCard loves={form.loves} onChange={(l) => set({ loves: l })} health={health} />
            </div>
          ) : (
            <div className="ob-stack">
              <RoomCard rooms={rooms} roomsError={roomsError} form={form} set={set} error={roomErr} />
              <div className="ob-cols">
                <OwnedCard owned={form.owned} onChange={(o) => set({ owned: o })} />
                <PrefsCard form={form} set={set} />
              </div>
            </div>
          )}
        </div>
      </div>
    </Sheet>
  )
}

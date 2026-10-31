import { useEffect, useRef, useState } from 'react'
import { Cell, Sheet, TbMark, ThemeCell } from '../sheet/Sheet'
import { Icon, type IconName } from '../ui'
import { getJob } from './api'
import { type JobError, type JobView, type LogLine, type Service, type StageView } from './jobEvents'
import { useJobStream, type MockSource } from './useJobStream'

/* development-only replay of a recorded stream: ?mockEvents=<url>&mockSpeed=4 (production builds never read it) */
const MOCK: MockSource | null = (() => {
  if (!import.meta.env.DEV) return null
  const q = new URLSearchParams(window.location.search)
  const url = q.get('mockEvents')
  return url ? { url, speed: Math.max(0.1, parseFloat(q.get('mockSpeed') ?? '1') || 1) } : null
})()

const SERVICE_INFO: Record<Exclude<Service, 'compute'>, { name: string; icon: IconName; tip: string; idle: string }> = {
  qloo: { name: 'Qloo', icon: 'qloo', tip: 'Qloo: decor styles, places and brands that people with your taste love.', idle: 'Taste data' },
  llm: { name: 'AI', icon: 'dividers', tip: 'AI: reads your taste and designs the room.', idle: 'Designer' },
  serpapi: { name: 'Shopping', icon: 'bag', tip: 'Shopping: searches stores for real pieces that match the design.', idle: 'Real products' },
}
const SHOWN: Exclude<Service, 'compute'>[] = ['qloo', 'llm', 'serpapi']

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
const dur = (ms?: number) => (ms === undefined ? '' : ms < 950 ? '<1 s' : ms < 59500 ? `${Math.round(ms / 1000)} s` : `${Math.floor(ms / 60000)} m ${Math.round((ms % 60000) / 1000)} s`)

const ERR_TITLE: Record<string, string> = {
  qloo_cap: 'Qloo is paused for now', qloo_unavailable: 'Qloo is not answering', qloo_not_configured: 'Qloo is not connected',
  llm_budget: 'The AI designer is at its limit', llm_not_configured: 'The AI designer is not connected', no_taste: 'Not enough to go on yet',
  bad_export: 'That export could not be read', empty_export: 'Nothing to read in that export', bad_room: 'That room could not be used',
  design_rejected: 'The design needs another try', layout_failed: 'The furniture would not fit', cancelled: 'This run was cancelled', gone: 'This session has ended',
}

/** Keeps a fast-changing value on screen long enough to read: each value stays at least `ms`. */
function useDwell<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value)
  const since = useRef(performance.now())
  const latest = useRef(value)
  latest.current = value
  useEffect(() => {
    if (Object.is(shown, value)) return
    const wait = Math.max(0, ms - (performance.now() - since.current))
    const t = window.setTimeout(() => { since.current = performance.now(); setShown(latest.current) }, wait)
    return () => window.clearTimeout(t)
  }, [value, shown, ms])
  return shown
}

function Elapsed({ view, running }: { view: JobView; running: boolean }) {
  const [, tick] = useState(0)
  const at = useRef(Date.now())
  useEffect(() => { at.current = Date.now() }, [view.elapsedS])
  useEffect(() => { if (!running) return; const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t) }, [running])
  const s = view.elapsedS + (running ? (Date.now() - at.current) / 1000 : 0)
  return <span className="elapsed" aria-hidden>{clock(s)}</span>
}

/** One service in the live strip. It turns rubric while a request is in flight; the counter shows how many are open at once. */
function ServicePill({ svc, view }: { svc: Exclude<Service, 'compute'>; view: JobView }) {
  const s = view.services[svc]
  const info = SERVICE_INFO[svc]
  const on = s.active > 0
  const tipId = `tip-${svc}`
  const status = on ? (s.active > 1 ? `${s.active} requests` : s.total > 0 ? `${s.total} done, working` : 'Working') : s.total > 0 ? `${s.total} done` : info.idle
  return (
    <li className={'svc ' + svc + (on ? ' on' : '')} tabIndex={0} aria-describedby={tipId} data-active={on || undefined}>
      <Icon name={info.icon} size={28} />
      <span className="svc-txt"><b>{info.name}</b><small>{status}</small></span>
      <span id={tipId} role="tooltip" className="svc-tip">{info.tip}{s.failed > 0 ? ` (${s.failed} retried or failed)` : ''}</span>
      <span className="sr">{info.name}: {on ? `${s.active} request${s.active > 1 ? 's' : ''} in progress` : s.total > 0 ? `${s.total} requests done` : 'not used yet'}</span>
    </li>
  )
}

const CODE = (i: number) => 'S-' + String(i + 1).padStart(2, '0')

/** Seconds the current stage has been running (restarts when the stage changes). */
function useStageClock(current: string | null, running: boolean): number {
  const start = useRef(Date.now())
  const [, tick] = useState(0)
  useEffect(() => { start.current = Date.now(); tick((n) => n + 1) }, [current])
  useEffect(() => { if (!running) return; const t = window.setInterval(() => tick((n) => n + 1), 1000); return () => window.clearInterval(t) }, [running])
  return (Date.now() - start.current) / 1000
}

/** The seven stages as a drawing set: a sheet number, a title, a status and a time. */
function StageTable({ stages, failed, clock: stageClock }: { stages: StageView[]; failed: boolean; clock: number }) {
  const statusOf = (s: StageView): { text: string; icon?: boolean } =>
    failed && s.state === 'active' ? { text: 'Stopped' } : s.state === 'done' ? { text: s.cached ? 'Issued, kept' : 'Issued', icon: true } : s.state === 'active' ? { text: 'Drawing' } : s.state === 'skipped' ? { text: 'Skipped' } : { text: 'Waiting' }
  const rows = stages.length === 0 ? [{ id: 'start', label: 'Getting started', state: 'active', progress: 0, weight: 1 } as StageView] : stages
  return (
    <table className="sheets">
      <caption className="sr">The seven sheets of your drawing set</caption>
      <thead><tr><th scope="col">Sheet</th><th scope="col">Stage</th><th scope="col">Status</th><th scope="col" className="r">Time</th></tr></thead>
      <tbody>
        {rows.map((s, i) => {
          const st = statusOf(s)
          const stopped = failed && s.state === 'active'
          return (
            <tr key={s.id} className={s.state + (stopped ? ' stopped' : '')} aria-current={s.state === 'active' && !failed ? 'step' : undefined}>
              <td className="code">{CODE(i)}</td>
              <td className="name">{s.label}</td>
              <td className="st">{st.icon && <Icon name="check" size={18} />}{stopped && <Icon name="alert" size={18} />}{st.text}</td>
              <td className="tm num">{s.state === 'done' ? (s.cached ? '' : dur(s.durationMs)) : s.state === 'active' && !failed ? clock(stageClock) : ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** A schematic plan that is drawn as the work goes on: walls first, then the pieces proposed (dashed), then issued (solid). */
function PlanSheet({ designed, laidOut }: { designed: boolean; laidOut: boolean }) {
  const piece = (x: number, y: number, w: number, h: number, label: string, rot = false, round = false) => (
    <g key={label}>
      <rect x={x} y={y} width={w} height={h} rx={round ? w / 2 : 0} className="pc" />
      <text x={rot ? x + w / 2 : x + w / 2} y={rot ? y + h / 2 : y + h / 2 + 4} textAnchor="middle" transform={rot ? `rotate(-90 ${x + w / 2} ${y + h / 2})` : undefined} className="pl">{label}</text>
    </g>
  )
  return (
    <svg className="plan-svg" viewBox="0 0 610 392" role="img" aria-label="A schematic plan of a room, drawn as the layout is made">
      <g transform="translate(-30 22) scale(1.1)">
        <g className="walls">
          <rect className="draw" style={{ ['--len' as string]: 1400 }} x="116" y="74" width="378" height="222" strokeWidth="2.2" />
          <rect className="draw" style={{ ['--len' as string]: 1300 }} x="126" y="84" width="358" height="202" />
          <rect x="150" y="283" width="52" height="6" className="gap" />
          <path d="M150 286V234A52 52 0 0 1 202 286" className="swing" />
          <path d="M150 286h52" strokeWidth="1.6" />
          <rect x="270" y="71" width="120" height="6" className="gap" />
          <path d="M270 72h120M270 76h120M270 74h120" strokeWidth="1" />
        </g>
        {designed && (
          <g className={'pieces ' + (laidOut ? 'issued' : 'proposed')}>
            {piece(330, 104, 94, 22, 'sideboard')}
            {piece(296, 162, 108, 48, 'coffee table')}
            {piece(316, 236, 150, 40, 'sofa')}
            {piece(140, 236, 26, 26, '', false, true)}
            {piece(454, 94, 24, 96, 'shelf', true)}
          </g>
        )}
        <g stroke="var(--carbon)" strokeWidth="1" fill="none"><path d="M116 312h378M116 306v12M494 306v12" /><path d="M104 74v222M98 74h12M98 296h12" /></g>
        <rect x="262" y="305" width="86" height="14" className="gap" />
        <text x="305" y="316" textAnchor="middle" className="pl strong">room width</text>
      </g>
    </svg>
  )
}

function LogList({ lines }: { lines: LogLine[] }) {
  const shown = lines.slice(-80).reverse()
  if (!shown.length) return <p className="muted soft">Nothing yet.</p>
  return (
    <ul className="logl">
      {shown.map((l) => (
        <li key={l.seq + l.text} className={'k-' + l.kind + (l.ok === false ? ' failed' : '') + (l.service === 'qloo' ? ' q' : '')}>
          <time>{clock(l.t)}</time>
          <span className="w">{l.service === 'qloo' ? 'Qloo' : l.service === 'llm' ? 'AI' : l.service === 'serpapi' ? 'Shop' : 'Ptah'}</span>
          <span className="lt">{l.text}</span>
          {l.ms !== undefined && <small>{dur(l.ms)}</small>}
        </li>
      ))}
    </ul>
  )
}

function ConfirmButton({ label, confirm, onConfirm, busy }: { label: string; confirm: string; onConfirm: () => void; busy?: boolean }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => { if (!armed) return; const t = window.setTimeout(() => setArmed(false), 5000); return () => window.clearTimeout(t) }, [armed])
  return (
    <button className={'tb-link' + (armed ? ' armed' : '')} disabled={busy} onClick={() => (armed ? onConfirm() : setArmed(true))} onBlur={() => setArmed(false)}>
      <Icon name="trash" size={18} /> {busy ? 'Deleting' : armed ? confirm : label}
    </button>
  )
}

function ErrorPanel({ err, onResume, resuming, resumeError, onStartOver }: { err: JobError; onResume: () => void; resuming: boolean; resumeError: string | null; onStartOver: () => void }) {
  return (
    <div className="banner bad err-panel" role="alert">
      <Icon name="alert" size={20} />
      <div className="grow">
        <b>{ERR_TITLE[err.code] ?? 'Something went wrong'}</b>
        <p>{err.message}</p>
        {err.hint && <p className="muted">{err.hint}</p>}
        {resumeError && <p className="muted">{resumeError}</p>}
        {err.retryable && <p className="muted">Nothing is lost: finished steps are kept, and Ptah picks up where it stopped.</p>}
        <div className="row-btns">
          {err.retryable && <button className="btn primary sm" onClick={onResume} disabled={resuming}>{resuming ? <><span className="spinner sm" aria-hidden /> Restarting</> : 'Resume'}</button>}
          <button className="btn sm" onClick={onStartOver}>Start over</button>
        </div>
        {err.technical && <details className="tech"><summary>Technical details</summary><div><code>{err.code}</code><p>{err.technical}</p></div></details>}
      </div>
    </div>
  )
}

export default function Progress({ jobId, leaving, fade, onResult, onStartOver, onDelete }: {
  jobId: string; leaving: boolean; fade: boolean; onResult: (jobId: string) => void; onStartOver: () => void; onDelete: () => void | Promise<void>
}) {
  const { view, connection, resume, resuming, resumeError } = useJobStream(jobId, MOCK)
  const [deleting, setDeleting] = useState(false)
  const [queue, setQueue] = useState(0)
  const resultRef = useRef(onResult)
  resultRef.current = onResult
  const headRef = useRef<HTMLHeadingElement>(null)

  const done = view.phase === 'done'
  const failed = view.phase === 'error'
  const running = !done && !failed

  // hand over to the 3D room shortly after the result (long enough to read "ready", short enough to feel instant)
  const gaps = !!(view.result?.partial || view.result?.warnings.length)
  useEffect(() => {
    if (!done) return
    const t = window.setTimeout(() => resultRef.current(jobId), gaps ? 1800 : 900)
    return () => window.clearTimeout(t)
  }, [done, jobId, gaps])

  // "waiting in line": only before the first stage starts
  useEffect(() => {
    if (MOCK || view.stages.length > 0 || done || failed) { setQueue(0); return }
    const ac = new AbortController()
    const t = window.setInterval(() => { getJob(jobId, ac.signal).then((s) => setQueue(s.status === 'queued' ? s.queue_position ?? 0 : 0)).catch(() => {}) }, 2500)
    return () => { window.clearInterval(t); ac.abort() }
  }, [jobId, view.stages.length, done, failed])

  // screen-reader updates only when a step changes (never per event)
  const stageNow = view.stages.find((s) => s.id === view.current)
  const announce = failed ? '' : done ? 'Your room is ready.' : stageNow ? `${stageNow.label}. Step ${view.stages.indexOf(stageNow) + 1} of ${view.stages.length}.` : 'Starting.'

  useEffect(() => { headRef.current?.focus({ preventScroll: true }) }, [failed])

  const now = useDwell(view.now, 650)
  const pct = Math.round(view.overall * 100)
  const heading = failed ? 'We hit a snag' : done ? 'Your room is ready' : 'Designing your room'
  const caption = queue > 0 ? `Waiting for your turn (${queue} ahead)` : view.caption
  const summary = done ? [view.result?.items ? `${view.result.items} pieces` : '', view.result?.products ? `${view.result.products} real products` : ''].filter(Boolean).join(', ') : ''
  const stageIdx = Math.max(0, view.stages.findIndex((s) => s.id === view.current))
  const stageClock = useStageClock(view.current, running)
  const stageState = (id: string) => view.stages.find((s) => s.id === id)?.state
  const designed = stageState('design') === 'done'
  const laidOut = stageState('layout') === 'done'

  const tb = (
    <>
      <TbMark label="Ptah" />
      <Cell label="Drawing set" className="hide-s">Your room</Cell>
      <Cell label="Sheet" className="num grow-s">{done ? '07 of 07' : `${String(stageIdx + 1).padStart(2, '0')} of ${String(Math.max(view.stages.length, 7)).padStart(2, '0')}`}</Cell>
      <Cell label="Status" fill className="hide-s">{failed ? 'Stopped, finished sheets are kept' : done ? 'Issued, opening the 3D room' : 'In progress, issued sheets are final'}</Cell>
      {!leaving && (
        <div className="tb-act">
          <ConfirmButton
            label={running ? 'Cancel and delete my data' : 'Delete my data'} confirm="Yes, delete everything" busy={deleting}
            onConfirm={async () => { setDeleting(true); try { await onDelete() } finally { setDeleting(false) } }}
          />
        </div>
      )}
      <ThemeCell />
    </>
  )

  return (
    <Sheet className={'ob ob-progress' + (leaving ? ' leaving' : '') + (fade ? ' fade' : '')} title="Progress" tb={tb} bodyClass="prog-body">
      <div className="prog-left">
        <div className="sr" role="status" aria-live="polite">{announce}</div>
        <h1 ref={headRef} tabIndex={-1} className="t-display prog-h">{heading}</h1>
        <p className="sub">{done ? (summary || 'Opening the 3D view.') : failed ? 'Here is what happened.' : 'Keep this tab open. Ptah is reading your taste, asking Qloo and drawing.'}</p>

        <section className={'scale-block' + (failed ? ' failed' : done ? ' done' : '')} aria-label="Progress">
          <div className="sc-row">
            <strong aria-hidden>{done ? (leaving ? 'Setting up your room' : 'Ready') : caption}</strong>
            <span className="pct" aria-hidden>{pct}%</span>
          </div>
          <div className="ruler" role="progressbar" aria-label="Overall progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={`${pct} percent. ${stageNow?.label ?? ''}`}>
            <i style={{ width: `${pct}%` }} />
          </div>
          <div className="ticks" aria-hidden><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
          {connection === 'reconnecting' && running && <p className="conn" role="status">Reconnecting</p>}
          {connection === 'polling' && running && <p className="conn" role="status">Live updates paused; checking every few seconds.</p>}
        </section>

        {failed && view.error && <ErrorPanel err={view.error} onResume={resume} resuming={resuming} resumeError={resumeError} onStartOver={onStartOver} />}
        {done && view.result && (view.result.partial || view.result.warnings.length > 0) && (
          <div className="banner warn" role="status"><Icon name="alert" size={18} /><div><b>Your room is ready, with a few gaps</b><p>{view.result.warnings[0] ?? 'Some steps were skipped, so parts of the room use designed pieces instead of real products.'}</p></div></div>
        )}

        <StageTable stages={view.stages} failed={failed} clock={stageClock} />
      </div>

      <div className="prog-right">
        <div className="plan-sheet canon">
          <div className="cap tl"><b>Plan, schematic</b>{laidOut ? 'Layout issued.' : designed ? 'Pieces proposed. Layout to follow.' : 'Walls first. Pieces to follow.'}</div>
          <div className="cap tr">Solid, drawn<br />Dashed, proposed</div>
          <PlanSheet designed={designed} laidOut={laidOut} />
        </div>
        <div className="live">
          <div className="top"><span className="cap">{failed ? 'Stopped at' : done ? 'Finished' : 'Now drawing'}</span>{running && <Elapsed view={view} running={running} />}</div>
          <p className={running ? 'writing' : undefined}>{running ? (now?.label ?? view.caption) : failed ? (stageNow?.label ?? 'This run') : 'Every sheet is issued'}</p>
        </div>
        <ul className="svcs" aria-label="What Ptah is using">
          {SHOWN.map((k) => <ServicePill key={k} svc={k} view={view} />)}
        </ul>
        <div className="log">
          <h3><span>Activity</span><span className="num">{view.log.length} entries</span></h3>
          <LogList lines={view.log} />
        </div>
      </div>
    </Sheet>
  )
}

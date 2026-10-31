import { useEffect, useRef, useState } from 'react'
import { warmUp } from './loaders'
import { getHealth, viewerBase, type Health } from './api'
import { recallJob } from './session'
import { Icon } from '../ui'
import { Cell, Dim, Sheet, TbMark, ThemeCell } from '../sheet/Sheet'
import { DataNote } from './DataNote'
import { PAGES, REPO_URL } from '../pages'

const demoSeconds = (() => { const n = parseFloat(new URLSearchParams(window.location.search).get('demo_seconds') ?? ''); return Number.isFinite(n) && n > 0 ? n : undefined })()
const PLATE = `${import.meta.env.BASE_URL}brand/room-plate.png`

export default function Welcome({ notice, onStart, onDemo, offlineApi }: { notice: string | null; onStart: () => void; onDemo: (seconds?: number) => Promise<void>; offlineApi: string }) {
  const [health, setHealth] = useState<Health | null | 'down'>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [dataOpen, setDataOpen] = useState(false)
  const last = useRef(recallJob()).current

  useEffect(() => {
    const ac = new AbortController()
    if (!PAGES) getHealth(ac.signal).then(setHealth).catch((e) => { if (e?.name !== 'AbortError') setHealth('down') })
    // after the first paint: fetch the form + progress screens, then (when idle) the 3D viewer
    let idle = 0
    const t = window.setTimeout(() => {
      warmUp('form')
      const ric = (window as unknown as { requestIdleCallback?: (f: () => void, o?: { timeout: number }) => number }).requestIdleCallback
      idle = ric ? ric(() => warmUp('viewer'), { timeout: 4000 }) : window.setTimeout(() => warmUp('viewer'), 1500)
    }, 400)
    return () => { ac.abort(); window.clearTimeout(t); const cic = (window as unknown as { cancelIdleCallback?: (n: number) => void }).cancelIdleCallback; if (idle && cic) cic(idle) }
  }, [])

  const down = health === 'down'
  const demoOk = health !== 'down' && (health === null || health.demo)
  const demo = async () => {
    setBusy(true); setErr(null)
    try { await onDemo(demoSeconds) } catch (e) { setErr((e as Error).message || 'The sample could not start.'); setBusy(false) }
  }

  const tb = (
    <>
      <TbMark label="Ptah" />
      <Cell label="Drawing set" className="grow-s">Ptah, a room from your taste</Cell>
      <Cell label="Sheet" className="hide-s num">00 of 07</Cell>
      <Cell label="Prepared with" fill className="hide-s">Qloo taste graph</Cell>
      <div className="tb-act"><button type="button" className="tb-link" onClick={() => setDataOpen(true)}>How Ptah uses your data</button></div>
      <ThemeCell />
    </>
  )

  return (
    <Sheet className="ob ob-welcome" title="Welcome" tb={tb} bodyClass="welcome-body">
      <div className="copy">
        <h1 className="t-display">Your taste, drawn to scale.</h1>
        <p className="lead">Give Ptah your Instagram export and a few things you love. It asks Qloo what people like you live with, then furnishes your room with real pieces you can buy.</p>
        {PAGES && (
          <p className="ob-note ok" role="note">
            <Icon name="alert" size={16} />
            <span><b>Static demo.</b> No server runs here, so nothing is uploaded. "Try with a sample person" replays a recorded run and opens a room Ptah designed for a real person, with their personal data removed. Live design runs in the <a href={REPO_URL} target="_blank" rel="noreferrer">full app</a>.</span>
          </p>
        )}
        {notice && <p className="ob-note ok" role="status"><Icon name="check" size={16} /> {notice}</p>}
        {down && (
          <p className="ob-note warn" role="alert">
            <Icon name="alert" size={16} />
            <span>Ptah's server isn't reachable{offlineApi ? ` at ${offlineApi}` : ''}. You can still explore the sample room.</span>
          </p>
        )}
        <div className="cta">
          <button className="btn primary lg reg" onClick={onStart} onPointerEnter={() => warmUp('form')} onFocus={() => warmUp('form')} disabled={down}>Design my room</button>
          {demoOk && (
            <button className="btn lg" onClick={demo} disabled={busy} aria-busy={busy}>
              {busy ? <><span className="spinner sm" aria-hidden /> Starting</> : 'Try with a sample person'}
            </button>
          )}
          {down && <a className="btn lg" href="?sample=1" onPointerEnter={() => warmUp('viewer')}>Open the sample room</a>}
        </div>
        {err && <p className="ob-note bad" role="alert"><Icon name="alert" size={16} /> {err}</p>}
        {last && !down && (
          <p className="ob-resume">
            {last.done ? 'Your last room is still saved.' : 'You have a room in progress.'}{' '}
            <a href={last.done ? `?data=${encodeURIComponent(viewerBase(last.id))}` : `?job=${last.id}`}>{last.done ? 'Open it' : 'Continue'}</a>
          </p>
        )}
        <div className="fine">
          <div><b>About two minutes.</b><br />Seven sheets, drawn while you wait.</div>
          <div><b>Yours to delete.</b><br />Remove your data at any point.</div>
        </div>
      </div>

      <div className="plate canon">
        <div className="cap tl"><b>Sample living room</b>Furnished from a sample person's taste</div>
        <Dim className="dim-h">4.20 m</Dim>
        <div className="room">
          <img src={PLATE} alt="A furnished sample living room in 3D, lit warmly in the evening" width={787} height={649} />
          <svg className="leader" viewBox="0 0 787 649" aria-hidden>
            <polyline points="407.9,258.4 407.9,-47.2 474.8,-47.2" /><circle cx="407.9" cy="258.4" r="4.5" />
            <polyline points="506.3,511.5 506.3,650" /><circle cx="506.3" cy="511.5" r="4.5" />
          </svg>
          <div className="call c1"><span>Walnut sideboard<em>Qloo 0.83</em></span></div>
          <div className="call c2"><span>Boucle sofa, sage<em>Article, $899</em></span></div>
        </div>
        <div className="key"><span>Evening light, warm 2,700 K</span><span>30 pieces, 3 already yours</span></div>
      </div>
      {dataOpen && <DataNote onClose={() => setDataOpen(false)} />}
    </Sheet>
  )
}

import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { Logo } from './brand/Logo'
import Welcome from './onboarding/Welcome'
import { apiBase, deleteJob, startDemo, viewerBase } from './onboarding/api'
import { loadFlow, loadProgress, loadViewer } from './onboarding/loaders'
import { forgetJob, rememberJob } from './onboarding/session'
import './onboarding/onboarding.css'

// The form and progress screens are small chunks; the 3D viewer (three.js) is the heavy one and is fetched in the background.
const ViewerApp = lazy(loadViewer)
const Flow = lazy(loadFlow)
const Progress = lazy(loadProgress)

export type Route =
  | { name: 'welcome' }
  | { name: 'taste' }
  | { name: 'room' }
  | { name: 'job'; id: string }
  | { name: 'view' }

/** Development builds only: replay a recorded event stream instead of calling the backend (`?mockEvents=<url>&mockData=<bundle url>`). */
const DEV_MOCK = import.meta.env.DEV ? new URLSearchParams(window.location.search) : null

function parse(): Route {
  const q = new URLSearchParams(window.location.search)
  if (DEV_MOCK?.get('mockEvents')) return { name: 'job', id: 'mock' }
  if (q.has('data') || q.has('sample')) return { name: 'view' }
  const job = q.get('job')
  if (job && /^[A-Za-z0-9_-]{4,64}$/.test(job)) return { name: 'job', id: job }
  const step = q.get('step')
  if (step === 'taste' || step === 'room') return { name: step }
  return { name: 'welcome' }
}

/** URL for a route. Keeps ?api= so a dev backend survives navigation. */
function urlFor(r: Route, data?: string): string {
  const keep = new URLSearchParams()
  const api = new URLSearchParams(window.location.search).get('api')
  if (api) keep.set('api', api)
  if (r.name === 'taste' || r.name === 'room') keep.set('step', r.name)
  if (r.name === 'job') keep.set('job', r.id)
  if (r.name === 'view' && data) keep.set('data', data)
  const s = keep.toString()
  return window.location.pathname + (s ? '?' + s : '')
}

function Splash({ text }: { text: string }) {
  return (
    <div className="ob-splash" role="status" aria-live="polite">
      <Logo size={56} />
      <span>{text}</span>
    </div>
  )
}

interface Handoff { jobId: string; data: string; ready: boolean }

export default function Shell() {
  const [route, setRoute] = useState<Route>(parse)
  /** A finished job: the 3D room is built behind the progress screen, which then fades out. */
  const [handoff, setHandoff] = useState<Handoff | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const mockData = DEV_MOCK?.get('mockData') ?? null

  const go = useCallback((r: Route, opts: { replace?: boolean; data?: string } = {}) => {
    const url = urlFor(r, opts.data)
    if (opts.replace) window.history.replaceState(null, '', url)
    else window.history.pushState(null, '', url)
    setRoute(r)
    window.scrollTo(0, 0)
  }, [])

  useEffect(() => {
    const onPop = () => { setHandoff(null); setRoute(parse()) }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const startOver = useCallback(() => { setHandoff(null); setNotice(null); go({ name: 'welcome' }) }, [go])
  const deleteAndExit = useCallback(async (jobId: string | null) => {
    if (jobId) { try { await deleteJob(jobId) } catch { /* already gone */ } forgetJob(jobId) }
    setHandoff(null)
    setNotice(jobId ? 'Your data was deleted.' : null)
    go({ name: 'welcome' }, { replace: true })
  }, [go])

  // hand-off: viewer ready (or 20 s, whichever first) -> fade the progress screen -> the URL becomes the shareable one
  const markReady = useCallback(() => setHandoff((h) => (h && !h.ready ? { ...h, ready: true } : h)), [])
  const hid = handoff?.jobId
  useEffect(() => {
    if (!hid) return
    const t = window.setTimeout(markReady, 20000)
    return () => window.clearTimeout(t)
  }, [hid, markReady])
  const finish = handoff?.ready ? handoff : null
  useEffect(() => {
    if (!finish) return
    const t = window.setTimeout(() => {
      setHandoff(null)
      rememberJob(finish.jobId, true)
      go({ name: 'view' }, { replace: true, data: finish.data })
    }, 650)
    return () => window.clearTimeout(t)
  }, [finish, go])

  const viewData = route.name === 'view' ? new URLSearchParams(window.location.search).get('data') : null
  const viewJobId = viewData?.match(/\/api\/jobs\/([A-Za-z0-9_-]+)\/viewer\/?/)?.[1] ?? null
  const viewerBaseUrl = handoff ? handoff.data : route.name === 'view' ? viewData ?? undefined : undefined
  const showViewer = !!handoff || route.name === 'view'
  const viewerJob = handoff?.jobId ?? viewJobId

  return (
    <>
      {showViewer && (
        // one stable element: it keeps running when the hand-off ends and the route becomes "view"
        <Suspense fallback={route.name === 'view' ? <Splash text="Opening your room" /> : null}>
          <ViewerApp base={viewerBaseUrl} onExit={startOver} onDelete={viewerJob ? () => deleteAndExit(viewerJob) : undefined} onReady={markReady} />
        </Suspense>
      )}
      {route.name === 'job' && (
        <Suspense fallback={<Splash text="Loading" />}>
          <Progress
            key={route.id}
            jobId={route.id}
            leaving={!!handoff}
            fade={!!handoff?.ready}
            onResult={(id) => setHandoff({ jobId: id, data: mockData ? new URL(mockData, document.baseURI).href : viewerBase(id), ready: false })}
            onStartOver={startOver}
            onDelete={() => deleteAndExit(route.id)}
          />
        </Suspense>
      )}
      {(route.name === 'taste' || route.name === 'room') && (
        <Suspense fallback={<Splash text="Loading" />}>
          <Flow step={route.name} onStep={(s) => go({ name: s })} onHome={startOver} onStarted={(id) => { rememberJob(id, false); go({ name: 'job', id }) }} />
        </Suspense>
      )}
      {route.name === 'welcome' && (
        <Welcome
          notice={notice}
          offlineApi={apiBase()}
          onStart={() => go({ name: 'taste' })}
          onDemo={async (seconds) => {
            const j = await startDemo(seconds)
            rememberJob(j.job_id, false)
            go({ name: 'job', id: j.job_id })
          }}
        />
      )}
    </>
  )
}

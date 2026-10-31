import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl, getJob, resumeJob } from './api'
import { applyEvents, initialView, viewFromStatus, type JobView, type PtahEvent } from './jobEvents'

export type Connection = 'connecting' | 'live' | 'reconnecting' | 'polling'
/** At most ~10 state updates per second, whatever the event rate. */
const FLUSH_MS = 100
const POLL_MS = 2500

export interface MockSource { url: string; speed: number }

/**
 * Follow one job: EventSource (auto-reconnects with Last-Event-ID) with a polling fallback.
 * Events are buffered and applied in batches, so a burst of 100 events costs one render.
 * `mock` (development builds only) replays a recorded stream client-side, for screenshots.
 */
export function useJobStream(jobId: string | null, mock: MockSource | null): { view: JobView; connection: Connection; resume: () => Promise<void>; resuming: boolean; resumeError: string | null } {
  const [view, setView] = useState<JobView>(initialView)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [epoch, setEpoch] = useState(0)
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState<string | null>(null)
  const cur = useRef<JobView>(view)
  const mockExtra = useRef<PtahEvent[] | null>(null)

  useEffect(() => {
    if (!jobId) return
    let closed = false
    let es: EventSource | null = null
    let flushT = 0, pollT = 0, lastFlush = 0
    const timers: number[] = []
    let buf: PtahEvent[] = []
    let seen = cur.current.lastSeq // keeps counting across resumes
    let terminal = false

    const flush = () => {
      flushT = 0
      lastFlush = performance.now()
      if (!buf.length) return
      const batch = buf
      buf = []
      const next = applyEvents(cur.current, batch)
      if (next !== cur.current) { cur.current = next; setView(next) }
    }
    const schedule = (now: boolean) => {
      if (now) { if (flushT) window.clearTimeout(flushT); flush(); return }
      if (flushT) return
      flushT = window.setTimeout(flush, Math.max(0, FLUSH_MS - (performance.now() - lastFlush)))
    }
    const push = (ev: PtahEvent) => {
      if (typeof ev?.seq !== 'number' || ev.seq <= seen) return
      seen = ev.seq
      buf.push(ev)
      if (ev.type === 'result' || ev.type === 'error') { terminal = true; schedule(true); closeStream() } else schedule(false)
    }
    const closeStream = () => { es?.close(); es = null; if (pollT) { window.clearTimeout(pollT); pollT = 0 } }

    // ---- development-only replay of a recorded stream (never in production builds)
    if (import.meta.env.DEV && mock) {
      let cancelled = false
      fetch(mock.url).then((r) => r.json()).then((raw: PtahEvent[] | { events: PtahEvent[]; resume?: PtahEvent[] }) => {
        if (cancelled) return
        const list = Array.isArray(raw) ? raw : raw.events
        if (!Array.isArray(raw)) mockExtra.current = raw.resume ?? null
        const start = performance.now()
        const t0 = (list as (PtahEvent & { t?: number })[])[0]?.t ?? 0
        let n = seen
        const base = Date.now() / 1000
        const useResume = epoch > 0 && mockExtra.current
        const src = (useResume ? mockExtra.current! : list) as (PtahEvent & { t?: number })[]
        setConnection('live')
        for (const e of src) {
          const at = (((e.t ?? 0) - (useResume ? 0 : t0)) * 1000) / mock.speed
          timers.push(window.setTimeout(() => push({ ...e, seq: ++n, ts: base + (performance.now() - start) / 1000 }), Math.max(0, at)))
        }
      }).catch(() => setConnection('polling'))
      return () => { cancelled = true; timers.forEach(clearTimeout); if (flushT) clearTimeout(flushT) }
    }

    // ---- polling backstop: only runs while the stream is down
    const poll = async () => {
      pollT = 0
      if (closed || terminal) return
      try {
        const s = await getJob(jobId)
        if (closed) return
        if (s.status === 'done' || s.status === 'error' || s.status === 'cancelled' || s.status === 'interrupted') {
          terminal = true
          const next = viewFromStatus(cur.current, s)
          cur.current = next; setView(next)
          closeStream()
          return
        }
        if (!es || es.readyState === EventSource.CLOSED) {
          const next = viewFromStatus(cur.current, s)
          cur.current = next; setView(next)
        }
      } catch (e) {
        if ((e as { status?: number })?.status === 404) {
          terminal = true
          const next: JobView = { ...cur.current, phase: 'error', error: { code: 'gone', message: 'This session has ended and its files are gone.', retryable: false } }
          cur.current = next; setView(next)
          closeStream()
          return
        }
      }
      if (!closed && !terminal) pollT = window.setTimeout(poll, POLL_MS)
    }

    const open = () => {
      const url = apiUrl(`/api/jobs/${jobId}/events`) + (seen > 0 ? `?last_event_id=${seen}` : '')
      es = new EventSource(url)
      es.onopen = () => { setConnection('live'); if (pollT) { window.clearTimeout(pollT); pollT = 0 } }
      es.onmessage = (m) => {
        try { push(JSON.parse(m.data) as PtahEvent) } catch { /* ignore a malformed frame */ }
      }
      es.onerror = () => {
        if (closed || terminal) return
        // CONNECTING: the browser retries (with Last-Event-ID) by itself; CLOSED: it gave up (404, 204, bad content type)
        setConnection(es && es.readyState === EventSource.CLOSED ? 'polling' : 'reconnecting')
        if (!pollT) pollT = window.setTimeout(poll, es && es.readyState === EventSource.CLOSED ? 0 : POLL_MS)
      }
    }
    if (typeof EventSource === 'undefined') { setConnection('polling'); pollT = window.setTimeout(poll, 0) } else open()

    return () => {
      closed = true
      closeStream()
      if (flushT) window.clearTimeout(flushT)
      timers.forEach(clearTimeout)
    }
  }, [jobId, mock, epoch])

  const resume = useCallback(async () => {
    if (!jobId) return
    setResuming(true); setResumeError(null)
    try {
      if (!(import.meta.env.DEV && mock)) await resumeJob(jobId)
      setConnection('connecting')
      setEpoch((e) => e + 1)
    } catch (e) {
      setResumeError((e as Error)?.message || 'We could not restart this run.')
    } finally { setResuming(false) }
  }, [jobId, mock])

  return { view, connection, resume, resuming, resumeError }
}

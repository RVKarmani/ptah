/** Pipeline events (docs/contracts.md "Pipeline events") folded into one small view model for the progress screen. */

export type Service = 'qloo' | 'llm' | 'serpapi' | 'compute'
export type EventType = 'stage_start' | 'stage_progress' | 'stage_end' | 'activity_start' | 'activity_end' | 'log' | 'result' | 'error'
export interface PtahEvent {
  job_id?: string; seq: number; ts?: number; type: EventType
  stage?: string; progress?: number; stage_progress?: number
  service?: Service; id?: string; label?: string; detail?: Record<string, unknown>; duration_ms?: number
}

export type StageState = 'pending' | 'active' | 'done' | 'skipped'
export interface StageView { id: string; label: string; weight: number; state: StageState; progress: number; durationMs?: number; cached?: boolean }
export interface ServiceView { active: number; total: number; failed: number; label: string | null }
export interface LogLine { seq: number; t: number; kind: 'stage' | 'activity' | 'log' | 'warn' | 'error'; service?: Service; text: string; ms?: number; ok?: boolean }
export interface JobError { code: string; message: string; hint?: string; retryable: boolean; stage?: string; technical?: string }
export interface JobResult { partial: boolean; warnings: string[]; skipped: string[]; items?: number; products?: number }

export type Phase = 'connecting' | 'running' | 'done' | 'error'
export interface JobView {
  phase: Phase
  lastSeq: number
  /** overall 0-1, never decreases */
  overall: number
  stages: StageView[]
  current: string | null
  /** what the agent is doing right now (stage title, then progress labels) */
  caption: string
  /** the most recent request in flight, for the small "now" line */
  now: { service: Service; label: string } | null
  services: Record<Service, ServiceView>
  log: LogLine[]
  /** activity id -> service for requests in flight (pairs start/end, ignores replays) */
  open: Record<string, Service>
  warnings: string[]
  result: JobResult | null
  error: JobError | null
  startTs: number | null
  elapsedS: number
}

export const SERVICES: Service[] = ['qloo', 'llm', 'serpapi', 'compute']
/** Fallback plan (names from the contract) for streams that omit `detail.plan`. */
const DEFAULT_PLAN: { stage: string; label: string; weight: number }[] = [
  { stage: 'ingest', label: 'Reading your posts', weight: 5 },
  { stage: 'taste', label: 'Understanding your taste', weight: 45 },
  { stage: 'brief', label: 'Asking Qloo what people like you love', weight: 15 },
  { stage: 'design', label: 'Designing your room', weight: 15 },
  { stage: 'products', label: 'Finding real pieces', weight: 12 },
  { stage: 'layout', label: 'Arranging the furniture', weight: 5 },
  { stage: 'lighting', label: 'Planning the lighting', weight: 3 },
]

export function initialView(): JobView {
  const z = (): ServiceView => ({ active: 0, total: 0, failed: 0, label: null })
  return {
    phase: 'connecting', lastSeq: 0, overall: 0, stages: [], current: null, caption: 'Getting started', now: null,
    services: { qloo: z(), llm: z(), serpapi: z(), compute: z() }, log: [], open: {}, warnings: [], result: null, error: null, startTs: null, elapsedS: 0,
  }
}

const str = (v: unknown, max = 200): string => (typeof v === 'string' ? v.slice(0, max) : '')
const MAX_LOG = 400

type PlanItem = { stage: string; label: string; weight?: number }
function planFrom(detail: Record<string, unknown> | undefined): PlanItem[] | null {
  const p = detail?.plan
  if (!Array.isArray(p)) return null
  const out = p.filter((x): x is PlanItem => !!x && typeof x === 'object' && typeof (x as PlanItem).stage === 'string')
  return out.length ? out : null
}

function makeStages(plan: PlanItem[]): StageView[] {
  return plan.map((p) => ({ id: p.stage, label: str(p.label) || p.stage, weight: p.weight ?? 1, state: 'pending' as StageState, progress: 0 }))
}

/** Fold a batch of events into a new view. Events at or below `lastSeq` are ignored (reconnects re-deliver). Pure: `prev` is not mutated. */
export function applyEvents(prev: JobView, evs: PtahEvent[]): JobView {
  let v: JobView | null = null
  for (const ev of evs) {
    if (!ev || typeof ev.seq !== 'number') continue
    const base: JobView = v ?? prev
    if (ev.seq <= base.lastSeq) continue
    if (!v) v = { ...prev, stages: prev.stages.map((s) => ({ ...s })), services: { qloo: { ...prev.services.qloo }, llm: { ...prev.services.llm }, serpapi: { ...prev.services.serpapi }, compute: { ...prev.services.compute } }, log: prev.log.slice(), open: { ...prev.open }, warnings: prev.warnings.slice() }
    reduce(v, ev)
  }
  return v ?? prev
}

function reduce(v: JobView, ev: PtahEvent): void {
  v.lastSeq = ev.seq
  if (typeof ev.ts === 'number') {
    if (v.startTs === null) v.startTs = ev.ts
    v.elapsedS = Math.max(v.elapsedS, ev.ts - v.startTs)
  }
  if (typeof ev.progress === 'number') v.overall = Math.max(v.overall, Math.min(1, ev.progress))
  const label = str(ev.label)
  const t = v.elapsedS
  const push = (l: Omit<LogLine, 'seq' | 't'>) => { v.log.push({ seq: ev.seq, t, ...l }); if (v.log.length > MAX_LOG) v.log.splice(0, v.log.length - MAX_LOG) }
  // a new event after an error means the job was resumed
  if (v.phase === 'error' && ev.type !== 'error') { v.phase = 'running'; v.error = null; v.result = null }
  if (v.phase === 'connecting' && ev.type !== 'error' && ev.type !== 'result') v.phase = 'running'
  const stage = (id?: string): StageView | undefined => {
    if (!id) return undefined
    let s = v.stages.find((x) => x.id === id)
    if (!s) { s = { id, label: id, weight: 1, state: 'pending', progress: 0 }; v.stages.push(s) }
    return s
  }
  switch (ev.type) {
    case 'stage_start': {
      if (v.stages.length === 0) {
        const first = DEFAULT_PLAN.findIndex((p) => p.stage === ev.stage)
        v.stages = makeStages(planFrom(ev.detail) ?? (first >= 0 ? DEFAULT_PLAN.slice(first) : []))
      }
      const s = stage(ev.stage)
      if (s) {
        if (label) s.label = label
        s.state = 'active'; s.progress = 0
        // earlier stages that never reported an end (e.g. skipped silently) count as done
        const i = v.stages.indexOf(s)
        for (let k = 0; k < i; k++) if (v.stages[k].state === 'pending' || v.stages[k].state === 'active') v.stages[k].state = 'done'
      }
      v.current = ev.stage ?? v.current
      if (label) v.caption = label
      push({ kind: 'stage', text: label || ev.stage || 'Next step' })
      break
    }
    case 'stage_progress': {
      const s = stage(ev.stage ?? v.current ?? undefined)
      if (s && typeof ev.stage_progress === 'number') s.progress = Math.max(s.progress, Math.min(1, ev.stage_progress))
      if (label) v.caption = label
      break
    }
    case 'stage_end': {
      const s = stage(ev.stage)
      if (s) {
        s.progress = 1
        s.state = ev.detail?.skipped ? 'skipped' : 'done'
        if (typeof ev.duration_ms === 'number') s.durationMs = ev.duration_ms
        if (ev.detail?.cached) s.cached = true
      }
      push({ kind: 'stage', text: ((s?.label || label) + (s?.state === 'skipped' ? ' (skipped)' : s?.cached ? ' (from earlier)' : ' done')).trim(), ms: ev.duration_ms })
      break
    }
    case 'activity_start': {
      const svc = ev.service
      if (!svc || !v.services[svc]) break
      if (ev.id && v.open[ev.id]) break
      if (ev.id) v.open[ev.id] = svc
      const sv = v.services[svc]
      sv.active += 1; sv.total += 1; sv.label = label || sv.label
      v.now = { service: svc, label }
      break
    }
    case 'activity_end': {
      const svc = ev.service
      if (!svc || !v.services[svc]) break
      if (ev.id) { if (!v.open[ev.id]) break; delete v.open[ev.id] }
      const sv = v.services[svc]
      sv.active = Math.max(0, sv.active - 1)
      const ok = ev.detail?.ok !== false
      if (!ok) sv.failed += 1
      if (sv.active === 0 && v.now?.service === svc) v.now = null
      if (label) push({ kind: 'activity', service: svc, text: label, ms: ev.duration_ms, ok })
      break
    }
    case 'log': {
      const warn = ev.detail?.warning === true
      if (warn && label) v.warnings.push(label)
      if (label) push({ kind: warn ? 'warn' : 'log', text: label })
      break
    }
    case 'result': {
      const d = ev.detail ?? {}
      v.phase = 'done'; v.overall = 1; v.current = null
      v.now = null
      for (const s of v.stages) if (s.state === 'active' || s.state === 'pending') s.state = 'done'
      for (const k of SERVICES) v.services[k].active = 0
      v.result = {
        partial: d.partial === true,
        warnings: Array.isArray(d.warnings) ? d.warnings.map((w) => str(w)).filter(Boolean) : [],
        skipped: Array.isArray(d.skipped) ? d.skipped.map((w) => str(w)).filter(Boolean) : [],
        items: typeof d.items === 'number' ? d.items : undefined,
        products: typeof d.products === 'number' ? d.products : undefined,
      }
      v.caption = label || 'Your room is ready'
      push({ kind: 'stage', text: v.caption })
      break
    }
    case 'error': {
      const d = ev.detail ?? {}
      v.phase = 'error'; v.now = null
      for (const k of SERVICES) v.services[k].active = 0
      v.open = {}
      v.error = {
        code: str(d.code, 40) || 'internal', message: label || 'Something went wrong.', hint: str(d.hint) || undefined,
        retryable: d.retryable === true, stage: str(d.stage, 40) || undefined, technical: str(d.technical, 600) || undefined,
      }
      push({ kind: 'error', text: v.error.message })
      break
    }
  }
}

/** Synthesise a terminal view from `GET /api/jobs/{id}` when the stream is unavailable (polling fallback). */
export function viewFromStatus(prev: JobView, s: { status: string; progress?: number; stage?: string | null; plan?: { stage: string; label: string; weight?: number }[] | null; error?: { code?: string; message?: string; hint?: string; retryable?: boolean } | null; result?: Record<string, unknown> | null; partial?: boolean }): JobView {
  const v: JobView = { ...prev, stages: prev.stages.map((x) => ({ ...x })) }
  if (!v.stages.length && s.plan?.length) v.stages = makeStages(s.plan)
  if (typeof s.progress === 'number') v.overall = Math.max(v.overall, s.progress)
  if (s.stage && s.stage !== v.current) {
    v.current = s.stage
    const i = v.stages.findIndex((x) => x.id === s.stage)
    v.stages.forEach((x, k) => { if (i >= 0) x.state = k < i ? (x.state === 'skipped' ? 'skipped' : 'done') : k === i ? 'active' : x.state })
    if (i >= 0) v.caption = v.stages[i].label
  }
  if (v.phase === 'connecting' && (s.status === 'running' || s.status === 'queued')) v.phase = 'running'
  if (s.status === 'done') {
    v.phase = 'done'; v.overall = 1; v.stages.forEach((x) => { if (x.state !== 'skipped') x.state = 'done' })
    v.result = { partial: s.partial === true, warnings: [], skipped: [] }
    v.caption = 'Your room is ready'
  } else if (s.status === 'error' || s.status === 'interrupted' || s.status === 'cancelled') {
    v.phase = 'error'
    const e = s.error ?? {}
    v.error = {
      code: s.status === 'cancelled' ? 'cancelled' : e.code ?? 'internal', message: e.message ?? (s.status === 'cancelled' ? 'This run was cancelled.' : 'The run stopped before it finished.'),
      hint: e.hint, retryable: e.retryable ?? s.status === 'interrupted', stage: undefined,
    }
  }
  return v
}

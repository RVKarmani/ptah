/** Thin client for the Ptah backend (README "Backend"). Same origin by default; `?api=http://localhost:8000` points the dev server at a backend. */

export type Health = { ok: boolean; qloo: boolean; llm: boolean; demo: boolean; active_jobs?: number; max_upload_mb: number }
export type SampleRoom = { id: string; name: string; width_cm: number; depth_cm: number }
export type QlooHit = { qloo_id: string; name: string; type: string; tags?: string[]; description?: string | null }
export type Love = { name: string; type?: string; qloo_id?: string }
export type JobCreated = { job_id: string; events_url: string; viewer_url: string; viewer_base?: string }
export type JobStatus = {
  job_id: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'interrupted'
  progress?: number
  stage?: string | null
  plan?: { stage: string; label: string; weight?: number }[] | null
  queue_position?: number
  error?: { code?: string; message?: string; hint?: string; retryable?: boolean } | null
  result?: Record<string, unknown> | null
  partial?: boolean
  kind?: string
}

export class ApiError extends Error {
  code: string
  status: number
  hint?: string
  constructor(status: number, code: string, message: string, hint?: string) {
    super(message)
    this.status = status
    this.code = code
    this.hint = hint
  }
}

let base: string | null = null
export function apiBase(): string {
  if (base !== null) return base
  let b = ''
  try {
    const q = new URLSearchParams(window.location.search).get('api')
    if (q) { b = q.replace(/\/+$/, ''); sessionStorage.setItem('ptah.api', b) } else b = sessionStorage.getItem('ptah.api') ?? ''
  } catch { /* storage blocked: same origin */ }
  base = b
  return b
}
export const apiUrl = (path: string) => apiBase() + path
/** Absolute URL of a finished job's bundle: what `?data=` expects. */
export const viewerBase = (jobId: string) => new URL(apiUrl(`/api/jobs/${jobId}/viewer/`), document.baseURI).href

async function parseError(r: Response): Promise<ApiError> {
  let code = 'http_' + r.status, message = r.status === 413 ? 'That file is too large.' : 'Something went wrong. Please try again.', hint: string | undefined
  try {
    const j = await r.json()
    if (j?.error) { code = j.error.code ?? code; message = j.error.message ?? message; hint = j.error.hint }
  } catch { /* not JSON */ }
  return new ApiError(r.status, code, message, hint)
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let r: Response
  try { r = await fetch(apiUrl(path), { signal, headers: { Accept: 'application/json' } }) } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError(0, 'offline', 'Ptah could not reach its server.')
  }
  if (!r.ok) throw await parseError(r)
  return (await r.json()) as T
}

export async function sendJson<T>(method: 'POST' | 'DELETE', path: string): Promise<T> {
  let r: Response
  try { r = await fetch(apiUrl(path), { method, headers: { Accept: 'application/json' } }) } catch {
    throw new ApiError(0, 'offline', 'Ptah could not reach its server.')
  }
  if (!r.ok) throw await parseError(r)
  return (await r.json().catch(() => ({}))) as T
}

export const getHealth = (signal?: AbortSignal) => getJson<Health>('/api/health', signal)
export const getRooms = (signal?: AbortSignal) => getJson<SampleRoom[]>('/api/rooms', signal)
export const searchQloo = (q: string, signal?: AbortSignal) => getJson<QlooHit[]>(`/api/qloo/search?q=${encodeURIComponent(q)}&take=6`, signal)
export const startDemo = (seconds?: number) => sendJson<JobCreated>('POST', '/api/demo' + (seconds ? `?seconds=${seconds}` : ''))
export const getJob = (id: string, signal?: AbortSignal) => getJson<JobStatus>(`/api/jobs/${id}`, signal)
export const resumeJob = (id: string) => sendJson<{ job_id: string }>('POST', `/api/jobs/${id}/resume`)
export const deleteJob = (id: string) => sendJson<{ job_id: string; status: string }>('DELETE', `/api/jobs/${id}`)

/** POST /api/jobs with upload progress (fetch cannot report it). `abort()` cancels the upload. */
export function createJob(form: FormData, onProgress: (frac: number) => void): { promise: Promise<JobCreated>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const promise = new Promise<JobCreated>((resolve, reject) => {
    xhr.open('POST', apiUrl('/api/jobs'))
    xhr.responseType = 'json'
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total) }
    xhr.onload = () => {
      const j = xhr.response
      if (xhr.status >= 200 && xhr.status < 300 && j?.job_id) return resolve(j as JobCreated)
      const e = j?.error
      reject(new ApiError(xhr.status, e?.code ?? 'http_' + xhr.status, e?.message ?? (xhr.status === 413 ? 'That file is too large for this server.' : 'The upload did not work. Please try again.'), e?.hint))
    }
    xhr.onerror = () => reject(new ApiError(0, 'offline', 'Ptah could not reach its server.'))
    xhr.onabort = () => reject(new ApiError(0, 'aborted', 'Upload cancelled.'))
    xhr.send(form)
  })
  return { promise, abort: () => xhr.abort() }
}

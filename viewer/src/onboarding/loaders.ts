// Lazy chunks: the form and progress screens are small; the 3D viewer (three.js) is the heavy one, so it is fetched in the background.
export const loadViewer = () => import('../App')
export const loadFlow = () => import('./Flow')
export const loadProgress = () => import('./Progress')

let warmed = false
/** Fetch the next screens ahead of time (skipped on data-saver connections). */
export function warmUp(level: 'form' | 'viewer'): void {
  const conn = (navigator as unknown as { connection?: { saveData?: boolean } }).connection
  if (conn?.saveData) return
  if (level === 'form') { void loadFlow(); void loadProgress() } else if (!warmed) { warmed = true; void loadViewer() }
}

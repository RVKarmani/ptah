import { useEffect, useRef, useState, type ReactNode } from 'react'
import { SCENE_DESC } from '../LightingUI'
import type { SceneName } from '../lights'

/**
 * One line of the plate's caption strip (outside the 3D view): the usual caption, replaced for a moment by what
 * the chosen lighting scene means, a few seconds after it changes.
 */
export function SceneNote({ scene, children }: { scene: SceneName; children?: ReactNode }) {
  const [note, setNote] = useState<SceneName | null>(null)
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    if (scene === 'Custom') return
    setNote(scene)
    const t = window.setTimeout(() => setNote(null), 2600)
    return () => window.clearTimeout(t)
  }, [scene])
  return (
    <div className={'cap-line' + (note ? ' noting' : '')} aria-live="polite">
      <span className="cap-idle">{children}</span>
      <span className="scene-note">{note ? <><b>{note}</b> {SCENE_DESC[note]}</> : null}</span>
    </div>
  )
}

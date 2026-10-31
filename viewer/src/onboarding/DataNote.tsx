import { useEffect, useRef } from 'react'
import { Icon } from '../ui'

/** "How Ptah uses your data": a note pinned to the sheet. Closes on Esc, on the button, or on a click outside. */
export function DataNote({ onClose }: { onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const prev = useRef<Element | null>(null)
  useEffect(() => {
    prev.current = document.activeElement
    ref.current?.querySelector<HTMLElement>('button')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose() }
      if (e.key === 'Tab') {
        const f = ref.current?.querySelectorAll<HTMLElement>('button, a[href]')
        if (!f || !f.length) return
        const first = f[0], lastEl = f[f.length - 1]
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastEl.focus() }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); first.focus() }
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => { window.removeEventListener('keydown', onKey, true); (prev.current as HTMLElement | null)?.focus?.() }
  }, [onClose])
  return (
    <div className="modal-scrim" onPointerDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="note data-note" role="dialog" aria-modal="true" aria-labelledby="dn-h" ref={ref}>
        <h2 id="dn-h">How Ptah uses your data</h2>
        <ul>
          <li>Your files are used only to design your room.</li>
          <li>From an Instagram export, Ptah reads only your saved posts, likes and saved music. The ZIP is deleted as soon as it has been read.</li>
          <li>You can delete everything at any time: while Ptah is working, or from the menu of your finished room.</li>
        </ul>
        <button className="btn primary" onClick={onClose}><Icon name="check" size={16} /> Got it</button>
      </div>
    </div>
  )
}

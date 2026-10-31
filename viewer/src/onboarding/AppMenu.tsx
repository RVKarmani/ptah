import { useEffect, useRef, useState } from 'react'
import { Icon } from '../ui'

/** Top-bar menu of the 3D view: start over, delete my data (two steps, so it is never an accident). */
export function AppMenu({ onExit, onDelete }: { onExit: () => void; onDelete?: () => void | Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (!ref.current?.contains(e.target as Node)) { setOpen(false); setConfirm(false) } }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); setConfirm(false); (ref.current?.querySelector('.menu-btn') as HTMLElement | null)?.focus() } }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('pointerdown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  return (
    <div className="menu tb-cell" ref={ref}>
      <button className="tb-link menu-btn" aria-haspopup="true" aria-expanded={open} aria-label="Menu" onClick={() => { setOpen(!open); setConfirm(false) }}>
        <Icon name="more" size={22} />
      </button>
      {open && (
        <div className="menu-pop" role="menu">
          <button role="menuitem" className="menu-item" onClick={onExit}><Icon name="plus" size={18} /> Design another room</button>
          {onDelete && !confirm && <button role="menuitem" className="menu-item" onClick={() => setConfirm(true)}><Icon name="trash" size={18} /> Delete my data</button>}
          {onDelete && confirm && (
            <div className="menu-confirm" role="group" aria-label="Confirm deleting your data">
              <p>This removes your room and everything Ptah kept for it. It cannot be undone.</p>
              <div className="row-btns">
                <button className="btn sm" onClick={() => setConfirm(false)}>Keep it</button>
                <button className="btn sm danger" disabled={busy} onClick={async () => { setBusy(true); try { await onDelete() } finally { setBusy(false) } }}>{busy ? 'Deleting' : 'Delete'}</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

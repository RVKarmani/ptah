import { useMemo, type MutableRefObject } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import type { Item } from './types'

/** Balloons on the room: the first nine rows of the schedule, plus whichever piece is hovered or chosen. */
const MAX_BALLOONS = 9

export interface BalloonPoint { id: string; x: number; y: number; z: number }

/**
 * Inside the canvas: every frame, move each balloon (plain DOM, drawn by <Balloons> beside the canvas) to where its piece is on screen.
 * No per-frame React work, and no second React root per label.
 */
export function Projector({ points, els }: { points: BalloonPoint[]; els: MutableRefObject<Map<string, HTMLElement>> }) {
  const camera = useThree((s) => s.camera)
  const size = useThree((s) => s.size)
  const v = useMemo(() => new THREE.Vector3(), [])
  useFrame(() => {
    if (!els.current.size) return
    camera.updateMatrixWorld()
    for (const p of points) {
      const el = els.current.get(p.id)
      if (!el) continue
      v.set(p.x, p.y, p.z).project(camera)
      const inView = v.z < 1 && Math.abs(v.x) < 1.05 && Math.abs(v.y) < 1.05
      el.style.visibility = inView ? 'visible' : 'hidden'
      if (inView) el.style.transform = `translate(${((v.x * 0.5 + 0.5) * size.width).toFixed(1)}px, ${((-v.y * 0.5 + 0.5) * size.height).toFixed(1)}px)`
    }
  })
  return null
}

/** The numbered balloons, and the tag with its leader line for the piece you point at or choose. */
export function Balloons({ items, points, numbers, hoverId, selectedId, els, onHover, onSelect }: {
  items: Map<string, Item>; points: BalloonPoint[]; numbers: Map<string, string>; hoverId: string | null; selectedId: string | null
  els: MutableRefObject<Map<string, HTMLElement>>; onHover: (id: string | null) => void; onSelect: (id: string | null) => void
}) {
  return (
    <div className="balloons">
      {points.map((p) => {
        const item = items.get(p.id)
        if (!item) return null
        const num = numbers.get(p.id)
        const selected = selectedId === p.id
        const hovered = hoverId === p.id
        const top = !!num && !num.includes('.')
        const balloon = top && (Number(num) <= MAX_BALLOONS || selected || hovered)
        if (!balloon && !selected && !hovered) return null
        const store = item.product?.store?.trim()
        const price = item.owned ? null : item.product?.price || item.price || null
        const sub = item.owned ? 'Yours' : [store, price].filter(Boolean).join(', ') || null
        return (
          <div key={p.id} className="bal" style={{ zIndex: selected ? 3 : hovered ? 2 : 1 }} ref={(el) => { if (el) els.current.set(p.id, el); else els.current.delete(p.id) }}>
            {balloon ? (
              <button
                type="button"
                className={'m' + (selected ? ' sel' : hovered ? ' hot' : selectedId ? ' dim' : '')}
                aria-label={`${num}, ${item.name}`}
                tabIndex={-1}
                onClick={() => onSelect(p.id)}
                onPointerEnter={() => onHover(p.id)}
                onPointerLeave={() => onHover(null)}
              >{num}</button>
            ) : <i className="pt" aria-hidden />}
            {(selected || hovered) && (
              <div className="tag-wrap"><div className="tag"><b>{item.name}</b>{sub}</div><i className="lead" /></div>
            )}
          </div>
        )
      })}
    </div>
  )
}

import type { Room } from './types'
import { FLOORS } from './floors'
import { Switch } from './ui'

export interface RoomPanelProps {
  room: Room
  floorId: string
  setFloor: (id: string) => void
  wallColor: string
  previewPaint: boolean
  setPreviewPaint: (v: boolean) => void
}

export function RoomPanel(p: RoomPanelProps) {
  const fin = p.room.finishes ?? {}
  const paint = fin.wall_paint
  const canPaint = p.room.can_paint !== false
  const baseWall = fin.wall_color || '#f7f3ec'
  return (
    <>
      <h2>Room</h2>
      <section className="section">
        <h3>Floor</h3>
        <div className="floors" role="group" aria-label="Floor finish">
          {FLOORS.map((f) => (
            <button key={f.id} className="floor-btn" aria-pressed={p.floorId === f.id} onClick={() => p.setFloor(f.id)} title={f.label}>
              <span className={`fsw ${f.id}`} style={{ background: f.swatch }} />
              <small>{f.label}</small>
            </button>
          ))}
        </div>
      </section>
      <section className="section">
        <h3>Walls</h3>
        <div className="wall-row">
          <span className="swatch lg" style={{ background: p.wallColor }} title={p.wallColor} />
          <div>
            <b>{p.previewPaint && paint?.hex ? paint.name ?? 'Suggested paint' : 'Current walls'}</b>
            <span className="muted">{p.wallColor.toUpperCase()}</span>
          </div>
        </div>
        {!canPaint ? (
          <div className="note-card">
            <b>Walls stay as they are</b>
            <span>Painting isn't possible here (rental, tiled or listed surface). Bring colour in through accessories, textiles and lighting instead.</span>
          </div>
        ) : paint && (paint.hex || paint.name) ? (
          <div className="paint-card">
            <span className="swatch" style={{ background: paint.hex ?? baseWall }} />
            <div className="grow">
              <span className="muted">Suggested paint</span>
              <b>{paint.name ?? paint.hex}</b>
              <span className="muted">{[paint.brand, paint.hex?.toUpperCase(), paint.price].filter(Boolean).join(' · ')}</span>
            </div>
            {paint.url && <a className="btn sm" href={paint.url} target="_blank" rel="noreferrer noopener">Buy</a>}
          </div>
        ) : (
          <p className="muted">No paint suggestion yet.</p>
        )}
        {canPaint && paint?.hex && (
          <div className="toggle-row">
            <span>Preview the suggested paint</span>
            <Switch on={p.previewPaint} onChange={p.setPreviewPaint} label="Preview the suggested paint on the walls" />
          </div>
        )}
      </section>
    </>
  )
}

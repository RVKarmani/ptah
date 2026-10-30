import type { Room } from './types'
import { FLOORS } from './floors'
import type { Quality } from './Viewer'
import { Switch } from './LightingUI'

export interface RoomPanelProps {
  room: Room
  floorId: string
  setFloor: (id: string) => void
  wallColor: string
  previewPaint: boolean
  setPreviewPaint: (v: boolean) => void
  quality: Quality
  setQuality: (q: Quality) => void
}

export function RoomPanel(p: RoomPanelProps) {
  const fin = p.room.finishes ?? {}
  const paint = fin.wall_paint
  const canPaint = p.room.can_paint !== false
  const baseWall = fin.wall_color || '#f7f3ec'
  return (
    <>
      <h2>Room finishes</h2>
      <section>
        <h3>Floor</h3>
        <div className="floors">
          {FLOORS.map((f) => (
            <button key={f.id} className={p.floorId === f.id ? 'floorbtn on' : 'floorbtn'} onClick={() => p.setFloor(f.id)} title={f.label}>
              <span className={`fsw ${f.id}`} style={{ background: f.swatch }} />
              <small>{f.label}</small>
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3>Walls</h3>
        <div className="wallrow">
          <span className="swatch lg" style={{ background: p.wallColor }} title={p.wallColor} />
          <div>
            <b>{p.previewPaint && paint?.hex ? paint.name ?? 'Suggested paint' : 'Current walls'}</b>
            <small className="muted">{p.wallColor.toUpperCase()}</small>
          </div>
        </div>
        {!canPaint ? (
          <div className="cantpaint">
            <b>Can't paint here</b>
            <span>Walls stay as they are (rental, tiled or listed surface). Look at colour through accessories, textiles and lighting instead.</span>
          </div>
        ) : paint && (paint.hex || paint.name) ? (
          <div className="paintcard">
            <span className="swatch" style={{ background: paint.hex ?? baseWall }} />
            <div className="grow">
              <small className="muted">Suggested paint</small>
              <b>{paint.name ?? paint.hex}</b>
              <small className="muted">{[paint.brand, paint.hex?.toUpperCase(), paint.price].filter(Boolean).join(' · ')}</small>
            </div>
            {paint.url && <a className="mini" href={paint.url} target="_blank" rel="noreferrer noopener">Buy ↗</a>}
          </div>
        ) : (
          <p className="muted">No paint suggestion yet.</p>
        )}
        {canPaint && paint?.hex && (
          <label className="heat-toggle">
            <Switch on={p.previewPaint} onChange={p.setPreviewPaint} label="Preview suggested paint" />
            <span>Preview suggested paint on the walls</span>
          </label>
        )}
      </section>
      <section>
        <h3>Render quality</h3>
        <label className="heat-toggle"><Switch on={p.quality.ao} onChange={(v) => p.setQuality({ ...p.quality, ao: v })} label="Ambient occlusion" /><span>Ambient occlusion</span></label>
        <label className="heat-toggle"><Switch on={p.quality.bloom} onChange={(v) => p.setQuality({ ...p.quality, bloom: v })} label="Bloom" /><span>Light glow (bloom)</span></label>
        <label className="heat-toggle"><Switch on={p.quality.tone === 'agx'} onChange={(v) => p.setQuality({ ...p.quality, tone: v ? 'agx' : 'aces' })} label="AgX tone mapping" /><span>AgX tone mapping <small className="muted">(off = ACES)</small></span></label>
      </section>
    </>
  )
}

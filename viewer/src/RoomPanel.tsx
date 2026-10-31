import type { Room } from './types'
import { FLOORS } from './floors'
import { Icon } from './ui'
import { wallChoices, type WallSel } from './walls'

export interface RoomPanelProps {
  room: Room
  floorId: string
  setFloor: (id: string) => void
  wall: WallSel
  wallHex: string
  setWall: (w: WallSel) => void
}

export function RoomPanel(p: RoomPanelProps) {
  const canPaint = p.room.can_paint !== false
  const choices = wallChoices(p.room)
  const active = choices.find((c) => c.key === p.wall.sel) ?? (canPaint && /^#[0-9a-f]{6}$/i.test(p.wall.sel) ? null : choices[0])
  const isCustom = canPaint && !active
  const paint = active?.kind === 'designed' ? active.paint : undefined
  return (
    <>
      <h2>Room</h2>
      <section className="section">
        <h3 className="sec-h">Floor</h3>
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
        <h3 className="sec-h">Walls</h3>
        {!canPaint ? (
          <>
            <div className="wall-row">
              <span className="swatch lg" style={{ background: p.wallHex }} title={p.wallHex} />
              <div><b>Your walls</b><span className="muted">{p.wallHex.toUpperCase()}</span></div>
            </div>
            <div className="note-card">
              <b>Walls stay as they are</b>
              <span>Painting isn't possible here (rental, tiled or listed surface), so paint suggestions are hidden. Bring colour in through accessories, textiles and lighting instead.</span>
            </div>
          </>
        ) : (
          <>
            <div className="floors" role="group" aria-label="Wall colour">
              {choices.map((c) => (
                <button key={c.key} className={'floor-btn wall-btn' + (c.kind === 'designed' ? ' designed' : '')} aria-pressed={active?.key === c.key} onClick={() => p.setWall({ ...p.wall, sel: c.key })} title={c.label + ' ' + c.hex.toUpperCase()}>
                  <span className="fsw" style={{ background: c.hex }} />
                  {c.kind === 'designed' && <span className="wall-star" aria-hidden><Icon name="dividers" size={12} /></span>}
                  <small>{c.label}</small>
                </button>
              ))}
              <label className={'floor-btn wall-btn custom'} data-active={isCustom} title="Pick your own colour">
                <span className="fsw custom-sw" style={isCustom ? { background: p.wall.sel } : undefined}>{!isCustom && <Icon name="plus" size={16} />}</span>
                <small>Custom</small>
                <input
                  type="color"
                  className="color-input"
                  aria-label="Custom wall colour"
                  value={isCustom ? p.wall.sel : p.wall.custom}
                  onChange={(e) => p.setWall({ sel: e.target.value.toLowerCase(), custom: e.target.value.toLowerCase() })}
                />
              </label>
            </div>
            {active?.kind === 'designed' && <span className="badge designed wall-tag" style={{ alignSelf: 'flex-start' }}><Icon name="dividers" size={12} /> Designed for you</span>}
            <div className="paint-card">
              <span className="swatch" style={{ background: p.wallHex }} />
              <div className="grow">
                <b>{isCustom ? 'Your own colour' : active?.label ?? 'Your walls'}</b>
                <span className="muted">{[paint?.brand, p.wallHex.toUpperCase(), paint?.price].filter(Boolean).join(', ')}</span>
              </div>
              {paint?.url && <a className="btn sm" href={paint.url} target="_blank" rel="noreferrer noopener">Buy paint<span className="sr"> (opens in a new tab)</span></a>}
            </div>
          </>
        )}
      </section>
    </>
  )
}

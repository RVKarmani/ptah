import type { ReactNode } from 'react'
import type { Lighting, LightLayer, Item } from './types'
import { LAYERS, SCENES, fmtTime, type LayerState, type LayerStates, type LightSpec, type LightState, type LightStates, type SceneName, sceneKey, sunState } from './lights'
import { kelvinToRGB } from './color'
import { HEAT_GRADIENT, heatDomain, statusColor } from './Heatmap'

export const LAYER_LABEL: Record<LightLayer, string> = { ambient: 'Ambient', task: 'Task', accent: 'Accent' }
const KELVIN_GRADIENT = `linear-gradient(90deg, ${[1800, 2400, 3000, 4000, 5000, 6500].map((k) => { const [r, g, b] = kelvinToRGB(k); return `rgb(${r | 0},${g | 0},${b | 0})` }).join(', ')})`

export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={on ? 'sw on' : 'sw'} onClick={() => onChange(!on)}>
      <span />
    </button>
  )
}

/** Floating scene chips + time-of-day slider shown over the 3D view. */
export function Dock({ scene, onScene, timeH, onTime, hasLights }: { scene: SceneName; onScene: (s: SceneName) => void; timeH: number; onTime: (t: number) => void; hasLights: boolean }) {
  const sun = sunState(timeH)
  const icon = sun.alt > 4 ? '☀' : sun.alt > -6 ? '◐' : '☾'
  return (
    <div className="dock">
      <div className="scenes" role="group" aria-label="Lighting scenes">
        {SCENES.map((s) => (
          <button key={s} className={scene === s ? 'chipbtn on' : 'chipbtn'} onClick={() => onScene(s)} title={hasLights ? undefined : 'No fixtures placed yet'}>
            {s}
          </button>
        ))}
      </div>
      <label className="time">
        <span className="sunicon" aria-hidden>{icon}</span>
        <input type="range" min={0} max={24} step={0.05} value={timeH} onChange={(e) => onTime(Math.min(23.99, +e.target.value))} aria-label="Time of day" />
        <b>{fmtTime(timeH)}</b>
      </label>
    </div>
  )
}

export function LightRow({ spec, st, onChange, onHover, hot, onSelect }: { spec: LightSpec; st: LightState; onChange: (p: Partial<LightState>) => void; onHover: (id: string | null) => void; hot: boolean; onSelect?: () => void }) {
  const [r, g, b] = kelvinToRGB(st.k)
  return (
    <li className={hot ? 'lrow hot' : 'lrow'} onMouseEnter={() => onHover(spec.id)} onMouseLeave={() => onHover(null)}>
      <div className="lhead">
        <span className="ldot" style={{ background: st.on ? `rgb(${r | 0},${g | 0},${b | 0})` : '#d9d3c8', boxShadow: st.on ? `0 0 8px rgb(${r | 0},${g | 0},${b | 0})` : 'none' }} />
        {onSelect ? <button className="lname" onClick={onSelect}>{spec.item.name}</button> : <b className="lname">{spec.item.name}</b>}
        <span className={`chip layer ${spec.layer}`}>{LAYER_LABEL[spec.layer]}</span>
        <Switch on={st.on} onChange={(v) => onChange({ on: v })} label={`${spec.item.name} on/off`} />
      </div>
      <div className="lctl">
        <label title="Dimmer">
          <small>Dim</small>
          <input type="range" min={0.05} max={1} step={0.01} value={st.dim} disabled={!st.on || !spec.dimmable} onChange={(e) => onChange({ dim: +e.target.value })} />
          <em>{spec.dimmable ? Math.round(st.dim * 100) + '%' : 'fixed'}</em>
        </label>
        {spec.rgb ? (
          <label title="Coloured light">
            <small>Colour</small>
            <span className="rgbbar" /><em>RGB</em>
          </label>
        ) : (
          <label title="Colour temperature">
            <small>Warmth</small>
            <input className="kelvin" style={{ background: KELVIN_GRADIENT }} type="range" min={1800} max={6500} step={50} value={st.k} disabled={!st.on} onChange={(e) => onChange({ k: +e.target.value })} />
            <em>{st.k}K</em>
          </label>
        )}
      </div>
    </li>
  )
}

export interface LightingPanelProps {
  specs: LightSpec[]
  states: LightStates
  layers: LayerStates
  scene: SceneName
  setLight: (id: string, p: Partial<LightState>) => void
  setLayer: (layer: LightLayer, p: Partial<LayerState>) => void
  onHover: (id: string | null) => void
  hoverId: string | null
  onSelect: (id: string) => void
  lighting: Lighting | null
  showHeat: boolean
  setShowHeat: (v: boolean) => void
  itemsById: Map<string, Item>
}

export function LightingPanel(p: LightingPanelProps) {
  const { specs, states, lighting, layers } = p
  return (
    <>
      <h2>Lighting</h2>
      <p className="muted small">Scene: <b>{p.scene}</b>. Scenes are presets over the three layers below; open a layer to tune single fixtures.</p>
      {!specs.length ? (
        <p className="muted">No light fixtures placed yet. Lamps, pendants, sconces, LED strips and picture lights show up here, grouped by layer, with on/off, dimmer and colour temperature. The time-of-day slider below the room still moves the sun.</p>
      ) : (
        <div className="layergroups">
          {LAYERS.map((l) => {
            const inLayer = specs.filter((s) => s.layer === l)
            const ls = layers[l]
            return (
              <div key={l} className={`lgroup ${l}${ls.on ? ' on' : ''}`}>
                <div className="lg-head">
                  <span className="lg-title">
                    <i className={`ldot2 ${l}`} />
                    <b>{LAYER_LABEL[l]}</b>
                    <small>{inLayer.length ? `${inLayer.length} fixture${inLayer.length > 1 ? 's' : ''}` : 'none placed'}</small>
                  </span>
                  <Switch on={ls.on} onChange={(v) => p.setLayer(l, { on: v })} label={`${LAYER_LABEL[l]} layer on/off`} />
                </div>
                <div className="lg-dim">
                  <small>Layer dimmer</small>
                  <input type="range" min={0.05} max={1} step={0.01} value={ls.dim} disabled={!ls.on} onChange={(e) => p.setLayer(l, { dim: +e.target.value })} aria-label={`${LAYER_LABEL[l]} layer dimmer`} />
                  <em>{Math.round(ls.dim * 100)}%</em>
                </div>
                {inLayer.length > 0 && (
                  <details className="lg-fix">
                    <summary>Fixtures ({inLayer.length})</summary>
                    <ul className="list lights">
                      {inLayer.map((s) => (
                        <LightRow key={s.id} spec={s} st={states[s.id] ?? { on: true, dim: 1, k: s.kelvin }} onChange={(pp) => p.setLight(s.id, pp)} onHover={p.onHover} hot={p.hoverId === s.id} onSelect={() => p.onSelect(s.id)} />
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )
          })}
        </div>
      )}
      {lighting && <Analysis {...p} />}
    </>
  )
}

const ZONE_LABEL = (s?: string) => (s ? s.replace(/_/g, ' ') : '')

function Analysis({ lighting, showHeat, setShowHeat, itemsById, scene }: LightingPanelProps) {
  if (!lighting) return null
  const layers = lighting.layers ?? {}
  const domain = heatDomain(lighting)
  const issues = lighting.issues ?? []
  const zones = lighting.zones ?? []
  const focal = lighting.focal_points ?? []
  const name = (id: string) => itemsById.get(id)?.name ?? id
  const row = (key: string, label: ReactNode, right: ReactNode) => <li key={key} className="arow"><span>{label}</span>{right}</li>
  return (
    <section className="analysis">
      <h3>Lighting analysis<span className="chip tiny">{lighting.scenes?.[sceneKey(scene)] ? scene : lighting.mode ?? 'overall'}</span></h3>
      {lighting.lux?.length ? (
        <label className="heat-toggle">
          <Switch on={showHeat} onChange={setShowHeat} label="Show lux heatmap" />
          <span>Lux heatmap on the floor{lighting.plane_cm ? <small> at {lighting.plane_cm} cm</small> : null}</span>
        </label>
      ) : null}
      {showHeat && lighting.lux?.length ? (
        <div className="legend">
          <div className="bar" style={{ background: HEAT_GRADIENT }} />
          <div className="ticks"><span>0</span><span>{Math.round(domain / 4)}</span><span>{Math.round(domain / 2)}</span><span>{Math.round((domain * 3) / 4)}</span><span>{domain}+ lx</span></div>
        </div>
      ) : null}
      <div className="coverage">
        {(['ambient', 'task', 'accent'] as LightLayer[]).map((l) => (
          <span key={l} className={`cov ${layers[l] ? 'yes' : 'no'}`} title={layers[l] ? `${LAYER_LABEL[l]} light covers the room` : `${LAYER_LABEL[l]} layer missing`}>
            {layers[l] ? '✓' : '✕'} {LAYER_LABEL[l]}
          </span>
        ))}
      </div>
      {zones.length > 0 && (
        <>
          <h4>Zones</h4>
          <ul className="alist">
            {zones.map((z) => row(z.name,
              <><i className="sdot" style={{ background: statusColor(z.status) }} />{z.name.replace(':', ' · ')}</>,
              <span className="muted">{z.lux != null ? `${Math.round(z.lux)}` : '–'}{z.target != null ? ` / ${Math.round(z.target)} lx` : ' lx'} {z.status ? <em className="status" style={{ color: statusColor(z.status) }}>{ZONE_LABEL(z.status)}</em> : null}</span>,
            ))}
          </ul>
        </>
      )}
      {focal.length > 0 && (
        <>
          <h4>Focal points</h4>
          <ul className="alist">
            {focal.map((f) => row(f.item_id, name(f.item_id), <em className="status" style={{ color: f.accent_lit ? '#3f8a5a' : '#d98a1c' }}>{f.accent_lit ? 'accent lit' : 'unlit'}</em>))}
          </ul>
        </>
      )}
      <h4>Issues {issues.length ? `(${issues.length})` : ''}</h4>
      {issues.length ? (
        <ul className="issues">
          {issues.map((i, n) => (
            <li key={n}>
              <b>{(i.code ?? 'ISSUE').replace(/_/g, ' ')}</b>
              <span>{i.message}</span>
              {i.suggest?.category && <em>Suggest: {String(i.suggest.category).replace(/_/g, ' ')}{i.suggest.near ? ` near ${name(String(i.suggest.near))}` : ''}</em>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No lighting issues found.</p>
      )}
    </section>
  )
}

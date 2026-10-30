import { useId, useState, type ReactNode } from 'react'
import type { Lighting, LightLayer, Item } from './types'
import { LAYERS, SCENES, fmtTime, sunState, type LayerState, type LayerStates, type LightSpec, type LightState, type LightStates, type SceneName } from './lights'
import { kelvinToRGB } from './color'
import { HEAT_GRADIENT, heatDomain, statusColor } from './Heatmap'
import { Icon, Slider, Switch } from './ui'
import { zoneLabel } from './zones'

export const LAYER_LABEL: Record<LightLayer, string> = { ambient: 'Ambient', task: 'Task', accent: 'Accent' }
const LAYER_HINT: Record<LightLayer, string> = { ambient: 'Overall glow', task: 'For reading and tasks', accent: 'Mood and highlights' }
const LAYER_COVER: Record<LightLayer, string> = { ambient: 'Ambient glow', task: 'Task lighting', accent: 'Accent highlights' }

export const SCENE_DESC: Record<SceneName, string> = {
  Day: 'Natural daylight through the window, lamps off.',
  Evening: 'A soft, warm glow with accent lights on.',
  Reading: 'Bright task light where you sit, the rest turned down.',
  Night: 'Only the accent lights, for a quiet late-night mood.',
  'All off': 'Every lamp off. Daylight follows the time of day.',
  Custom: 'Your own mix of lights.',
}

/** The one primary lighting control: scene chips floating over the room. */
export function SceneBar({ scene, onScene }: { scene: SceneName; onScene: (s: SceneName) => void }) {
  return (
    <div className="scene-bar" role="group" aria-label="Lighting scene">
      {SCENES.map((s) => (
        <button key={s} className="scene-chip" aria-pressed={scene === s} onClick={() => onScene(s)} title={SCENE_DESC[s]}>
          {s}
        </button>
      ))}
    </div>
  )
}

const rgbOf = (k: number) => { const [r, g, b] = kelvinToRGB(k); return `rgb(${r | 0},${g | 0},${b | 0})` }

/** One fixture: on/off, brightness, and warm-to-cool colour. */
export function LightRow({ spec, st, onChange, onHover, hot, onSelect }: {
  spec: LightSpec; st: LightState; onChange: (p: Partial<LightState>) => void; onHover?: (id: string | null) => void; hot?: boolean; onSelect?: () => void
}) {
  const col = rgbOf(st.k)
  return (
    <li className={'light-row' + (hot ? ' hot' : '')} onMouseEnter={() => onHover?.(spec.id)} onMouseLeave={() => onHover?.(null)}>
      <div className="light-head">
        <span className="light-glow" style={{ background: st.on ? col : 'var(--line-2)', boxShadow: st.on ? `0 0 8px ${col}` : 'none' }} />
        {onSelect ? <button className="light-name" onClick={onSelect}>{spec.item.name}</button> : <b className="light-name">{spec.item.name}</b>}
        <Switch on={st.on} onChange={(v) => onChange({ on: v })} label={`${spec.item.name}: on or off`} />
      </div>
      {st.on && (
        <div className="light-detail">
          {spec.dimmable && (
            <label className="ctl">
              <span>Brightness</span>
              <Slider value={st.dim} min={0.05} max={1} step={0.01} onChange={(v) => onChange({ dim: v })} label={`${spec.item.name} brightness`} />
              <span className="val">{Math.round(st.dim * 100)}%</span>
            </label>
          )}
          {!spec.rgb && (
            <label className="ctl">
              <span>Tone</span>
              <Slider className="plain warm-cool" value={st.k} min={1800} max={6500} step={50} onChange={(v) => onChange({ k: v })} label={`${spec.item.name} colour, warmer to cooler`} />
              <span className="val" />
              <span className="ctl-ends"><span>Warmer</span><span>Cooler</span></span>
            </label>
          )}
        </div>
      )}
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
  timeH: number
  setTimeH: (t: number) => void
  open: boolean
  setOpen: (v: boolean) => void
}

function Disclosure({ title, sub, open, onToggle, children, nested = false }: { title: string; sub?: string; open: boolean; onToggle: () => void; children: ReactNode; nested?: boolean }) {
  const id = useId()
  return (
    <div className={'disclosure' + (nested ? ' nested' : '')}>
      <button className="disc-head" aria-expanded={open} aria-controls={id} onClick={onToggle}>
        {!nested && <Icon name="sliders" />}
        <span className="grow">{title}{sub && <> <small>{sub}</small></>}</span>
        <Icon name={open ? 'chevron-up' : 'chevron-down'} />
      </button>
      {open && <div className="disc-body" id={id}>{children}</div>}
    </div>
  )
}


export function LightingPanel(p: LightingPanelProps) {
  const { specs, states, lighting, layers, timeH } = p
  const sun = sunState(timeH)
  const [fixOpen, setFixOpen] = useState(false)
  const nounOf = (id: string) => { const it = p.itemsById.get(id); return it ? 'the ' + it.category.replace(/_/g, ' ') : 'it' }
  return (
    <>
      <div className="section">
        <h2>Lighting</h2>
        <div className="scene-now">
          <b>{p.scene}</b>
          <span className="muted">{SCENE_DESC[p.scene]}</span>
        </div>
        <p className="intro">Pick a scene with the buttons under the room.</p>
      </div>

      {lighting && <Summary lighting={lighting} scene={p.scene} nounOf={nounOf} itemsById={p.itemsById} />}

      <Disclosure title="Adjust lighting" open={p.open} onToggle={() => p.setOpen(!p.open)}>
        <section>
          <h4>Time of day</h4>
          <label className="ctl">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name={sun.alt > 4 ? 'sun' : sun.alt > -6 ? 'dusk' : 'moon'} size={16} /> Time</span>
            <Slider value={timeH} min={0} max={24} step={0.05} onChange={(v) => p.setTimeH(Math.min(23.99, v))} label="Time of day" />
            <span className="val">{fmtTime(timeH)}</span>
          </label>
        </section>

        {specs.length === 0 ? (
          <section><p className="muted">No lamps or lights in this room yet. Once the design includes some, you can dim and tune them here.</p></section>
        ) : (
          <>
            <section>
              <h4>Groups</h4>
              {LAYERS.map((l) => {
                const n = specs.filter((s) => s.layer === l).length
                const ls = layers[l]
                return (
                  <div key={l} className={'layer-row' + (ls.on && n ? ' on' : '')}>
                    <div className="layer-head">
                      <i className={'layer-dot ' + l} />
                      <b>{LAYER_LABEL[l]}</b>
                      <small>{n ? LAYER_HINT[l] : 'None in this room'}</small>
                      <Switch on={ls.on} disabled={!n} onChange={(v) => p.setLayer(l, { on: v })} label={`${LAYER_LABEL[l]} lights: on or off`} />
                    </div>
                    {n > 0 && (
                      <label className="ctl wide">
                        <span>Brightness</span>
                        <Slider value={ls.dim} min={0.05} max={1} step={0.01} disabled={!ls.on} onChange={(v) => p.setLayer(l, { dim: v })} label={`${LAYER_LABEL[l]} lights brightness`} />
                      </label>
                    )}
                  </div>
                )
              })}
            </section>

            <section>
              <Disclosure nested title="Individual lights" sub={`${specs.length}`} open={fixOpen} onToggle={() => setFixOpen(!fixOpen)}>
                <ul className="list">
                  {specs.map((s) => (
                    <LightRow key={s.id} spec={s} st={states[s.id] ?? { on: true, dim: 1, k: s.kelvin }} onChange={(pp) => p.setLight(s.id, pp)} onHover={p.onHover} hot={p.hoverId === s.id} onSelect={() => p.onSelect(s.id)} />
                  ))}
                </ul>
              </Disclosure>
            </section>
          </>
        )}

        {lighting?.lux?.length ? (
          <section>
            <h4>Light map</h4>
            <div className="toggle-row">
              <span>Show light levels on the floor<small>Brighter areas glow warmer</small></span>
              <Switch on={p.showHeat} onChange={p.setShowHeat} label="Show light levels on the floor" />
            </div>
            {p.showHeat && (
              <div className="legend">
                <div className="bar" style={{ background: HEAT_GRADIENT }} />
                {(() => {
                  const d = heatDomain(lighting)
                  return <div className="ticks"><span>0</span><span>{Math.round(d / 2)}</span><span>{d}+ lx</span></div>
                })()}
              </div>
            )}
          </section>
        ) : null}
      </Disclosure>
    </>
  )
}

function zoneState(status?: string): { text: string; color: string } {
  const s = (status ?? '').toLowerCase()
  if (s === 'ok' || s === 'good' || s === 'met') return { text: 'Good', color: statusColor('ok') }
  if (s === 'dim' || s === 'low') return { text: 'A bit dim', color: statusColor('dim') }
  if (s === 'bright' || s === 'glare' || s === 'high') return { text: 'Very bright', color: statusColor('high') }
  return { text: 'Not checked in this scene', color: statusColor() }
}

function Summary({ lighting, scene, nounOf, itemsById }: { lighting: Lighting; scene: SceneName; nounOf: (id: string) => string; itemsById: Map<string, Item> }) {
  const layers = lighting.layers ?? {}
  const zones = lighting.zones ?? []
  const issues = lighting.issues ?? []
  const name = (id: string) => itemsById.get(id)?.name ?? 'This piece'
  const issueText = (i: NonNullable<Lighting['issues']>[number]): string => {
    const sug = i.suggest
    const near = sug?.near ? ` near ${nounOf(String(sug.near))}` : ''
    const cat = sug?.category ? String(sug.category).replace(/_/g, ' ') : ''
    switch (i.code) {
      case 'DIM_TASK_ZONE': return `The seating area is a bit dim for reading.${cat ? ` Try a ${cat}${near}.` : ''}`
      case 'NO_AMBIENT': return `The room needs a softer overall glow.${cat ? ` A ${cat} would help.` : ''}`
      case 'NO_ACCENT_LAYER': return `Add an accent light for mood${cat ? `, such as a ${cat}` : ''}.`
      case 'GLARE_RISK': return 'A bare bulb sits near eye level and may glare. A shade would soften it.'
      case 'UNLIT_FOCAL_POINT': return `${i.item_id ? name(i.item_id) : 'A key piece'} could use a highlight${cat ? `, like a ${cat}` : ''}.`
      default: return i.message.replace(/\s*\(?\d+(\.\d+)?\s*(lux|lx)\)?/gi, '')
    }
  }
  return (
    <>
      <div className="section">
        <h3>Light mix</h3>
        <div className="cover">
          {(['ambient', 'task', 'accent'] as LightLayer[]).map((l) => (
            <span key={l} className={'chip ' + (layers[l] ? 'yes' : 'no')} style={{ textTransform: 'none' }}>
              <Icon name={layers[l] ? 'check' : 'cross'} size={12} /> {LAYER_COVER[l]}
            </span>
          ))}
        </div>
      </div>
      {zones.length > 0 && (
        <div className="section">
          <h3>Zones</h3>
          <ul className="stat-list">
            {zones.map((z) => {
              const label = zoneLabel(z.name, itemsById)
              const st = zoneState(z.status)
              return (
                <li key={z.name}>
                  <span>{label}</span>
                  <span className="state" style={{ color: st.text.startsWith('Not') ? 'var(--muted)' : st.color }}><i className="dot" style={{ background: st.color }} />{st.text}</span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
      <div className="section">
        <h3>Suggestions</h3>
        {issues.length ? (
          <ul className="suggest-list">{issues.map((i, n) => <li key={n}>{issueText(i)}</li>)}</ul>
        ) : (
          <p className="muted">Nothing to fix in the {scene === 'Custom' ? 'current' : scene} scene.</p>
        )}
      </div>
    </>
  )
}

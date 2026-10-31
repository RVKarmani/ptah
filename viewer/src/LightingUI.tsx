import { useId, useState, type ReactNode } from 'react'
import type { Lighting, LightLayer, Item } from './types'
import { LAYERS, SCENES, fmtTime, type LayerState, type LayerStates, type LightSpec, type LightState, type LightStates, type SceneName } from './lights'
import { WarmthDial, WarmthControl, rgbCss, warmthLabel } from './WarmthDial'
import { heatCss, heatDomain } from './Heatmap'
import { BulbMark, LayerIcon, SceneIcon, TIME_BANDS, TimeIcon, timeBand } from './LightIcons'
import { parseRgb, hexOf } from './color'
import { Icon, Slider, Switch } from './ui'
import { zoneLabel } from './zones'
import './css/lighting.css'

export const LAYER_LABEL: Record<LightLayer, string> = { ambient: 'Ambient', task: 'Task', accent: 'Accent' }
const LAYER_HINT: Record<LightLayer, string> = { ambient: 'Overall glow', task: 'For reading and tasks', accent: 'Mood and highlights' }
/** What each layer does in the room, in one plain sentence. */
const LAYER_SENTENCE: Record<LightLayer, string> = {
  ambient: 'Soft light that fills the room and sets its overall mood.',
  task: 'Focused light for reading and working, where you need to see clearly.',
  accent: 'Small pools of light that pick out art, shelves and corners.',
}

export const SCENE_DESC: Record<SceneName, string> = {
  Day: 'Natural daylight through the window, lamps off.',
  Evening: 'A soft, warm glow with accent lights on.',
  Reading: 'Bright task light where you sit, the rest turned down.',
  Night: 'Only the accent lights, for a quiet late-night mood.',
  'All off': 'Every lamp off. Daylight follows the time of day.',
  Custom: 'Your own mix of lights.',
}

/** The one primary lighting control: the time-of-day scale under the room. A drafted rule with a stop per scene; the chosen scene is marked with a rubric diamond. */
export function SceneBar({ scene, onScene }: { scene: SceneName; onScene: (s: SceneName) => void }) {
  return (
    <div className="scale" role="group" aria-label="Lighting scene">
      {SCENES.map((s) => (
        <button key={s} type="button" className="stop" aria-pressed={scene === s} onClick={() => onScene(s)} title={SCENE_DESC[s]}>
          <SceneIcon scene={s} size={26} />
          <span>{s}</span>
          <i aria-hidden />
        </button>
      ))}
    </div>
  )
}

/** One fixture: on/off, brightness, and warm-to-cool colour. (Used by the product detail card.) */
export function LightRow({ spec, st, onChange, onHover, hot, onSelect, warmth = false }: {
  spec: LightSpec; st: LightState; onChange: (p: Partial<LightState>) => void; onHover?: (id: string | null) => void; hot?: boolean; onSelect?: () => void
  /** show the warmth dial (item detail) instead of the compact brightness-only row (lists) */
  warmth?: boolean
}) {
  const col = rgbCss(st.k)
  if (warmth) {
    return (
      <li className="light-row warm">
        {spec.rgb ? (
          <>
            <div className="light-head"><b className="light-name">{spec.item.name}</b><Switch on={st.on} onChange={(v) => onChange({ on: v })} label={`${spec.item.name}: on or off`} /></div>
            {st.on && spec.dimmable && (
              <label className="ctl">
                <span>Brightness</span>
                <Slider value={st.dim} min={0.05} max={1} step={0.01} onChange={(v) => onChange({ dim: v })} label={`${spec.item.name} brightness`} />
                <span className="val">{Math.round(st.dim * 100)}%</span>
              </label>
            )}
          </>
        ) : (
          <WarmthControl
            title="This light"
            sub={LAYER_HINT[spec.layer]}
            label={`${spec.item.name} warmth`}
            kelvin={st.k}
            dim={st.dim}
            on={st.on}
            dimmable={spec.dimmable}
            onToggle={(v) => onChange({ on: v })}
            onKelvin={(k) => onChange({ k })}
            onDim={(v) => onChange({ dim: v })}
            changed={Math.abs(st.k - spec.kelvin) >= 50}
            onReset={() => onChange({ k: spec.kelvin })}
          />
        )}
      </li>
    )
  }
  return (
    <li className={'light-row' + (hot ? ' hot' : '')} onMouseEnter={() => onHover?.(spec.id)} onMouseLeave={() => onHover?.(null)}>
      <div className="light-head">
        <span className="light-glow" style={{ background: st.on ? col : 'var(--paper)' }} aria-hidden />
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
  /** choose a scene (sets the layers, the time of day and the lamps) */
  onScene?: (s: SceneName) => void
  setLight: (id: string, p: Partial<LightState>) => void
  setLayer: (layer: LightLayer, p: Partial<LayerState>) => void
  /** set the warmth (colour temperature) of several lamps at once; does not turn the scene into "Custom" */
  setWarmth: (ids: string[], k: number) => void
  onHover: (id: string | null) => void
  hoverId: string | null
  onSelect: (id: string) => void
  lighting: Lighting | null
  showHeat: boolean
  setShowHeat: (v: boolean) => void
  itemsById: Map<string, Item>
  timeH: number
  setTimeH: (t: number) => void
  /** kept for the old "Adjust lighting" disclosure; the controls are no longer hidden behind it */
  open?: boolean
  setOpen?: (v: boolean) => void
}

/* ------------------------------------------------------------------------------------------------ scenes */

function SceneChoices({ scene, onScene }: { scene: SceneName; onScene?: (s: SceneName) => void }) {
  const name = useId()
  return (
    <fieldset className="lt-scenes">
      <legend className="sr">Lighting scene</legend>
      {SCENES.map((s) => (
        <label key={s} className={'lt-scene' + (scene === s ? ' on' : '')}>
          <input type="radio" name={name} checked={scene === s} onChange={() => onScene?.(s)} disabled={!onScene} />
          <span className="lt-scene-ic"><SceneIcon scene={s} size={36} /></span>
          <span className="lt-scene-tx"><b>{s}</b><span>{SCENE_DESC[s]}</span></span>
          <i className="lt-mark" aria-hidden />
        </label>
      ))}
      {scene === 'Custom' && <p className="lt-custom" role="status">You have changed the lights, so this is your own mix. Pick a scene to start again.</p>}
    </fieldset>
  )
}

/** Time of day: a slider over a strip that is coloured band by band, with the icon of the band you are in. */
const BAND_PIG: Record<string, string> = { dawn: 'dawn', morning: 'morning', noon: 'noon', golden: 'golden', dusk: 'dusk', night: 'night' }
const TIME_STRIP = (() => {
  const stops: string[] = []
  TIME_BANDS.forEach((b, i) => {
    const a = (b.from / 24) * 100
    const z = ((TIME_BANDS[i + 1]?.from ?? 24) / 24) * 100
    stops.push(`var(--pg-${BAND_PIG[b.band]}) ${a.toFixed(2)}% ${z.toFixed(2)}%`)
  })
  return `linear-gradient(90deg, ${stops.join(', ')})`
})()

function TimeOfDay({ timeH, setTimeH }: { timeH: number; setTimeH: (t: number) => void }) {
  const b = timeBand(timeH)
  return (
    <div className="lt-time">
      <div className="lt-time-head">
        <TimeIcon band={b.band} size={32} />
        <b>{b.label}</b>
        <span className="lt-time-clock">{fmtTime(timeH)}</span>
      </div>
      <div className="lt-time-track" style={{ backgroundImage: TIME_STRIP }}>
        <input
          type="range"
          className="lt-range"
          min={0}
          max={24}
          step={0.25}
          value={timeH}
          aria-label="Time of day"
          aria-valuetext={`${b.label}, ${fmtTime(timeH)}`}
          onChange={(e) => setTimeH(Math.min(23.99, +e.target.value))}
        />
      </div>
      <div className="lt-time-ends" aria-hidden><span>Midnight</span><span>Noon</span><span>Midnight</span></div>
    </div>
  )
}

/* ------------------------------------------------------------------------------------------------ layers */

function LayerMark({ layer }: { layer: LightLayer }) {
  return <span className="lt-lm"><LayerIcon layer={layer} size={28} /></span>
}

interface LayerView { layer: LightLayer; specs: LightSpec[]; tunable: LightSpec[]; k: number; changed: boolean }

function LayerBlock({ v, ls, open, onOpen, states, setLayer, setWarmth }: {
  v: LayerView; ls: LayerState; open: boolean; onOpen: () => void; states: LightStates
  setLayer: (layer: LightLayer, p: Partial<LayerState>) => void; setWarmth: (ids: string[], k: number) => void
}) {
  const id = useId()
  const label = LAYER_LABEL[v.layer]
  const n = v.specs.length
  const w = warmthLabel(v.k)
  void states
  return (
    <section className={'lt-layer' + (ls.on ? ' on' : ' off')} aria-labelledby={id + 'h'}>
      <header className="lt-layer-head">
        <LayerMark layer={v.layer} />
        <div className="lt-layer-t">
          <h4 id={id + 'h'}>{label}</h4>
          <p>{LAYER_SENTENCE[v.layer]}</p>
        </div>
        {n > 0 && (
          <div className="lt-layer-sw">
            <span className="lt-state" aria-hidden>{ls.on ? 'On' : 'Off'}</span>
            <Switch on={ls.on} onChange={(on) => setLayer(v.layer, { on })} label={`${label} lights: on or off`} />
          </div>
        )}
      </header>
      {n === 0 ? (
        <p className="lt-none">No {label.toLowerCase()} lights in this room yet.</p>
      ) : ls.on ? (
        <div className="lt-layer-body">
          <label className="lt-ctl">
            <span>Brightness</span>
            <Slider value={ls.dim} min={0.05} max={1} step={0.01} onChange={(d) => setLayer(v.layer, { dim: d })} label={`${label} brightness`} />
            <span className="val">{Math.round(ls.dim * 100)}%</span>
          </label>
          {v.tunable.length > 0 ? (
            <>
              <button type="button" className="lt-warm" aria-expanded={open} aria-controls={id + 'd'} onClick={onOpen}>
                <BulbMark k={v.k} on dim={ls.dim} size={30} />
                <span className="lt-warm-t"><b>Warmth</b><small>{w.name}, {w.hint}</small></span>
                <span className="lt-warm-go">{open ? 'Done' : 'Adjust'}<Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} /></span>
              </button>
              {open && (
                <div className="lt-dial" id={id + 'd'}>
                  <WarmthDial kelvin={v.k} dim={ls.dim} on={ls.on} compact label={`${label} lights warmth`} onChange={(k) => setWarmth(v.tunable.map((s) => s.id), k)} />
                  {v.changed && <button type="button" className="btn quiet sm" onClick={() => v.tunable.forEach((s) => setWarmth([s.id], s.kelvin))}>Reset warmth</button>}
                </div>
              )}
            </>
          ) : (
            <p className="lt-none">These lights have a fixed colour, so only their brightness can change.</p>
          )}
        </div>
      ) : null}
    </section>
  )
}

/* ------------------------------------------------------------------------------------------------ individual lamps */

function LampRow({ spec, st, layer, hot, open, onOpen, onChange, onHover }: {
  spec: LightSpec; st: LightState; layer: LayerState; hot: boolean; open: boolean; onOpen: () => void
  onChange: (p: Partial<LightState>) => void; onHover: (id: string | null) => void
}) {
  const id = useId()
  const lit = st.on && layer.on
  const fixed = !!spec.rgb
  const fixedCol = fixed ? parseRgb(spec.rgb) : null
  const w = warmthLabel(st.k)
  const status = !st.on ? 'off' : !layer.on ? 'off with its layer' : fixed ? 'fixed colour' : w.name.toLowerCase()
  const changed = Math.abs(st.k - spec.kelvin) >= 50
  return (
    <li className={'lt-lamp' + (hot ? ' hot' : '') + (open ? ' open' : '')} onMouseEnter={() => onHover(spec.id)} onMouseLeave={() => onHover(null)}>
      <div className="lt-lamp-row">
        <button type="button" className="lt-lamp-main" aria-expanded={open} aria-controls={id} onClick={onOpen} onFocus={() => onHover(spec.id)} onBlur={() => onHover(null)}>
          <BulbMark k={st.k} on={lit} dim={st.dim * layer.dim} size={26} color={fixedCol ? hexOf(fixedCol) : undefined} />
          <span className="lt-lamp-t"><b>{spec.item.name}</b><small>{LAYER_LABEL[spec.layer]} light, {status}</small></span>
          <Icon name={open ? 'chevron-up' : 'chevron-down'} size={16} />
        </button>
        <Switch on={st.on} onChange={(on) => onChange({ on })} label={`${spec.item.name}: on or off`} />
      </div>
      {open && (
        <div className="lt-lamp-detail" id={id}>
          {!fixed && <WarmthDial kelvin={st.k} dim={st.dim * layer.dim} on={lit} compact label={`${spec.item.name} warmth`} onChange={(k) => onChange({ k })} />}
          {fixed && <p className="lt-none">This light has a fixed colour, so only its brightness can change.</p>}
          {spec.dimmable ? (
            <label className="lt-ctl">
              <span>Brightness</span>
              <Slider value={st.dim} min={0.05} max={1} step={0.01} disabled={!st.on} onChange={(d) => onChange({ dim: d })} label={`${spec.item.name} brightness`} />
              <span className="val">{Math.round(st.dim * 100)}%</span>
            </label>
          ) : (
            <p className="lt-none">This light cannot be dimmed.</p>
          )}
          {!fixed && changed && <button type="button" className="btn quiet sm" onClick={() => onChange({ k: spec.kelvin })}>Reset warmth</button>}
        </div>
      )}
    </li>
  )
}

const LAMPS_SHOWN = 6

/* ------------------------------------------------------------------------------------------------ the panel */

function Head({ title, sub, children }: { title: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="lt-head">
      <h3>{title}</h3>
      {sub && <p>{sub}</p>}
      {children}
    </div>
  )
}

export function LightingPanel(p: LightingPanelProps) {
  const { specs, states, lighting, layers, timeH } = p
  const view = (l: LightLayer): LayerView => {
    const ls = specs.filter((s) => s.layer === l)
    const tunable = ls.filter((s) => !s.rgb)
    const k = tunable.length ? Math.round(tunable.reduce((a, s) => a + (states[s.id]?.k ?? s.kelvin), 0) / tunable.length / 50) * 50 : 2700
    const changed = tunable.some((s) => Math.abs((states[s.id]?.k ?? s.kelvin) - s.kelvin) >= 50)
    return { layer: l, specs: ls, tunable, k, changed }
  }
  const views = LAYERS.map(view)
  // One dial is open at a time. By default the first layer that has a tunable light shows its dial.
  const [dialPick, setDialPick] = useState<LightLayer | 'none' | null>(null)
  const firstTunable = views.find((v) => v.tunable.length > 0)?.layer ?? null
  const dialLayer = dialPick === null ? firstTunable : dialPick === 'none' ? null : dialPick
  const [lampOpen, setLampOpen] = useState<string | null>(null)
  const [allLamps, setAllLamps] = useState(false)
  const nounOf = (id: string) => { const it = p.itemsById.get(id); return it ? 'the ' + it.category.replace(/_/g, ' ') : 'it' }
  const shownLamps = allLamps ? specs : specs.slice(0, LAMPS_SHOWN)
  const openLamp = lampOpen && specs.some((s) => s.id === lampOpen) ? lampOpen : null
  const lampsId = useId()

  return (
    <div className="lt">
      <div className="lt-top">
        <h2>Lighting</h2>
        <p className="intro">Choose a mood, then fine-tune the lights if you like.</p>
      </div>

      <section className="lt-sec" aria-labelledby={lampsId + 's'}>
        <Head title="Scene" sub="Each scene sets the lights and the time of day." />
        <span id={lampsId + 's'} className="sr">Scene</span>
        <SceneChoices scene={p.scene} onScene={p.onScene} />
      </section>

      <section className="lt-sec">
        <Head title="Time of day" sub="Daylight through the window follows the clock." />
        <TimeOfDay timeH={timeH} setTimeH={p.setTimeH} />
      </section>

      <section className="lt-sec">
        <Head title="Light layers" sub="Rooms feel right when three kinds of light work together." />
        {specs.length === 0 ? (
          <p className="lt-none">No lamps or lights in this room yet. Once the design includes some, you can dim and tune them here.</p>
        ) : (
          <div className="lt-layers">
            {views.map((v) => (
              <LayerBlock
                key={v.layer}
                v={v}
                ls={layers[v.layer]}
                open={dialLayer === v.layer}
                onOpen={() => setDialPick(dialLayer === v.layer ? 'none' : v.layer)}
                states={states}
                setLayer={p.setLayer}
                setWarmth={p.setWarmth}
              />
            ))}
          </div>
        )}
      </section>

      {specs.length > 0 && (
        <section className="lt-sec lt-minor">
          <Head title="Individual lamps" sub="Change one light on its own. Select a lamp to tune its colour." />
          <ul className="lt-lamps">
            {shownLamps.map((s) => (
              <LampRow
                key={s.id}
                spec={s}
                st={states[s.id] ?? { on: true, dim: 1, k: s.kelvin }}
                layer={layers[s.layer]}
                hot={p.hoverId === s.id}
                open={openLamp === s.id}
                onOpen={() => setLampOpen(openLamp === s.id ? null : s.id)}
                onChange={(pp) => p.setLight(s.id, pp)}
                onHover={p.onHover}
              />
            ))}
          </ul>
          {specs.length > LAMPS_SHOWN && (
            <button type="button" className="btn quiet sm lt-more" aria-expanded={allLamps} onClick={() => setAllLamps(!allLamps)}>
              {allLamps ? 'Show fewer lamps' : `Show all ${specs.length} lamps`}
            </button>
          )}
        </section>
      )}

      {lighting?.lux?.length ? (
        <section className="lt-sec lt-minor">
          <Head title="Light map" />
          <div className="toggle-row">
            <span>Show light levels on the floor<small>Brighter areas glow warmer</small></span>
            <Switch on={p.showHeat} onChange={p.setShowHeat} label="Show light levels on the floor" />
          </div>
          {p.showHeat && (
            <div className="legend">
              <div className="bar steps" aria-hidden>{Array.from({ length: 14 }, (_, i) => <i key={i} style={{ background: heatCss(Math.pow(i / 13, 1 / 0.75)) }} />)}</div>
              {(() => {
                const d = heatDomain(lighting)
                return <div className="ticks"><span>0</span><span>{Math.round(d / 2)}</span><span>{d}+ lx</span></div>
              })()}
            </div>
          )}
        </section>
      ) : null}

      {lighting && <RoomCheck lighting={lighting} scene={p.scene} nounOf={nounOf} itemsById={p.itemsById} />}
    </div>
  )
}

function zoneState(status?: string): { text: string; cls: string } {
  const s = (status ?? '').toLowerCase()
  if (s === 'ok' || s === 'good' || s === 'met') return { text: 'Good', cls: 'ok' }
  if (s === 'dim' || s === 'low') return { text: 'A bit dim', cls: 'dim' }
  if (s === 'bright' || s === 'glare' || s === 'high') return { text: 'Very bright', cls: 'high' }
  return { text: 'Not checked in this scene', cls: 'none' }
}

function RoomCheck({ lighting, scene, nounOf, itemsById }: { lighting: Lighting; scene: SceneName; nounOf: (id: string) => string; itemsById: Map<string, Item> }) {
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
      {zones.length > 0 && (
        <section className="lt-sec lt-minor">
          <Head title="Zones" sub={`How well each part of the room is lit${scene === 'Custom' ? '' : ' in the ' + scene + ' scene'}.`} />
          <ul className="stat-list">
            {zones.map((z) => {
              const label = zoneLabel(z.name, itemsById)
              const st = zoneState(z.status)
              return (
                <li key={z.name}>
                  <span>{label}</span>
                  <span className="state"><i className={'dot ' + st.cls} />{st.text}</span>
                </li>
              )
            })}
          </ul>
        </section>
      )}
      <section className="lt-sec lt-minor">
        <Head title="Suggestions" />
        {issues.length ? (
          <ul className="suggest-list">{issues.map((i, n) => <li key={n}>{issueText(i)}</li>)}</ul>
        ) : (
          <p className="muted">Nothing to fix in the {scene === 'Custom' ? 'current' : scene} scene.</p>
        )}
      </section>
    </>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { loadScene, resolveUrl, setDataBase } from './data'
import type { Item, LightLayer, Scene } from './types'
import Viewer, { type Quality } from './Viewer'
import Panel, { type Tab } from './Panel'
import { SceneBar } from './LightingUI'
import { useChoiceItems } from './NearMisses'
import { SceneNote } from './sheet/SceneNote'
import { WarmthDial } from './WarmthDial'
import { Cell, Dim, Sheet, TbMark, ThemeCell } from './sheet/Sheet'
import { groupDecor, numbering } from './schedule'
import { roomBounds } from './geometry'
import { Boundary } from './Boundary'
import { Icon } from './ui'
import { Logo } from './brand/Logo'
import { AppMenu } from './onboarding/AppMenu'
import { enableSnapshots } from './snapshot'
import {
  DEFAULT_SCENE, SCENE_LAYERS, SCENE_TIME, SCENES, effectiveStates, initialStates, lightSpecs, lightingFor, sceneKey,
  type LayerState, type LayerStates, type LightState, type LightStates, type SceneName,
} from './lights'
import { floorById } from './floors'
import { HEX, loadWall, resolveWall, saveWall, type WallSel } from './walls'

const coarse = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches

function ErrorCard({ title, children, action, details, page = false }: { title: string; children: ReactNode; action?: ReactNode; details?: ReactNode; page?: boolean }) {
  const H = page ? 'h1' : 'h2'
  return (
    <div className="note" role="alert">
      <span className="note-icon" aria-hidden><Icon name="alert" size={28} /></span>
      <H>{title}</H>
      <div className="muted">{children}</div>
      {details && <details className="tech"><summary>Technical details</summary><div>{details}</div></details>}
      {action}
    </div>
  )
}
const reload = () => window.location.reload()

/** One full-screen loader for the whole start-up (fetching, then building the room), so nothing ever flashes blank. */
function LoadScreen({ stage, done }: { stage: 'data' | 'room'; done: boolean }) {
  return (
    <div className={'loading' + (done ? ' done' : '')} role="status" aria-live="polite">
      <div className="load-mark" aria-hidden><Logo size={56} className="logo-lg" /></div>
      <b>{stage === 'data' ? 'Opening your room' : 'Setting up your room'}</b>
      <p className="muted">{stage === 'data' ? 'Gathering your design.' : 'Building the furniture and lights.'}</p>
      <div className="load-bar" aria-hidden><i /></div>
      <p className="muted slow">Still working. The first load can take a little longer on some devices.</p>
    </div>
  )
}

export interface AppProps {
  /** Data prefix of a finished bundle (default: ?data= or the bundled sample). */
  base?: string
  /** Shown as "Design another room" in the top bar menu. */
  onExit?: () => void
  /** Shown as "Delete my data" (only for a bundle that belongs to a job). */
  onDelete?: () => void | Promise<void>
  /** Called once the room is on screen. */
  onReady?: () => void
}

export default function App({ base, onExit, onDelete, onReady }: AppProps = {}) {
  const qp = useMemo(() => new URLSearchParams(window.location.search), [])
  const QUALITY = useMemo<Quality>(() => ({ ao: qp.get('ao') !== '0', bloom: qp.get('bloom') !== '0', tone: qp.get('tone') === 'aces' ? 'aces' : 'agx' }), [qp])
  const [scene, setScene] = useState<Scene | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showScan, setShowScan] = useState(false)
  const [scanErr, setScanErr] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>((['items', 'lighting', 'room'] as Tab[]).includes(qp.get('tab') as Tab) ? (qp.get('tab') as Tab) : 'items')
  const [own, setOwn] = useState<LightStates>({})
  const [layers, setLayers] = useState<LayerStates>(SCENE_LAYERS[DEFAULT_SCENE])
  const [sceneName, setSceneName] = useState<SceneName>(DEFAULT_SCENE)
  const [timeH, setTimeH] = useState(SCENE_TIME[DEFAULT_SCENE])
  const [floorId, setFloorId] = useState('light_oak')
  const [wall, setWallState] = useState<WallSel>({ sel: 'current', custom: '#c9b8a4' })
  const [showHeat, setShowHeat] = useState(qp.get('heat') === '1')
  const [sheetOpen, setSheetOpen] = useState(false)
  const [ready, setReady] = useState(false)
  const [lost, setLost] = useState(false)
  const [resetKey, setResetKey] = useState(0)

  useEffect(() => {
    setDataBase(base)
    loadScene().then(setScene).catch((e) => setError(String(e?.message ?? e)))
  }, [base])

  // the scene's items with the user's "Use this" product picks applied, so the room itself follows the choice
  const items = useChoiceItems(scene?.items, scene?.baseUrl)

  const specs = useMemo(() => (scene ? lightSpecs(items ?? scene.items, scene.layout.placements, scene.room) : []), [scene, items])

  // initialise lighting + finishes once the scene is in (URL params let screenshots pick a state)
  useEffect(() => {
    if (!scene) return
    setOwn(initialStates(specs))
    const want = qp.get('scene')
    const found = SCENES.find((s) => sceneKey(s) === want) ?? DEFAULT_SCENE
    setSceneName(found)
    setLayers(SCENE_LAYERS[found])
    const t = parseFloat(qp.get('time') ?? '')
    setTimeH(Number.isFinite(t) ? t : SCENE_TIME[found])
    setFloorId(floorById(qp.get('floor') ?? scene.room.finishes?.floor).id)
    const saved = loadWall(scene.baseUrl)
    const wq = qp.get('wall')
    setWallState(wq ? { sel: wq === 'designed' || wq === 'current' || HEX.test(wq) ? wq : 'current', custom: saved?.custom ?? '#c9b8a4' } : saved ?? { sel: 'current', custom: '#c9b8a4' })
    const sel = qp.get('select')
    if (sel) setSelectedId(sel)
  }, [scene, specs])

  const pickScene = useCallback((n: SceneName) => {
    setSceneName(n)
    setLayers(SCENE_LAYERS[n])
    setTimeH(SCENE_TIME[n])
    setOwn((o) => Object.fromEntries(Object.entries(o).map(([id, st]) => [id, { ...st, on: true, dim: 1 }])))
  }, [])
  const setWall = useCallback((w: WallSel) => { setWallState(w); if (scene) saveWall(scene.baseUrl, w) }, [scene])
  const setLight = (id: string, patch: Partial<LightState>) => {
    setOwn((o) => ({ ...o, [id]: { ...o[id], ...patch } }))
    // warmth alone is a personal tweak, not a different scene
    if (Object.keys(patch).some((k) => k !== 'k')) setSceneName('Custom')
  }
  const setWarmth = useCallback((ids: string[], k: number) => {
    setOwn((o) => {
      const n = { ...o }
      for (const id of ids) if (n[id]) n[id] = { ...n[id], k }
      return n
    })
  }, [])
  const setLayer = (l: LightLayer, patch: Partial<LayerState>) => {
    setLayers((ls) => ({ ...ls, [l]: { ...ls[l], ...patch } }))
    setSceneName('Custom')
  }
  const effective = useMemo(() => effectiveStates(specs, own, layers), [specs, own, layers])
  const lightingView = useMemo(() => lightingFor(scene?.lighting ?? null, sceneName), [scene, sceneName])

  const itemsById = useMemo(() => new Map<string, Item>((items ?? []).map((i) => [i.id, i])), [items])
  const placed = useMemo(
    () =>
      (scene?.layout.placements ?? []).flatMap((placement) => {
        const item = itemsById.get(placement.item_id)
        return item ? [{ item, placement, imageUrl: resolveUrl(item.image, scene!.baseUrl) }] : []
      }),
    [scene, itemsById],
  )

  const grouped = useMemo(() => groupDecor(placed), [placed])
  const numbers = useMemo(() => numbering(grouped), [grouped])

  const onFrame = useCallback((n: number) => { if (n >= 3) setReady(true) }, [])
  useEffect(() => { if (ready) enableSnapshots() }, [ready])
  const readyRef = useRef(onReady)
  readyRef.current = onReady
  useEffect(() => { if (ready) readyRef.current?.() }, [ready])
  const onContextLost = useCallback(() => setLost(true), [])
  const onScanError = useCallback((m: string) => { setScanErr(m); setShowScan(false) }, [])
  const select = useCallback((id: string | null) => {
    setSelectedId(id)
    if (!id) setHoverId(null) // touch screens keep "hover" after a tap, which would leave a stray label in the room
    if (id) setTab('items')
  }, [])

  // Esc: deselect (the camera eases back), otherwise collapse the sheet
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (selectedId) { setSelectedId(null); setHoverId(null) }
      else if (sheetOpen) setSheetOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, sheetOpen])

  if (error) {
    return (
      <main className="center">
        <ErrorCard
          page
          title="We couldn't load this room"
          action={<button className="btn primary" onClick={reload}>Try again</button>}
          details={<><p>{error}</p><p>Expected <code>room.json</code>, <code>items.json</code> and <code>layout.json</code> under the data path (default <code>sample/</code>, or <code>?data=https://host/run/</code>).</p></>}
        >
          The room data didn't come through. Check your connection and try again.
        </ErrorCard>
      </main>
    )
  }
  if (!scene) return <LoadScreen stage="data" done={false} />

  const { taste, layout, room } = scene
  const hasScan = !!room.scan?.glb
  const wallColor = resolveWall(room, wall).hex
  const lightingProps = {
    specs, states: own, layers, scene: sceneName, onScene: pickScene, setLight, setLayer, setWarmth, onHover: setHoverId, hoverId, onSelect: select,
    lighting: lightingView, showHeat, setShowHeat, itemsById, timeH, setTimeH,
  }
  const roomProps = { room, floorId, setFloor: setFloorId, wall, wallHex: wallColor, setWall }
  const tags = (taste?.aesthetic_tags ?? []).slice(0, 3)

  // ambient warmth: the dial under the room drives every tunable ambient lamp at once
  const ambient = specs.filter((s) => s.layer === 'ambient' && !s.rgb)
  const ambientK = ambient.length ? Math.round(ambient.reduce((a, s) => a + (own[s.id]?.k ?? s.kelvin), 0) / ambient.length / 50) * 50 : 2700
  const ambientChanged = ambient.some((s) => Math.abs((own[s.id]?.k ?? s.kelvin) - s.kelvin) >= 50)
  const bounds = roomBounds(room)
  const meters = (cm: number) => (cm / 100).toFixed(2)
  const isSample = /(^|\/)sample[^/]*\/?$/.test(scene.baseUrl) || !!taste?.style_summary?.startsWith('Sample placeholder')
  const title = `${room.name ?? 'Your room'}, ${sceneName.toLowerCase()}`

  const tb = (
    <>
      <TbMark label="Ptah" />
      <Cell label="Project" className="grow-s">{room.name ?? 'Your room'}</Cell>
      <Cell label="Sheet" className="hide-s hide-m num">07 of 07</Cell>
      <Cell label="Room" className="hide-s num">{meters(bounds.w)} × {meters(bounds.d)} m</Cell>
      <Cell label="Lighting" className="hide-s">{sceneName}</Cell>
      {typeof layout.score === 'number' && (
        <div className="tb-cell hide-s" title="How well this layout fits your room and taste"><small>Fit to your taste</small><b className="num rub">{Math.round(layout.score * 100)}</b></div>
      )}
      <Cell label="Data" fill className="hide-s hide-m">{isSample ? 'Sample data' : 'Your design'}</Cell>
      <div className="tb-act">
        <button className="tb-link" onClick={() => { setSelectedId(null); setResetKey((k) => k + 1) }} title="Back to the overview" aria-label="Reset view">
          <Icon name="reset" size={18} /> <span>Reset view</span>
        </button>
      </div>
      {onExit && <AppMenu onExit={onExit} onDelete={onDelete} />}
      <ThemeCell />
    </>
  )

  return (
    <>
      <Sheet className={'app' + (sheetOpen ? ' sheet-open' : '')} title="Your furnished room" tb={tb} onTop>
        <div className="app-body">
          <div className="left">
            {/* The plate's captions sit in strips above and below the 3D view, never on it, so the room is always fully in sight. */}
            <header className="plate-cap">
              <div className="pc-title">
                <b title={title}>{title}</b>
                <SceneNote scene={sceneName}><span className="cap">Perspective view, {coarse ? 'drag to look around' : 'drag to orbit'}</span></SceneNote>
              </div>
              {taste?.style_summary && <p className="pc-sum" title={taste.style_summary}>{taste.style_summary}</p>}
              {tags.length > 0 && <div className="plate-tags">{tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>}
              <div className="tools-right">
                {scanErr && <span className="warn-text" role="status">The scan didn't load</span>}
                {hasScan && (
                  <button className="btn" aria-pressed={showScan} onClick={() => { setScanErr(null); setShowScan(!showScan) }}>
                    <Icon name="cube" size={16} /> Room scan
                  </button>
                )}
              </div>
            </header>
            <div className="stage" onPointerDown={() => setSheetOpen(false)}>
              <div className="stage-grid canon" aria-hidden />
              <Boundary
                fallback={
                  <div className="state-overlay">
                    <ErrorCard title="The 3D view couldn't start" action={<button className="btn primary" onClick={reload}>Reload</button>}>
                      Your browser couldn't set up 3D graphics. You can still browse the items list; try a recent version of Chrome, Safari or Firefox for the full view.
                    </ErrorCard>
                  </div>
                }
              >
                <Viewer
                  scene={scene}
                  itemsById={itemsById}
                  specs={specs}
                  lightStates={effective}
                  lighting={lightingView}
                  timeH={timeH}
                  floorId={floorId}
                  wallColor={wallColor}
                  showHeat={showHeat}
                  quality={QUALITY}
                  hoverId={hoverId}
                  selectedId={selectedId}
                  showScan={showScan}
                  resetKey={resetKey}
                  ready={ready}
                  numbers={numbers}
                  onFrame={onFrame}
                  onHover={setHoverId}
                  onSelect={select}
                  onScanError={onScanError}
                  onContextLost={onContextLost}
                />
              </Boundary>

              {lost && (
                <div className="state-overlay">
                  <ErrorCard title="The 3D view was interrupted" action={<button className="btn primary" onClick={reload}>Reload the view</button>}>
                    Your browser paused the graphics, which can happen when the device is busy. Reloading brings the room back.
                  </ErrorCard>
                </div>
              )}
            </div>
            <div className="plate-foot">
              <Dim className="plate-dim">{meters(bounds.w)} m</Dim>
              <span className="plate-hint" aria-hidden>
                {coarse ? 'Drag to look around, pinch to zoom, tap a piece' : 'Drag to orbit, scroll to zoom, click a piece, Esc to go back'}
              </span>
            </div>
            <div className={'ctrl' + (ambient.length ? '' : ' one')}>
              <div className="tod">
                <h4>Time of day</h4>
                <SceneBar scene={sceneName} onScene={pickScene} />
              </div>
              {ambient.length > 0 && (
                <div className="dial-box">
                  <h4>Ambient warmth</h4>
                  {ambientChanged && <button className="tb-link rd" onClick={() => ambient.forEach((s) => setWarmth([s.id], s.kelvin))}>Reset warmth</button>}
                  <WarmthDial kelvin={ambientK} dim={layers.ambient.dim} on={layers.ambient.on} onChange={(k) => setWarmth(ambient.map((s) => s.id), k)} label="Ambient lights warmth" />
                </div>
              )}
            </div>
          </div>
          <Panel
            scene={scene}
            itemsById={itemsById}
            placed={placed}
            grouped={grouped}
            numbers={numbers}
            selectedId={selectedId}
            hoverId={hoverId}
            onSelect={select}
            onHover={setHoverId}
            tab={tab}
            setTab={setTab}
            lighting={lightingProps}
            roomProps={roomProps}
            sheetOpen={sheetOpen}
            setSheetOpen={setSheetOpen}
          />
        </div>
      </Sheet>
      <LoadScreen stage="room" done={ready} />
    </>
  )
}

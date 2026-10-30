import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { loadScene, resolveUrl } from './data'
import type { Item, LightLayer, Scene } from './types'
import Viewer, { type Quality } from './Viewer'
import Panel, { type Tab } from './Panel'
import { SceneBar } from './LightingUI'
import { Boundary } from './Boundary'
import { Icon } from './ui'
import {
  DEFAULT_SCENE, SCENE_LAYERS, SCENE_TIME, SCENES, effectiveStates, initialStates, lightSpecs, lightingFor, sceneKey,
  type LayerState, type LayerStates, type LightState, type LightStates, type SceneName,
} from './lights'
import { floorById } from './floors'

const qp = new URLSearchParams(window.location.search)
const QUALITY: Quality = { ao: qp.get('ao') !== '0', bloom: qp.get('bloom') !== '0', tone: qp.get('tone') === 'aces' ? 'aces' : 'agx' }
const coarse = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches

function ErrorCard({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <div className="card" role="alert">
      <h2>{title}</h2>
      <div className="muted">{children}</div>
      {action}
    </div>
  )
}
const reload = () => window.location.reload()

export default function App() {
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
  const [previewPaint, setPreviewPaint] = useState(false)
  const [showHeat, setShowHeat] = useState(qp.get('heat') === '1')
  const [adjustOpen, setAdjustOpen] = useState(qp.get('adjust') === '1')
  const [sheetOpen, setSheetOpen] = useState(false)
  const [ready, setReady] = useState(false)
  const [lost, setLost] = useState(false)
  const [resetKey, setResetKey] = useState(0)
  const [hint, setHint] = useState(false)

  useEffect(() => {
    loadScene().then(setScene).catch((e) => setError(String(e?.message ?? e)))
  }, [])

  const specs = useMemo(() => (scene ? lightSpecs(scene.items, scene.layout.placements, scene.room) : []), [scene])

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
    const sel = qp.get('select')
    if (sel) setSelectedId(sel)
  }, [scene, specs])

  const pickScene = useCallback((n: SceneName) => {
    setSceneName(n)
    setLayers(SCENE_LAYERS[n])
    setTimeH(SCENE_TIME[n])
    setOwn((o) => Object.fromEntries(Object.entries(o).map(([id, st]) => [id, { ...st, on: true, dim: 1 }])))
  }, [])
  const setLight = (id: string, patch: Partial<LightState>) => {
    setOwn((o) => ({ ...o, [id]: { ...o[id], ...patch } }))
    setSceneName('Custom')
  }
  const setLayer = (l: LightLayer, patch: Partial<LayerState>) => {
    setLayers((ls) => ({ ...ls, [l]: { ...ls[l], ...patch } }))
    setSceneName('Custom')
  }
  const effective = useMemo(() => effectiveStates(specs, own, layers), [specs, own, layers])
  const lightingView = useMemo(() => lightingFor(scene?.lighting ?? null, sceneName), [scene, sceneName])

  const itemsById = useMemo(() => new Map<string, Item>((scene?.items ?? []).map((i) => [i.id, i])), [scene])
  const placed = useMemo(
    () =>
      (scene?.layout.placements ?? []).flatMap((placement) => {
        const item = itemsById.get(placement.item_id)
        return item ? [{ item, placement, imageUrl: resolveUrl(item.image, scene!.baseUrl) }] : []
      }),
    [scene, itemsById],
  )

  const onFrame = useCallback((n: number) => { if (n >= 3) setReady(true) }, [])
  const onContextLost = useCallback(() => setLost(true), [])
  const onScanError = useCallback((m: string) => { setScanErr(m); setShowScan(false) }, [])
  const select = useCallback((id: string | null) => {
    setSelectedId(id)
    if (id) setTab('items')
  }, [])

  // hint pill: shown for a few seconds once the room is up, gone after the first interaction
  useEffect(() => {
    if (!ready) return
    setHint(true)
    const t = window.setTimeout(() => setHint(false), 9000)
    return () => window.clearTimeout(t)
  }, [ready])

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
      <div className="center">
        <ErrorCard title="We couldn't load this room" action={<button className="btn primary" onClick={reload}>Try again</button>}>
          <p>The room data didn't come through. Check your connection and try again.</p>
          <p style={{ marginTop: 8 }}>Details: {error}</p>
          <p style={{ marginTop: 8 }}>Expected <code>room.json</code>, <code>items.json</code> and <code>layout.json</code> under the data path (default <code>sample/</code>, or <code>?data=https://host/run/</code>).</p>
        </ErrorCard>
      </div>
    )
  }
  if (!scene) return <div className="center" role="status"><div className="spinner" /><p className="muted">Loading your room…</p></div>

  const { taste, layout, room } = scene
  const hasScan = !!room.scan?.glb
  const fin = room.finishes ?? {}
  const paintHex = fin.wall_paint?.hex
  const wallColor = previewPaint && room.can_paint !== false && paintHex ? paintHex : fin.wall_color || '#f7f3ec'
  const lightingProps = {
    specs, states: own, layers, scene: sceneName, setLight, setLayer, onHover: setHoverId, hoverId, onSelect: select,
    lighting: lightingView, showHeat, setShowHeat, itemsById, timeH, setTimeH, open: adjustOpen, setOpen: setAdjustOpen,
  }
  const roomProps = { room, floorId, setFloor: setFloorId, wallColor, previewPaint, setPreviewPaint }
  const tags = (taste?.aesthetic_tags ?? []).slice(0, 3)

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>P</span>
          <div className="brand-text">
            <b>Ptah</b>
            <span>{room.name ?? 'Your room'}</span>
          </div>
        </div>
        {taste && (taste.style_summary || tags.length > 0) && (
          <div className="taste">
            {taste.style_summary && <p title={taste.style_summary}>{taste.style_summary}</p>}
            {tags.length > 0 && <div className="taste-tags">{tags.map((t) => <span className="chip" key={t}>{t}</span>)}</div>}
          </div>
        )}
        {typeof layout.score === 'number' && (
          <div className="fit" title="How well this layout fits your room and taste">
            <b>{Math.round(layout.score * 100)}</b><span>fit</span>
          </div>
        )}
      </header>
      <main>
        <div className="stage" onPointerDown={() => setHint(false)}>
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
              onFrame={onFrame}
              onHover={setHoverId}
              onSelect={select}
              onScanError={onScanError}
              onContextLost={onContextLost}
            />
          </Boundary>

          <div className="tools-left">
            {hasScan && (
              <button className="btn glass" aria-pressed={showScan} onClick={() => { setScanErr(null); setShowScan(!showScan) }}>
                <Icon name="cube" size={16} /> Room scan
              </button>
            )}
            {scanErr && <span className="warn-text" role="status">The scan didn't load</span>}
          </div>
          <div className="tools-right">
            <button className="btn glass" onClick={() => { setSelectedId(null); setResetKey((k) => k + 1) }} title="Back to the overview" aria-label="Reset view">
              <Icon name="reset" size={16} /> <span>Reset view</span>
            </button>
          </div>
          <div className={'hint-pill' + (hint ? ' show' : '')} aria-hidden>
            {coarse ? 'Drag to look around · pinch to zoom · tap a piece' : 'Drag to orbit · scroll to zoom · click a piece · Esc to go back'}
          </div>
          <SceneBar scene={sceneName} onScene={pickScene} />

          <div className={'loading' + (ready ? ' done' : '')} role="status" aria-live="polite">
            <div className="spinner" />
            <b>Setting up your room</b>
            <p className="muted">Building furniture and lights. This takes a few seconds the first time.</p>
          </div>
          {lost && (
            <div className="state-overlay">
              <ErrorCard title="The 3D view was interrupted" action={<button className="btn primary" onClick={reload}>Reload the view</button>}>
                Your browser paused the graphics, which can happen when the device is busy. Reloading brings the room back.
              </ErrorCard>
            </div>
          )}
        </div>
        <Panel
          scene={scene}
          itemsById={itemsById}
          placed={placed}
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
      </main>
    </div>
  )
}

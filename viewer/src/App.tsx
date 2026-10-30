import { useEffect, useMemo, useState } from 'react'
import { loadScene, resolveUrl } from './data'
import type { Item, LightLayer, Scene } from './types'
import Viewer, { type Quality } from './Viewer'
import Panel, { type Tab } from './Panel'
import { Dock } from './LightingUI'
import {
  DEFAULT_SCENE, SCENE_LAYERS, SCENE_TIME, SCENES, effectiveStates, initialStates, lightSpecs, lightingFor, sceneKey,
  type LayerState, type LayerStates, type LightState, type LightStates, type SceneName,
} from './lights'
import { floorById } from './floors'

const qp = new URLSearchParams(window.location.search)

export default function App() {
  const [scene, setScene] = useState<Scene | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showScan, setShowScan] = useState(false)
  const [scanErr, setScanErr] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>((qp.get('tab') as Tab) || 'items')
  const [own, setOwn] = useState<LightStates>({})
  const [layers, setLayers] = useState<LayerStates>(SCENE_LAYERS[DEFAULT_SCENE])
  const [sceneName, setSceneName] = useState<SceneName>(DEFAULT_SCENE)
  const [timeH, setTimeH] = useState(SCENE_TIME[DEFAULT_SCENE])
  const [floorId, setFloorId] = useState('light_oak')
  const [previewPaint, setPreviewPaint] = useState(false)
  const [showHeat, setShowHeat] = useState(qp.get('heat') === '1')
  const [quality, setQuality] = useState<Quality>({ ao: qp.get('ao') !== '0', bloom: qp.get('bloom') !== '0', tone: qp.get('tone') === 'aces' ? 'aces' : 'agx' })

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

  const pickScene = (n: SceneName) => {
    setSceneName(n)
    setLayers(SCENE_LAYERS[n])
    setTimeH(SCENE_TIME[n])
    setOwn((o) => Object.fromEntries(Object.entries(o).map(([id, st]) => [id, { ...st, on: true, dim: 1 }])))
  }
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

  if (error) {
    return (
      <div className="center">
        <div className="card">
          <h2>Couldn't load the room</h2>
          <p className="muted">{error}</p>
          <p className="muted">Expected room.json, items.json and layout.json under the data path (default <code>sample/</code>, or <code>?data=https://host/run/</code>).</p>
        </div>
      </div>
    )
  }
  if (!scene) return <div className="center"><div className="spinner" /><p className="muted">Loading your room…</p></div>

  const { taste, layout, room } = scene
  const hasScan = !!room.scan?.glb
  const fin = room.finishes ?? {}
  const paintHex = fin.wall_paint?.hex
  const wallColor = previewPaint && room.can_paint !== false && paintHex ? paintHex : fin.wall_color || '#f7f3ec'
  const lightingProps = {
    specs, states: own, layers, scene: sceneName, setLight, setLayer, onHover: setHoverId, hoverId, onSelect: setSelectedId,
    lighting: lightingView, showHeat, setShowHeat, itemsById,
  }
  const roomProps = { room, floorId, setFloor: setFloorId, wallColor, previewPaint, setPreviewPaint, quality, setQuality }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">P</span>
          <div>
            <h1>Ptah</h1>
            <small>{room.name ?? 'Your room'}</small>
          </div>
        </div>
        {taste && (taste.style_summary || taste.aesthetic_tags?.length) && (
          <div className="taste">
            {taste.style_summary && <p>{taste.style_summary}</p>}
            {taste.aesthetic_tags && (
              <div className="tags">{taste.aesthetic_tags.map((t) => <span key={t}>{t}</span>)}</div>
            )}
          </div>
        )}
        {typeof layout.score === 'number' && (
          <div className="score" title="Layout score">
            <b>{Math.round(layout.score * 100)}</b><small>fit</small>
          </div>
        )}
      </header>
      <main>
        <div className="stage">
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
            quality={quality}
            hoverId={hoverId}
            selectedId={selectedId}
            showScan={showScan}
            onHover={setHoverId}
            onSelect={setSelectedId}
            onScanError={(m) => { setScanErr(m); setShowScan(false) }}
          />
          <div className="overlay">
            {hasScan && (
              <label className="toggle">
                <input type="checkbox" checked={showScan} onChange={(e) => { setScanErr(null); setShowScan(e.target.checked) }} />
                Show scan
              </label>
            )}
            {scanErr && <span className="warn">Scan failed to load</span>}
          </div>
          <div className="hint">Drag to orbit · scroll to zoom · right-drag to pan</div>
          <Dock scene={sceneName} onScene={pickScene} timeH={timeH} onTime={setTimeH} hasLights={specs.length > 0} />
        </div>
        <Panel
          scene={scene}
          itemsById={itemsById}
          placed={placed}
          selectedId={selectedId}
          hoverId={hoverId}
          onSelect={(id) => { setSelectedId(id); if (id) setTab('items') }}
          onHover={setHoverId}
          tab={tab}
          setTab={setTab}
          lighting={lightingProps}
          roomProps={roomProps}
        />
      </main>
    </div>
  )
}

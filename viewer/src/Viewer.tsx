import { Suspense, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Item, Lighting, Scene } from './types'
import { roomBounds } from './geometry'
import { resolveUrl } from './data'
import { ExistingObjects, RoomFloor, RoomWalls } from './RoomModel'
import { PlacedItem } from './Items'
import ScanModel from './ScanModel'
import Daylight from './Daylight'
import Post, { type RigRef } from './Post'
import Heatmap, { type ZoneMark } from './Heatmap'
import type { LightSpec, LightStates } from './lights'
import type { ShelfBlock } from './models'
import { baseIntensity } from './lights'
import { zoneLabel } from './zones'
import { FOV, defaultView, easeInOut, focusPose, lerpPose, type Pose } from './camera'

export interface Quality { ao: boolean; bloom: boolean; tone: 'agx' | 'aces' }

interface Props {
  scene: Scene
  itemsById: Map<string, Item>
  specs: LightSpec[]
  lightStates: LightStates
  lighting: Lighting | null
  timeH: number
  floorId: string
  wallColor: string
  showHeat: boolean
  quality: Quality
  hoverId: string | null
  selectedId: string | null
  showScan: boolean
  /** bump to fly back to the default view */
  resetKey: number
  /** the first frames have been drawn (hides the loader and starts the intro) */
  ready: boolean
  onFrame: (n: number) => void
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
  onScanError: (m: string) => void
  onContextLost: () => void
}

/** Shadow maps only need re-rendering when something moved or a light changed: skip them on idle frames. */
function ShadowGate({ dep }: { dep: unknown[] }) {
  const gl = useThree((s) => s.gl)
  const dirty = useRef(400)
  useEffect(() => { gl.shadowMap.autoUpdate = false }, [gl])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { dirty.current = Math.max(dirty.current, 8) }, dep)
  useFrame(() => {
    if (dirty.current > 0) { gl.shadowMap.needsUpdate = true; dirty.current-- }
  }, -1)
  return null
}

interface Anim { from: Pose; to: Pose; t: number; ms: number }
const reduceMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false

/**
 * Camera director: intro swoop, ease-to-item on selection (and back on deselect), reset, and the limits that keep
 * users from getting lost (distance, no going under the floor, pan stays near the room).
 */
function CameraRig({ scene, itemsById, selectedId, resetKey, ready }: { scene: Scene; itemsById: Map<string, Item>; selectedId: string | null; resetKey: number; ready: boolean }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera
  const size = useThree((s) => s.size)
  const controls = useThree((s) => s.controls) as unknown as OrbitControlsImpl | null
  const aspect = size.width / Math.max(1, size.height)
  const view = useMemo(() => defaultView(scene, itemsById, aspect), [scene, itemsById, aspect])
  const viewRef = useRef(view)
  viewRef.current = view
  const b = useMemo(() => roomBounds(scene.room), [scene.room])

  const anim = useRef<Anim | null>(null)
  const saved = useRef<Pose | null>(null)
  const started = useRef(false)
  const lastReset = useRef(resetKey)
  const tmp = useMemo<Pose>(() => ({ pos: new THREE.Vector3(), target: new THREE.Vector3() }), [])

  const current = (): Pose => ({ pos: camera.position.clone(), target: (controls?.target ?? new THREE.Vector3()).clone() })
  const apply = (p: Pose) => {
    camera.position.copy(p.pos)
    if (controls) { controls.target.copy(p.target); controls.update() } else camera.lookAt(p.target)
  }
  const go = (to: Pose, ms: number) => {
    if (ms <= 0 || reduceMotion()) { anim.current = null; apply(to); return }
    anim.current = { from: current(), to, t: 0, ms }
  }

  // park the camera on the intro start pose before the first paint
  useLayoutEffect(() => { if (controls) apply(viewRef.current.intro) }, [controls]) // eslint-disable-line react-hooks/exhaustive-deps

  // user input cancels any running move
  useEffect(() => {
    if (!controls) return
    const stop = () => { anim.current = null }
    controls.addEventListener('start', stop)
    return () => controls.removeEventListener('start', stop)
  }, [controls])

  useEffect(() => {
    if (!ready || !controls) return
    const focusFor = (id: string): Pose | null => {
      const it = itemsById.get(id)
      const pl = scene.layout.placements.find((p) => p.item_id === id)
      if (!it || !pl) return null
      const c = current()
      return focusPose(it, pl, c.pos, c.target, aspect, viewRef.current.fit)
    }
    if (!started.current) {
      started.current = true
      const intro = new URLSearchParams(window.location.search).get('intro') !== '0'
      const f = selectedId ? focusFor(selectedId) : null
      if (f) { saved.current = viewRef.current.pose; go(f, intro ? 1800 : 0) } else go(viewRef.current.pose, intro ? 1900 : 0)
      return
    }
    if (resetKey !== lastReset.current) {
      lastReset.current = resetKey
      saved.current = null
      go(viewRef.current.pose, 900)
      return
    }
    if (selectedId) {
      const f = focusFor(selectedId)
      if (!f) return
      if (!saved.current) saved.current = current()
      go(f, 800)
    } else if (saved.current) {
      go(saved.current, 800)
      saved.current = null
    }
  }, [ready, controls, selectedId, resetKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const lastNear = useRef(0)
  useFrame((_, dt) => {
    if (!controls) return
    const a = anim.current
    if (a) {
      a.t += Math.min(dt, 0.05) * 1000
      const k = Math.min(1, a.t / a.ms)
      lerpPose(a.from, a.to, easeInOut(k), tmp)
      camera.position.copy(tmp.pos)
      controls.target.copy(tmp.target)
      controls.update()
      if (k >= 1) anim.current = null
    }
    // keep the pivot near the room (pan can't carry the view away) and above the floor
    const t = controls.target
    const mx = b.w * 0.5, mz = b.d * 0.5
    const cx = THREE.MathUtils.clamp(t.x, b.minX - mx, b.maxX + mx)
    const cz = THREE.MathUtils.clamp(t.z, b.minZ - mz, b.maxZ + mz)
    const cy = THREE.MathUtils.clamp(t.y, 0, scene.room.height)
    if (cx !== t.x || cz !== t.z || cy !== t.y) {
      camera.position.add(new THREE.Vector3(cx - t.x, cy - t.y, cz - t.z))
      t.set(cx, cy, cz)
    }
    if (camera.position.y < 8) camera.position.y = 8
    // near plane follows the zoom: close-ups stay unclipped, far views keep depth precision (no coplanar flicker)
    const dist = camera.position.distanceTo(t)
    const near = THREE.MathUtils.clamp(dist * 0.035, 3, 24)
    if (Math.abs(near - lastNear.current) / near > 0.08) {
      lastNear.current = near
      camera.near = near
      camera.far = Math.max(3000, dist * 5)
      camera.updateProjectionMatrix()
    }
  }, -0.5)

  return (
    <OrbitControls
      makeDefault
      enableDamping
      dampingFactor={0.08}
      screenSpacePanning={false}
      minPolarAngle={0.12}
      maxPolarAngle={Math.PI / 2 - 0.05}
      minDistance={45}
      maxDistance={view.fit * 1.7}
      rotateSpeed={0.8}
      zoomSpeed={0.9}
    />
  )
}

export default function Viewer(p: Props) {
  const { scene, itemsById, specs, lightStates, lighting, timeH, floorId, wallColor, showHeat, quality, hoverId, selectedId, showScan } = p
  const { room, layout, baseUrl } = scene
  const scanUrl = resolveUrl(room.scan?.glb ?? null, baseUrl)
  const useScan = showScan && !!scanUrl
  const rig = useRef<RigRef>({ exposure: 1 })
  const registry = useRef(new Map<string, THREE.Object3D>())
  const specById = useMemo(() => new Map(specs.map((s) => [s.id, s])), [specs])

  // The three strongest point/spot fixtures cast shadows. Chosen from the fixtures themselves (not their on/off state)
  // so the set of shadow-casting lights never changes when a scene switches, which would recompile every material.
  const shadowIds = useMemo(() => {
    const act = specs.filter((s) => s.kind !== 'area')
    act.sort((a, b) => baseIntensity(b) - baseIntensity(a))
    return new Set(act.slice(0, 3).map((s) => s.id))
  }, [specs])

  // decor standing on bookshelf boards: where the shelf's filler books must leave room (shelf-local x ranges per board)
  const shelfBlocks = useMemo(() => {
    const m = new Map<string, ShelfBlock[]>()
    const byId = new Map(layout.placements.map((pl) => [pl.item_id, pl]))
    for (const pl of layout.placements) {
      if (pl.surface !== 'shelf' || !pl.parent || pl.level == null) continue
      const par = byId.get(pl.parent)
      const it = itemsById.get(pl.item_id)
      if (!par || !it) continue
      const r = THREE.MathUtils.degToRad(par.rotation_deg ?? 0)
      const lx = (pl.x - par.x) * Math.cos(r) - (pl.z - par.z) * Math.sin(r)
      const arr = m.get(pl.parent) ?? []
      arr.push({ level: pl.level, x0: lx - it.w / 2 - 1.5, x1: lx + it.w / 2 + 1.5 })
      m.set(pl.parent, arr)
    }
    return m
  }, [layout.placements, itemsById])

  const zones = useMemo<ZoneMark[]>(() => {
    const z = lighting?.zones ?? []
    const byItem = new Map(layout.placements.map((pl) => [pl.item_id, pl]))
    return z.flatMap((zz) => {
      if (zz.center && zz.center.length >= 2) return [{ name: zoneLabel(zz.name, itemsById), x: zz.center[0], z: zz.center[1], lux: zz.lux, target: zz.target, status: zz.status }]
      const pts = (zz.item_ids ?? []).map((id) => byItem.get(id)).filter(Boolean) as { x: number; z: number }[]
      if (!pts.length) return []
      const x = pts.reduce((s, q) => s + q.x, 0) / pts.length
      const zc = pts.reduce((s, q) => s + q.z, 0) / pts.length
      return [{ name: zoneLabel(zz.name, itemsById), x, z: zc, lux: zz.lux, target: zz.target, status: zz.status }]
    })
  }, [lighting, layout.placements, itemsById])

  const initial = useMemo(() => {
    const b = roomBounds(room)
    return { position: [b.cx + b.w, room.height * 3, b.cz + b.d * 2] as [number, number, number] }
  }, [room])

  return (
    <Canvas
      flat
      shadows="percentage"
      dpr={[1, 2]}
      camera={{ position: initial.position, fov: FOV, near: 5, far: 6000 }}
      gl={{ antialias: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' }}
      onCreated={({ gl }) => {
        gl.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); p.onContextLost() })
      }}
      onPointerMissed={() => p.onSelect(null)}
      aria-label="3D view of your room"
    >
      <Daylight room={room} timeH={timeH} rig={rig} />
      <ShadowGate dep={[lightStates, timeH, layout, floorId]} />

      {useScan && scanUrl ? (
        <Suspense3 url={scanUrl} onError={p.onScanError} />
      ) : (
        <>
          <RoomFloor room={room} floorId={floorId} />
          <RoomWalls room={room} wallColor={wallColor} />
          <ExistingObjects room={room} />
        </>
      )}

      {layout.placements.map((pl, i) => {
        const item = itemsById.get(pl.item_id)
        if (!item) return null
        const spec = specById.get(item.id) ?? null
        return (
          <PlacedItem
            key={item.id}
            item={item}
            placement={pl}
            index={i}
            hovered={hoverId === item.id}
            selected={selectedId === item.id}
            roomHeight={room.height}
            spec={spec}
            state={spec ? lightStates[item.id] ?? null : null}
            shadow={shadowIds.has(item.id)}
            shelfBlocks={shelfBlocks.get(item.id)}
            parentItem={pl.parent ? itemsById.get(pl.parent) ?? null : null}
            registry={registry}
            onHover={p.onHover}
            onSelect={p.onSelect}
          />
        )
      })}

      {showHeat && lighting && <Heatmap room={room} lighting={lighting} rig={rig} zones={zones} />}

      <CameraRig scene={scene} itemsById={itemsById} selectedId={selectedId} resetKey={p.resetKey} ready={p.ready} />
      <Post rig={rig} ao={quality.ao} bloom={quality.bloom} mode={quality.tone} registry={registry} hoverId={hoverId} selectedId={selectedId} onFrame={p.onFrame} />
    </Canvas>
  )
}

function Suspense3({ url, onError }: { url: string; onError: (m: string) => void }) {
  return (
    <Suspense fallback={null}>
      <ScanModel url={url} onError={onError} />
    </Suspense>
  )
}

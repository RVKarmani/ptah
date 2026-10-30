import { Suspense, useEffect, useMemo, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
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
  onHover: (id: string | null) => void
  onSelect: (id: string | null) => void
  onScanError: (m: string) => void
}

/** Pick a 3/4 camera corner: the diagonal that most of the furniture faces towards. */
function cameraSetup(scene: Scene, itemsById: Map<string, Item>) {
  const b = roomBounds(scene.room)
  let fx = 0, fz = 0
  for (const p of scene.layout.placements) {
    const it = itemsById.get(p.item_id)
    if (!it || /rug|carpet/i.test(it.category) || it.h <= 3 || (p.y ?? 0) > 20) continue
    const r = THREE.MathUtils.degToRad(p.rotation_deg ?? 0)
    const wgt = it.w * it.h
    fx += Math.sin(r) * wgt
    fz += Math.cos(r) * wgt
  }
  const sx = Math.abs(fx) < 1e-6 ? 1 : Math.sign(fx)
  const sz = Math.abs(fz) < 1e-6 ? 1 : Math.sign(fz)
  const R = Math.hypot(b.w, b.d) / 2
  const horiz = R * 2.0
  const k = window.innerWidth / window.innerHeight < 1.1 ? 1.45 : 1.2 // pull back further on portrait screens
  const pos: [number, number, number] = [b.cx + sx * horiz * 0.62 * k, R * 1.75 * k, b.cz + sz * horiz * 0.78 * k]
  let target: [number, number, number] = [b.cx, scene.room.height * 0.12, b.cz]
  // ?cam=x,y,z,tx,ty,tz overrides the camera (handy for close-up screenshots)
  const cq = new URLSearchParams(window.location.search).get('cam')?.split(',').map(Number)
  if (cq && cq.length === 6 && cq.every(Number.isFinite)) {
    pos[0] = cq[0]; pos[1] = cq[1]; pos[2] = cq[2]
    target = [cq[3], cq[4], cq[5]]
  }
  return { pos, target, R, b }
}

/** Shadow maps only need re-rendering when something moved or a light changed: skip them on idle frames. */
function ShadowGate({ dep }: { dep: unknown[] }) {
  const gl = useThree((s) => s.gl)
  const dirty = useRef(400)
  useEffect(() => { gl.shadowMap.autoUpdate = false }, [gl])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { dirty.current = Math.max(dirty.current, 6) }, dep)
  useFrame(() => {
    if (dirty.current > 0) { gl.shadowMap.needsUpdate = true; dirty.current-- }
  }, -1)
  return null
}

export default function Viewer(p: Props) {
  const { scene, itemsById, specs, lightStates, lighting, timeH, floorId, wallColor, showHeat, quality, hoverId, selectedId, showScan } = p
  const { room, layout, baseUrl } = scene
  const cam = useMemo(() => cameraSetup(scene, itemsById), [scene, itemsById])
  const scanUrl = resolveUrl(room.scan?.glb ?? null, baseUrl)
  const useScan = showScan && !!scanUrl
  const { R } = cam
  const rig = useRef<RigRef>({ exposure: 1 })
  const specById = useMemo(() => new Map(specs.map((s) => [s.id, s])), [specs])

  // the 3 strongest active point/spot lights cast shadows
  const shadowIds = useMemo(() => {
    const act = specs.filter((s) => s.kind !== 'area' && lightStates[s.id]?.on && lightStates[s.id].dim > 0.01)
    act.sort((a, b) => baseIntensity(b) * lightStates[b.id].dim - baseIntensity(a) * lightStates[a.id].dim)
    return new Set(act.slice(0, 3).map((s) => s.id))
  }, [specs, lightStates])

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
      if (zz.center && zz.center.length >= 2) return [{ name: zz.name, x: zz.center[0], z: zz.center[1], lux: zz.lux, target: zz.target, status: zz.status }]
      const pts = (zz.item_ids ?? []).map((id) => byItem.get(id)).filter(Boolean) as { x: number; z: number }[]
      if (!pts.length) return []
      const x = pts.reduce((s, q) => s + q.x, 0) / pts.length
      const zc = pts.reduce((s, q) => s + q.z, 0) / pts.length
      return [{ name: zz.name, x, z: zc, lux: zz.lux, target: zz.target, status: zz.status }]
    })
  }, [lighting, layout.placements])

  return (
    <Canvas
      flat
      shadows="percentage"
      dpr={[1, 2]}
      camera={{ position: cam.pos, fov: 38, near: 5, far: 6000 }}
      gl={{ antialias: false, preserveDrawingBuffer: true, powerPreference: 'high-performance' }}
      onPointerMissed={() => p.onSelect(null)}
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
            onHover={p.onHover}
            onSelect={p.onSelect}
          />
        )
      })}

      {showHeat && lighting && <Heatmap room={room} lighting={lighting} rig={rig} zones={zones} />}

      <OrbitControls
        makeDefault
        target={cam.target}
        enableDamping
        dampingFactor={0.08}
        maxPolarAngle={Math.PI / 2 - 0.06}
        minDistance={R * 0.8}
        maxDistance={R * 5}
        enablePan
      />
      <Post rig={rig} ao={quality.ao} bloom={quality.bloom} mode={quality.tone} />
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

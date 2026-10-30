import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Edges, Html } from '@react-three/drei'
import * as THREE from 'three'
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js'
import type { Item, Placement } from './types'
import { buildModel, type Built, type ShelfBlock } from './models'
import { ACCENT, categoryColor } from './palette'
import { baseIntensity, dimCurve, type LightSpec, type LightState } from './lights'
import { kelvinTint, lightColorFrom } from './color'

export { ACCENT, categoryColor }

RectAreaLightUniformsLib.init()

const noRaycast = () => null

interface Props {
  item: Item
  placement: Placement
  index: number
  hovered: boolean
  selected: boolean
  roomHeight: number
  spec: LightSpec | null
  state: LightState | null
  shadow: boolean
  /** decor standing on this bookshelf's boards (filler books skip those spots) */
  shelfBlocks?: ShelfBlock[]
  /** the item this one stands on (e.g. the sofa a throw drapes over) */
  parentItem?: Item | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

function ItemLight({ spec, state, shadow }: { spec: LightSpec; state: LightState; shadow: boolean }) {
  const active = state.on && state.dim > 0.002
  const k = state.k
  const color = useMemo(() => lightColorFrom(spec.rgb, k), [spec.rgb, k])
  const intensity = baseIntensity(spec) * dimCurve(state.dim)
  const target = useMemo(() => new THREE.Object3D(), [])
  const quat = useMemo(() => new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), new THREE.Vector3(...spec.dir).normalize()), [spec.dir])
  const pos = spec.pos
  if (!active) return null
  if (spec.kind === 'area') {
    return <rectAreaLight position={pos} quaternion={quat} width={spec.size[0]} height={spec.size[1]} intensity={intensity} color={color} />
  }
  if (spec.kind === 'spot') {
    const dir = spec.dir
    return (
      <>
        <spotLight
          position={pos}
          target={target}
          angle={spec.halfAngle}
          penumbra={0.65}
          decay={2}
          intensity={intensity}
          color={color}
          castShadow={shadow}
          shadow-mapSize={[1024, 1024]}
          shadow-bias={-0.0006}
          shadow-normalBias={1.0}
          shadow-camera-near={3}
          shadow-camera-far={500}
          shadow-radius={4}
        />
        <primitive object={target} position={[pos[0] + dir[0] * 100, pos[1] + dir[1] * 100, pos[2] + dir[2] * 100]} />
      </>
    )
  }
  return (
    <pointLight
      position={pos}
      decay={2}
      intensity={intensity}
      color={color}
      castShadow={shadow}
      shadow-mapSize={[1024, 1024]}
      shadow-bias={-0.0008}
      shadow-normalBias={1.2}
      shadow-camera-near={3}
      shadow-camera-far={600}
      shadow-radius={4}
    />
  )
}

export function PlacedItem({ item, placement, index, hovered, selected, roomHeight, spec, state, shadow, shelfBlocks, parentItem, onHover, onSelect }: Props) {
  const y = placement.y ?? placement.elevation ?? 0
  const built = useMemo<Built>(() => buildModel(item, { ceilingGap: roomHeight - (y + item.h), shelfBlocks, parent: parentItem }), [item, roomHeight, y, shelfBlocks, parentItem])
  const group = useRef<THREE.Group>(null)

  // shade / bulb / strip glow follows the light's state
  useLayoutEffect(() => {
    const on = !!state && state.on
    const lvl = on ? dimCurve(state!.dim) : 0
    const tint = kelvinTint(state?.k ?? 2700)
    const lum = spec ? Math.min(1.6, Math.max(0.45, spec.lumens / 800)) : 1
    for (const g of built.glows) {
      g.mat.emissive.copy(tint)
      g.mat.emissiveIntensity = lvl * g.strength * lum * 0.9
      g.mat.needsUpdate = false
    }
  }, [built, state, spec])

  useEffect(() => () => { for (const g of built.glows) g.mat.dispose() }, [built])

  // little rise-in animation, staggered
  const t0 = useRef(0)
  useFrame((_, dt) => {
    const g = group.current
    if (!g) return
    if (t0.current > 2.2 + index * 0.12) return
    t0.current += dt
    const k = THREE.MathUtils.clamp((t0.current - index * 0.12) / 0.55, 0, 1)
    const e = 1 - Math.pow(1 - k, 3)
    g.scale.setScalar(Math.max(e, 0.001))
  })

  const rot = THREE.MathUtils.degToRad(placement.rotation_deg ?? 0)
  const showLabel = hovered || selected
  const hh = Math.max(item.h, 1.5)

  return (
    <group position={[placement.x, y, placement.z]}>
      <group ref={group} rotation={[0, rot, 0]}>
        <group
          onPointerOver={(e) => { e.stopPropagation(); onHover(item.id); document.body.style.cursor = 'pointer' }}
          onPointerOut={() => { onHover(null); document.body.style.cursor = '' }}
          onClick={(e) => { e.stopPropagation(); onSelect(item.id) }}
        >
          <primitive object={built.group} />
        </group>
        {spec && state && <ItemLight spec={spec} state={state} shadow={shadow} />}
        {(hovered || selected) && (
          <mesh position={[0, hh / 2, 0]} raycast={noRaycast}>
            <boxGeometry args={[item.w + 0.6, hh + 0.6, item.d + 0.6]} />
            <meshBasicMaterial color={ACCENT} transparent opacity={selected ? 0.1 : 0.06} depthWrite={false} toneMapped={false} />
            <Edges threshold={15} color={ACCENT} />
          </mesh>
        )}
        {selected && (
          <mesh position={[0, 0.8, item.d / 2 + 16]} rotation={[Math.PI / 2, 0, 0]} raycast={noRaycast}>
            <coneGeometry args={[7, 14, 3]} />
            <meshBasicMaterial color={ACCENT} toneMapped={false} />
          </mesh>
        )}
      </group>
      {showLabel && (
        <Html position={[0, item.h + 14, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className="tag">{item.name}</div>
        </Html>
      )}
    </group>
  )
}

import { memo, useEffect, useLayoutEffect, useMemo, useRef, type MutableRefObject } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js'
import type { Item, Placement } from './types'
import { buildModel, type Built, type ShelfBlock } from './models'
import { ACCENT, categoryColor } from './palette'
import { baseIntensity, dimCurve, type LightSpec, type LightState } from './lights'
import { kelvinTint, lightColorFrom } from './color'

export { ACCENT, categoryColor }

RectAreaLightUniformsLib.init()

/** how quickly lights / glows settle on a new scene (time constant, s): ~600 ms to 95% */
const TAU = 0.2

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
  /** id -> model root, used by the outline pass */
  registry: MutableRefObject<Map<string, THREE.Object3D>>
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

/**
 * A fixture's light. It is ALWAYS mounted (off = intensity 0) so the number of lights, and therefore the shader
 * programs, never changes when a scene switches; intensity eases towards its target for smooth transitions.
 */
function ItemLight({ spec, state, shadow }: { spec: LightSpec; state: LightState; shadow: boolean }) {
  const ref = useRef<THREE.Light>(null)
  const cur = useRef(0)
  const k = state.k
  const color = useMemo(() => lightColorFrom(spec.rgb, k), [spec.rgb, k])
  const target = state.on && state.dim > 0.002 ? baseIntensity(spec) * dimCurve(state.dim) : 0
  const aim = useMemo(() => new THREE.Object3D(), [])
  const quat = useMemo(() => new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), new THREE.Vector3(...spec.dir).normalize()), [spec.dir])
  const pos = spec.pos

  useFrame((_, dt) => {
    const l = ref.current
    if (!l) return
    if (cur.current !== target) {
      const e = 1 - Math.exp(-Math.min(dt, 0.1) / TAU)
      cur.current += (target - cur.current) * e
      if (Math.abs(target - cur.current) < Math.max(target, 1) * 0.003) cur.current = target
      l.intensity = cur.current
    }
  })

  if (spec.kind === 'area') {
    return <rectAreaLight ref={ref as never} position={pos} quaternion={quat} width={spec.size[0]} height={spec.size[1]} intensity={0} color={color} />
  }
  if (spec.kind === 'spot') {
    const dir = spec.dir
    return (
      <>
        <spotLight
          ref={ref as never}
          position={pos}
          target={aim}
          angle={spec.halfAngle}
          penumbra={0.65}
          decay={2}
          intensity={0}
          color={color}
          castShadow={shadow}
          shadow-mapSize={[1024, 1024]}
          shadow-bias={-0.0006}
          shadow-normalBias={1.0}
          shadow-camera-near={3}
          shadow-camera-far={500}
          shadow-radius={4}
        />
        <primitive object={aim} position={[pos[0] + dir[0] * 100, pos[1] + dir[1] * 100, pos[2] + dir[2] * 100]} />
      </>
    )
  }
  return (
    <pointLight
      ref={ref as never}
      position={pos}
      decay={2}
      intensity={0}
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

function PlacedItemImpl({ item, placement, index, hovered, selected, roomHeight, spec, state, shadow, shelfBlocks, parentItem, registry, onHover, onSelect }: Props) {
  const y = placement.y ?? placement.elevation ?? 0
  const built = useMemo<Built>(() => buildModel(item, { ceilingGap: roomHeight - (y + item.h), shelfBlocks, parent: parentItem }), [item, roomHeight, y, shelfBlocks, parentItem])
  const group = useRef<THREE.Group>(null)

  useEffect(() => {
    const map = registry.current
    map.set(item.id, built.group)
    return () => { if (map.get(item.id) === built.group) map.delete(item.id) }
  }, [registry, item.id, built])

  // shade / bulb / strip glow follows the light's state, eased like the light itself
  const glowLvl = useRef(0)
  const glowTarget = state && state.on ? dimCurve(state.dim) : 0
  const lum = spec ? Math.min(1.6, Math.max(0.45, spec.lumens / 800)) : 1
  const kelvin = state?.k ?? 2700
  const applyGlow = (lvl: number) => {
    const tint = kelvinTint(kelvin)
    for (const g of built.glows) {
      g.mat.emissive.copy(tint)
      g.mat.emissiveIntensity = lvl * g.strength * lum * 0.9
    }
  }
  useLayoutEffect(() => { applyGlow(glowLvl.current) })
  useEffect(() => () => { for (const g of built.glows) g.mat.dispose() }, [built])

  // little rise-in animation, staggered
  const t0 = useRef(0)
  useFrame((_, dt) => {
    const g = group.current
    if (g && t0.current <= 0.7 + index * 0.05) {
      t0.current += dt
      const k = THREE.MathUtils.clamp((t0.current - index * 0.05) / 0.5, 0, 1)
      const e = 1 - Math.pow(1 - k, 3)
      g.scale.setScalar(Math.max(e, 0.001))
    }
    if (built.glows.length && glowLvl.current !== glowTarget) {
      glowLvl.current += (glowTarget - glowLvl.current) * (1 - Math.exp(-Math.min(dt, 0.1) / TAU))
      if (Math.abs(glowTarget - glowLvl.current) < 0.004) glowLvl.current = glowTarget
      applyGlow(glowLvl.current)
    }
  })

  const rot = THREE.MathUtils.degToRad(placement.rotation_deg ?? 0)
  const showLabel = hovered || selected

  return (
    <group position={[placement.x, y, placement.z]}>
      <group ref={group} rotation={[0, rot, 0]} scale={0.001}>
        <group
          onPointerOver={(e) => { e.stopPropagation(); onHover(item.id); document.body.style.cursor = 'pointer' }}
          onPointerOut={() => { onHover(null); document.body.style.cursor = '' }}
          onClick={(e) => { e.stopPropagation(); onSelect(item.id) }}
        >
          <primitive object={built.group} />
        </group>
        {spec && state && <ItemLight spec={spec} state={state} shadow={shadow} />}
      </group>
      {showLabel && (
        <Html position={[0, item.h + 14, 0]} center zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className="tag">{item.name}</div>
        </Html>
      )}
    </group>
  )
}

export const PlacedItem = memo(PlacedItemImpl)

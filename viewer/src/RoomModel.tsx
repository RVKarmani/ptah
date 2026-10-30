import { useEffect, useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import type { Room, Opening } from './types'
import { wallInfos, type WallInfo } from './geometry'
import { floorById, useFloorTextures } from './floors'
import { safeColor } from './color'
import { getMaterial } from './materials'

const T = 10 // wall thickness (cm), built outside the floor polygon so interior dimensions stay true
const noRaycast = () => null

export function RoomFloor({ room, floorId }: { room: Room; floorId: string }) {
  const geo = useMemo(() => {
    const shape = new THREE.Shape(room.floor.map(([x, z]) => new THREE.Vector2(x, -z)))
    const g = new THREE.ExtrudeGeometry(shape, { depth: 8, bevelEnabled: false })
    g.rotateX(-Math.PI / 2)
    g.translate(0, -8, 0)
    return g
  }, [room])
  const tex = useFloorTextures(floorId)
  const preset = floorById(floorId)
  const mat = useMemo(() => {
    const m = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: preset.rough, metalness: 0 })
    m.map = tex.map
    m.normalMap = tex.normalMap
    m.roughnessMap = tex.roughnessMap
    m.normalScale.set(preset.normalScale, preset.normalScale)
    if (!tex.roughnessMap) m.roughness = preset.rough
    m.envMapIntensity = 0.8
    return m
  }, [tex, preset])
  useEffect(() => () => mat.dispose(), [mat])
  const side = useMemo(() => new THREE.MeshStandardMaterial({ color: '#b9a98f', roughness: 0.9 }), [])
  return <mesh geometry={geo} material={[mat, side]} receiveShadow raycast={noRaycast} />
}

export function ExistingObjects({ room }: { room: Room }) {
  return (
    <>
      {(room.existing ?? []).map((o) => (
        <mesh
          key={o.id}
          position={[o.x, o.h / 2, o.z]}
          rotation={[0, THREE.MathUtils.degToRad(o.rotation_deg ?? 0), 0]}
          castShadow
          receiveShadow
          raycast={noRaycast}
        >
          <boxGeometry args={[o.w, o.h, o.d]} />
          <meshStandardMaterial color="#d6d4cf" roughness={0.6} />
        </mesh>
      ))}
    </>
  )
}

type Kind = 'wall' | 'frame' | 'glass' | 'door'
interface Mats { wall: THREE.Material; frame: THREE.Material; glass: THREE.Material; door: THREE.Material }

function WallPieces({ info, room, mats, setRef, shadowOnly }: { info: WallInfo; room: Room; setRef?: (g: THREE.Group | null) => void; mats: Mats; shadowOnly?: boolean }) {
  const H = room.height
  const ops = (room.openings ?? [])
    .filter((o) => o.wall === info.wall.id)
    .map((o) => ({ ...o, a: Math.max(0, o.offset), b: Math.min(info.length, o.offset + o.width) }))
    .sort((p, q) => p.a - q.a)

  // local z of wall slab centre: outside the room
  const nLocal: [number, number] = [Math.sin(info.angle), Math.cos(info.angle)] // world direction of local +z
  const inwardIsLocalPlusZ = nLocal[0] * info.inward[0] + nLocal[1] * info.inward[1] > 0
  const zc = inwardIsLocalPlusZ ? -T / 2 : T / 2
  const inSign = inwardIsLocalPlusZ ? 1 : -1

  const boxes: { key: string; u0: number; u1: number; y0: number; y1: number; kind: Kind; t?: number; zo?: number }[] = []
  let cursor = 0
  ops.forEach((o: Opening & { a: number; b: number }) => {
    if (o.a > cursor) boxes.push({ key: `s${cursor}`, u0: cursor, u1: o.a, y0: 0, y1: H, kind: 'wall' })
    const top = (o.type === 'door' ? 0 : o.sill ?? 90) + o.height
    const bottom = o.type === 'door' ? 0 : o.sill ?? 90
    if (top < H) boxes.push({ key: `${o.id}-h`, u0: o.a, u1: o.b, y0: top, y1: H, kind: 'wall' })
    if (bottom > 0) boxes.push({ key: `${o.id}-s`, u0: o.a, u1: o.b, y0: 0, y1: bottom, kind: 'wall' })
    const f = 4
    const th = T * 0.7
    boxes.push({ key: `${o.id}-fl`, u0: o.a, u1: o.a + f, y0: bottom, y1: top, kind: 'frame', t: th })
    boxes.push({ key: `${o.id}-fr`, u0: o.b - f, u1: o.b, y0: bottom, y1: top, kind: 'frame', t: th })
    boxes.push({ key: `${o.id}-ft`, u0: o.a, u1: o.b, y0: top - f, y1: top, kind: 'frame', t: th })
    if (o.type === 'window') {
      boxes.push({ key: `${o.id}-fb`, u0: o.a, u1: o.b, y0: bottom, y1: bottom + f, kind: 'frame', t: th })
      boxes.push({ key: `${o.id}-mid`, u0: (o.a + o.b) / 2 - 1.5, u1: (o.a + o.b) / 2 + 1.5, y0: bottom, y1: top, kind: 'frame', t: th * 0.6 })
      boxes.push({ key: `${o.id}-glass`, u0: o.a + f, u1: o.b - f, y0: bottom + f, y1: top - f, kind: 'glass', t: 1 })
    } else {
      // closed door leaf (blocks sunlight, reads as a door)
      boxes.push({ key: `${o.id}-leaf`, u0: o.a + f, u1: o.b - f, y0: 0, y1: top - f, kind: 'door', t: 3.5, zo: inSign * -T * 0.15 })
      boxes.push({ key: `${o.id}-knob`, u0: o.b - f - 10, u1: o.b - f - 7, y0: 98, y1: 101, kind: 'frame', t: 3.5 + 7, zo: inSign * 0.5 })
    }
    cursor = Math.max(cursor, o.b)
  })
  if (cursor < info.length) boxes.push({ key: `s${cursor}-end`, u0: cursor, u1: info.length, y0: 0, y1: H, kind: 'wall' })

  return (
    <group ref={setRef} position={[info.wall.start[0], 0, info.wall.start[1]]} rotation={[0, info.angle, 0]}>
      {boxes
        .filter((b) => !(shadowOnly && b.kind === 'glass'))
        .map((b) => (
          <mesh
            key={b.key}
            position={[(b.u0 + b.u1) / 2, (b.y0 + b.y1) / 2, zc + (b.zo ?? 0)]}
            material={mats[b.kind]}
            raycast={noRaycast}
            castShadow={!!shadowOnly}
            receiveShadow={!shadowOnly && (b.kind === 'wall' || b.kind === 'door')}
          >
            <boxGeometry args={[b.u1 - b.u0, b.y1 - b.y0, b.t ?? T]} />
          </mesh>
        ))}
    </group>
  )
}

/**
 * Walls with door/window openings. Walls between the camera and the room collapse to a low curb (dollhouse view).
 * Invisible full-height copies keep casting shadows, and an invisible ceiling plane keeps the sun out, so the sun
 * only reaches the floor through the real window openings.
 */
export function RoomWalls({ room, wallColor }: { room: Room; wallColor: string }) {
  const infos = useMemo(() => wallInfos(room), [room])
  const camera = useThree((s) => s.camera)
  const groups = useRef<(THREE.Group | null)[]>([])

  const mats = useMemo<Mats>(
    () => ({
      wall: new THREE.MeshStandardMaterial({ color: safeColor(wallColor, '#f7f3ec'), roughness: 0.93 }),
      frame: new THREE.MeshStandardMaterial({ color: '#f1ede6', roughness: 0.5 }),
      glass: new THREE.MeshStandardMaterial({ color: '#cfe3ee', roughness: 0.05, metalness: 0.0, transparent: true, opacity: 0.22, depthWrite: false }),
      door: getMaterial('oak', '#d9c9ad', 'oak').clone(),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )
  useEffect(() => { (mats.wall as THREE.MeshStandardMaterial).color.copy(safeColor(wallColor, '#f7f3ec')) }, [wallColor, mats])

  const shadowMat = useMemo(() => new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false, side: THREE.DoubleSide }), [])

  useFrame((_, dt) => {
    infos.forEach((info, i) => {
      const g = groups.current[i]
      if (!g) return
      const dx = camera.position.x - info.mid[0]
      const dz = camera.position.z - info.mid[1]
      const outside = dx * info.inward[0] + dz * info.inward[1] < -5
      const target = outside ? 0.04 : 1
      g.scale.y += (target - g.scale.y) * Math.min(1, dt * 9)
    })
  })

  const shadowMats: Mats = { wall: shadowMat, frame: shadowMat, glass: shadowMat, door: shadowMat }
  const ceilingGeo = useMemo(() => {
    const shape = new THREE.Shape(room.floor.map(([x, z]) => new THREE.Vector2(x, z)))
    const g = new THREE.ShapeGeometry(shape)
    g.rotateX(Math.PI / 2) // shape (x, z) -> world (x, 0, z); normal faces down
    return g
  }, [room])

  return (
    <>
      {infos.map((info, i) => (
        <WallPieces key={info.wall.id} info={info} room={room} mats={mats} setRef={(g) => { groups.current[i] = g }} />
      ))}
      {infos.map((info) => (
        <WallPieces key={'sh-' + info.wall.id} info={info} room={room} mats={shadowMats} shadowOnly />
      ))}
      <mesh geometry={ceilingGeo} position={[0, room.height + 0.5, 0]} material={shadowMat} castShadow raycast={noRaycast}>
      </mesh>
    </>
  )
}

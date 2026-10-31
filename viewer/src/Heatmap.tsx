import { useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import type { Lighting, Room } from './types'
import { roomBounds } from './geometry'
import { makeCanvas } from './gfx'
import type { RigRef } from './Post'
import { HEAT_STOPS, STATUS } from './tokens'

const STOPS = HEAT_STOPS
const cols = STOPS.map(([t, c]) => [t, new THREE.Color(c)] as [number, THREE.Color])

export function heatColor(t: number, out = new THREE.Color()): THREE.Color {
  const x = Math.min(1, Math.max(0, t))
  for (let i = 1; i < cols.length; i++) {
    if (x <= cols[i][0]) {
      const k = (x - cols[i - 1][0]) / (cols[i][0] - cols[i - 1][0])
      return out.copy(cols[i - 1][1]).lerp(cols[i][1], k)
    }
  }
  return out.copy(cols[cols.length - 1][1])
}
export const heatCss = (t: number) => '#' + heatColor(t).getHexString(THREE.SRGBColorSpace)

export function heatDomain(l: Lighting): number {
  const all = (l.lux ?? []).flat().filter((v) => Number.isFinite(v)).sort((a, b) => a - b)
  if (!all.length) return 500
  const p95 = all[Math.floor(all.length * 0.95)]
  return Math.max(200, Math.round(p95 / 50) * 50)
}

/** lux[row][col] with rows along z (default) – falls back to transposed if that is what fits the room. */
function orient(room: Room, l: Lighting): { grid: number[][]; rows: number; cols: number } | null {
  const lux = l.lux
  if (!lux || !lux.length || !lux[0]?.length) return null
  const g = l.grid_cm || 25
  const b = roomBounds(room)
  const nz = Math.ceil(b.d / g), nx = Math.ceil(b.w / g)
  const r = lux.length, c = lux[0].length
  const fitsNormal = Math.abs(r - nz) + Math.abs(c - nx)
  const fitsTrans = Math.abs(r - nx) + Math.abs(c - nz)
  if (fitsTrans < fitsNormal) {
    const t: number[][] = Array.from({ length: c }, (_, i) => Array.from({ length: r }, (_, j) => lux[j][i]))
    return { grid: t, rows: c, cols: r }
  }
  return { grid: lux, rows: r, cols: c }
}

export interface ZoneMark { name: string; x: number; z: number; lux?: number; target?: number; status?: string }

export function statusColor(s?: string): string {
  const v = (s ?? '').toLowerCase()
  if (v === 'ok' || v === 'good' || v === 'met') return STATUS.ok
  if (v === 'dim' || v === 'low') return STATUS.dim
  if (v === 'bright' || v === 'glare' || v === 'high') return STATUS.high
  return STATUS.none
}

export default function Heatmap({ room, lighting, rig, zones }: { room: Room; lighting: Lighting; rig: { current: RigRef }; zones: ZoneMark[] }) {
  const o = useMemo(() => orient(room, lighting), [room, lighting])
  const domain = useMemo(() => heatDomain(lighting), [lighting])
  const g = lighting.grid_cm || 25
  const origin = lighting.origin ?? [0, 0]

  const tex = useMemo(() => {
    if (!o) return null
    const c = makeCanvas(o.cols, o.rows)
    const ctx = c.getContext('2d')!
    const img = ctx.createImageData(o.cols, o.rows)
    const col = new THREE.Color()
    for (let r = 0; r < o.rows; r++) for (let q = 0; q < o.cols; q++) {
      const v = o.grid[r][q] ?? 0
      heatColor(Math.pow(Math.max(0, v) / domain, 0.75), col)
      const i = (r * o.cols + q) * 4
      img.data[i] = col.r * 255; img.data[i + 1] = col.g * 255; img.data[i + 2] = col.b * 255; img.data[i + 3] = 255
    }
    ctx.putImageData(img, 0, 0)
    const t = new THREE.CanvasTexture(c)
    t.flipY = false
    t.magFilter = THREE.LinearFilter
    t.minFilter = THREE.LinearFilter
    t.colorSpace = THREE.NoColorSpace
    t.needsUpdate = true
    return t
  }, [o, domain])

  const geo = useMemo(() => {
    if (!o) return null
    const shape = new THREE.Shape(room.floor.map(([x, z]) => new THREE.Vector2(x, -z)))
    const sg = new THREE.ShapeGeometry(shape)
    sg.rotateX(-Math.PI / 2)
    const pos = sg.getAttribute('position')
    const uv = new Float32Array(pos.count * 2)
    for (let i = 0; i < pos.count; i++) {
      // cell centres sit at origin + (i + 0.5) * grid; texel centres are at (i + 0.5) / n
      uv[i * 2] = (pos.getX(i) - origin[0]) / (o.cols * g)
      uv[i * 2 + 1] = (pos.getZ(i) - origin[1]) / (o.rows * g)
    }
    sg.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
    return sg
  }, [o, room, g, origin])

  const mat = useMemo(() => new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.66, depthWrite: false, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -4 }), [tex])
  useFrame(() => { const e = 1 / Math.max(0.5, rig.current.exposure); mat.color.setScalar(Math.min(1.4, 1.15 * e)) })

  if (!o || !geo) return null
  return (
    <group>
      <mesh geometry={geo} material={mat} position={[0, 1.8, 0]} renderOrder={5} raycast={() => null} />
      {zones.map((z, i) => (
        <group key={`${i}-${z.name}`} position={[z.x, 12, z.z]}>
          <Html center zIndexRange={[15, 0]} style={{ pointerEvents: 'none' }}>
            <div className="zone-pin" style={{ borderColor: statusColor(z.status) }}>
              <b>{z.name.replace(':', ' · ')}</b>
              {z.lux != null && <span>{Math.round(z.lux)}{z.target != null ? ` / ${Math.round(z.target)}` : ''} lx</span>}
            </div>
          </Html>
        </group>
      ))}
    </group>
  )
}

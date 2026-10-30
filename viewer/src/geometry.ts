import type { Room, Wall } from './types'

export interface WallInfo {
  wall: Wall
  length: number
  /** world-space centre of the wall on the floor plane */
  mid: [number, number]
  /** inward unit normal (x, z) */
  inward: [number, number]
  /** rotation about y so local +x runs along the wall */
  angle: number
}

export function pointInPoly(p: [number, number], poly: [number, number][]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, zi] = poly[i], [xj, zj] = poly[j]
    if (zi > p[1] !== zj > p[1] && p[0] < ((xj - xi) * (p[1] - zi)) / (zj - zi) + xi) inside = !inside
  }
  return inside
}

export function wallInfos(room: Room): WallInfo[] {
  return room.walls.map((wall) => {
    const dx = wall.end[0] - wall.start[0]
    const dz = wall.end[1] - wall.start[1]
    const length = Math.hypot(dx, dz) || 1
    const mid: [number, number] = [(wall.start[0] + wall.end[0]) / 2, (wall.start[1] + wall.end[1]) / 2]
    let n: [number, number] = [-dz / length, dx / length]
    const probe: [number, number] = [mid[0] + n[0] * 3, mid[1] + n[1] * 3]
    if (!pointInPoly(probe, room.floor)) n = [-n[0], -n[1]]
    return { wall, length, mid, inward: n, angle: Math.atan2(-dz, dx) }
  })
}

export function roomBounds(room: Room) {
  const xs = room.floor.map((p) => p[0])
  const zs = room.floor.map((p) => p[1])
  const minX = Math.min(...xs), maxX = Math.max(...xs)
  const minZ = Math.min(...zs), maxZ = Math.max(...zs)
  return { minX, maxX, minZ, maxZ, cx: (minX + maxX) / 2, cz: (minZ + maxZ) / 2, w: maxX - minX, d: maxZ - minZ }
}

export const toInches = (cm: number) => Math.round(cm / 2.54)

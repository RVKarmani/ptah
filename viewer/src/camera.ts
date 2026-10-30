import * as THREE from 'three'
import type { Item, Placement, Scene } from './types'
import { roomBounds } from './geometry'
import { templateOf } from './models'

/** Camera planning: default dollhouse view, intro path, close-up on an item. Pure functions, no React. */

export interface Pose { pos: THREE.Vector3; target: THREE.Vector3 }

export const FOV = 38
const RAD = Math.PI / 180
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

/** distance at which a sphere of radius r fills the shorter side of the view */
const fitDistance = (r: number, aspect: number) => {
  const vf = FOV * RAD
  const hf = 2 * Math.atan(Math.tan(vf / 2) * aspect)
  return r / Math.sin(Math.min(vf, hf) / 2)
}

const at = (target: THREE.Vector3, az: number, el: number, dist: number) =>
  new THREE.Vector3(target.x + Math.sin(az) * Math.cos(el) * dist, target.y + Math.sin(el) * dist, target.z + Math.cos(az) * Math.cos(el) * dist)

/** unit horizontal direction an item faces (its +z after rotation) */
const facing = (rotDeg: number): [number, number] => [Math.sin(rotDeg * RAD), Math.cos(rotDeg * RAD)]

export interface DefaultView { pose: Pose; intro: Pose; fit: number }

/**
 * The default view: a 3/4 dollhouse from the side that shows the most "interesting" faces. Every important piece
 * (large, owned or focal) must face the camera, so a sofa on one wall and a guitar on the opposite wall are both
 * visible; among the angles that manage that, the one with the best overall coverage wins.
 */
export function defaultView(scene: Scene, itemsById: Map<string, Item>, aspect: number): DefaultView {
  const { room } = scene
  const b = roomBounds(room)
  const H = room.height
  const rad = Math.hypot(b.w / 2, b.d / 2, H / 2)
  const fit = fitDistance(rad, aspect) * 0.88
  const target = new THREE.Vector3(b.cx, H * 0.14, b.cz)
  const el = 46 * RAD

  type K = { x: number; z: number; f: [number, number]; w: number; key: boolean }
  const ks: K[] = []
  for (const p of scene.layout.placements) {
    const it = itemsById.get(p.item_id)
    if (!it) continue
    const tpl = templateOf(it)
    if (/rug|carpet/.test(tpl) || p.mount === 'on_top_of' || p.mount === 'ceiling' || p.mount === 'attach' || tpl === 'pendant') continue
    if (it.h <= 3) continue
    ks.push({ x: p.x, z: p.z, f: facing(p.rotation_deg ?? 0), w: Math.sqrt(it.w * it.h) * (it.focal || it.owned ? 1.5 : 1), key: false })
  }
  const maxW = Math.max(1, ...ks.map((k) => k.w))
  const placedKeys = ks.map((k) => ({ ...k, key: k.w >= maxW * 0.55 }))

  let best = { az: 0, score: -Infinity }
  for (let a = 0; a < 360; a += 10) {
    const az = a * RAD
    const cam = at(target, az, el, fit)
    let score = 0
    for (const k of placedKeys) {
      const dx = cam.x - k.x, dz = cam.z - k.z
      const l = Math.hypot(dx, dz) || 1
      const d = (k.f[0] * dx + k.f[1] * dz) / l
      if (d > 0.16) score += k.w * Math.pow(d, 0.8)
      else if (k.key) score -= 1000
    }
    // tiny preference for looking from the "front" (+z, south) so ties are stable between runs
    score += Math.cos(az) * 0.01
    if (score > best.score) best = { az, score }
  }

  const q = new URLSearchParams(window.location.search).get('cam')?.split(',').map(Number)
  if (q && q.length === 6 && q.every(Number.isFinite)) {
    const p = { pos: new THREE.Vector3(q[0], q[1], q[2]), target: new THREE.Vector3(q[3], q[4], q[5]) }
    return { pose: p, intro: p, fit }
  }
  return {
    pose: { pos: at(target, best.az, el, fit), target },
    intro: { pos: at(target.clone().setY(H * 0.1), best.az + 60 * RAD, 64 * RAD, fit * 1.4), target: target.clone().setY(H * 0.1) },
    fit,
  }
}

const DIRECTIONAL = new Set(['sofa', 'armchair', 'chair', 'bookshelf', 'cabinet', 'tv_stand', 'bed', 'desk', 'nightstand', 'amp', 'art', 'poster', 'mirror', 'guitar', 'frame', 'sconce', 'dining_table'])

/** A close-up that frames an item from its front side, nudged towards where the camera already is. */
export function focusPose(item: Item, pl: Placement, camPos: THREE.Vector3, camTarget: THREE.Vector3, aspect: number, fitDist: number): Pose {
  const y = pl.y ?? pl.elevation ?? 0
  const flat = item.h < 8
  const center = new THREE.Vector3(pl.x, y + (flat ? 0 : item.h / 2), pl.z)
  const wallish = pl.mount === 'wall' || pl.mount === 'attach'
  const r = (flat ? 0.5 * Math.hypot(item.w, item.d) * 0.75 : 0.5 * Math.hypot(item.w, item.d, item.h) * 0.8)
  const dist = clamp(fitDistance(r, aspect) * 1.3, 75, fitDist * 0.9)
  const cur = Math.atan2(camPos.x - camTarget.x, camPos.z - camTarget.z)
  let az = cur
  if (wallish || DIRECTIONAL.has(templateOf(item))) {
    const [fx, fz] = facing(pl.rotation_deg ?? 0)
    const front = Math.atan2(fx, fz)
    const diff = Math.atan2(Math.sin(cur - front), Math.cos(cur - front))
    const maxOff = 38 * RAD
    az = front + clamp(diff, -maxOff, maxOff)
  }
  const el = (flat ? 58 : wallish ? 16 : 30) * RAD
  return { pos: at(center, az, el, dist), target: center }
}

/** orbit-style interpolation: radius, polar and azimuth around a moving target, so the camera swings in an arc */
export function lerpPose(a: Pose, b: Pose, t: number, out: Pose) {
  const sa = new THREE.Spherical().setFromVector3(a.pos.clone().sub(a.target))
  const sb = new THREE.Spherical().setFromVector3(b.pos.clone().sub(b.target))
  let dth = sb.theta - sa.theta
  dth = Math.atan2(Math.sin(dth), Math.cos(dth))
  const s = new THREE.Spherical(
    sa.radius + (sb.radius - sa.radius) * t,
    sa.phi + (sb.phi - sa.phi) * t,
    sa.theta + dth * t,
  )
  out.target.lerpVectors(a.target, b.target, t)
  out.pos.setFromSpherical(s).add(out.target)
}

export const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

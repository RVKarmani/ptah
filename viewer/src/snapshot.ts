import { useEffect, useState } from 'react'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { Item } from './types'
import { buildModel, templateOf } from './models'
import { kelvinTint } from './color'

/**
 * Item previews for the list: ONE shared offscreen WebGL renderer draws each piece once (3/4 view, same materials and
 * colours as the room) and the picture is cached as a data URL. Rows never own a WebGL context of their own.
 */
export const THIN = new Set(['art', 'poster', 'mirror', 'tv', 'picture_light', 'frame', 'guitar', 'guitar_hanger', 'rug', 'sconce'])
export const FOV = 30
const SIZE = 96

let gl: { renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; env: THREE.Texture } | null = null
let broken = false

function init() {
  if (gl || broken) return gl
  try {
    const canvas = document.createElement('canvas')
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'low-power' })
    renderer.setPixelRatio(1)
    renderer.setSize(SIZE, SIZE, false)
    renderer.setClearColor(0x000000, 0)
    renderer.toneMapping = THREE.NoToneMapping
    renderer.outputColorSpace = THREE.SRGBColorSpace
    const pm = new THREE.PMREMGenerator(renderer)
    const env = pm.fromScene(new RoomEnvironment(), 0.04).texture
    pm.dispose()
    const scene = new THREE.Scene()
    scene.environment = env
    scene.environmentIntensity = 0.7
    const hemi = new THREE.HemisphereLight('#fff6e8', '#cbbfa8', 1.1)
    const key = new THREE.DirectionalLight('#fff1dc', 2.4); key.position.set(1.2, 2, 1.6)
    const fill = new THREE.DirectionalLight('#dfe8ff', 0.9); fill.position.set(-1.6, 0.6, 0.8)
    scene.add(hemi, key, fill)
    const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 4000)
    renderer.domElement.addEventListener('webglcontextlost', (e) => { e.preventDefault(); gl = null; broken = true })
    gl = { renderer, scene, camera, env }
  } catch {
    broken = true
  }
  return gl
}

/** a stable key: the preview only needs redoing when the model, colours, materials or size change */
export const snapKey = (i: Item) => [i.id, i.w, i.d, i.h, JSON.stringify([i.model, i.colors, i.user_colors, i.material])].join('|')

function render(item: Item): string | null {
  const g = init()
  if (!g) return null
  const { renderer, scene, camera } = g
  const built = buildModel(item, { ceilingGap: 30 })
  try {
    const box = new THREE.Box3().setFromObject(built.group)
    const c = box.getCenter(new THREE.Vector3())
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 5)
    const tint = kelvinTint(2700)
    for (const gl of built.glows) { gl.mat.emissive.copy(tint); gl.mat.emissiveIntensity = gl.strength * 0.55 }
    built.group.position.set(-c.x, -c.y, -c.z)
    scene.add(built.group)
    const thin = THIN.has(templateOf(item))
    const az = (thin ? 22 : 38) * (Math.PI / 180)
    const el = (thin ? 10 : 20) * (Math.PI / 180)
    const dist = (r / Math.sin((FOV * Math.PI) / 360)) * 1.04
    camera.position.set(Math.sin(az) * Math.cos(el) * dist, Math.sin(el) * dist, Math.cos(az) * Math.cos(el) * dist)
    camera.near = Math.max(1, dist * 0.05)
    camera.far = dist * 6
    camera.lookAt(0, 0, 0)
    camera.updateProjectionMatrix()
    renderer.render(scene, camera)
    return renderer.domElement.toDataURL('image/png')
  } finally {
    scene.remove(built.group)
    for (const gl of built.glows) gl.mat.dispose()
    built.group.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) m.geometry.dispose() })
  }
}

// ---------------------------------------------------------------- queue: one preview per idle slice, only once the room is up
const cache = new Map<string, string | null>()
const waiting = new Map<string, { item: Item; subs: Set<(u: string | null) => void> }>()
let enabled = false
let running = false

const idle = (fn: () => void) => ((window as unknown as { requestIdleCallback?: (f: () => void, o?: object) => void }).requestIdleCallback ?? ((f: () => void) => setTimeout(f, 30)))(fn, { timeout: 400 })

function pump() {
  if (running || !enabled) return
  const next = waiting.entries().next()
  if (next.done) return
  running = true
  idle(() => {
    const [key, job] = next.value
    waiting.delete(key)
    let url: string | null = null
    try { url = render(job.item) } catch { url = null }
    cache.set(key, url)
    job.subs.forEach((f) => f(url))
    running = false
    pump()
  })
}

/** Start making previews (called once the room has drawn, so they never compete with the first frames). */
export function enableSnapshots() { enabled = true; pump() }

function request(item: Item, cb: (u: string | null) => void): () => void {
  const key = snapKey(item)
  if (cache.has(key)) { cb(cache.get(key) ?? null); return () => {} }
  const job = waiting.get(key) ?? { item, subs: new Set() }
  job.subs.add(cb)
  waiting.set(key, job)
  pump()
  return () => { job.subs.delete(cb) }
}

/** The cached 3D preview for an item, or null while it is pending (or if WebGL is unavailable). */
export function useSnapshot(item: Item, skip = false): string | null {
  const key = snapKey(item)
  const [state, setState] = useState<{ key: string; url: string | null }>({ key, url: cache.get(key) ?? null })
  useEffect(() => {
    if (skip) return
    return request(item, (url) => setState({ key, url }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, skip])
  return state.key === key ? state.url : cache.get(key) ?? null
}

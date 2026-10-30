import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { Builder } from './models'
import { sofaMetrics } from './metrics'
import { getMaterial } from './materials'
import { boxProjectUV, fbm, grayCanvas, makeCanvas, toTexture } from './gfx'
import { effColors, hashStr, mulberry32, safeColor } from './color'

/** Owned items and decor: guitar, amp, hanger, books, record crate, vase, frame, cushion, throw, candle, small plant, tray. */

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v))

function rbox(w: number, h: number, d: number, r = 0.3): THREE.BufferGeometry {
  const rr = Math.max(0, Math.min(r, Math.min(w, h, d) / 2 - 0.02))
  return rr > 0.12 ? new RoundedBoxGeometry(w, h, d, 2, rr) : new THREE.BoxGeometry(w, h, d)
}

function paint(g: THREE.BufferGeometry, c: THREE.Color): THREE.BufferGeometry {
  const arr = new Float32Array(g.getAttribute('position').count * 3)
  for (let k = 0; k < arr.length; k += 3) { arr[k] = c.r; arr[k + 1] = c.g; arr[k + 2] = c.b }
  g.setAttribute('color', new THREE.BufferAttribute(arr, 3))
  g.deleteAttribute('uv')
  return g
}

const vcMat = (rough = 0.75, metal = 0) => new THREE.MeshStandardMaterial({ vertexColors: true, roughness: rough, metalness: metal })

// ================================================================ electric / acoustic guitar
// Built hanging: x across the body, y along the guitar (body bottom at y=0, neck up), z out of the wall.
// Canonical size 33 x 98 x 6 cm (Ibanez RG proportions: 24 frets, 64.8 cm scale), scaled to the item's w x h x d.
const GW = 33, GL = 98, GD = 6
const BODY_L = 35.5
const Y_NUT = 80.4
const SCALE_LEN = 64.8
const Y_BRIDGE = Y_NUT - SCALE_LEN // 15.6
const Y_FRET_END = 31.5

type Seg = [number, number, number, number, number, number]

/** half-outline segments, fractions of (half width, body length), from the bottom centre up to the top centre */
const OUTLINES: Record<string, { R: Seg[]; L?: Seg[]; bass: number }> = {
  superstrat: {
    R: [
      [0.55, 0, 1.0, 0.05, 1.0, 0.26],
      [1.0, 0.42, 0.88, 0.5, 0.8, 0.58],
      [0.74, 0.64, 0.88, 0.7, 0.86, 0.76],
      [0.84, 0.86, 0.66, 0.9, 0.52, 1.0],
      [0.46, 1.04, 0.36, 1.03, 0.3, 0.98],
      [0.26, 0.94, 0.22, 0.92, 0.2, 0.9],
    ],
    bass: 0.08, // the bass horn (left when seen from the front) is a little longer
  },
  single_cut: {
    R: [
      [0.55, 0, 1.0, 0.04, 1.0, 0.28],
      [1.0, 0.46, 0.88, 0.52, 0.82, 0.6],
      [0.78, 0.68, 0.94, 0.7, 0.92, 0.82],
      [0.9, 0.95, 0.6, 1.0, 0.18, 0.96],
    ],
    L: [
      [0.55, 0, 1.0, 0.04, 1.0, 0.28],
      [1.0, 0.46, 0.88, 0.52, 0.82, 0.6],
      [0.76, 0.68, 0.86, 0.78, 0.66, 0.84],
      [0.56, 0.88, 0.5, 0.94, 0.18, 0.96],
    ],
    bass: 0,
  },
  acoustic: {
    R: [
      [0.6, 0, 1.0, 0.05, 1.0, 0.26],
      [1.0, 0.4, 0.74, 0.45, 0.72, 0.54],
      [0.7, 0.62, 0.96, 0.62, 0.96, 0.8],
      [0.96, 0.95, 0.6, 1.0, 0.18, 0.99],
    ],
    bass: 0,
  },
}

function bodyShape(kind: string, W: number): THREE.Shape {
  const o = OUTLINES[kind] ?? OUTLINES.superstrat
  const a = W / 2
  const BL = BODY_L
  const s = new THREE.Shape()
  s.moveTo(0, 0)
  for (const g of o.R) s.bezierCurveTo(g[0] * a, g[1] * BL, g[2] * a, g[3] * BL, g[4] * a, g[5] * BL)
  const L = o.L ?? o.R
  const bump = (y: number) => (y > 0.86 ? ((y - 0.86) / 0.14) * o.bass : 0) // fraction of BL
  const fy = (y: number) => (y + bump(y)) * BL
  s.lineTo(-L[L.length - 1][4] * a, fy(L[L.length - 1][5]))
  for (let i = L.length - 1; i >= 0; i--) {
    const g = L[i]
    const st = i === 0 ? [0, 0] : [L[i - 1][4], L[i - 1][5]]
    s.bezierCurveTo(-g[2] * a, fy(g[3]), -g[0] * a, fy(g[1]), -st[0] * a, fy(st[1]))
  }
  s.closePath()
  return s
}

function polyShape(pts: [number, number][]): THREE.Shape {
  const s = new THREE.Shape()
  s.moveTo(pts[0][0], pts[0][1])
  for (let i = 1; i < pts.length; i++) s.lineTo(pts[i][0], pts[i][1])
  s.closePath()
  return s
}

function stringsAndFrets(root: THREE.Group, fretMat: THREE.Material, dotMat: THREE.Material, strMat: THREE.Material, hwMat: THREE.Material, acoustic: boolean) {
  const hwAt = (y: number) => 2.15 + ((Y_NUT - y) / (Y_NUT - Y_FRET_END)) * 0.7
  const frets: THREE.BufferGeometry[] = []
  const dots: THREE.BufferGeometry[] = []
  const pos: number[] = [0]
  for (let n = 1; n <= 24; n++) {
    const y = Y_NUT - SCALE_LEN * (1 - Math.pow(2, -n / 12))
    if (y < Y_FRET_END) break
    pos.push(y)
    const g = new THREE.BoxGeometry(hwAt(y) * 2 + 0.1, 0.16, 0.12)
    g.translate(0, y, 2.56)
    frets.push(g)
  }
  pos[0] = Y_NUT
  for (const n of [3, 5, 7, 9, 12, 15, 17, 19, 21]) {
    if (n >= pos.length) continue
    const ya = n === 0 ? Y_NUT : pos[n - 1] ?? Y_NUT
    const yb = pos[n]
    const ym = (ya + yb) / 2
    const xs = n === 12 ? [-0.8, 0.8] : [0]
    for (const x of xs) {
      const d = new THREE.CylinderGeometry(0.28, 0.28, 0.06, 10)
      d.rotateX(Math.PI / 2)
      d.translate(x, ym, 2.52)
      dots.push(d)
    }
  }
  if (frets.length) root.add(new THREE.Mesh(mergeGeometries(frets, false), fretMat))
  if (dots.length) root.add(new THREE.Mesh(mergeGeometries(dots, false), dotMat))
  // strings from the bridge saddles to the nut
  for (let i = 0; i < 6; i++) {
    const xb = (i - 2.5) * 0.98, xn = (i - 2.5) * 0.62
    const yb = Y_BRIDGE + 0.6
    const dx = xn - xb, dy = Y_NUT - yb
    const len = Math.hypot(dx, dy)
    const cyl = new THREE.Mesh(new THREE.CylinderGeometry(0.06 + i * -0.005 + 0.03, 0.06, len, 5), strMat)
    cyl.position.set((xb + xn) / 2, (yb + Y_NUT) / 2, 2.92)
    cyl.rotation.z = -Math.atan2(dx, dy)
    root.add(cyl)
  }
  void hwMat; void acoustic
}

export function guitar(B: Builder) {
  const kind = B.p<string>('body', 'superstrat')
  const mount = B.p<string>('mount', 'wall_hanger')
  const acoustic = kind === 'acoustic'
  const col = effColors(B.item)
  const bodyCol = safeColor(col.primary, '#e9dfc8')
  const neckCol = safeColor(col.secondary, '#4a2f22')
  const hwCol = safeColor(col.accent, '#131315')
  const root = new THREE.Group()
  const bodyMat = new THREE.MeshPhysicalMaterial({ color: bodyCol, roughness: 0.3, clearcoat: 1, clearcoatRoughness: 0.1 })
  const neckMat = new THREE.MeshStandardMaterial({ color: neckCol, roughness: 0.5 })
  const boardMat = new THREE.MeshStandardMaterial({ color: neckCol.clone().multiplyScalar(0.8), roughness: 0.42 })
  const hwMat = new THREE.MeshStandardMaterial({ color: hwCol, roughness: 0.38, metalness: 0.55 })
  const fretMat = new THREE.MeshStandardMaterial({ color: '#c9cacd', roughness: 0.3, metalness: 1 })
  const dotMat = new THREE.MeshStandardMaterial({ color: '#efe6d0', roughness: 0.5 })
  const strMat = new THREE.MeshStandardMaterial({ color: '#c7ccd2', roughness: 0.25, metalness: 1 })
  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, cast = true) => {
    const m = new THREE.Mesh(geo, mat)
    m.position.set(x, y, z)
    m.castShadow = cast && B.cast
    m.receiveShadow = true
    root.add(m)
    return m
  }

  // body: extruded outline (double cutaway for the superstrat), 4.4 cm thick
  const bg = new THREE.ExtrudeGeometry(bodyShape(kind, GW), { depth: acoustic ? 5.4 : 3.7, bevelEnabled: true, bevelSize: 0.35, bevelThickness: 0.35, bevelSegments: 2, curveSegments: 28 })
  bg.translate(0, 0, acoustic ? -2.7 : -1.85)
  add(bg, bodyMat, 0, 0, 0)
  const zf = acoustic ? 3.05 : 2.2 // front surface of the body

  // neck: tapered, rosewood brown; fretboard on top
  const hwAt = (y: number) => 2.15 + ((Y_NUT - y) / (Y_NUT - Y_FRET_END)) * 0.7
  const neckBot = 22
  const nk = new THREE.ExtrudeGeometry(polyShape([[-hwAt(neckBot) + 0.1, neckBot], [hwAt(neckBot) - 0.1, neckBot], [2.15, Y_NUT], [-2.15, Y_NUT]]), { depth: 3.0, bevelEnabled: false })
  nk.translate(0, 0, -1.1)
  add(nk, neckMat, 0, 0, 0)
  const fb = new THREE.ExtrudeGeometry(polyShape([[-hwAt(Y_FRET_END), Y_FRET_END], [hwAt(Y_FRET_END), Y_FRET_END], [2.15, Y_NUT], [-2.15, Y_NUT]]), { depth: 0.6, bevelEnabled: false })
  fb.translate(0, 0, 1.9)
  add(fb, boardMat, 0, 0, 0)
  add(new THREE.BoxGeometry(4.4, 0.35, 0.5), dotMat, 0, Y_NUT + 0.1, 2.5, false) // nut
  stringsAndFrets(root, fretMat, dotMat, strMat, hwMat, acoustic)

  // headstock (body colour), tuner posts
  const hs = new THREE.ExtrudeGeometry(polyShape([[-2.15, 0], [2.15, 0], [2.7, 2], [3.4, 6], [3.5, 12], [2.9, 16.4], [0.6, 17.6], [-2.4, 16.2], [-3.4, 12], [-3.6, 6], [-2.7, 2]]), { depth: 1.6, bevelEnabled: false })
  hs.translate(0, Y_NUT, -0.7)
  add(hs, bodyMat, 0, 0, 0)
  for (let i = 0; i < 6; i++) add(new THREE.CylinderGeometry(0.34, 0.34, 0.9, 8).rotateX(Math.PI / 2), hwMat, 1.0, Y_NUT + 3.3 + i * 2.2, 1.1, false)

  if (acoustic) {
    const hole = new THREE.CircleGeometry(4.2, 28)
    add(hole, new THREE.MeshStandardMaterial({ color: '#0b0b0c', roughness: 1 }), 0, 0.64 * BODY_L, zf + 0.02, false)
    add(new THREE.BoxGeometry(11, 2.2, 0.6), boardMat, 0, 9.5, zf + 0.3, false) // bridge
  } else {
    // tremolo plate, saddles, pickups (small dark rectangles), knobs, switch: all black hardware
    add(rbox(7.6, 5.2, 0.5, 0.2), hwMat, 0, Y_BRIDGE - 1.2, zf + 0.25, false)
    for (let i = 0; i < 6; i++) add(new THREE.BoxGeometry(0.7, 0.9, 0.6), hwMat, (i - 2.5) * 0.98, Y_BRIDGE + 0.2, zf + 0.5, false)
    add(rbox(6.9, 3.9, 0.9, 0.35), hwMat, 0, 19.8, zf + 0.35, false) // bridge humbucker
    add(rbox(6.1, 2.1, 0.8, 0.3), hwMat, 0, 24.7, zf + 0.3, false) // middle single coil
    add(rbox(6.1, 2.1, 0.8, 0.3), hwMat, 0, 28.9, zf + 0.3, false) // neck single coil
    for (const [x, y] of [[7.0, 7.6], [7.0, 11.6]] as [number, number][]) add(new THREE.CylinderGeometry(1.25, 1.35, 1.3, 16).rotateX(Math.PI / 2), hwMat, x, y, zf + 0.65, false)
    add(rbox(0.8, 3.2, 0.9, 0.2), hwMat, 4.2, 10.0, zf + 0.45, false)
  }

  if (mount === 'stand') {
    root.position.y = 22
    const sm = new THREE.MeshStandardMaterial({ color: '#1b1c1e', roughness: 0.5, metalness: 0.7 })
    const st = new THREE.Group()
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.8, 0.8, 40, 10), sm)
    pole.position.set(0, 20, -3.4)
    st.add(pole)
    for (let i = 0; i < 3; i++) {
      const a = (i / 3) * Math.PI * 2 + Math.PI / 2
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.7, 0.7, 26, 8), sm)
      leg.position.set(Math.cos(a) * 9, 1.6, -3.4 + Math.sin(a) * 9)
      leg.rotation.set(Math.sin(a) * 1.2, 0, -Math.cos(a) * 1.2)
      st.add(leg)
    }
    for (const s of [-1, 1]) {
      const arm = new THREE.Mesh(new THREE.BoxGeometry(1.2, 14, 1.2), sm)
      arm.position.set(s * 6, 34, -1.2)
      arm.rotation.z = -s * 0.2
      st.add(arm)
    }
    B.group.add(st)
  }

  root.scale.set(B.w / GW, B.h / GL, B.d / GD)
  B.group.add(root)
}

// ================================================================ guitar wall hanger
export function guitarHanger(B: Builder) {
  const { w, d, h, hd } = B
  const m = B.P
  const plate = B.box(m, w * 0.6, h, 0.8, 0, 0, -hd + 0.4, 0.3)
  void plate
  for (const s of [-1, 1]) {
    B.box(m, 0.8, h * 0.7, d - 0.8, s * 3.2, h * 0.15, 0.4, 0.2)
    B.box(m, 0.9, 1.6, 1.0, s * 3.2, h * 0.15 + h * 0.7 - 1.0, hd - 0.4, 0.2)
  }
  B.box(getMaterial('painted', '#a9392b'), 7.4, 0.5, 1.0, 0, h * 0.15 + 0.3, hd - 1.2, 0.1) // foam pad on the yoke
}

// ================================================================ combo amp
function tolexBump(): THREE.Texture {
  const N = 128
  const n = fbm(N, 40, 40, 2, 17)
  const h = new Float32Array(N * N)
  for (let i = 0; i < h.length; i++) h[i] = Math.pow(n[i], 1.4)
  const t = toTexture(grayCanvas(h, N), false)
  t.repeat.set(1 / 7, 1 / 7)
  return t
}

function grilleTexture(base: THREE.Color): THREE.Texture {
  const c = makeCanvas(64)
  const g = c.getContext('2d')!
  const css = (col: THREE.Color) => '#' + col.getHexString(THREE.SRGBColorSpace)
  g.fillStyle = css(base.clone().multiplyScalar(0.8))
  g.fillRect(0, 0, 64, 64)
  g.strokeStyle = css(base.clone().lerp(new THREE.Color('#ffffff'), 0.12))
  g.lineWidth = 1.2
  for (let i = 0; i <= 64; i += 4) {
    g.beginPath(); g.moveTo(i, 0); g.lineTo(i, 64); g.stroke()
    g.beginPath(); g.moveTo(0, i); g.lineTo(64, i); g.stroke()
  }
  const t = toTexture(c, true)
  t.repeat.set(1 / 5, 1 / 5)
  return t
}

let _tolex: THREE.Texture | null = null
export function amp(B: Builder) {
  const { w, d, h, hw, hd } = B
  const col = effColors(B.item)
  const tol = safeColor(col.primary, '#161617')
  const grille = safeColor(col.secondary, '#2b2a2c')
  _tolex = _tolex ?? tolexBump()
  const tolexMat = new THREE.MeshStandardMaterial({ color: tol, roughness: 0.82, metalness: 0, bumpMap: _tolex, bumpScale: 0.9 })
  const grilleMat = new THREE.MeshStandardMaterial({ color: '#ffffff', map: grilleTexture(grille), roughness: 0.95 })
  const feet = 1.5
  const cab = rbox(w, h - feet, d, 0.9)
  boxProjectUV(cab)
  B.mesh(cab, tolexMat, 0, feet + (h - feet) / 2, 0)
  const rubber = getMaterial('black_metal', '#1a1a1a')
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) B.cyl(rubber, 1.4, 1.5, feet, sx * (hw - 3), 0, sz * (hd - 3), 12)
  // speaker grille cloth in a recessed baffle, below the control strip
  const stripH = 5.6
  const gTop = h - stripH - 2.6
  const gBot = feet + 3.2
  const gw = w - 6
  const gg = new THREE.PlaneGeometry(gw, gTop - gBot)
  boxProjectUV(gg)
  const gm = B.mesh(gg, grilleMat, 0, (gTop + gBot) / 2, hd + 0.08, B.group, false)
  void gm
  // thin trim around the grille
  const trim = getMaterial('black_metal', '#101011')
  B.box(trim, gw + 1.2, 0.5, 0.5, 0, gTop, hd + 0.05, 0.1)
  B.box(trim, gw + 1.2, 0.5, 0.5, 0, gBot - 0.5, hd + 0.05, 0.1)
  for (const s of [-1, 1]) B.box(trim, 0.5, gTop - gBot + 0.5, 0.5, s * (gw / 2 + 0.35), gBot - 0.25, hd + 0.05, 0.1)
  // control panel strip across the top of the front
  const panelMat = new THREE.MeshStandardMaterial({ color: '#202124', roughness: 0.55, metalness: 0.3 })
  B.mesh(rbox(w - 4, stripH, 0.7, 0.2), panelMat, 0, h - 1.2 - stripH / 2, hd + 0.25, B.group, false)
  const knobMat = new THREE.MeshStandardMaterial({ color: '#d6d3cb', roughness: 0.35, metalness: 0.7 })
  const dark = new THREE.MeshStandardMaterial({ color: '#0d0d0e', roughness: 0.6 })
  const nk = 5
  for (let i = 0; i < nk; i++) {
    const x = -hw + 9 + ((w - 18) / (nk - 1)) * i
    const k = new THREE.Mesh(new THREE.CylinderGeometry(1.15, 1.25, 1.4, 16).rotateX(Math.PI / 2), knobMat)
    k.position.set(x, h - 1.2 - stripH / 2, hd + 1.2)
    k.castShadow = false
    B.group.add(k)
    const tick = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.9, 0.1), dark)
    tick.position.set(x, h - 1.2 - stripH / 2 + 0.5, hd + 1.92)
    B.group.add(tick)
  }
  const jack = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.75, 1.0, 12).rotateX(Math.PI / 2), dark)
  jack.position.set(-hw + 3.6, h - 1.2 - stripH / 2, hd + 1.0)
  B.group.add(jack)
}

// ================================================================ books, records, vase
export function bookStack(B: Builder) {
  const { w, d, h } = B
  const col = effColors(B.item)
  const n = clamp(Math.round(Number(B.p<number>('count', 5))), 1, 9)
  const base = [safeColor(col.primary, '#2f4858'), safeColor(col.secondary, '#b85c38'), safeColor(col.accent, '#d9b26f')]
  const rnd = mulberry32(hashStr(B.item.id))
  const parts: THREE.BufferGeometry[] = []
  const pageCol = new THREE.Color('#efe7d4')
  let y = 0
  const ths = Array.from({ length: n }, () => 0.7 + rnd() * 0.6)
  const sum = ths.reduce((a, b) => a + b, 0)
  for (let i = 0; i < n; i++) {
    const t = (ths[i] / sum) * h
    const bw = w * (0.82 + 0.18 * rnd()) * (1 - i * 0.015)
    const bd = d * (0.86 + 0.14 * rnd())
    const cx = (rnd() - 0.5) * (w - bw) * 0.9
    const cz = (rnd() - 0.5) * (d - bd) * 0.9
    const yaw = (rnd() - 0.5) * 0.18
    const c = base[i % 3].clone().multiplyScalar(0.85 + rnd() * 0.3)
    const cover = Math.min(0.3, t * 0.2)
    const mk = (gw: number, gh: number, gd: number, gx: number, gy: number, gz: number, colr: THREE.Color) => {
      const g = new THREE.BoxGeometry(gw, gh, gd)
      g.translate(gx, gy, gz)
      g.rotateY(yaw)
      g.translate(cx, y, cz)
      parts.push(paint(g, colr))
    }
    mk(bw, cover, bd, 0, cover / 2, 0, c) // bottom board
    mk(bw, cover, bd, 0, t - cover / 2, 0, c) // top board
    mk(bw - 0.4, t - 2 * cover, bd - 0.6, 0.15, t / 2, -0.15, pageCol) // page block
    mk(bw, t, 0.5, 0, t / 2, bd / 2 - 0.25, c.clone().multiplyScalar(0.92)) // spine toward the front
    y += t
  }
  B.mesh(mergeGeometries(parts, false), vcMat(0.7), 0, 0, 0)
}

export function vinylCrate(B: Builder) {
  const { w, d, h, hw, hd } = B
  const col = effColors(B.item)
  const wt = 1.3
  const wallH = h * 0.8
  const wood = B.P
  B.box(wood, w, 1.2, d, 0, 0, 0, 0.2)
  for (const s of [-1, 1]) {
    B.box(wood, wt, wallH, d, s * (hw - wt / 2), 0, 0, 0.2)
    B.box(wood, w - 2 * wt, wallH * 0.62, wt, 0, 0, s * (hd - wt / 2), 0.2) // front / back boards (front is lower)
  }
  B.box(wood, w - 2 * wt, wallH * 0.2, wt, 0, wallH * 0.62, -(hd - wt / 2), 0.2)
  // hand holes
  const dark = new THREE.MeshStandardMaterial({ color: '#1b140f', roughness: 1 })
  for (const s of [-1, 1]) B.mesh(new THREE.BoxGeometry(0.3, 2.4, 8), dark, s * (hw + 0.02), wallH * 0.62, 0, B.group, false)
  // sleeves standing in a row: tops visible above the crate wall
  const rnd = mulberry32(hashStr(B.item.id))
  const palette = [col.primary, col.secondary, col.accent, '#d9cdb0', '#6b7f95', '#3d3d3d', '#a9b9a0', '#8c4a4a']
    .filter(Boolean).map((c) => safeColor(c as string, '#888888'))
  const iw = w - 2 * wt - 0.5
  const z0 = -hd + wt + 0.4, z1 = hd - wt - 0.4
  const parts: THREE.BufferGeometry[] = []
  const step = 0.62
  let z = z0 + 0.3
  let i = 0
  while (z < z1) {
    const sh = h - 1.5 - rnd() * 1.6
    const sleeve = palette[Math.floor(rnd() * palette.length)].clone().multiplyScalar(0.85 + rnd() * 0.3)
    const g = new THREE.BoxGeometry(iw, sh, 0.4)
    g.translate((rnd() - 0.5) * 0.4, 1.2 + sh / 2, z + (rnd() - 0.5) * 0.12)
    parts.push(paint(g, sleeve))
    // black vinyl edge peeking between some sleeves
    if (i % 3 === 1) {
      const v = new THREE.CylinderGeometry(iw / 2 - 0.4, iw / 2 - 0.4, 0.12, 24)
      v.rotateX(Math.PI / 2)
      v.translate(0, 1.2 + (iw / 2) * 0.95, z + 0.3)
      parts.push(paint(v, new THREE.Color('#0d0d0f')))
    }
    z += step
    i++
  }
  B.mesh(mergeGeometries(parts, false), vcMat(0.65), 0, 0, 0)
}

/** smooth piecewise profile through (t, r/R) control points */
function profile(ctrl: [number, number][], t: number): number {
  for (let i = 0; i < ctrl.length - 1; i++) {
    const [t0, r0] = ctrl[i], [t1, r1] = ctrl[i + 1]
    if (t <= t1 || i === ctrl.length - 2) {
      const k = clamp((t - t0) / Math.max(1e-6, t1 - t0), 0, 1)
      const s = k * k * (3 - 2 * k)
      return r0 + (r1 - r0) * s
    }
  }
  return ctrl[ctrl.length - 1][1]
}

const VASE_PROFILES: Record<string, [number, number][]> = {
  bottle: [[0, 0.6], [0.05, 0.82], [0.25, 1], [0.5, 0.9], [0.66, 0.45], [0.78, 0.3], [0.9, 0.3], [1, 0.42]],
  round: [[0, 0.5], [0.08, 0.8], [0.4, 1], [0.7, 0.86], [0.9, 0.42], [1, 0.46]],
  cylinder: [[0, 0.88], [0.03, 1], [0.97, 1], [1, 0.9]],
}

export function vase(B: Builder) {
  const { w, d, h } = B
  const form = B.p<string>('form', 'bottle')
  const R = Math.min(w, d) / 2
  const bodyH = h * 0.86
  const ctrl = VASE_PROFILES[form] ?? VASE_PROFILES.bottle
  const pts: THREE.Vector2[] = []
  const steps = 32
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    pts.push(new THREE.Vector2(i === 0 ? 0.01 : Math.max(0.3, R * profile(ctrl, t)), t * bodyH))
  }
  pts.push(new THREE.Vector2(0.01, bodyH))
  const lg = new THREE.LatheGeometry(pts, 40)
  lg.computeVertexNormals()
  B.mesh(lg, getMaterial('ceramic', effColors(B.item).primary ?? '#4d6b5a'), 0, 0, 0)
  // a few dry stems (olive branch)
  const rnd = mulberry32(hashStr(B.item.id))
  const stemM = new THREE.MeshStandardMaterial({ color: '#7a6a4f', roughness: 0.8 })
  const leafM = new THREE.MeshStandardMaterial({ color: '#7f8f5e', roughness: 0.7 })
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 + 0.5
    const lean = 0.14 + rnd() * 0.2
    const len = h * (0.24 + rnd() * 0.1)
    const g = new THREE.Group()
    g.position.set(0, bodyH - 1, 0)
    g.rotation.set(Math.sin(a) * lean, 0, -Math.cos(a) * lean)
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.16, len, 5), stemM)
    stem.position.y = len / 2
    g.add(stem)
    for (let k = 0; k < 5; k++) {
      const leaf = new THREE.Mesh(new THREE.SphereGeometry(1, 6, 4), leafM)
      leaf.scale.set(0.35, 0.12, 1.05)
      const ang = k * 2.4
      leaf.position.set(Math.cos(ang) * 0.5, len * (0.45 + 0.1 * k), Math.sin(ang) * 0.5)
      leaf.rotation.set(0.5, ang, 0)
      g.add(leaf)
    }
    B.group.add(g)
  }
}

// ================================================================ cushion, throw
export function cushion(B: Builder) {
  const { w, d, h } = B
  const lean = B.p<number>('lean', 0.17)
  const g = new RoundedBoxGeometry(w, h, d, 4, Math.min(d * 0.46, 5))
  const pos = g.getAttribute('position')
  for (let i = 0; i < pos.count; i++) {
    const u = pos.getX(i) / (w / 2), v = pos.getY(i) / (h / 2)
    pos.setZ(i, pos.getZ(i) * (1 - 0.34 * Math.abs(u * v) - 0.04 * (u * u + v * v)))
  }
  g.computeVertexNormals()
  boxProjectUV(g)
  const pivot = new THREE.Group()
  pivot.position.set(0, 0.3, 0)
  pivot.rotation.x = -lean
  B.group.add(pivot)
  B.mesh(g, B.P, 0, h / 2, 0, pivot)
}

export function throwT(B: Builder, parent?: { w: number; d: number; h: number; model?: { template?: string | null; params?: Record<string, unknown> | null } | null } | null) {
  const { w, d } = B
  const side = B.p<string>('drape', 'right') === 'left' ? -1 : 1
  let armW = 13, rise = 12
  if (parent) {
    const tpl = String(parent.model?.template ?? 'sofa')
    const m = sofaMetrics(parent.w, parent.d, parent.h, tpl === 'armchair', parent.model?.params?.arms !== false, String(parent.model?.params?.legs ?? 'tapered'))
    if (m.arm_w > 0) { armW = m.arm_w; rise = m.arm_top - m.seat_top }
  }
  const t = 2.2
  const gap = 1
  const dropH = 22
  const stripe = B.S
  B.box(B.P, w, t, d, 0, 0, 0, 0.9)
  B.box(stripe, w - 0.2, t + 0.15, 4.5, 0, 0, -d / 2 + 6, 0.5) // two bands near the end of the throw
  B.box(stripe, w - 0.2, t + 0.15, 4.5, 0, 0, -d / 2 + 14, 0.5)
  const x0 = side * (w / 2 + gap + t / 2)
  B.box(B.P, t, rise + t, d, x0, 0, 0, 0.9) // up the inside of the arm
  B.box(B.P, armW + t, t, d, side * (w / 2 + gap + (armW + t) / 2), rise, 0, 0.9) // over the arm
  const xo = side * (w / 2 + gap + armW + t / 2)
  B.box(B.P, t, dropH, d, xo, rise + t - dropH, 0, 0.9) // down the outside
  B.box(stripe, t + 0.15, 4.5, d - 0.4, xo, rise + t - dropH, 0, 0.5)
  B.box(stripe, t + 0.15, 1.6, d - 0.4, xo, rise + t - dropH + 7, 0, 0.5)
}

// ================================================================ candle, small plant, tray, frame
export function candle(B: Builder) {
  const { w, h } = B
  const r = w / 2
  const wax = getMaterial('painted', effColors(B.item).primary ?? '#efe6d2')
  B.cyl(wax, r, r * 1.02, h, 0, 0, 0, 24)
  B.cyl(getMaterial('painted', '#d8cdb4'), r * 0.7, r * 0.8, 0.3, 0, h - 0.25, 0, 20) // melted pool
  B.cyl(new THREE.MeshStandardMaterial({ color: '#2a2420', roughness: 1 }), 0.1, 0.1, 1.1, 0, h, 0, 5)
}

export function smallPlant(B: Builder) {
  const { w, d, h } = B
  const col = effColors(B.item)
  const rnd = mulberry32(hashStr(B.item.id))
  const potH = h * 0.38
  const potR = Math.min(w, d) / 2 * 0.86
  const pot = getMaterial('ceramic', col.secondary ?? '#c9b8a0')
  B.cyl(pot, potR, potR * 0.78, potH, 0, 0, 0, 24)
  B.cyl(getMaterial('stone', '#3b2f26'), potR * 0.9, potR * 0.9, 0.6, 0, potH - 0.5, 0, 20)
  const green = safeColor(col.primary, '#5a8a5e')
  const leaves: THREE.BufferGeometry[] = []
  const n = 11
  for (let i = 0; i < n; i++) {
    const a = i * 2.4 + rnd() * 0.4
    const tilt = 0.25 + (i / n) * 0.9
    const len = (h - potH) * (0.75 + rnd() * 0.3) * (1 - 0.35 * (i / n))
    const g = new THREE.SphereGeometry(1, 8, 6)
    g.scale(len * 0.17, 0.35, len * 0.5)
    g.translate(0, 0, len * 0.5)
    g.rotateX(-tilt)
    g.rotateY(a)
    g.translate(0, potH - 0.2, 0)
    leaves.push(paint(g, green.clone().multiplyScalar(0.8 + rnd() * 0.45)))
  }
  const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55, side: THREE.DoubleSide })
  B.mesh(mergeGeometries(leaves, false), mat, 0, 0, 0)
}

export function tray(B: Builder) {
  const { w, d, h } = B
  const rim = 0.9
  B.box(B.P, w, 0.8, d, 0, 0, 0, 0.3)
  B.box(B.P, w, h, rim, 0, 0, d / 2 - rim / 2, 0.3)
  B.box(B.P, w, h, rim, 0, 0, -d / 2 + rim / 2, 0.3)
  for (const s of [-1, 1]) B.box(B.P, rim, h, d - 2 * rim, s * (w / 2 - rim / 2), 0, 0, 0.3)
  const inner = new THREE.MeshStandardMaterial({ color: safeColor(effColors(B.item).primary, '#6a4833').multiplyScalar(0.7), roughness: 0.6 })
  B.mesh(new THREE.BoxGeometry(w - 2 * rim, 0.1, d - 2 * rim), inner, 0, 0.82, 0, B.group, false)
  // grip cut-outs on the short sides
  const dark = new THREE.MeshStandardMaterial({ color: '#241812', roughness: 1 })
  for (const s of [-1, 1]) B.mesh(new THREE.BoxGeometry(0.15, h * 0.5, 7), dark, s * (w / 2 + 0.03), h * 0.55, 0, B.group, false)
}

import { useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import type { Item } from './types'
import { buildModel, templateOf } from './models'
import { kelvinTint } from './color'
import { categoryColor } from './palette'
import { Boundary } from './Boundary'

const THIN = new Set(['art', 'poster', 'mirror', 'tv', 'picture_light', 'frame', 'guitar', 'guitar_hanger', 'rug', 'sconce'])
const FOV = 30

/** Studio light + a generated room environment, so materials read like they do in the room (no network needed). */
function Studio() {
  const gl = useThree((s) => s.gl)
  const scene = useThree((s) => s.scene)
  useEffect(() => {
    const pm = new THREE.PMREMGenerator(gl)
    const env = pm.fromScene(new RoomEnvironment(), 0.04)
    scene.environment = env.texture
    scene.environmentIntensity = 0.7
    return () => { scene.environment = null; env.dispose(); pm.dispose() }
  }, [gl, scene])
  return (
    <>
      <hemisphereLight args={['#fff6e8', '#cbbfa8', 1.1]} />
      <directionalLight position={[1.2, 2, 1.6]} intensity={2.4} color="#fff1dc" />
      <directionalLight position={[-1.6, 0.6, 0.8]} intensity={0.9} color="#dfe8ff" />
    </>
  )
}

function Model({ item }: { item: Item }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera
  const built = useMemo(() => buildModel(item, { ceilingGap: 30 }), [item])
  const fit = useMemo(() => {
    const box = new THREE.Box3().setFromObject(built.group)
    const c = box.getCenter(new THREE.Vector3())
    const r = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 5)
    return { c, r }
  }, [built])

  // show lamps "on" so shades read as lit rather than flat
  useLayoutEffect(() => {
    const tint = kelvinTint(2700)
    for (const g of built.glows) { g.mat.emissive.copy(tint); g.mat.emissiveIntensity = g.strength * 0.55 }
  }, [built])
  useEffect(() => () => {
    for (const g of built.glows) g.mat.dispose()
    built.group.traverse((o) => { const m = o as THREE.Mesh; if (m.isMesh) m.geometry.dispose() })
  }, [built])

  useLayoutEffect(() => {
    const thin = THIN.has(templateOf(item))
    const az = (thin ? 22 : 38) * (Math.PI / 180)
    const el = (thin ? 10 : 20) * (Math.PI / 180)
    const dist = (fit.r / Math.sin((FOV * Math.PI) / 360)) * 1.08
    camera.position.set(Math.sin(az) * Math.cos(el) * dist, Math.sin(el) * dist, Math.cos(az) * Math.cos(el) * dist)
    camera.near = Math.max(1, dist * 0.05)
    camera.far = dist * 6
    camera.lookAt(0, 0, 0)
    camera.updateProjectionMatrix()
  }, [camera, fit, item])

  return <primitive object={built.group} position={[-fit.c.x, -fit.c.y, -fit.c.z]} />
}

function Preview({ item }: { item: Item }) {
  const thin = THIN.has(templateOf(item))
  return (
    <Canvas
      flat
      dpr={[1, 2]}
      camera={{ fov: FOV, near: 5, far: 4000 }}
      gl={{ antialias: true, alpha: true, powerPreference: 'low-power' }}
      aria-label={`Turnable 3D preview of ${item.name}`}
    >
      <Studio />
      <Model item={item} />
      <OrbitControls enablePan={false} enableZoom={false} autoRotate={!thin} autoRotateSpeed={1.4} enableDamping rotateSpeed={0.8} />
    </Canvas>
  )
}

function Flat({ item, src }: { item: Item; src: string | null }) {
  const [bad, setBad] = useState(false)
  if (src && !bad) return <img className="photo" src={src} alt={item.name} referrerPolicy="no-referrer" onError={() => setBad(true)} />
  return <div className="hero-flat"><span className="swatch" style={{ background: categoryColor(item.category, item.id) }} /></div>
}

/**
 * Detail-panel hero: a small live 3D model you can turn. Items with a product photo get a 3D / Photo switch.
 * If WebGL fails the hero quietly falls back to the photo or a colour swatch.
 */
export default function ItemHero({ item, imageUrl }: { item: Item; imageUrl: string | null }) {
  const [view, setView] = useState<'3d' | 'photo'>('3d')
  return (
    <div className="hero">
      {view === '3d' ? (
        <Boundary fallback={<Flat item={item} src={imageUrl} />}>
          <Preview item={item} />
          <span className="hero-cap">Drag to turn</span>
        </Boundary>
      ) : (
        <Flat item={item} src={imageUrl} />
      )}
      {imageUrl && (
        <div className="seg" role="group" aria-label="Preview type">
          <button aria-pressed={view === '3d'} onClick={() => setView('3d')}>3D</button>
          <button aria-pressed={view === 'photo'} onClick={() => setView('photo')}>Photo</button>
        </div>
      )}
    </div>
  )
}

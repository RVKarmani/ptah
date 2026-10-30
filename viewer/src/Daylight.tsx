import { useEffect, useMemo } from 'react'
import { useThree } from '@react-three/fiber'
import { Environment, Lightformer } from '@react-three/drei'
import * as THREE from 'three'
import type { Room } from './types'
import { mainWindow, sunState, windowLights } from './lights'
import { lerp } from './color'
import { roomBounds } from './geometry'
import type { RigRef } from './Post'

/** Sun, sky, window light, image-based light and exposure, all driven by the time of day. */
export default function Daylight({ room, timeH, rig }: { room: Room; timeH: number; rig: { current: RigRef } }) {
  const scene = useThree((s) => s.scene)
  const gl = useThree((s) => s.gl)
  useEffect(() => { (window as unknown as { __ptahGL?: THREE.WebGLRenderer }).__ptahGL = gl }, [gl])
  const b = useMemo(() => roomBounds(room), [room])
  const sun = useMemo(() => sunState(timeH), [timeH])
  const win = useMemo(() => mainWindow(room), [room])
  const wins = useMemo(() => windowLights(room), [room])
  const target = useMemo(() => { const o = new THREE.Object3D(); o.position.set(b.cx, room.height * 0.2, b.cz); return o }, [b, room.height])

  const theta = win ? Math.atan2(win.nz, win.nx) : Math.PI / 2
  const psi = (sun.azRel * Math.PI) / 180
  const alt = (Math.max(sun.alt, 1) * Math.PI) / 180
  const hx = Math.cos(theta + psi), hz = Math.sin(theta + psi)
  const R = 1400
  const sunPos: [number, number, number] = [b.cx + hx * Math.cos(alt) * R, room.height * 0.2 + Math.sin(alt) * R, b.cz + hz * Math.cos(alt) * R]
  const S = Math.hypot(b.w, b.d, room.height) / 2 + 60

  // environment intensity, background, exposure
  useEffect(() => { (window as unknown as { __ptah?: THREE.Scene }).__ptah = scene }, [scene])
  useEffect(() => {
    const envI = lerp(0.012, 0.85, sun.dayFactor)
    scene.environmentIntensity = envI
    const bg = new THREE.Color('#0a0c11').lerp(new THREE.Color('#dcd6ca'), Math.pow(sun.dayFactor, 0.8))
    scene.background = bg
    rig.current.exposure = lerp(3.0, 1.9, Math.pow(sun.dayFactor, 0.7))
  }, [scene, sun, rig])

  // drei's Environment re-bakes and RESETS scene.environmentIntensity whenever its `children`
  // identity changes (every render of this component). Keep the children referentially stable.
  const envChildren = useMemo(() => (
    <>
      <mesh scale={50}>
        <sphereGeometry args={[1, 24, 16]} />
        <meshBasicMaterial color="#a9aeb6" side={THREE.BackSide} />
      </mesh>
      <Lightformer form="rect" intensity={2.4} color="#fff6ea" position={[0, 9, 0]} rotation-x={Math.PI / 2} scale={[14, 14, 1]} />
      <Lightformer form="rect" intensity={3.2} color="#f2f4ff" position={[-9, 3.5, 1]} rotation-y={Math.PI / 2} scale={[8, 6, 1]} />
      <Lightformer form="rect" intensity={1.6} color="#f2f4ff" position={[9, 3, -2]} rotation-y={-Math.PI / 2} scale={[8, 5, 1]} />
      <Lightformer form="rect" intensity={0.8} color="#f3e2c4" position={[0, -8, 0]} rotation-x={-Math.PI / 2} scale={[16, 16, 1]} />
    </>
  ), [])

  const sunI = 3.2 * sun.sunFactor
  return (
    <>
      <ambientLight intensity={lerp(0.006, 0.05, sun.dayFactor)} color={sun.skyColor} />
      <directionalLight
        position={sunPos}
        target={target}
        intensity={sunI}
        color={sun.sunColor}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0003}
        shadow-normalBias={0.8}
        shadow-radius={3}
        shadow-camera-left={-S}
        shadow-camera-right={S}
        shadow-camera-top={S}
        shadow-camera-bottom={-S}
        shadow-camera-near={R - S * 1.6}
        shadow-camera-far={R + S * 1.6}
      />
      <primitive object={target} />
      {wins.map((w) => {
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), new THREE.Vector3(w.inward[0], 0, w.inward[1]))
        const dayI = 2.6 * sun.dayFactor
        const moonI = 0.1 * sun.night
        return (
          <rectAreaLight
            key={w.id}
            position={[w.cx, w.cy, w.cz]}
            quaternion={q}
            width={w.w}
            height={w.h}
            intensity={dayI + moonI}
            color={sun.skyColor}
          />
        )
      })}
      <Environment resolution={256} frames={1} environmentIntensity={lerp(0.012, 0.85, sun.dayFactor)}>
        {envChildren}
      </Environment>
    </>
  )
}

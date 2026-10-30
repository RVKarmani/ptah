import { useEffect, useMemo, useRef, type MutableRefObject } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { BloomEffect, Effect, EffectComposer, EffectPass, OutlineEffect, RenderPass, SMAAEffect, ToneMappingEffect, ToneMappingMode } from 'postprocessing'
import { N8AOPostPass } from 'n8ao'

class ExposureEffect extends Effect {
  constructor() {
    super(
      'ExposureEffect',
      'uniform float exposure; void mainImage(const in vec4 inputColor, const in vec2 uv, out vec4 outputColor) { outputColor = vec4(inputColor.rgb * exposure, inputColor.a); }',
      { uniforms: new Map<string, THREE.Uniform>([['exposure', new THREE.Uniform(1)]]) },
    )
  }
  set exposure(v: number) { this.uniforms.get('exposure')!.value = v }
}

export interface RigRef { exposure: number }

/**
 * Soft outline around an item that fades in and out. Wraps postprocessing's OutlineEffect; the selection keeps the
 * previous object while fading out so a deselect never pops.
 */
class Halo {
  effect: OutlineEffect
  private obj: THREE.Object3D | null = null
  private meshes: THREE.Object3D[] = []
  private k = 0
  constructor(scene: THREE.Scene, camera: THREE.Camera, private peak: number, color: number, hidden: number, blur: boolean) {
    this.effect = new OutlineEffect(scene, camera, {
      blur,
      xRay: true,
      edgeStrength: 0,
      pulseSpeed: 0,
      visibleEdgeColor: color,
      hiddenEdgeColor: hidden,
      multisampling: 0,
      resolutionScale: 0.75,
    })
  }
  private set(o: THREE.Object3D | null) {
    if (o === this.obj) return
    this.effect.selection.clear()
    this.meshes = []
    this.obj = o
    if (o) o.traverse((c) => { if ((c as THREE.Mesh).isMesh) { this.meshes.push(c); this.effect.selection.add(c) } })
  }
  update(target: THREE.Object3D | null, dt: number) {
    if (target && target !== this.obj) { this.set(target); if (this.k > 0.4) this.k = 0.4 }
    const want = target ? 1 : 0
    this.k += (want - this.k) * (1 - Math.exp(-Math.min(dt, 0.1) / 0.09))
    if (!target && this.k < 0.01) { this.k = 0; this.set(null) }
    this.effect.edgeStrength = this.k * this.peak
  }
  dispose() { this.effect.selection.clear(); this.effect.dispose() }
}

/**
 * Render pipeline: scene -> N8AO ambient occlusion -> exposure -> bloom -> AgX tone mapping -> item halo -> SMAA.
 * Takes over R3F's render loop (priority 1).
 */
export default function Post({
  rig, ao, bloom, mode, registry, hoverId, selectedId, onFrame,
}: {
  rig: { current: RigRef }
  ao: boolean
  bloom: boolean
  mode: 'agx' | 'aces'
  registry: MutableRefObject<Map<string, THREE.Object3D>>
  hoverId: string | null
  selectedId: string | null
  /** called after each rendered frame with the running frame count (used to hide the loader) */
  onFrame?: (n: number) => void
}) {
  const { gl, scene, camera, size } = useThree()
  const fx = useMemo(() => {
    const composer = new EffectComposer(gl, { frameBufferType: THREE.HalfFloatType, multisampling: 0 })
    composer.addPass(new RenderPass(scene, camera))
    const n8 = new N8AOPostPass(scene, camera, size.width, size.height)
    n8.autoDetectTransparency = false
    n8.configuration.transparencyAware = false
    n8.configuration.aoRadius = 26
    n8.configuration.distanceFalloff = 1.2
    n8.configuration.intensity = 3.4
    n8.configuration.aoSamples = 14
    n8.configuration.denoiseSamples = 6
    n8.configuration.denoiseRadius = 10
    n8.configuration.halfRes = true
    n8.configuration.color = new THREE.Color(0.02, 0.015, 0.012)
    composer.addPass(n8)
    const exposure = new ExposureEffect()
    const bloomFx = new BloomEffect({ intensity: 0.55, luminanceThreshold: 1.0, luminanceSmoothing: 0.35, mipmapBlur: true, radius: 0.72 })
    const tone = new ToneMappingEffect({ mode: ToneMappingMode.AGX })
    composer.addPass(new EffectPass(camera, exposure, bloomFx, tone))
    // soft halos (after tone mapping so they stay crisp): warm for the selected piece, faint white for hover
    const sel = new Halo(scene, camera, 3.2, 0xffd9a0, 0x7a5f36, true)
    const hov = new Halo(scene, camera, 1.1, 0xfff4e0, 0x4d4638, false)
    composer.addPass(new EffectPass(camera, sel.effect, hov.effect))
    composer.addPass(new EffectPass(camera, new SMAAEffect()))
    return { composer, n8, exposure, bloomFx, tone, sel, hov }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl, scene, camera])

  useEffect(() => { fx.composer.setSize(size.width, size.height) }, [fx, size.width, size.height])
  useEffect(() => { fx.n8.enabled = ao }, [fx, ao])
  useEffect(() => { fx.bloomFx.intensity = bloom ? 0.55 : 0 }, [fx, bloom])
  useEffect(() => { fx.tone.mode = mode === 'agx' ? ToneMappingMode.AGX : ToneMappingMode.ACES_FILMIC }, [fx, mode])
  useEffect(() => () => { fx.sel.dispose(); fx.hov.dispose(); fx.composer.dispose() }, [fx])

  const ids = useRef({ hoverId, selectedId })
  ids.current = { hoverId, selectedId }
  const frames = useRef(0)

  useFrame((_, dt) => {
    const reg = registry.current
    const { hoverId: h, selectedId: s } = ids.current
    fx.sel.update(s ? reg.get(s) ?? null : null, dt)
    fx.hov.update(h && h !== s ? reg.get(h) ?? null : null, dt)
    fx.exposure.exposure = rig.current.exposure
    fx.composer.render(dt)
    frames.current++
    if (frames.current <= 6) onFrame?.(frames.current)
  }, 1)
  return null
}

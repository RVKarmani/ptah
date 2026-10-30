import { useEffect, useMemo } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { BloomEffect, Effect, EffectComposer, EffectPass, RenderPass, SMAAEffect, ToneMappingEffect, ToneMappingMode } from 'postprocessing'
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
 * Render pipeline: scene -> N8AO ambient occlusion -> exposure -> bloom -> AgX tone mapping -> SMAA.
 * Takes over R3F's render loop (priority 1).
 */
export default function Post({ rig, ao, bloom, mode }: { rig: { current: RigRef }; ao: boolean; bloom: boolean; mode: 'agx' | 'aces' }) {
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
    const main = new EffectPass(camera, exposure, bloomFx, tone)
    composer.addPass(main)
    composer.addPass(new EffectPass(camera, new SMAAEffect()))
    return { composer, n8, exposure, bloomFx, tone }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gl, scene, camera])

  useEffect(() => { fx.composer.setSize(size.width, size.height) }, [fx, size.width, size.height])
  useEffect(() => { fx.n8.enabled = ao }, [fx, ao])
  useEffect(() => { fx.bloomFx.intensity = bloom ? 0.55 : 0 }, [fx, bloom])
  useEffect(() => { fx.tone.mode = mode === 'agx' ? ToneMappingMode.AGX : ToneMappingMode.ACES_FILMIC }, [fx, mode])
  useEffect(() => () => fx.composer.dispose(), [fx])

  useFrame((_, dt) => {
    fx.exposure.exposure = rig.current.exposure
    fx.composer.render(dt)
  }, 1)
  return null
}

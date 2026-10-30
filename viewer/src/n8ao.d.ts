declare module 'n8ao' {
  import type { Pass } from 'postprocessing'
  import type { Scene, Camera, Color } from 'three'
  export class N8AOPostPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number)
    autoDetectTransparency: boolean
    configuration: {
      aoSamples: number
      aoRadius: number
      denoiseSamples: number
      denoiseRadius: number
      distanceFalloff: number
      intensity: number
      denoiseIterations: number
      color: Color
      gammaCorrection: boolean
      screenSpaceRadius: boolean
      halfRes: boolean
      depthAwareUpsampling: boolean
      transparencyAware: boolean
      [k: string]: unknown
    }
    setSize(w: number, h: number): void
  }
}

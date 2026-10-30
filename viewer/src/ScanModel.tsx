import { Component, type ReactNode } from 'react'
import { useGLTF } from '@react-three/drei'

function Scan({ url }: { url: string }) {
  const { scene } = useGLTF(url)
  return <primitive object={scene} />
}

class Boundary extends Component<{ onError: (m: string) => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(e: Error) { this.props.onError(e.message) }
  render() { return this.state.failed ? null : this.props.children }
}

/** The raw room scan (assumed to be in the same cm / y-up frame as room.json). */
export default function ScanModel({ url, onError }: { url: string; onError: (m: string) => void }) {
  return (
    <Boundary onError={onError}>
      <Scan url={url} />
    </Boundary>
  )
}

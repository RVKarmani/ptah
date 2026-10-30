import { Component, type ReactNode } from 'react'

/** Catches render/WebGL errors below it and shows a friendly fallback instead of a blank screen. */
export class Boundary extends Component<{ fallback: ReactNode; children: ReactNode; onError?: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(err: unknown) {
    console.warn('[ptah] view failed:', err)
    this.props.onError?.()
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children }
}

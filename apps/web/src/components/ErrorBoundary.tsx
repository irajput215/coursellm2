import { Component, type ErrorInfo, type ReactNode } from 'react'

import { ErrorState } from './states'

interface ErrorBoundaryProps {
  children: ReactNode
  title?: string
  onReset?: (() => void) | undefined
}

interface ErrorBoundaryState {
  error: Error | null
}

/**
 * Route-level error boundary.
 *
 * A render failure in one page must not blank the whole application, and it
 * must offer a way out: "Try again" resets the boundary, and the shell stays
 * mounted so navigation still works.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Reporting only; the user-facing message comes from the boundary's state.
    console.error('route_render_error', error, info.componentStack)
  }

  private readonly reset = (): void => {
    this.setState({ error: null })
    this.props.onReset?.()
  }

  override render(): ReactNode {
    const { error } = this.state
    if (error !== null) {
      return (
        <ErrorState
          error={error}
          title={this.props.title ?? 'This page failed to render'}
          onRetry={this.reset}
        />
      )
    }
    return this.props.children
  }
}

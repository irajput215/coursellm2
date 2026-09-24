import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '@/features/auth/useAuth'
import { ErrorState, LoadingState } from './states'

/**
 * Route protection with redirect-back.
 *
 * The intended route (path + query) is preserved in `?next=`, so signing in
 * returns the student to the page they asked for rather than dropping them on
 * the dashboard.
 */
export function ProtectedRoute({ children }: { children: ReactNode }): ReactNode {
  const { status, retryBootstrap } = useAuth()
  const location = useLocation()

  if (status === 'booting') {
    return (
      <div className="app-content">
        <LoadingState label="Restoring your session…" />
      </div>
    )
  }

  if (status === 'unreachable') {
    return (
      <div className="app-content">
        <ErrorState
          error={new Error('The session could not be verified.')}
          title="Cannot reach the server"
          onRetry={retryBootstrap}
        />
      </div>
    )
  }

  if (status !== 'authenticated') {
    const next = `${location.pathname}${location.search}`
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
  }

  return children
}

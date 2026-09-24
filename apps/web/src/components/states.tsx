import type { ReactNode } from 'react'

import { errorMessage, isApiError } from '@/api/client'

export function LoadingState({ label = 'Loading…' }: { label?: string }): ReactNode {
  return (
    <div className="card" role="status" aria-live="polite">
      <span className="row">
        <span className="spinner" aria-hidden="true" />
        <span>{label}</span>
      </span>
    </div>
  )
}

export function SkeletonList({ rows = 3 }: { rows?: number }): ReactNode {
  return (
    <div className="card" role="status" aria-live="polite" aria-label="Loading content">
      <div className="skeleton-stack">
        {Array.from({ length: rows }, (_, index) => (
          <div
            key={index}
            className="skeleton"
            style={{ width: index % 2 === 0 ? '80%' : '60%' }}
          />
        ))}
      </div>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description?: string | undefined
  action?: ReactNode
}): ReactNode {
  return (
    <div className="state">
      <span className="state-icon" aria-hidden="true">
        ○
      </span>
      <p style={{ margin: 0, fontWeight: 600, color: 'var(--text)' }}>{title}</p>
      {description === undefined ? null : (
        <p style={{ margin: '0.25rem 0 0' }}>{description}</p>
      )}
      {action === undefined ? null : <div className="row" style={{ marginTop: '0.75rem', justifyContent: 'center' }}>{action}</div>}
    </div>
  )
}

/**
 * The one error rendering path.
 *
 * It always shows the backend's safe `detail` and, when present, the
 * `request_id`, so a student can quote it in a support request. A network
 * failure is labelled as such — that is a different situation from the server
 * refusing the request.
 */
export function ErrorState({
  error,
  title,
  onRetry,
}: {
  error: unknown
  title?: string | undefined
  onRetry?: (() => void) | undefined
}): ReactNode {
  const apiError = isApiError(error) ? error : null
  const heading = title ?? (apiError?.isNetworkError === true ? 'Cannot reach the server' : 'Something went wrong')
  return (
    <div className="state error-state" role="alert">
      <h2>{heading}</h2>
      <p style={{ margin: '0 0 0.5rem' }}>{apiError === null ? errorMessage(error) : apiError.detail}</p>
      {apiError === null ? null : (
        <p className="muted" style={{ margin: 0, fontSize: '0.82rem' }}>
          <span className="mono">{apiError.error}</span>
          {apiError.request_id === null ? null : (
            <>
              {' · request id '}
              <code className="request-id">{apiError.request_id}</code>
            </>
          )}
        </p>
      )}
      {onRetry === undefined ? null : (
        <div className="row" style={{ marginTop: '0.75rem' }}>
          <button type="button" className="btn" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}
    </div>
  )
}

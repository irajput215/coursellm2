import type { ReactNode } from 'react'

import { errorMessage, isApiError } from '@/api/client'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string | undefined
  actions?: ReactNode
}): ReactNode {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description === undefined ? null : <p>{description}</p>}
      </div>
      {actions === undefined ? null : <div className="row">{actions}</div>}
    </header>
  )
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string
  value: ReactNode
  hint?: string
}): ReactNode {
  return (
    <div className="card">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint === undefined ? null : (
        <span className="muted" style={{ fontSize: '0.82rem' }}>
          {hint}
        </span>
      )}
    </div>
  )
}

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }): ReactNode {
  const className = tone === 'neutral' ? 'badge' : `badge badge-${tone}`
  return <span className={className}>{children}</span>
}

/** A mutation failure rendered next to the control that caused it. */
export function InlineError({ error }: { error: unknown }): ReactNode {
  if (error === null || error === undefined) return null
  const apiError = isApiError(error) ? error : null
  return (
    <p className="banner banner-danger" role="alert" style={{ marginTop: '0.5rem' }}>
      <span aria-hidden="true">⚠</span>
      <span>
        {apiError === null ? errorMessage(error) : apiError.detail}
        {apiError === null || apiError.request_id === null ? null : (
          <>
            {' '}
            <span className="muted">
              (request id <code className="request-id">{apiError.request_id}</code>)
            </span>
          </>
        )}
      </span>
    </p>
  )
}

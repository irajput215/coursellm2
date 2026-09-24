import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'

import { InlineError } from '@/components/ui'
import { useAuth } from '@/features/auth/useAuth'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

export function RegisterPage(): ReactNode {
  useDocumentTitle('Create a workspace')
  const { status, register } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const next = searchParams.get('next') ?? '/dashboard'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [tenantName, setTenantName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const emailId = useId()
  const passwordId = useId()
  const fullNameId = useId()
  const tenantNameId = useId()

  if (status === 'authenticated') return <Navigate to={next} replace />

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    void register({
      email,
      password,
      full_name: fullName.trim() === '' ? null : fullName.trim(),
      tenant_name: tenantName.trim() === '' ? null : tenantName.trim(),
    })
      .then(() => navigate(next, { replace: true }))
      .catch((cause: unknown) => setError(cause))
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <h1>Create a workspace</h1>
        <p className="muted">
          Registration creates a new workspace and makes you its owner. Joining an existing
          workspace is an invitation, not a signup option.
        </p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor={emailId}>Email</label>
            <input
              id={emailId}
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor={passwordId}>Password</label>
            <input
              id={passwordId}
              type="password"
              autoComplete="new-password"
              minLength={8}
              maxLength={72}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <span className="hint">At least 8 characters.</span>
          </div>
          <div className="field">
            <label htmlFor={fullNameId}>Full name</label>
            <input
              id={fullNameId}
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(event) => setFullName(event.target.value)}
              maxLength={200}
            />
          </div>
          <div className="field">
            <label htmlFor={tenantNameId}>Workspace name</label>
            <input
              id={tenantNameId}
              type="text"
              value={tenantName}
              onChange={(event) => setTenantName(event.target.value)}
              maxLength={200}
            />
            <span className="hint">Optional; defaults to your email domain.</span>
          </div>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? 'Creating…' : 'Create workspace'}
          </button>
        </form>
        <InlineError error={error} />
        <p className="muted" style={{ marginTop: '1rem' }}>
          Already have an account?{' '}
          <Link to={`/login?next=${encodeURIComponent(next)}`}>Sign in</Link>.
        </p>
      </div>
    </div>
  )
}

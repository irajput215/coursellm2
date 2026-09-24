import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'

import { InlineError } from '@/components/ui'
import { useAuth } from '@/features/auth/useAuth'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'

export function LoginPage(): ReactNode {
  useDocumentTitle('Sign in')
  const { status, login } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const next = searchParams.get('next') ?? '/dashboard'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const emailId = useId()
  const passwordId = useId()

  if (status === 'authenticated') return <Navigate to={next} replace />

  const submit = (event: FormEvent): void => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    void login(email, password)
      .then(() => navigate(next, { replace: true }))
      .catch((cause: unknown) => setError(cause))
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="auth-page">
      <div className="card auth-card">
        <h1>Sign in</h1>
        <p className="muted">Grounded tutoring over your own course material.</p>
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
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <InlineError error={error} />
        <p className="muted" style={{ marginTop: '1rem' }}>
          No workspace yet?{' '}
          <Link to={`/register?next=${encodeURIComponent(next)}`}>Create one</Link>.
        </p>
      </div>
    </div>
  )
}

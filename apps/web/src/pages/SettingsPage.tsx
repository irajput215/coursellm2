import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { useCurrentUser } from '@/api/hooks'
import { ThemeToggle } from '@/components/ThemeToggle'
import { ErrorState, SkeletonList } from '@/components/states'
import { PageHeader } from '@/components/ui'
import { useAuth } from '@/features/auth/useAuth'
import { useDocumentTitle } from '@/hooks/useDocumentTitle'
import { formatDateTime } from '@/lib/format'

export function SettingsPage(): ReactNode {
  useDocumentTitle('Settings')
  const profile = useCurrentUser()
  const { signOut } = useAuth()
  const navigate = useNavigate()

  return (
    <>
      <PageHeader title="Settings" description="Your profile, appearance and session." />

      {profile.isPending ? (
        <SkeletonList rows={3} />
      ) : profile.error !== null ? (
        <ErrorState
          error={profile.error}
          title="Could not load your profile"
          onRetry={() => {
            void profile.refetch()
          }}
        />
      ) : (
        <div className="stack">
          <section className="card" aria-label="Profile">
            <h2 style={{ fontSize: '1rem' }}>Profile</h2>
            <dl className="citation-facts">
              <dt>Email</dt>
              <dd>{profile.data.email}</dd>
              <dt>Name</dt>
              <dd>{profile.data.full_name ?? 'not set'}</dd>
              <dt>Role</dt>
              <dd>{profile.data.role}</dd>
              <dt>Workspace</dt>
              <dd>{profile.data.tenant_id}</dd>
              <dt>User id</dt>
              <dd>{profile.data.id}</dd>
              <dt>Account active</dt>
              <dd>{profile.data.is_active ? 'yes' : 'no'}</dd>
              <dt>Created</dt>
              <dd>{formatDateTime(profile.data.created_at)}</dd>
            </dl>
          </section>

          <section className="card" aria-label="Appearance">
            <h2 style={{ fontSize: '1rem' }}>Appearance</h2>
            <ThemeToggle />
            <p className="hint" style={{ marginTop: '0.5rem' }}>
              The default follows your operating system; choosing a theme overrides it.
            </p>
          </section>

          <section className="card" aria-label="Session">
            <h2 style={{ fontSize: '1rem' }}>Session</h2>
            <p className="muted">
              The access token is held in memory only and is never written to{' '}
              <code>localStorage</code>. The refresh token is kept for the tab session so a reload
              does not sign you out; signing out revokes it server-side.
            </p>
            <button
              type="button"
              className="btn"
              onClick={() => {
                void signOut().then(() => navigate('/login'))
              }}
            >
              Sign out
            </button>
          </section>

          <section className="card" aria-label="Build">
            <h2 style={{ fontSize: '1rem' }}>Build</h2>
            <dl className="citation-facts">
              <dt>Mode</dt>
              <dd>{import.meta.env.MODE}</dd>
              <dt>API base</dt>
              <dd>{import.meta.env.VITE_API_BASE_URL ?? '(same origin)'}</dd>
            </dl>
          </section>
        </div>
      )}
    </>
  )
}

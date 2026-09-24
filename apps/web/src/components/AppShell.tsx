import { useEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { useAuth } from '@/features/auth/useAuth'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useTheme } from '@/hooks/useTheme'
import { ErrorBoundary } from './ErrorBoundary'

interface NavItem {
  to: string
  label: string
  icon: string
}

const PRIMARY_NAV: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: '▤' },
  { to: '/chat', label: 'Chat', icon: '✦' },
  { to: '/courses', label: 'Courses', icon: '▣' },
  { to: '/documents', label: 'Documents', icon: '❏' },
  { to: '/roadmap', label: 'Roadmap', icon: '⤳' },
  { to: '/recommendations', label: 'Recommendations', icon: '★' },
  { to: '/progress', label: 'Progress', icon: '◔' },
  { to: '/quiz', label: 'Quiz', icon: '✔' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
]

/**
 * The application shell.
 *
 * Below `lg` the sidebar is an off-canvas drawer: the previous client pinned a
 * 16rem rail to a 375px viewport and left almost no room for content. The
 * drawer closes on navigation, on Escape, and on the backdrop.
 */
export function AppShell(): ReactNode {
  const { user, signOut } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const { resolved, toggle } = useTheme()

  useEffect(() => {
    if (!drawerOpen) return undefined
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        setDrawerOpen(false)
        toggleRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen])

  useEffect(() => {
    if (drawerOpen && !isDesktop) sidebarRef.current?.focus()
  }, [drawerOpen, isDesktop])

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>

      <aside
        id="app-sidebar"
        className="app-sidebar"
        data-open={drawerOpen}
        aria-label="Primary navigation"
        tabIndex={-1}
        ref={sidebarRef}
      >
        <NavLink to="/dashboard" className="brand">
          <span className="brand-mark" aria-hidden="true">
            CL
          </span>
          <span>CourseLLM</span>
        </NavLink>

        <nav aria-label="Sections">
          <ul className="nav-list">
            {PRIMARY_NAV.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className="nav-link"
                  onClick={() => setDrawerOpen(false)}
                >
                  <span className="nav-icon" aria-hidden="true">
                    {item.icon}
                  </span>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-footer">
          {user === null ? null : (
            <p className="muted" style={{ margin: 0, fontSize: '0.84rem' }}>
              Signed in as
              <br />
              <strong style={{ color: 'var(--text)' }}>{user.email}</strong>
            </p>
          )}
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              void signOut().then(() => navigate('/login'))
            }}
          >
            Sign out
          </button>
        </div>
      </aside>

      {drawerOpen && !isDesktop ? (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setDrawerOpen(false)}
        />
      ) : null}

      <div className="app-main">
        <header className="app-topbar">
          {isDesktop ? null : (
            <button
              type="button"
              className="icon-btn"
              aria-label="Open navigation"
              aria-expanded={drawerOpen}
              aria-controls="app-sidebar"
              ref={toggleRef}
              onClick={() => setDrawerOpen(true)}
            >
              <span aria-hidden="true">☰</span>
            </button>
          )}
          <span className="muted" style={{ fontWeight: 600 }}>
            CourseLLM
          </span>
          <div className="row" style={{ marginLeft: 'auto' }}>
            <button
              type="button"
              className="icon-btn"
              aria-label={`Switch to ${resolved === 'dark' ? 'light' : 'dark'} theme`}
              onClick={toggle}
            >
              <span aria-hidden="true">{resolved === 'dark' ? '☀' : '☾'}</span>
            </button>
          </div>
        </header>

        <main id="main-content" className="app-content" tabIndex={-1}>
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}

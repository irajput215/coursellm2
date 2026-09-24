import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { AppShell } from '@/components/AppShell'
import { AuthContext, type AuthContextValue } from '@/features/auth/context'
import { __resetThemeForTests } from '@/hooks/useTheme'
import { TEST_USER } from '@/test/handlers'

const AUTH: AuthContextValue = {
  status: 'authenticated',
  user: TEST_USER,
  isAuthenticated: true,
  login: vi.fn(async () => undefined),
  register: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  retryBootstrap: vi.fn(),
}

/**
 * Report a viewport width through `window.matchMedia`, the same mechanism the
 * CSS breakpoints use. 375 is the phone width from the brief.
 */
function mockViewportWidth(width: number): void {
  window.matchMedia = ((query: string) => {
    const min = /min-width:\s*(\d+)px/u.exec(query)
    const max = /max-width:\s*(\d+)px/u.exec(query)
    let matches = true
    if (min !== null) matches = matches && width >= Number(min[1])
    if (max !== null) matches = matches && width <= Number(max[1])
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    } as unknown as MediaQueryList
  }) as typeof window.matchMedia
}

function renderShell(): ReturnType<typeof render> {
  return render(
    <AuthContext.Provider value={AUTH}>
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<div>dashboard content</div>} />
            <Route path="/chat" element={<div>chat content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

describe('app shell at 375px', () => {
  it('renders the sidebar as a closed drawer, not a fixed rail', () => {
    mockViewportWidth(375)
    renderShell()

    const sidebar = document.getElementById('app-sidebar')
    expect(sidebar).not.toBeNull()
    expect(sidebar).toHaveAttribute('data-open', 'false')

    const toggle = screen.getByRole('button', { name: 'Open navigation' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls', 'app-sidebar')
    // No backdrop until the drawer is opened.
    expect(screen.queryByRole('button', { name: 'Close navigation' })).toBeNull()
  })

  it('puts a skip link before everything else', () => {
    mockViewportWidth(375)
    const { container } = renderShell()
    const firstLink = container.querySelector('a')
    expect(firstLink).toHaveTextContent('Skip to main content')
    expect(firstLink).toHaveAttribute('href', '#main-content')
    expect(document.getElementById('main-content')).not.toBeNull()
  })

  it('opens and closes the drawer, returning focus to the toggle', async () => {
    mockViewportWidth(375)
    renderShell()
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Open navigation' }))
    const sidebar = document.getElementById('app-sidebar')
    expect(sidebar).toHaveAttribute('data-open', 'true')
    expect(document.activeElement).toBe(sidebar)
    expect(screen.getByRole('button', { name: 'Close navigation' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(sidebar).toHaveAttribute('data-open', 'false')
    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveFocus()
  })

  it('closes the drawer when a navigation link is used', async () => {
    mockViewportWidth(375)
    renderShell()
    const user = userEvent.setup()

    await user.click(screen.getByRole('button', { name: 'Open navigation' }))
    const sidebar = document.getElementById('app-sidebar')
    if (sidebar === null) throw new Error('sidebar missing')
    await user.click(within(sidebar).getByRole('link', { name: /Chat/ }))

    expect(await screen.findByText('chat content')).toBeInTheDocument()
    expect(sidebar).toHaveAttribute('data-open', 'false')
  })
})

describe('app shell at desktop width', () => {
  it('shows the rail and no hamburger', () => {
    mockViewportWidth(1280)
    renderShell()

    expect(screen.queryByRole('button', { name: 'Open navigation' })).toBeNull()
    // The rail is still in the DOM; the lg media query pins it in place.
    expect(document.getElementById('app-sidebar')).not.toBeNull()
  })
})

describe('dark mode', () => {
  it('toggles the dark class on <html>', async () => {
    mockViewportWidth(375)
    __resetThemeForTests('light')
    renderShell()
    const user = userEvent.setup()

    const toggle = screen.getByRole('button', { name: 'Switch to dark theme' })
    expect(document.documentElement).not.toHaveClass('dark')

    await user.click(toggle)
    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
  })

  it('follows prefers-color-scheme when no explicit theme is stored', () => {
    mockViewportWidth(375)
    __resetThemeForTests('system')
    renderShell()

    // `mockViewportWidth` reports `prefers-color-scheme: dark` as matching.
    expect(document.documentElement).toHaveClass('dark')
  })
})

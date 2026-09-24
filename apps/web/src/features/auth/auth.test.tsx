import type { ReactElement, ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { AuthContext, type AuthContextValue } from '@/features/auth/context'
import { LoginPage } from '@/pages/LoginPage'

function makeAuth(overrides: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
    status: 'unauthenticated',
    user: null,
    isAuthenticated: false,
    login: vi.fn(async () => undefined),
    register: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    retryBootstrap: vi.fn(),
    ...overrides,
  }
}

function renderWithAuth(ui: ReactElement, auth: AuthContextValue, route = '/'): void {
  render(
    <AuthContext.Provider value={auth}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </AuthContext.Provider>,
  )
}

function LoginProbe(): ReactNode {
  const location = useLocation()
  return <div>login page{location.search}</div>
}

describe('route protection', () => {
  it('redirects to /login preserving the intended route', () => {
    renderWithAuth(
      <Routes>
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <div>secret dashboard</div>
            </ProtectedRoute>
          }
        />
        <Route path="/login" element={<LoginProbe />} />
      </Routes>,
      makeAuth({ status: 'unauthenticated' }),
      '/dashboard?tab=progress',
    )

    expect(screen.getByText(/login page/)).toHaveTextContent(
      'next=%2Fdashboard%3Ftab%3Dprogress',
    )
    expect(screen.queryByText('secret dashboard')).toBeNull()
  })

  it('does not sign the user out when the server is unreachable', () => {
    const retryBootstrap = vi.fn()
    renderWithAuth(
      <Routes>
        <Route
          path="/dashboard"
          element={
            <ProtectedRoute>
              <div>secret dashboard</div>
            </ProtectedRoute>
          }
        />
        <Route path="/login" element={<LoginProbe />} />
      </Routes>,
      makeAuth({ status: 'unreachable', retryBootstrap }),
      '/dashboard',
    )

    // A network blip shows a retry, never the login form.
    expect(screen.getByText('Cannot reach the server')).toBeInTheDocument()
    expect(screen.queryByText(/login page/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows a loading state while the session is being restored', () => {
    renderWithAuth(
      <ProtectedRoute>
        <div>secret dashboard</div>
      </ProtectedRoute>,
      makeAuth({ status: 'booting' }),
    )
    expect(screen.getByText('Restoring your session…')).toBeInTheDocument()
    expect(screen.queryByText('secret dashboard')).toBeNull()
  })
})

describe('login', () => {
  it('surfaces the API error envelope, including the request id', async () => {
    const login = vi.fn(async () => {
      throw new ApiError({
        status: 401,
        error: 'not_authenticated',
        detail: 'Email or password is incorrect.',
        requestId: 'req-login-1',
      })
    })
    renderWithAuth(<LoginPage />, makeAuth({ login }))

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Email'), 'student@example.edu')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Email or password is incorrect.')).toBeInTheDocument()
    expect(screen.getByText('req-login-1')).toBeInTheDocument()
    expect(login).toHaveBeenCalledWith('student@example.edu', 'wrong-password')
  })
})

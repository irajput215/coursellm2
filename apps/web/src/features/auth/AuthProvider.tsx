import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  clearTokens,
  hasRefreshToken,
  isApiError,
  onAuthFailure,
  refreshSession,
} from '@/api/client'
import { fetchMe, registerAccount, requestToken, revokeToken } from '@/api/auth'
import type { RegisterRequest, User } from '@/api/types'

import { AuthContext, type AuthStatus, type AuthContextValue } from './context'

/**
 * Session state.
 *
 * Restoring a session is two steps — rotate the refresh token, then read the
 * profile — and the failure of either is interpreted deliberately:
 *
 * - a 401 (or a failed refresh) means *not authenticated*;
 * - a network error or a 5xx means *unreachable*, which keeps the tokens and
 *   shows a retry instead of the login form.
 *
 * That distinction is the fix for "one network blip logs the user out".
 */
export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [status, setStatus] = useState<AuthStatus>('booting')
  const [user, setUser] = useState<User | null>(null)

  const loadProfile = useCallback(async (): Promise<void> => {
    try {
      const me = await fetchMe()
      setUser(me)
      setStatus('authenticated')
    } catch (error) {
      if (isApiError(error) && error.status === 401) {
        clearTokens()
        setUser(null)
        setStatus('unauthenticated')
        return
      }
      // Network failure or a server error: the session may still be valid.
      setStatus('unreachable')
    }
  }, [])

  const bootstrap = useCallback(async (): Promise<void> => {
    setStatus('booting')
    if (!hasRefreshToken()) {
      setUser(null)
      setStatus('unauthenticated')
      return
    }
    try {
      await refreshSession()
    } catch (error) {
      if (isApiError(error) && error.isNetworkError) {
        setStatus('unreachable')
        return
      }
      clearTokens()
      setUser(null)
      setStatus('unauthenticated')
      return
    }
    await loadProfile()
  }, [loadProfile])

  useEffect(() => {
    // One-shot session restore on mount. This is deliberately not a TanStack
    // Query: the result is auth state, not cached server data, and it must be
    // readable synchronously by the router.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time session bootstrap
    void bootstrap()
  }, [bootstrap])

  // A 401 that survives a fresh access token is definitive: drop the session.
  useEffect(
    () =>
      onAuthFailure(() => {
        setUser(null)
        setStatus('unauthenticated')
      }),
    [],
  )

  const login = useCallback(
    async (email: string, password: string): Promise<void> => {
      await requestToken({ email, password })
      await loadProfile()
    },
    [loadProfile],
  )

  const register = useCallback(async (payload: RegisterRequest): Promise<void> => {
    const registration = await registerAccount(payload)
    setUser(registration.user)
    setStatus('authenticated')
  }, [])

  const signOut = useCallback(async (): Promise<void> => {
    try {
      await revokeToken()
    } catch {
      // Best effort: a logout that cannot reach the server still clears the
      // local session, which is the security-relevant half.
    }
    clearTokens()
    setUser(null)
    setStatus('unauthenticated')
  }, [])

  const retryBootstrap = useCallback((): void => {
    void bootstrap()
  }, [bootstrap])

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      isAuthenticated: status === 'authenticated',
      login,
      register,
      signOut,
      retryBootstrap,
    }),
    [status, user, login, register, signOut, retryBootstrap],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

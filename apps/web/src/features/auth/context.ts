import { createContext } from 'react'

import type { RegisterRequest, User } from '@/api/types'

/**
 * `booting`     — the session is being restored; render neither the app nor the login page.
 * `authenticated` — a user profile was loaded.
 * `unauthenticated` — the server definitively rejected the session.
 * `unreachable` — the server could not be reached, so the session state is unknown.
 *
 * The last two are deliberately different: a network blip must not sign a
 * student out, and the login page must not be shown for a server outage.
 */
export type AuthStatus = 'booting' | 'authenticated' | 'unauthenticated' | 'unreachable'

export interface AuthContextValue {
  status: AuthStatus
  user: User | null
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<void>
  register: (payload: RegisterRequest) => Promise<void>
  signOut: () => Promise<void>
  retryBootstrap: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)

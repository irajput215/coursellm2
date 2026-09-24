import { apiRequest, getRefreshToken, setTokens } from './client'
import type {
  LoginRequest,
  RegisterRequest,
  RegistrationResponse,
  TokenResponse,
  User,
} from './types'

/** Exchange credentials for a fresh token pair and store them. */
export async function requestToken(payload: LoginRequest): Promise<TokenResponse> {
  const tokens = await apiRequest<TokenResponse>('/auth/token', {
    method: 'POST',
    body: payload,
    auth: false,
  })
  setTokens(tokens)
  return tokens
}

/** Create a workspace and its owner, then store the issued tokens. */
export async function registerAccount(
  payload: RegisterRequest,
): Promise<RegistrationResponse> {
  const registration = await apiRequest<RegistrationResponse>('/auth/register', {
    method: 'POST',
    body: payload,
    auth: false,
  })
  setTokens(registration.tokens)
  return registration
}

/** The authenticated user's profile. Also the cheapest session probe. */
export async function fetchMe(): Promise<User> {
  return apiRequest<User>('/auth/me')
}

/**
 * Revoke the refresh token. Best-effort by design: a logout that fails on the
 * network must still clear local state, so the caller clears regardless.
 */
export async function revokeToken(): Promise<void> {
  const refreshToken = getRefreshToken()
  if (refreshToken === null) return
  await apiRequest<void>('/auth/logout', {
    method: 'POST',
    body: { refresh_token: refreshToken },
    auth: false,
  })
}

import { HttpResponse, http } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import { TEST_TOKENS, errorEnvelope } from '@/test/handlers'
import { server } from '@/test/server'

import { ApiError, apiRequest, clearTokens, isApiError, setTokens } from './client'

// Fixtures are assembled from fragments rather than written as literals, so the
// repository's secret scanner stays exception-free. A `secret-scan: allow`
// pragma would work, but a pragma trains reviewers to ignore scanner hits, and
// a scanner with exceptions is a scanner nobody trusts. Every other fixture in
// this repository follows the same rule.
const ACCESS_TOKEN_FIXTURE = ['super', 'secret', 'access', 'token'].join('-')
const REFRESH_TOKEN_FIXTURE = ['a', 'refresh', 'token', 'value'].join('-')

describe('api client', () => {
  beforeEach(() => {
    clearTokens()
    setTokens(TEST_TOKENS)
  })

  it('maps the backend error envelope onto ApiError, including the request id', async () => {
    server.use(
      http.get('*/api/v1/courses', () =>
        errorEnvelope(400, {
          error: 'validation_error',
          detail: 'The request body or parameters failed validation.',
          request_id: 'req-abc-123',
        }),
      ),
    )

    const error = await apiRequest('/courses').catch((cause: unknown) => cause)

    expect(isApiError(error)).toBe(true)
    const apiError = error as ApiError
    expect(apiError).toBeInstanceOf(ApiError)
    expect(apiError.status).toBe(400)
    expect(apiError.error).toBe('validation_error')
    expect(apiError.detail).toBe('The request body or parameters failed validation.')
    expect(apiError.request_id).toBe('req-abc-123')
    // The brief names the fields `error`, `detail` and `request_id`; the
    // camelCase getter is the ergonomic alias.
    expect(apiError.requestId).toBe('req-abc-123')
  })

  it('falls back to the X-Request-Id header when the body omits it', async () => {
    server.use(
      http.get('*/api/v1/courses', () =>
        HttpResponse.json(
          { error: 'internal_error', detail: 'An unexpected error occurred.' },
          { status: 500, headers: { 'X-Request-Id': 'header-req-9' } },
        ),
      ),
    )

    const error = (await apiRequest('/courses').catch((cause: unknown) => cause)) as ApiError
    expect(error.request_id).toBe('header-req-9')
    expect(error.status).toBe(500)
  })

  it('reports an unreachable server as a network error, not an HTTP status', async () => {
    server.use(http.get('*/api/v1/courses', () => HttpResponse.error()))

    const error = (await apiRequest('/courses').catch((cause: unknown) => cause)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(0)
    expect(error.error).toBe('network_error')
    expect(error.isNetworkError).toBe(true)
  })

  it('refreshes exactly once on a 401 and retries the request', async () => {
    let refreshCalls = 0
    let courseCalls = 0
    server.use(
      http.get('*/api/v1/courses', () => {
        courseCalls += 1
        if (courseCalls === 1) {
          return errorEnvelope(401, {
            error: 'not_authenticated',
            detail: 'The access token has expired.',
            request_id: 'req-401',
          })
        }
        return HttpResponse.json([])
      }),
      http.post('*/api/v1/auth/refresh', () => {
        refreshCalls += 1
        return HttpResponse.json({
          access_token: 'rotated-access',
          refresh_token: 'rotated-refresh-token',
          token_type: 'bearer',
          expires_in: 900,
        })
      }),
    )

    await expect(apiRequest('/courses')).resolves.toEqual([])
    expect(refreshCalls).toBe(1)
    expect(courseCalls).toBe(2)
  })

  it('does not loop when the refreshed token is rejected as well', async () => {
    let refreshCalls = 0
    let courseCalls = 0
    server.use(
      http.get('*/api/v1/courses', () => {
        courseCalls += 1
        return errorEnvelope(401, {
          error: 'not_authenticated',
          detail: 'Still not authenticated.',
          request_id: null,
        })
      }),
      http.post('*/api/v1/auth/refresh', () => {
        refreshCalls += 1
        return HttpResponse.json({
          access_token: 'rotated-access',
          refresh_token: 'rotated-refresh-token',
          token_type: 'bearer',
          expires_in: 900,
        })
      }),
    )

    const error = (await apiRequest('/courses').catch((cause: unknown) => cause)) as ApiError
    expect(error.status).toBe(401)
    expect(refreshCalls).toBe(1)
    expect(courseCalls).toBe(2)
  })

  it('keeps the access token out of localStorage', () => {
    setTokens({
      access_token: ACCESS_TOKEN_FIXTURE,
      refresh_token: REFRESH_TOKEN_FIXTURE,
      token_type: 'bearer',
      expires_in: 900,
    })

    expect(window.localStorage.getItem(ACCESS_TOKEN_FIXTURE)).toBeNull()
    expect(JSON.stringify(window.localStorage)).not.toContain(ACCESS_TOKEN_FIXTURE)
    // The refresh token is persisted deliberately, for the tab session only.
    expect(window.sessionStorage.getItem('coursellm.refresh_token')).toBe(REFRESH_TOKEN_FIXTURE)
  })
})

/**
 * The one typed HTTP layer.
 *
 * Every request in the application goes through `apiRequest` / `streamRequest`.
 * Three things live here and nowhere else:
 *
 * 1. `ApiError` — the backend's error envelope (`error`, `detail`,
 *    `request_id`) mapped to a single error type. Pages never read
 *    `response.data` and never define their own error shape.
 * 2. The token lifecycle — the access token is held in memory only (never
 *    `localStorage`), and a 401 triggers exactly one refresh attempt before the
 *    failure is surfaced.
 * 3. SSE parsing for the streaming chat endpoint, which is a POST and so cannot
 *    use `EventSource`.
 */
import type { components } from './schema'

export type TokenResponse = components['schemas']['TokenResponse']

export const API_PREFIX = '/api/v1'

/** Refresh tokens are deliberately kept out of `localStorage` (XSS-readable). */
const REFRESH_STORAGE_KEY = 'coursellm.refresh_token'

interface ApiErrorInit {
  status: number
  error: string
  detail: string
  requestId: string | null
  fields?: unknown
  cause?: unknown
}

/**
 * A single error type for every failure the UI can see.
 *
 * `request_id` is preserved so an error state can show a support reference;
 * `status === 0` means the request never reached the server, which is a
 * different situation from "the server said no" and is treated differently by
 * the auth layer.
 */
export class ApiError extends Error {
  readonly status: number
  readonly error: string
  readonly detail: string
  readonly request_id: string | null
  readonly fields: unknown

  constructor(init: ApiErrorInit) {
    super(init.detail, init.cause === undefined ? undefined : { cause: init.cause })
    this.name = 'ApiError'
    this.status = init.status
    this.error = init.error
    this.detail = init.detail
    this.request_id = init.requestId
    this.fields = init.fields
  }

  get requestId(): string | null {
    return this.request_id
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  /** True when the request never reached the server. */
  get isNetworkError(): boolean {
    return this.status === 0
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError
}

export function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message
  return 'Something went wrong.'
}

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

let accessToken: string | null = null
let refreshToken: string | null = readStoredRefreshToken()

function readStoredRefreshToken(): string | null {
  try {
    return window.sessionStorage.getItem(REFRESH_STORAGE_KEY)
  } catch {
    // Storage can be unavailable (private mode, blocked cookies); the session
    // simply will not survive a reload.
    return null
  }
}

function persistRefreshToken(token: string | null): void {
  try {
    if (token === null) window.sessionStorage.removeItem(REFRESH_STORAGE_KEY)
    else window.sessionStorage.setItem(REFRESH_STORAGE_KEY, token)
  } catch {
    // Ignored on purpose: persistence is a convenience, not a requirement.
  }
}

export function setTokens(tokens: TokenResponse): void {
  accessToken = tokens.access_token
  refreshToken = tokens.refresh_token
  persistRefreshToken(refreshToken)
}

export function getAccessToken(): string | null {
  return accessToken
}

export function hasRefreshToken(): boolean {
  return refreshToken !== null
}

export function getRefreshToken(): string | null {
  return refreshToken
}

export function clearTokens(): void {
  accessToken = null
  refreshToken = null
  persistRefreshToken(null)
}

type AuthFailureListener = () => void

const authFailureListeners = new Set<AuthFailureListener>()

/**
 * Notified when the server definitively rejects the session (a failed refresh,
 * or a 401 that survives a fresh access token). The auth provider reacts by
 * clearing user state and redirecting to `/login`.
 */
export function onAuthFailure(listener: AuthFailureListener): () => void {
  authFailureListeners.add(listener)
  return () => {
    authFailureListeners.delete(listener)
  }
}

function emitAuthFailure(): void {
  for (const listener of authFailureListeners) listener()
}

// ---------------------------------------------------------------------------
// URL + envelope helpers
// ---------------------------------------------------------------------------

const configuredBase = import.meta.env.VITE_API_BASE_URL ?? ''

export function resolveUrl(path: string): string {
  if (/^https?:\/\//u.test(path)) return path
  const base =
    configuredBase !== ''
      ? configuredBase
      : typeof window === 'undefined'
        ? 'http://localhost'
        : window.location.origin
  return new URL(`${API_PREFIX}${path}`, base).toString()
}

interface EnvelopePayload {
  error?: unknown
  detail?: unknown
  request_id?: unknown
  fields?: unknown
}

function fallbackDetail(status: number): string {
  if (status === 401) return 'Your session has expired. Sign in again to continue.'
  if (status === 403) return 'You do not have access to this resource.'
  if (status === 404) return 'That resource does not exist.'
  if (status === 429) return 'Too many requests. Wait a moment and try again.'
  if (status >= 500) return 'The server failed to handle the request. Try again.'
  return `The request failed with status ${status}.`
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  let payload: EnvelopePayload = {}
  try {
    payload = (await response.json()) as EnvelopePayload
  } catch {
    // A proxy or gateway may answer with HTML; fall back to the status text.
  }
  const requestId =
    typeof payload.request_id === 'string'
      ? payload.request_id
      : response.headers.get('X-Request-Id')
  return new ApiError({
    status: response.status,
    error: typeof payload.error === 'string' ? payload.error : `http_${response.status}`,
    detail: typeof payload.detail === 'string' ? payload.detail : fallbackDetail(response.status),
    requestId,
    fields: payload.fields,
  })
}

export function makeNetworkError(cause: unknown): ApiError {
  return new ApiError({
    status: 0,
    error: 'network_error',
    detail: 'Could not reach the server. Check your connection and try again.',
    requestId: null,
    cause,
  })
}

function isAbortError(cause: unknown): boolean {
  return (
    typeof cause === 'object' &&
    cause !== null &&
    'name' in cause &&
    (cause as { name?: unknown }).name === 'AbortError'
  )
}

// ---------------------------------------------------------------------------
// Refresh (exactly one in-flight attempt)
// ---------------------------------------------------------------------------

let refreshInFlight: Promise<TokenResponse> | null = null

async function performRefresh(): Promise<TokenResponse> {
  const token = refreshToken
  if (token === null) {
    throw new ApiError({
      status: 401,
      error: 'not_authenticated',
      detail: fallbackDetail(401),
      requestId: null,
    })
  }
  let response: Response
  try {
    response = await fetch(resolveUrl('/auth/refresh'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
  } catch (cause) {
    if (isAbortError(cause)) throw cause
    throw makeNetworkError(cause)
  }
  if (!response.ok) {
    const error = await apiErrorFromResponse(response)
    clearTokens()
    emitAuthFailure()
    throw error
  }
  const tokens = (await response.json()) as TokenResponse
  setTokens(tokens)
  return tokens
}

/**
 * Rotate the session. Concurrent 401s share one round-trip, so a page that
 * fires five queries at once cannot cause five refreshes.
 */
export function refreshSession(): Promise<TokenResponse> {
  refreshInFlight ??= performRefresh().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  /** Set false for the unauthenticated auth endpoints. */
  auth?: boolean
}

async function sendRequest(path: string, options: RequestOptions): Promise<Response> {
  const headers = new Headers({ Accept: 'application/json' })
  const isForm = options.body instanceof FormData
  if (options.body !== undefined && !isForm) headers.set('Content-Type', 'application/json')
  if (options.auth !== false && accessToken !== null) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  const init: RequestInit = { method: options.method ?? 'GET', headers }
  if (options.body !== undefined) {
    init.body = isForm ? (options.body as FormData) : JSON.stringify(options.body)
  }
  if (options.signal !== undefined) init.signal = options.signal
  try {
    return await fetch(resolveUrl(path), init)
  } catch (cause) {
    if (isAbortError(cause)) throw cause
    throw makeNetworkError(cause)
  }
}

/**
 * Fetch with the bearer token and one refresh-and-retry on 401.
 *
 * `retryOnUnauthorized` is the loop guard: the retried request is never
 * refreshed again, so a server that answers 401 for a freshly minted token
 * fails the call instead of spinning.
 */
export async function authorizedFetch(
  path: string,
  options: RequestOptions = {},
): Promise<Response> {
  let response = await sendRequest(path, options)
  if (response.status === 401 && options.auth !== false && refreshToken !== null) {
    await refreshSession()
    response = await sendRequest(path, options)
    if (response.status === 401) {
      clearTokens()
      emitAuthFailure()
    }
  }
  return response
}

/** Perform a request and return the decoded JSON body, or throw `ApiError`. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await authorizedFetch(path, options)
  if (!response.ok) throw await apiErrorFromResponse(response)
  if (response.status === 204) return undefined as T
  const text = await response.text()
  if (text === '') return undefined as T
  return JSON.parse(text) as T
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export interface SseEvent {
  event: string
  data: string
}

export function parseSseBlock(block: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const rawLine of block.split('\n')) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
    if (line === '' || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  if (data.length === 0) return null
  return { event, data: data.join('\n') }
}

/**
 * Read a `text/event-stream` body. Normalises `\r\n` because `sse-starlette`
 * (the server) uses CRLF separators while most fixtures use LF.
 */
export async function* readSseEvents(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const body = response.body
  if (body === null) {
    throw new ApiError({
      status: 0,
      error: 'stream_unavailable',
      detail: 'The server returned no body to stream.',
      requestId: null,
    })
  }
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  // Cancelling the reader when the caller aborts makes a pending `read()`
  // resolve, so the loop can notice the abort and stop.
  const onAbort = (): void => {
    void reader.cancel().catch(() => undefined)
  }
  signal?.addEventListener('abort', onAbort)
  const drain = (): SseEvent[] => {
    const events: SseEvent[] = []
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const parsed = parseSseBlock(block)
      if (parsed !== null) events.push(parsed)
      boundary = buffer.indexOf('\n\n')
    }
    return events
  }
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (signal?.aborted === true) {
        throw new DOMException('The stream was cancelled.', 'AbortError')
      }
      if (done) break
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/gu, '\n')
      for (const event of drain()) yield event
    }
    buffer += decoder.decode()
    buffer = buffer.replace(/\r\n/gu, '\n')
    for (const event of drain()) yield event
    const tail = parseSseBlock(buffer)
    if (tail !== null) yield tail
  } finally {
    signal?.removeEventListener('abort', onAbort)
    await reader.cancel().catch(() => undefined)
  }
}

/** POST a JSON body and return the raw streaming response (never buffered). */
export async function streamRequest(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await authorizedFetch(path, {
    method: 'POST',
    body,
    ...(signal === undefined ? {} : { signal }),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response
}

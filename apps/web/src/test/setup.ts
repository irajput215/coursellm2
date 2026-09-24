import '@testing-library/jest-dom/vitest'

import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { clearTokens } from '@/api/client'
import { __resetThemeForTests } from '@/hooks/useTheme'

import { server } from './server'

// jsdom does not always expose the WHATWG streams/encoding globals that the
// streaming chat transport and MSW's Response need.
if (globalThis.TextEncoder === undefined) {
  const { TextEncoder } = await import('node:util')
  globalThis.TextEncoder = TextEncoder
}
if (globalThis.TextDecoder === undefined) {
  const { TextDecoder } = await import('node:util')
  globalThis.TextDecoder = TextDecoder
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(() => {
  server.resetHandlers()
  cleanup()
  clearTokens()
  __resetThemeForTests()
  window.sessionStorage.clear()
  window.localStorage.clear()
})

afterAll(() => {
  server.close()
})

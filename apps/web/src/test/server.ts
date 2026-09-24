import { setupServer } from 'msw/node'

import { handlers } from './handlers'

/**
 * All network access in the test suite goes through this MSW server.
 * `onUnhandledRequest: 'error'` is set in `setup.ts`, so a test that forgets a
 * handler fails loudly instead of reaching a real API.
 */
export const server = setupServer(...handlers)

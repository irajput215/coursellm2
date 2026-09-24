import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  })
}

export interface RenderOptions {
  route?: string
  queryClient?: QueryClient
}

/** Render a page with a fresh QueryClient and an in-memory router. */
export function renderWithProviders(
  ui: ReactElement,
  options: RenderOptions = {},
): RenderResult & { queryClient: QueryClient } {
  const queryClient = options.queryClient ?? createTestQueryClient()
  const result = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[options.route ?? '/']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
  return Object.assign(result, { queryClient })
}

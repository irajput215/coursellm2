import { QueryClient } from '@tanstack/react-query'

import { ApiError } from '@/api/client'

/**
 * Retry policy.
 *
 * A 4xx is a decision, not a blip: retrying a 404 or a validation error only
 * delays the error state. Network failures and 5xx responses get two retries.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: shouldRetry,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

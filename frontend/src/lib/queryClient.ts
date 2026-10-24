import { QueryClient } from '@tanstack/react-query'

/** Whether a failed query is worth asking again (ai-trainer-ops#267).
 *
 * Only what a retry can mend: no answer at all, or the server's own failure,
 * and twice at most. A 4xx is the answer, so it shows at once, not after the
 * library's three retries and seven seconds of backoff. A request that ran out
 * of time is not asked again either: three of them would keep the athlete
 * waiting a minute and a half. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  const { status, timedOut } = (error ?? {}) as { status?: number; timedOut?: boolean }
  if (failureCount >= 2 || timedOut || status === undefined) return false
  return status === 0 || status >= 500
}

/** The single react-query client, shared so sign-out can clear it (ops#130).
 *
 * On a shared device the next account must not briefly see the previous one's
 * cached data, so ``endSession`` calls ``queryClient.clear()``. That needs one
 * instance both the provider and the sign-out path can reach. */
export const queryClient = new QueryClient({ defaultOptions: { queries: { retry: shouldRetry } } })

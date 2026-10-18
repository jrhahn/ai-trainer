import { QueryClient } from '@tanstack/react-query'

/** The single react-query client, shared so sign-out can clear it (ops#130).
 *
 * On a shared device the next account must not briefly see the previous one's
 * cached data, so ``endSession`` calls ``queryClient.clear()``. That needs one
 * instance both the provider and the sign-out path can reach. */
export const queryClient = new QueryClient()

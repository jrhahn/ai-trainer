import { useAppStore } from '../store/useAppStore'

/** The session token, for a page or hook that only works signed in
 *  (ai-trainer-ops#275, in place of a non-null assertion on the store's token).
 *
 * `''` while signed out, which `apiFetch` treats as no token, the same as the
 * `null` the assertion passed on. The queries keep their `enabled: !!authToken`,
 * so they still wait for a session. */
export function useAuthToken(): string {
  return useAppStore((s) => s.authToken) ?? ''
}

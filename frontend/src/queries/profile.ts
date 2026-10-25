import { skipToken, useQuery } from '@tanstack/react-query'
import { queryClient } from '../lib/queryClient'
import type { UserProfile } from '../types/domain'
import { PROFILE_QUERY_KEY } from './keys'

/*
 * The athlete's profile, the second slice of server state out of the store
 * (ai-trainer-ops#273). It is held like the plan in plan.ts: in the app's one
 * query client, filled by loadUserData, and cleared by sign-out. It never
 * fetches by itself. Every write goes through setProfile.
 */

const PROFILE = [PROFILE_QUERY_KEY] as const
queryClient.setQueryDefaults(PROFILE, { staleTime: Infinity, gcTime: Infinity })

/** The profile outside React: for an event handler or a hook's callback. */
export function getProfile(): UserProfile | null {
  return queryClient.getQueryData<UserProfile | null>(PROFILE) ?? null
}

/** The profile for a component: it renders again when the profile changes. */
export function useProfile(): UserProfile | null {
  return useQuery<UserProfile | null>({ queryKey: PROFILE, queryFn: skipToken }, queryClient).data ?? null
}

/** The profile the server stored or sent, in place of the one held. */
export function setProfile(profile: UserProfile | null): void {
  queryClient.setQueryData<UserProfile | null>(PROFILE, profile)
}

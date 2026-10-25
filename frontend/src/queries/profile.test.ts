import { beforeEach, describe, expect, it } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAppStore } from '../store/useAppStore'
import type { UserProfile } from '../types/domain'
import { getProfile, setProfile, useProfile } from './profile'

const profile = { name: 'Mara', email: 'mara@example.com', weeklyHours: 6 } as UserProfile

beforeEach(() => {
  useAppStore.getState().resetAll()
})

describe('the profile in react-query (ai-trainer-ops#273)', () => {
  it('is none until one is set, and the one set after', () => {
    expect(getProfile()).toBeNull()
    setProfile(profile)
    expect(getProfile()).toEqual(profile)
  })

  it('renders a component again when the profile changes', async () => {
    setProfile(profile)
    const { result } = renderHook(() => useProfile())
    expect(result.current?.weeklyHours).toBe(6)

    // react-query hands a cache change to its components a tick later.
    act(() => setProfile({ ...profile, weeklyHours: 8 }))
    await waitFor(() => expect(result.current?.weeklyHours).toBe(8))
  })

  it('is gone after sign-out', () => {
    setProfile(profile)
    useAppStore.getState().resetAll()
    expect(getProfile()).toBeNull()
  })
})

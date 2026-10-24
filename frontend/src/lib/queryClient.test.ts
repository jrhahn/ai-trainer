import { describe, expect, it } from 'vitest'
import { queryClient, shouldRetry } from './queryClient'

describe('shouldRetry (ai-trainer-ops#267)', () => {
  it('asks again only when there was no answer or the server failed, twice at most', () => {
    expect(shouldRetry(0, { status: 0 })).toBe(true)
    expect(shouldRetry(1, { status: 503 })).toBe(true)
    expect(shouldRetry(2, { status: 503 })).toBe(false)
  })

  it('shows an answer, a timeout or an unknown failure at once', () => {
    expect(shouldRetry(0, { status: 404 })).toBe(false)
    expect(shouldRetry(0, { status: 402 })).toBe(false)
    expect(shouldRetry(0, { status: 0, timedOut: true })).toBe(false)
    expect(shouldRetry(0, new Error('no status'))).toBe(false)
    expect(shouldRetry(0, null)).toBe(false)
  })

  it('is what the shared client uses', () => {
    expect(queryClient.getDefaultOptions().queries?.retry).toBe(shouldRetry)
  })
})

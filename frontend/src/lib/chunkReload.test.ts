import { afterEach, describe, expect, it, vi } from 'vitest'
import { reloadingForChunk, reloadOnceForMissingChunk } from './chunkReload'

const missingChunk = () => new Event('vite:preloadError', { cancelable: true })

describe('reloadOnceForMissingChunk (ai-trainer-ops#275)', () => {
  afterEach(() => {
    sessionStorage.clear()
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('loads the page again once when a chunk is gone, and lets a second failure through', () => {
    const reload = vi.fn()
    const first = missingChunk()
    const second = missingChunk()

    reloadOnceForMissingChunk(first, reload)
    reloadOnceForMissingChunk(second, reload)

    expect(reload).toHaveBeenCalledTimes(1)
    expect(reloadingForChunk()).toBe(true)
    expect(first.defaultPrevented).toBe(true)
    expect(second.defaultPrevented).toBe(false)
  })

  it('reloads again for the next deploy, later in the same tab', () => {
    vi.useFakeTimers()
    const reload = vi.fn()

    reloadOnceForMissingChunk(missingChunk(), reload)
    vi.advanceTimersByTime(60_000)
    reloadOnceForMissingChunk(missingChunk(), reload)

    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('does not reload without storage, which would leave nothing to stop a loop', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const reload = vi.fn()
    const event = missingChunk()

    reloadOnceForMissingChunk(event, reload)

    expect(reload).not.toHaveBeenCalled()
    expect(event.defaultPrevented).toBe(false)
  })
})
